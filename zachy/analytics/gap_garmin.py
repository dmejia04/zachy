"""Garmin's grade-adjusted pace model, reconstructed from your own FIT files.

Garmin doesn't publish its GAP curve, but your watch records, every second, both your speed and
Garmin's grade-adjusted speed (undocumented record field 140, see dynamics.py). Their ratio is
Garmin's cost factor at the slope you were on. Over your recent hilly runs: the slope of every
second is measured on the track (altitude smoothed over ~50 m, slope over ~100 m, like gap.py),
the ratio collected, and the median per 1% of slope (enough data) smoothed into a curve.
Stored in gap_models (row 3).
"""

import io
import json
import warnings
import zipfile
from datetime import date, datetime, timezone

import fitdecode
import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from zachy.models import Activity, FitFile, GapModel
from zachy.services.fit import zip_path

MODEL_ID = 3
RUNS = 40
MAX_GRADE = 40


def _read(garmin_id: str) -> pd.DataFrame | None:
    path = zip_path(garmin_id)
    if not path.exists():
        return None
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".fit")]
        if not names:
            return None
        data = z.read(names[0])
    rows = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            with fitdecode.FitReader(io.BytesIO(data)) as reader:
                for f in reader:
                    if isinstance(f, fitdecode.FitDataMessage) and f.name == "record":
                        rows.append((f.get_value("distance", fallback=None), f.get_value("enhanced_altitude", fallback=None),
                                     f.get_value("enhanced_speed", fallback=None), f.get_value(140, fallback=None)))   # by number: unnamed field
        except fitdecode.FitError:
            pass
    df = pd.DataFrame(rows, columns=["d", "z", "v", "gap"]).apply(pd.to_numeric, errors="coerce").dropna()
    return df if len(df) > 600 else None


def _ratios(df: pd.DataFrame) -> pd.DataFrame:
    """Slope (%) and Garmin's cost factor (GAP speed ÷ speed) for every record while running."""
    d = np.maximum.accumulate(df.d.to_numpy())
    keep = np.append(True, np.diff(d) > 0)
    d, z, v, gap = d[keep], df.z.to_numpy()[keep], df.v.to_numpy()[keep], df.gap.to_numpy()[keep] / 1000
    grid = np.arange(d[0], d[-1], 10.0)
    zg = pd.Series(np.interp(grid, d, z)).rolling(5, center=True, min_periods=1).mean().to_numpy()
    slope_grid = (np.roll(zg, -5) - np.roll(zg, 5)) / 100
    slope_grid[:5], slope_grid[-5:] = np.nan, np.nan
    g = np.interp(d, grid, slope_grid) * 100
    ok = (v > 1.0) & (gap > 0.3) & np.isfinite(g) & (np.abs(g) <= MAX_GRADE)
    return pd.DataFrame({"g": g[ok], "ratio": gap[ok] / v[ok]})


def fit_garmin(db: Session) -> dict:
    """Read your recent hilly runs and store Garmin's curve as seen in your files."""
    acts = (db.query(Activity).join(FitFile, FitFile.activity_id == Activity.id)
            .filter(FitFile.status == "ok", Activity.activity_type.in_(["running", "trail_running", "ultra_run"]),
                    Activity.distance_km >= 8, Activity.elevation_gain / Activity.distance_km >= 20,
                    Activity.date >= date(2023, 1, 1))   # older watches didn't record it
            .order_by(Activity.date.desc()).limit(RUNS * 3).all())
    parts, used = [], 0
    for a in acts:
        df = _read(a.garmin_id)
        if df is None or df.gap.max() <= 0:
            continue
        parts.append(_ratios(df))
        used += 1
        if used >= RUNS:
            break
    if not parts:
        raise ValueError("No run with Garmin's grade-adjusted speed in its FIT file")
    allr = pd.concat(parts)
    allr = allr[allr.ratio.between(0.3, 6)]
    tab = allr.assign(b=allr.g.round()).groupby("b").ratio.agg(["median", "count"])
    tab = tab[tab["count"] >= 200]
    flat = float(tab.loc[0, "median"]) if 0 in tab.index else 1.0
    grades = np.arange(tab.index.min(), tab.index.max() + 1)
    med = np.interp(grades, tab.index.to_numpy(float), tab["median"].to_numpy() / flat)
    factors = pd.Series(med).rolling(3, center=True, min_periods=1).mean().to_numpy()
    model = {"grades": grades.astype(int).tolist(), "factors": np.round(factors, 4).tolist(),
             "bins": [{"grade": int(g), "median": round(float(m) / flat, 3), "count": int(c)} for g, (m, c) in tab.iterrows()],
             "runs": used, "seconds": int(len(allr)),
             "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    db.query(GapModel).filter(GapModel.id == MODEL_ID).delete()
    db.add(GapModel(id=MODEL_ID, data=json.dumps(model)))
    db.commit()
    return model


def garmin_model(db: Session) -> dict | None:
    row = db.get(GapModel, MODEL_ID)
    return json.loads(row.data) if row else None


def cost_garmin(g, model: dict | None):
    if not model:
        return np.full_like(np.asarray(g, float), np.nan)
    grades = np.array(model["grades"]) / 100
    return np.interp(np.clip(g, grades[0], grades[-1]), grades, model["factors"])
