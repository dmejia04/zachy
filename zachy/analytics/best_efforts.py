"""Best efforts inside any run, from the per-second FIT records:
- 1 km, 1 mile, 3 km — absolute: downhill allowed (the "fastest you've ever moved" list);
- 5 km, 10 km, half, marathon — the fastest stretch of exactly that distance (a 5 km record can be a
  split of a 10 km race), but not downhill (see below);
- climbs — the fastest 100 m, 500 m and 1,000 m of elevation gain (altitude smoothed, 2 m hysteresis).

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

from zachy.analytics.elevation import moving_elevation
from zachy.analytics.races import robust_max_hr
from zachy.analytics.splits import elevation_steps
from zachy.analytics.terrain import FOOT_TYPES
from zachy.models import Activity, BestEffort, FitFile, Record

# key, km, downhill allowed
EFFORTS = [("1k", 1.0, True), ("1mi", 1.609344, True), ("3k", 3.0, True),
           ("5k", 5.0, False), ("10k", 10.0, False), ("half", 21.0975, False), ("marathon", 42.195, False)]
CLIMBS = [("climb100", 100.0), ("climb500", 500.0), ("climb1000", 1000.0)]
MAX_VERTICAL_MH = 3000.0     # faster climbing than this is an altimeter glitch
EFFORT_TYPES = FOOT_TYPES - {"treadmill_running"}   # treadmill distance isn't reliable
JUMP_SPEED_MS = 8.0          # a GPS jump: faster than this between two records…
JUMP_MIN_M = 25.0            # …and longer than this (1-second jitter is ~10 m)
MAX_EFFORT_SPEED_MS = 6.45   # 2:35/km: anything faster over a whole effort is a glitch
MAX_DROP_M_PER_KM = 5.0      # downhill efforts don't count as records
MIN_HR_FRACTION = 0.60       # below this share of max HR, you weren't running it
GAP_S = 30                   # no record for longer than this: a recording gap


def best_efforts_for(timer_s: np.ndarray, distance_km: np.ndarray,
                     elevation: np.ndarray | None = None, hr: np.ndarray | None = None,
                     max_hr: float | None = None,
                     elapsed_s: np.ndarray | None = None) -> dict[str, tuple[float, float]]:
    """{"5k": (seconds, start_km), …} for the distances the run is long enough for."""
    nan = np.full_like(timer_s, np.nan)
    elevation = nan if elevation is None else elevation
    hr = nan if hr is None else hr
    elapsed_s = nan if elapsed_s is None else elapsed_s
    ok = ~(np.isnan(timer_s) | np.isnan(distance_km))
    t, d, e, h, el = timer_s[ok], np.maximum.accumulate(distance_km[ok]), elevation[ok], hr[ok], elapsed_s[ok]
    if not len(t):
        return {}
    # One continuous moving time even if the watch timer reset during the activity.
    t = t[0] + np.concatenate([[0], np.cumsum(np.clip(np.diff(t), 0, None))])
    keep = np.append(True, (np.diff(d) > 0) & (np.diff(t) > 0))   # both strictly increasing
    t, d, e, h, el = t[keep], d[keep], e[keep], h[keep], el[keep]
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
        e = moving_elevation(e, t, el)   # altimeter jumps and moves during pauses aren't climbing
    if len(d) < 2:
        return {}
    out = {}
    for key, km, downhill_ok in EFFORTS:
        if d[-1] - d[0] < km:
            continue
        starts = np.nonzero(d <= d[-1] - km)[0]
        end_d = d[starts] + km
        end_t = np.interp(end_d, d, t)
        end_i = np.searchsorted(d, end_d)              # first record at or after the end
        durations = end_t - t[starts]
        clean = durations >= km * 1000 / MAX_EFFORT_SPEED_MS
        if has_elev and not downhill_ok:
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

    # Climbs: fastest time to accumulate 100 m / 500 m / 1,000 m of elevation gain.
    if has_elev:
        # Recording gaps (no data for over 30 s): the altitude gained while nothing was recorded
        # isn't a climb we can time, and smoothing would spread it over the next seconds.
        z = e.copy()
        for i in np.nonzero(np.diff(t) > GAP_S)[0]:
            z[i + 1:] -= z[i + 1] - z[i]
        z = pd.Series(z).rolling(15, center=True, min_periods=1).mean().to_numpy()
        gain, _ = elevation_steps(z)
        cum = np.cumsum(gain)
        for key, metres in CLIMBS:
            if cum[-1] < metres:
                continue
            starts = np.nonzero(cum <= cum[-1] - metres)[0]
            end_i = np.searchsorted(cum, cum[starts] + metres)
            durations = t[end_i] - t[starts]
            clean = durations >= metres / MAX_VERTICAL_MH * 3600
            if max_hr:
                secs = hr_secs[end_i] - hr_secs[starts]
                avg_hr = np.where(secs > 0, (beats[end_i] - beats[starts]) / np.maximum(secs, 1e-9), np.nan)
                clean &= ~(avg_hr < MIN_HR_FRACTION * max_hr)
            if clean.any():
                durations = np.where(clean, durations, np.inf)
                i = int(np.argmin(durations))
                out[key] = (float(durations[i]), float(d[starts[i]]))
    return out


def compute_for_activity(db: Session, activity: Activity) -> int:
    """(Re)compute and store one activity's best efforts. Returns how many distances it has."""
    rows = (db.query(Record.timer_s, Record.distance_km, Record.elevation, Record.hr, Record.elapsed_s)
            .filter(Record.activity_id == activity.id).order_by(Record.id).all())
    db.query(BestEffort).filter(BestEffort.activity_id == activity.id).delete()
    efforts = {}
    if rows:
        arr = np.array(rows, dtype=float)
        efforts = best_efforts_for(arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3],
                                   robust_max_hr(db, activity.date.year), arr[:, 4])
        for key, (secs, start) in efforts.items():
            db.add(BestEffort(activity_id=activity.id, key=key, duration_s=round(secs, 1),
                              start_km=round(start, 2)))
    db.commit()
    return len(efforts)


def eligible(db: Session):
    return (db.query(Activity).join(FitFile, FitFile.activity_id == Activity.id)
            .filter(Activity.activity_type.in_(EFFORT_TYPES), FitFile.status == "ok",
                    Activity.distance_km >= 1.0))
