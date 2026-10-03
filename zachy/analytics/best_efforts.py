"""Best efforts: the fastest stretch of exactly 5 km, 10 km, half and marathon inside any run,
from the per-second FIT records — so a 5 km record can be a split of a 10 km race.

For every starting point, the time at which the distance was reached is interpolated between
records; the shortest such time is the best effort. Moving time (watch pauses removed).

GPS jumps (more than 25 m at over 8 m/s between two records — ordinary 1-second jitter is
smaller) are repaired: the jumped distance is replaced by what the surrounding pace covers in that
time, so one glitch doesn't spoil a marathon. A stretch doesn't count if it's implausibly fast
overall (under 2:35/km),
if it runs downhill by more than 5 m per km (like official records, which allow ~1 m/km), or if
the average heart rate is under 60% of max (the watch was in a car or a bus). Watch timers that
reset mid-file are stitched back into one continuous moving time.
"""

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from zachy.analytics.races import robust_max_hr
from zachy.analytics.terrain import FOOT_TYPES
from zachy.models import Activity, BestEffort, FitFile, Record

EFFORTS = [("5k", 5.0), ("10k", 10.0), ("half", 21.0975), ("marathon", 42.195)]
EFFORT_TYPES = FOOT_TYPES - {"treadmill_running"}   # treadmill distance isn't reliable
JUMP_SPEED_MS = 8.0          # a GPS jump: faster than this between two records…
JUMP_MIN_M = 25.0            # …and longer than this (1-second jitter is ~10 m)
MAX_EFFORT_SPEED_MS = 6.45   # 2:35/km: anything faster over a whole effort is a glitch
MAX_DROP_M_PER_KM = 5.0      # downhill efforts don't count as records
MIN_HR_FRACTION = 0.60       # below this share of max HR, you weren't running it


def best_efforts_for(timer_s: np.ndarray, distance_km: np.ndarray,
                     elevation: np.ndarray | None = None, hr: np.ndarray | None = None,
                     max_hr: float | None = None) -> dict[str, tuple[float, float]]:
    """{"5k": (seconds, start_km), …} for the distances the run is long enough for."""
    nan = np.full_like(timer_s, np.nan)
    elevation = nan if elevation is None else elevation
    hr = nan if hr is None else hr
    ok = ~(np.isnan(timer_s) | np.isnan(distance_km))
    t, d, e, h = timer_s[ok], np.maximum.accumulate(distance_km[ok]), elevation[ok], hr[ok]
    if not len(t):
        return {}
    # One continuous moving time even if the watch timer reset during the activity.
    t = t[0] + np.concatenate([[0], np.cumsum(np.clip(np.diff(t), 0, None))])
    keep = np.append(True, (np.diff(d) > 0) & (np.diff(t) > 0))   # both strictly increasing
    t, d, e, h = t[keep], d[keep], e[keep], h[keep]
    # Repair GPS jumps: replace the jumped distance by the local median speed × time.
    seg_m, seg_t = np.diff(d) * 1000, np.diff(t)
    speed = seg_m / seg_t
    jump = (speed > JUMP_SPEED_MS) & (seg_m > JUMP_MIN_M)
    if jump.any():
        local = (pd.Series(np.where(jump, np.nan, speed))
                 .rolling(21, center=True, min_periods=1).median().to_numpy())
        seg_m = np.where(jump, np.nan_to_num(local, nan=3.0) * seg_t, seg_m)
        d = d[0] + np.concatenate([[0], np.cumsum(seg_m)]) / 1000
    # Cumulative heart beats (HR × seconds) to read any stretch's average HR quickly.
    h_ok = np.isfinite(h)
    beats = np.concatenate([[0], np.cumsum(np.where(h_ok[1:], h[1:], 0) * np.diff(t))])
    hr_secs = np.concatenate([[0], np.cumsum(np.where(h_ok[1:], np.diff(t), 0))])
    has_elev = np.isfinite(e).sum() > len(e) / 2
    if has_elev:   # fill small gaps so start/end altitude can be read anywhere
        idx = np.arange(len(e))
        e = np.interp(idx, idx[np.isfinite(e)], e[np.isfinite(e)])
    if len(d) < 2:
        return {}
    out = {}
    for key, km in EFFORTS:
        if d[-1] - d[0] < km:
            continue
        starts = np.nonzero(d <= d[-1] - km)[0]
        end_d = d[starts] + km
        end_t = np.interp(end_d, d, t)
        end_i = np.searchsorted(d, end_d)              # first record at or after the end
        durations = end_t - t[starts]
        clean = durations >= km * 1000 / MAX_EFFORT_SPEED_MS
        if has_elev:
            drop = e[starts] - np.interp(end_d, d, e)          # positive = net downhill
            clean &= drop <= MAX_DROP_M_PER_KM * km
        if max_hr:
            j = np.minimum(end_i, len(d) - 1)
            secs = hr_secs[j] - hr_secs[starts]
            avg_hr = np.where(secs > 0, (beats[j] - beats[starts]) / np.maximum(secs, 1e-9), np.nan)
            clean &= ~(avg_hr < MIN_HR_FRACTION * max_hr)       # no HR recorded: keep
        if not clean.any():
            continue
        durations = np.where(clean, durations, np.inf)
        i = int(np.argmin(durations))
        out[key] = (float(durations[i]), float(d[starts[i]]))
    return out


def compute_for_activity(db: Session, activity: Activity) -> int:
    """(Re)compute and store one activity's best efforts. Returns how many distances it has."""
    rows = (db.query(Record.timer_s, Record.distance_km, Record.elevation, Record.hr)
            .filter(Record.activity_id == activity.id).order_by(Record.id).all())
    db.query(BestEffort).filter(BestEffort.activity_id == activity.id).delete()
    efforts = {}
    if rows:
        arr = np.array(rows, dtype=float)
        efforts = best_efforts_for(arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3],
                                   robust_max_hr(db, activity.date.year))
        for key, (secs, start) in efforts.items():
            db.add(BestEffort(activity_id=activity.id, key=key, duration_s=round(secs, 1),
                              start_km=round(start, 2)))
    db.commit()
    return len(efforts)


def eligible(db: Session):
    return (db.query(Activity).join(FitFile, FitFile.activity_id == Activity.id)
            .filter(Activity.activity_type.in_(EFFORT_TYPES), FitFile.status == "ok",
                    Activity.distance_km >= EFFORTS[0][1]))
