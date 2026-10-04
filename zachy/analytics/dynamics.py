"""Running dynamics and Garmin's extra metrics, read from the original FIT file on demand.

Documented fields: ground contact time (stance_time, ms), vertical oscillation (mm), vertical
ratio (%), step length (mm). Undocumented Garmin fields, identified on your own files (how they
behave over a run, not guesses):
- 137 / 138: stamina, potential and current (%): 100 at the start, falling with effort;
- 90: performance condition (−20 … +20), appears after the first minutes;
- 140: Garmin's own grade-adjusted speed (mm/s): equal to speed on the flat, ×1.3 at +10%;
- 143: body battery during the activity.
Plus the watch's temperature, and the gait of every second (standing / walking / running, from
cadence and speed). Garmin's impact load is only a total per activity (effort.py).
Read once per activity (the FIT file never changes) and kept in data/dynamics/<garmin_id>.json.
"""

import io
import json
import warnings
import zipfile
from pathlib import Path

import fitdecode
import numpy as np
import pandas as pd

from zachy.services.fit import _paused_before, zip_path

CACHE_DIR = Path(zip_path("x")).parent.parent / "dynamics"
UNKNOWN = {"unknown_137": "stamina_potential", "unknown_138": "stamina", "unknown_90": "perf_condition",
           "unknown_140": "garmin_gap_mms", "unknown_143": "body_battery"}
KNOWN = {"stance_time": "gct", "vertical_oscillation": "vo_mm", "vertical_ratio": "vr", "step_length": "step_mm",
         "temperature": "temperature", "cadence": "cad", "enhanced_speed": "speed"}
VERSION = 5
WALK_CADENCE = 140                  # steps/min: under = walking
IDLE_SPEED = 0.5                    # m/s: under = standing (watch still running)                         # bump to re-read cached files after adding a metric
MAX_POINTS = 2000


def _read(fit: bytes) -> tuple[pd.DataFrame, list[dict]]:
    rows, laps, timer_events = [], [], []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            with fitdecode.FitReader(io.BytesIO(fit)) as reader:
                for frame in reader:
                    if not isinstance(frame, fitdecode.FitDataMessage):
                        continue
                    if frame.name == "record":
                        row = {"timestamp": frame.get_value("timestamp", fallback=None),
                               "distance": frame.get_value("distance", fallback=None)}
                        for f in frame.fields:
                            name = KNOWN.get(f.name) or UNKNOWN.get(f.name)
                            if name and f.value is not None and not isinstance(f.value, (tuple, list)):
                                row[name] = f.value
                        if row["timestamp"] is not None:
                            rows.append(row)
                    elif frame.name == "lap":
                        laps.append({k: frame.get_value(k, fallback=None) for k in
                                     ("start_time", "timestamp", "total_timer_time", "avg_stance_time",
                                      "avg_vertical_oscillation", "avg_vertical_ratio", "avg_step_length",
                                      "normalized_power")})
                    elif frame.name == "event" and frame.get_value("event", fallback=None) == "timer":
                        timer_events.append((frame.get_value("timestamp", fallback=None),
                                             frame.get_value("event_type", fallback=None)))
        except fitdecode.FitError:
            pass
    df = pd.DataFrame(rows)
    if df.empty:
        return df, laps
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp", kind="stable").reset_index(drop=True)
    elapsed = (df["timestamp"] - df["timestamp"].iloc[0]).dt.total_seconds()
    df["timer_s"] = elapsed - _paused_before(df["timestamp"], timer_events)
    df["distance_km"] = pd.to_numeric(df["distance"], errors="coerce") / 1000
    return df, laps


def dynamics(garmin_id: str) -> dict | None:
    """{"series": {metric: [...]}, "distance_km": [...], "timer_s": [...], "laps": [...]}, cached."""
    cache = CACHE_DIR / f"{garmin_id}.json"
    if cache.exists():
        cached = json.loads(cache.read_text())
        if cached.get("version") == VERSION:
            return cached
    path = zip_path(garmin_id)
    if not path.exists():
        return None
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".fit")]
        if not names:
            return None
        df, laps = _read(z.read(names[0]))
    if df.empty:
        return None
    for c in ["gct", "vo_mm", "vr", "step_mm", "temperature", "cad", "speed", *UNKNOWN.values()]:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    # Zero means "not measured" for the running dynamics (standing, walking without the pod).
    for c in ("gct", "vo_mm", "vr", "step_mm"):
        if c in df:
            df.loc[df[c] <= 0, c] = np.nan
    # Gait of every second: 0 standing, 1 walking, 2 running (cadence and speed).
    if "cad" in df and "speed" in df:
        spm = df.cad.fillna(0) * 2          # the FIT file counts one leg (strides/min): steps = ×2
        df["gait"] = np.where(df.speed.fillna(0) < IDLE_SPEED, 0, np.where(spm < WALK_CADENCE, 1, 2))
    if "garmin_gap_mms" in df:
        speed = df["garmin_gap_mms"] / 1000
        df["garmin_gap"] = np.where(speed > 0.5, 1000 / speed.where(speed > 0.5) / 60, np.nan)   # min/km
    metrics = {"gct": ("gct", 1), "vo": ("vo_mm", 0.1), "vr": ("vr", 1), "step": ("step_mm", 0.001),
               "stamina": ("stamina", 1), "stamina_potential": ("stamina_potential", 1),
               "perf_condition": ("perf_condition", 1), "garmin_gap": ("garmin_gap", 1),
               "body_battery": ("body_battery", 1), "temperature": ("temperature", 1), "gait": ("gait", 1)}
    present = {k: (c, f) for k, (c, f) in metrics.items() if c in df and df[c].notna().sum() > 30}
    step = max(1, int(np.ceil(len(df) / MAX_POINTS)))
    thin = df.iloc[::step]
    gait_s = None
    if "gait" in df:
        dt = df.timer_s.diff().shift(-1).clip(lower=0, upper=30).fillna(0)
        gait_s = {name: int(dt[df.gait == k].sum()) for k, name in ((2, "run"), (1, "walk"), (0, "idle"))}
    out = {"version": VERSION, "gait_s": gait_s, "distance_km": thin["distance_km"].round(3).tolist(), "timer_s": thin["timer_s"].round().tolist(),
           "series": {k: [None if pd.isna(v) else round(float(v) * f, 3) for v in thin[c]] for k, (c, f) in present.items()},
           "laps": _laps(df, laps)}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out))
    return out


def _laps(df: pd.DataFrame, laps: list[dict]) -> list[dict]:
    """Per lap: Garmin's averages (contact time, oscillation, ratio, step length) and, from the
    records inside the lap, stamina at its end, mean performance condition and Garmin GAP pace."""
    out = []
    # A lap ends where the next one starts (the lap's own end timestamp is sometimes wrong, e.g.
    # the same value on every lap of a structured workout); the last one at the last record.
    starts = [l.get("start_time") for l in laps]
    for i, lap in enumerate(laps):
        start = starts[i]
        end = starts[i + 1] if i + 1 < len(starts) and starts[i + 1] else df.timestamp.iloc[-1]
        inside = df[(df.timestamp >= pd.Timestamp(start)) & (df.timestamp < pd.Timestamp(end))] if start else df.iloc[0:0]
        mean = lambda c: float(inside[c].mean()) if c in inside and inside[c].notna().any() else None
        last = lambda c: float(inside[c].dropna().iloc[-1]) if c in inside and inside[c].notna().any() else None
        gap_speed = mean("garmin_gap_mms")
        run_pct = None
        if "gait" in inside and len(inside):
            dt = inside.timer_s.diff().shift(-1).clip(lower=0, upper=30).fillna(0)
            run_pct = round(float(dt[inside.gait == 2].sum() / max(dt.sum(), 1) * 100))
        out.append({
            "gct": lap.get("avg_stance_time") or mean("gct"),
            "vo": round((lap.get("avg_vertical_oscillation") or mean("vo_mm") or 0) / 10, 1) or None,
            "vr": lap.get("avg_vertical_ratio") or mean("vr"),
            "step": round((lap.get("avg_step_length") or mean("step_mm") or 0) / 1000, 2) or None,
            "normalized_power": lap.get("normalized_power"),
            "stamina_end": last("stamina"),
            "perf_condition": round(mean("perf_condition"), 1) if mean("perf_condition") is not None else None,
            "garmin_gap": round(1000 / (gap_speed / 1000) / 60, 3) if gap_speed and gap_speed > 500 else None,
            "run_pct": run_pct,
            "temperature": round(mean("temperature"), 1) if mean("temperature") is not None else None,
        })
    return out
