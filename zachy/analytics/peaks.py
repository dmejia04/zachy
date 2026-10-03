"""Sustained extremes of an activity, from the full-resolution FIT track.

- 30-second extremes of HR, pace, power and cadence: the highest and lowest value held as a
  30 s (moving-time) average. For power, cadence and pace the low end only considers windows
  where you were moving the whole 30 s, so standing at a crossing doesn't count as "min power".
- Altitude range and the steepest climb/descent over 50 m of horizontal distance.
"""

import numpy as np
import pandas as pd

WINDOW_S = 30
GRADIENT_WINDOW_M = 50
MOVING_SPEED_MS = 0.5   # below this you're standing, not running


def _per_second(track: pd.DataFrame, col: str) -> pd.Series:
    """`col` on a 1 s grid of moving time (smart recording gaps of a few seconds are filled)."""
    s = track[["timer_s", col]].dropna().drop_duplicates("timer_s", keep="last")
    if len(s) < 2:
        return pd.Series(dtype=float)
    s = s.set_index("timer_s")[col]
    grid = np.arange(int(s.index.min()), int(s.index.max()) + 1)
    return s.reindex(s.index.union(grid)).interpolate(method="index", limit=10).reindex(grid)


def _extremes(values: pd.Series, track: pd.DataFrame, moving_only: bool) -> dict | None:
    if values.empty:
        return None
    if moving_only:
        values = values.where(values > 0)
    rolled = values.rolling(WINDOW_S, min_periods=WINDOW_S).mean().dropna()
    if rolled.empty:
        return None
    dist = _per_second(track, "distance_km")

    def at(t):   # where the 30 s window was centred
        mid = int(t - WINDOW_S / 2)
        return {"timer_s": mid, "distance_km": round(float(dist.get(mid)), 2) if mid in dist.index and pd.notna(dist.get(mid)) else None}

    lo_t, hi_t = rolled.idxmin(), rolled.idxmax()
    return {"min": float(rolled[lo_t]), "max": float(rolled[hi_t]),
            "min_at": at(lo_t), "max_at": at(hi_t)}


def sustained_extremes(track: pd.DataFrame) -> dict:
    """{"hr": {...}, "pace": {...}, "power": {...}, "cadence": {...}} — missing metrics omitted.
    Pace is in min/km: "min" is the fastest 30 s, "max" the slowest (while moving)."""
    out = {}
    if "hr" in track and track["hr"].notna().any():
        out["hr"] = _extremes(_per_second(track, "hr"), track, moving_only=False)
    for col in ("power", "cadence"):
        if col in track and (track[col] > 0).any():
            out[col] = _extremes(_per_second(track, col), track, moving_only=True)
    if "speed_ms" in track and (track["speed_ms"] > MOVING_SPEED_MS).any():
        speed = _per_second(track, "speed_ms")
        e = _extremes(speed.where(speed > MOVING_SPEED_MS), track, moving_only=True)
        if e:   # fastest speed = lowest pace
            to_pace = lambda v: 1000 / v / 60
            out["pace"] = {"min": to_pace(e["max"]), "max": to_pace(e["min"]),
                           "min_at": e["max_at"], "max_at": e["min_at"]}
    return {k: v for k, v in out.items() if v}


def elevation_extremes(track: pd.DataFrame) -> dict | None:
    """Altitude range, and steepest climb/descent (%) over 50 m of horizontal distance.

    The slope is only measured inside continuous stretches: never across a pause (the watch can
    resume somewhere else) nor across a GPS gap of more than 30 m, and altitude is smoothed over
    25 m first, so jitter and resume jumps don't show up as 300% walls.
    """
    t = track[[c for c in ("timer_s", "elapsed_s", "distance_km", "elevation") if c in track]]
    t = t.dropna(subset=["distance_km", "elevation"]).reset_index(drop=True)
    if t.empty:
        return None
    out = {"alt_min": float(t["elevation"].min()), "alt_max": float(t["elevation"].max()),
           "alt_min_km": round(float(t.loc[t["elevation"].idxmin(), "distance_km"]), 2),
           "alt_max_km": round(float(t.loc[t["elevation"].idxmax(), "distance_km"]), 2)}

    d = np.maximum.accumulate(t["distance_km"].to_numpy()) * 1000   # metres, never decreasing
    gap = np.diff(d) > 30                                             # GPS jump between points
    if "elapsed_s" in t and t["elapsed_s"].notna().all():
        paused = (np.diff(t["elapsed_s"].to_numpy()) - np.diff(t["timer_s"].to_numpy())) > 5
        gap |= paused
    segment = np.concatenate([[0], np.cumsum(gap)])

    best_hi, best_lo = None, None
    step, n = 5.0, int(GRADIENT_WINDOW_M / 5.0)
    for seg in np.unique(segment):
        m = segment == seg
        ds, es = d[m], t["elevation"].to_numpy()[m]
        keep = np.append(True, np.diff(ds) > 0)                       # strictly increasing x
        ds, es = ds[keep], es[keep]
        if len(ds) < 2 or ds[-1] - ds[0] < GRADIENT_WINDOW_M + 25:
            continue
        grid = np.arange(ds[0], ds[-1], step)
        elev = pd.Series(np.interp(grid, ds, es)).rolling(5, center=True, min_periods=1).mean().to_numpy()
        grad = (elev[n:] - elev[:-n]) / GRADIENT_WINDOW_M * 100
        hi, lo = int(np.argmax(grad)), int(np.argmin(grad))
        if best_hi is None or grad[hi] > best_hi[0]:
            best_hi = (grad[hi], grid[hi] + GRADIENT_WINDOW_M / 2)
        if best_lo is None or grad[lo] < best_lo[0]:
            best_lo = (grad[lo], grid[lo] + GRADIENT_WINDOW_M / 2)
    if best_hi:
        out.update({"grad_max": round(float(best_hi[0]), 1), "grad_max_km": round(float(best_hi[1]) / 1000, 2),
                    "grad_min": round(float(best_lo[0]), 1), "grad_min_km": round(float(best_lo[1]) / 1000, 2)})
    return out
