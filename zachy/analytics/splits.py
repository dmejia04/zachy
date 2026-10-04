"""Per-km splits and per-lap elevation from an activity's FIT records.

Elevation gain/loss: raw altitude jitters, so changes are only counted once they exceed a
small threshold (hysteresis). No single threshold matches Garmin everywhere (flat city runs
want ~1 m, mountain races ~5 m), so the result is then scaled so the splits add up exactly to
the activity's official gain and loss — the same totals shown on the activity. Altimeter jumps
and altitude changes during pauses (a lift, a shuttle) are left out first (see elevation.py).
"""

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from zachy.analytics.elevation import moving_elevation
from zachy.models import Activity, Record

ELEVATION_THRESHOLD_M = 2.0


def load_track(db: Session, activity_id: int) -> pd.DataFrame:
    rows = (
        db.query(Record.timer_s, Record.elapsed_s, Record.distance_km, Record.hr, Record.cadence,
                 Record.power, Record.elevation)
        .filter(Record.activity_id == activity_id)
        .order_by(Record.id)
        .all()
    )
    return pd.DataFrame(rows, columns=["timer_s", "elapsed_s", "distance_km", "hr", "cadence", "power",
                                       "elevation"], dtype=float)


def elevation_steps(alt: np.ndarray, threshold: float = ELEVATION_THRESHOLD_M) -> tuple[np.ndarray, np.ndarray]:
    """Gain and loss (m) credited to each point, ignoring changes smaller than `threshold`."""
    gain = np.zeros(len(alt))
    loss = np.zeros(len(alt))
    ref = None
    for i, a in enumerate(alt):
        if np.isnan(a):
            continue
        if ref is None:
            ref = a
        elif a - ref >= threshold:
            gain[i], ref = a - ref, a
        elif ref - a >= threshold:
            loss[i], ref = ref - a, a
    return gain, loss


def calibrated_elevation(df: pd.DataFrame, activity: Activity) -> tuple[np.ndarray, np.ndarray]:
    """Per-point gain/loss, scaled so the totals equal Garmin's official gain/loss."""
    gain, loss = elevation_steps(moving_elevation(df["elevation"], df["timer_s"], df.get("elapsed_s")))
    if activity.elevation_gain and gain.sum() > 0:
        gain *= activity.elevation_gain / gain.sum()
    if activity.elevation_loss and loss.sum() > 0:
        loss *= activity.elevation_loss / loss.sum()
    return gain, loss


def _weighted_mean(values: np.ndarray, weights: np.ndarray):
    ok = ~np.isnan(values) & (weights > 0)
    return float(np.average(values[ok], weights=weights[ok])) if ok.any() else None


def _summarize(df: pd.DataFrame, mask: np.ndarray, dt: np.ndarray, gain: np.ndarray, loss: np.ndarray) -> dict:
    """Time-weighted averages (works for 1 s and 'smart' recording alike) for the selected points."""
    hr = df["hr"].to_numpy()[mask]
    return {
        "avg_hr": _weighted_mean(hr, dt[mask]),
        "max_hr": float(np.nanmax(hr)) if (~np.isnan(hr)).any() else None,
        "avg_cadence": _weighted_mean(df["cadence"].to_numpy()[mask], dt[mask]),
        "avg_power": _weighted_mean(df["power"].to_numpy()[mask], dt[mask]),
        "elevation_gain": round(float(gain[mask].sum()), 1),
        "elevation_loss": round(float(loss[mask].sum()), 1),
    }


def km_splits(df: pd.DataFrame, activity: Activity, split_km: float = 1.0) -> list[dict]:
    """One row per `split_km` of distance (the last one may be shorter)."""
    df = df.dropna(subset=["distance_km", "timer_s"]).reset_index(drop=True)
    if df.empty or df["distance_km"].iloc[-1] < 0.05:
        return []
    dist = np.maximum.accumulate(df["distance_km"].to_numpy())   # guard against tiny dips
    timer = df["timer_s"].to_numpy()
    total = dist[-1]
    # Time spent on each point = time until the next one.
    dt = np.append(np.diff(timer), 0).clip(min=0)
    gain, loss = calibrated_elevation(df, activity)

    marks = list(np.arange(split_km, total, split_km))
    if not marks or total - marks[-1] > 0.01:
        marks.append(total)
    marks = np.array(marks)
    times = np.interp(marks, dist, timer)          # moving time when each mark was reached
    which = np.searchsorted(marks, dist, side="left").clip(max=len(marks) - 1)

    splits, prev_mark, prev_time = [], 0.0, 0.0
    for k, (mark, t) in enumerate(zip(marks, times)):
        length, secs = mark - prev_mark, t - prev_time
        splits.append({
            "split": k + 1,
            "distance_km": round(float(length), 3),
            "end_km": round(float(mark), 3),
            "duration_s": round(float(secs)),
            "pace": round(secs / 60 / length, 3) if length > 0 else None,
            **_summarize(df, which == k, dt, gain, loss),
        })
        prev_mark, prev_time = mark, t
    return splits


def lap_stats(df: pd.DataFrame, activity: Activity, lap_durations: list) -> list[dict]:
    """Per lap, from the FIT track split at cumulative lap (moving) times: elevation gain/loss,
    max heart rate, and time-weighted average power and cadence while moving (zeros = stopped,
    left out)."""
    df = df.dropna(subset=["timer_s"]).reset_index(drop=True)
    if df.empty or not lap_durations:
        return []
    gain, loss = calibrated_elevation(df, activity)
    bounds = np.cumsum([d or 0 for d in lap_durations])
    timer = df["timer_s"].to_numpy()
    which = np.searchsorted(bounds, timer, side="left").clip(max=len(bounds) - 1)
    dt = np.append(np.diff(timer), 0).clip(min=0)

    def moving_mean(col, mask):
        if col not in df:
            return None
        v = df[col].to_numpy()[mask]
        v = np.where(v > 0, v, np.nan)
        return _weighted_mean(v, dt[mask])

    out = []
    for i in range(len(bounds)):
        m = which == i
        power, cadence = moving_mean("power", m), moving_mean("cadence", m)
        hr = df["hr"].to_numpy()[m] if "hr" in df else np.array([])
        max_hr = float(np.nanmax(hr)) if hr.size and np.isfinite(hr).any() else None
        out.append({"max_hr": max_hr, "elevation_gain": round(float(gain[m].sum()), 1),
                    "elevation_loss": round(float(loss[m].sum()), 1),
                    "avg_power": round(power, 1) if power is not None else None,
                    "avg_cadence": round(cadence, 1) if cadence is not None else None})
    return out
