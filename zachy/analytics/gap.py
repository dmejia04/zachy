"""Grade-adjusted pace: five ways to turn a hilly run into a flat-equivalent pace.

Every method is expressed the same way so they can be compared: a cost factor per slope — how
many flat metres one metre at that slope is worth (1.0 on the flat). The flat-equivalent distance
of a run is the sum of its metres × cost factor; adjusted pace = moving time ÷ that distance.

- km-effort (ITRA): 1 + 10 × slope when climbing (100 m up = 1 km), 1 when descending.
- Minetti: energy cost of running on slopes measured in the lab (Minetti et al., 2002),
  C(i) = 155.4i⁵ − 30.4i⁴ − 43.3i³ + 46.3i² + 19.5i + 3.6 J/kg/m, divided by its flat value 3.6.
- Strava-like: Strava's own curve is not published. This is an approximation of the shape shown
  in their 2017 GAP write-up — cheapest around −10% (≈ 0.88), +10% ≈ 1.36, +20% ≈ 1.96.
- Personal: fitted from your runs — heartbeats per metre on each slope divided by heartbeats per
  metre on the flat in the same run (so fitness, heat and fatigue cancel out), one-minute windows,
  median per 1% slope bin, smoothed.
- Race model: fitted from your trail races so they come out as flat as possible (gap_race.py).
"""

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from zachy.analytics.elevation import moving_elevation
from zachy.models import Activity, FitFile, GapModel, Record

MAX_GRADE = 0.45
METHODS = ["km_effort", "minetti", "strava", "personal", "race"]
LABELS = {"km_effort": "km-effort", "minetti": "Minetti", "strava": "Strava-like", "personal": "Personal",
          "race": "Race model"}


# ---------- the four cost curves (slope as a fraction, e.g. 0.10 = 10%) ----------

def cost_km_effort(g):
    return 1 + 10 * np.maximum(g, 0)


def cost_minetti(g):
    i = np.clip(g, -MAX_GRADE, MAX_GRADE)
    return (155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + 19.5 * i + 3.6) / 3.6


def cost_strava(g):
    i = np.clip(g, -MAX_GRADE, MAX_GRADE)
    return 1 + 2.4 * i + 12 * i**2


def cost_personal(g, model: dict | None):
    if not model:
        return np.full_like(np.asarray(g, float), np.nan)
    grades = np.array(model["grades"]) / 100
    return np.interp(np.clip(g, grades[0], grades[-1]), grades, model["factors"])


def costs(g, model: dict | None, race: dict | None = None) -> dict:
    """Every model's cost factor per slope. race: the race model (gap_race.py), if fitted."""
    from zachy.analytics.gap_race import cost_race
    return {"km_effort": cost_km_effort(g), "minetti": cost_minetti(g),
            "strava": cost_strava(g), "personal": cost_personal(g, model), "race": cost_race(g, race)}


# ---------- personal model ----------

def fit_personal(db: Session, window_s: int = 60) -> dict:
    """Fit the personal cost curve from all runs with HR, and store it (gap_models)."""
    runs = (db.query(Activity.id, Activity.date).join(FitFile, FitFile.activity_id == Activity.id)
            .filter(FitFile.status == "ok", Activity.distance_km >= 5,
                    Activity.activity_type.in_(["running", "trail_running", "ultra_run"])).all())
    parts = []
    for aid, day in runs:
        rows = (db.query(Record.timer_s, Record.elapsed_s, Record.distance_km, Record.elevation, Record.hr,
                         Record.cadence)
                .filter(Record.activity_id == aid).order_by(Record.id).all())
        r = (pd.DataFrame(rows, columns=["t", "el", "d", "z", "hr", "cad"]).astype(float)
             .dropna(subset=["t", "el", "d", "z", "hr"]))
        if len(r) < 600:
            continue
        r["z"] = moving_elevation(r.z, r.t, r.el)
        r = r.drop_duplicates("t").set_index("t")
        grid = np.arange(int(r.index.min()), int(r.index.max()) + 1)
        r = r.reindex(r.index.union(grid)).interpolate(limit=10).reindex(grid)
        z = r.z.rolling(15, center=True, min_periods=5).mean()
        w = pd.DataFrame({
            "d": r.d.diff(window_s) * 1000, "dz": z.diff(window_s), "hr": r.hr.rolling(window_s).mean(),
            "cad": r.cad.rolling(window_s, min_periods=10).median(),
            "paused": (r.el.diff(window_s) - window_s) > 5,
        }).iloc[window_s::window_s]
        # Warmed up (after 10 min), moving, no GPS jump, aerobic-to-hard HR.
        w = w[(w.index >= 600) & ~w.paused & (w.d >= 30) & (w.d / window_s < 7) & w.hr.between(100, 178)]
        w = w.assign(g=w.dz / w.d)
        w = w[w.g.abs() <= MAX_GRADE]
        cost = w.hr * window_s / w.d                       # heartbeats per metre
        flat = cost[w.g.abs() < 0.01]
        if len(flat) < 5:
            continue
        parts.append(pd.DataFrame({"g": w.g, "rel": cost / flat.median(), "aid": aid, "year": day.year,
                                   "cad": w.cad, "vert": w.dz / window_s * 3600}))
    df = pd.concat(parts)
    df["bin"] = (df.g * 100).round()
    grades, factors, tab = _smooth_curve(df, min_count=30, max_grade=40)
    # The same curve per year, to see how climbing / descending evolve (years with enough data,
    # over the slopes each year actually covers).
    by_year = {}
    for year, part in df.groupby("year"):
        if len(part) < 2000:
            continue
        g_y, f_y, _ = _smooth_curve(part, min_count=15, max_grade=30)
        if g_y is not None:
            by_year[str(year)] = {"grades": g_y.tolist(), "factors": np.round(f_y, 4).tolist(),
                                  "windows": int(len(part)), "runs": int(part.aid.nunique())}
    model = {
        "grades": grades.tolist(), "factors": np.round(factors, 4).tolist(),
        "bins": [{"grade": int(g), "median": round(float(m), 3), "count": int(c)}
                 for g, (m, c) in tab.iterrows()],
        "windows": int(len(df)), "runs": int(df.aid.nunique()), "by_year": by_year,
        "walk_run": walk_vs_run(df),
        "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    db.query(GapModel).filter(GapModel.id == 1).delete()   # row 2 is the race model (gap_race.py)
    db.add(GapModel(id=1, data=json.dumps(model)))
    db.commit()
    return model


WALK_CADENCE = 140   # steps/min: below = walking / power-hiking, above = running


def walk_vs_run(df: pd.DataFrame) -> dict:
    """Per 2% slope band: how often you walk, and walking vs running vertical speed and cost
    (heartbeats per metre relative to your flat running). Plus two switch points on climbs:
    where you start walking most of the time, and where walking becomes the cheaper option."""
    d = df[df.cad.notna() & (df.cad > 40)].copy()
    d["walk"] = d.cad < WALK_CADENCE
    d["band"] = (d.g * 50).round() * 2                         # 2% bands
    rows = []
    for band, part in d[d.band.abs() <= 40].groupby("band"):
        walk, run = part[part.walk], part[~part.walk]
        rows.append({
            "grade": int(band), "minutes": int(len(part)), "walk_share": round(float(part.walk.mean()), 3),
            "walk_cost": round(float(walk.rel.median()), 3) if len(walk) >= 15 else None,
            "run_cost": round(float(run.rel.median()), 3) if len(run) >= 15 else None,
            "walk_vert": round(float(walk.vert.median())) if len(walk) >= 15 and band > 0 else None,
            "run_vert": round(float(run.vert.median())) if len(run) >= 15 and band > 0 else None,
        })
    climbs = [r for r in rows if r["grade"] > 0]
    starts_walking = next((r["grade"] for r in climbs if r["walk_share"] >= 0.5), None)
    walking_cheaper = next((r["grade"] for r in climbs
                            if r["walk_cost"] and r["run_cost"] and r["walk_cost"] <= r["run_cost"]), None)
    # Heart rate is biased against walking (it doesn't drop as fast as speed, and you walk when tired),
    # so also report where walking gets within 5% of running.
    nearly_equal = next((r["grade"] for r in climbs
                         if r["walk_cost"] and r["run_cost"] and r["walk_cost"] <= 1.05 * r["run_cost"]), None)
    return {"bands": rows, "starts_walking": starts_walking, "walking_cheaper": walking_cheaper,
            "nearly_equal": nearly_equal, "cadence_threshold": WALK_CADENCE}


def _smooth_curve(df: pd.DataFrame, min_count: int, max_grade: int):
    """Median cost per 1% bin, then a weighted polynomial through it, scaled so the flat is 1.
    Returns (grades %, factors, bin table) or (None, None, table) when too few bins."""
    tab = df.groupby("bin").rel.agg(["median", "count"])
    tab = tab[(tab["count"] >= min_count) & (tab.index.to_series().abs() <= max_grade)]
    if len(tab) < 12 or tab.index.min() > -10 or tab.index.max() < 10:
        return None, None, tab
    x, y, w = tab.index.to_numpy(float), tab["median"].to_numpy(), np.sqrt(tab["count"].to_numpy())
    coef = np.polyfit(x, y, 5, w=w)
    grades = np.arange(x.min(), x.max() + 1)
    return grades, np.polyval(coef, grades) / np.polyval(coef, 0), tab


def personal_model(db: Session) -> dict | None:
    row = db.get(GapModel, 1)
    return json.loads(row.data) if row else None


# ---------- per activity ----------

def profile(db: Session, activity: Activity, step_m: float = 10, with_distance: bool = False):
    """Slope (fraction) of every 10 m of the run: altitude smoothed over ~50 m, slope over ~100 m.
    with_distance=True also returns where each 10 m piece starts (km)."""
    rows = (db.query(Record.timer_s, Record.elapsed_s, Record.distance_km, Record.elevation)
            .filter(Record.activity_id == activity.id).order_by(Record.id).all())
    r = np.array(rows, float).reshape(-1, 4)
    r[:, 3] = moving_elevation(r[:, 3], r[:, 0], r[:, 1])   # altimeter jumps and lifts aren't slope
    r = r[np.isfinite(r[:, 2]) & np.isfinite(r[:, 3])]
    if len(r) < 10:
        return None
    d = np.maximum.accumulate(r[:, 2]) * 1000
    keep = np.append(True, np.diff(d) > 0)
    d, z = d[keep], r[keep, 3]
    if d[-1] - d[0] < 500:
        return None
    grid = np.arange(d[0], d[-1], step_m)
    zg = pd.Series(np.interp(grid, d, z)).rolling(5, center=True, min_periods=1).mean().to_numpy()
    half = 5   # 5 × 10 m on each side = 100 m
    ahead, behind = np.roll(zg, -half), np.roll(zg, half)
    ahead[-half:], behind[:half] = zg[-1], zg[0]
    span = np.full(len(zg), 2 * half * step_m)
    span[:half] = (np.arange(half) + half) * step_m
    span[-half:] = (np.arange(half)[::-1] + half) * step_m
    grades = np.clip((ahead - behind) / span, -MAX_GRADE, MAX_GRADE)
    return (grid / 1000, grades) if with_distance else grades


def split_paces(db: Session, activity: Activity, splits: list[dict]) -> list[float | None]:
    """Personal flat-equivalent pace (min/km) of each km split (dicts with end_km, distance_km,
    duration_s, as from analytics/splits.km_splits)."""
    model = personal_model(db)
    prof = profile(db, activity, with_distance=True)
    if not model or prof is None:
        return [None] * len(splits)
    at_km, g = prof
    factor = cost_personal(g, model)
    out = []
    for s in splits:
        start = s["end_km"] - s["distance_km"]
        inside = (at_km >= start) & (at_km < s["end_km"])
        if not inside.any() or not s["duration_s"]:
            out.append(None)
            continue
        equivalent_km = float(factor[inside].mean()) * s["distance_km"]
        out.append(round(s["duration_s"] / 60 / equivalent_km, 3))
    return out


_cache: dict[tuple, dict | None] = {}


def adjusted_paces(db: Session, activity: Activity) -> dict | None:
    """Flat-equivalent pace (min/km) of a run with each method, plus the actual pace.
    Kept in memory per (activity, personal model version): FIT records never change."""
    from zachy.analytics.gap_race import race_model
    model, race = personal_model(db), race_model(db)
    key = (activity.id, model and model["fitted_at"], race and race["fitted_at"], activity.distance_km,
           activity.duration_s, activity.elevation_gain)
    if key not in _cache:
        _cache[key] = _adjusted_paces(db, activity, model, race)
    return _cache[key]


def _adjusted_paces(db: Session, activity: Activity, model: dict | None, race: dict | None = None) -> dict | None:
    if not activity.distance_km or not activity.duration_s:
        return None
    g = profile(db, activity)
    if g is None:
        return None
    minutes = activity.duration_s / 60
    out = {"actual": round(minutes / activity.distance_km, 3)}
    # km-effort uses the official climb (its standard definition); the others the slope profile,
    # scaled to the activity's official distance.
    out["km_effort"] = round(minutes / (activity.distance_km + (activity.elevation_gain or 0) / 100), 3)
    scale = activity.distance_km / (len(g) * 0.01)
    for m, c in costs(g, model, race).items():
        if m == "km_effort" or np.isnan(c).all():
            continue
        equivalent_km = float(np.sum(c) * 0.01 * scale)
        out[m] = round(minutes / equivalent_km, 3)
    return out


def curves(db: Session) -> dict:
    """All cost curves from −40% to +40%, plus the personal and race models' data, for the chart."""
    from zachy.analytics.gap_race import race_model
    model, race = personal_model(db), race_model(db)
    grades = np.arange(-40, 41)
    c = costs(grades / 100, model, race)
    return {
        "grades": grades.tolist(),
        "curves": {m: [None if np.isnan(v) else round(float(v), 3) for v in c[m]] for m in METHODS},
        "labels": LABELS,
        "personal": model and {k: model.get(k) for k in ("bins", "windows", "runs", "fitted_at", "by_year", "walk_run")},
        "race": race and {k: race.get(k) for k in ("n_races", "segments", "summary", "races", "beta_hr", "gamma_fatigue",
                                                   "fitted_at")},
    }
