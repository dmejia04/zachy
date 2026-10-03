"""Relative effort: a heart-rate training load, like Strava's "Relative Effort".

Strava doesn't publish its formula; it builds on Banister's TRIMP (training impulse), used here:

    TRIMP = sum over the activity of  minutes x HRr x 0.64 x e^(k x HRr)
    HRr   = (HR - resting HR) / (max HR - resting HR)      your share of heart-rate reserve
    k     = 1.92 for men, 1.67 for women

Time near your max counts far more than easy time (the exponential). Per second from the FIT
records (moving time); activities without records use their average heart rate over the whole
duration, which underrates interval sessions. Resting HR is that day's (wellness data, nearest
day within a week otherwise); max HR is that year's sustained max (analytics/body.py), so the
score follows your physiology over the years. An easy hour is roughly 50-80, a hard race 200+.
"""

from datetime import timedelta

import numpy as np
from sqlalchemy.orm import Session

from zachy.models import Activity, Record, Wellness

K = {"male": 1.92, "female": 1.67}
DEFAULT_RESTING = 50.0


def _resting_hr(db: Session, day) -> float:
    rows = (db.query(Wellness.date, Wellness.resting_hr)
            .filter(Wellness.resting_hr.isnot(None),
                    Wellness.date.between(day - timedelta(days=7), day + timedelta(days=7))).all())
    if not rows:
        return DEFAULT_RESTING
    return min(rows, key=lambda r: abs((r.date - day).days)).resting_hr


def _max_hr(db: Session, year: int, maxima: dict) -> float:
    if year in maxima:
        return maxima[year]["max_hr"]
    near = min(maxima, key=lambda y: abs(y - year)) if maxima else None
    if near is not None:
        return maxima[near]["max_hr"]
    from zachy.analytics.profile import max_hr_ceiling
    return max_hr_ceiling(db, year) - 10


def trimp(minutes: np.ndarray, hr: np.ndarray, resting: float, max_hr: float, k: float) -> float:
    hrr = np.clip((hr - resting) / (max_hr - resting), 0, 1)
    return float(np.sum(minutes * hrr * 0.64 * np.exp(k * hrr)))


def compute(db: Session, activity: Activity, maxima: dict | None = None, k: float | None = None) -> float | None:
    """Relative effort of one activity (stored on it). None without heart rate."""
    if maxima is None:
        from zachy.analytics.body import yearly_max_hr
        maxima = yearly_max_hr(db)
    if k is None:
        from zachy.analytics.profile import profile
        k = K.get(profile(db)["effective"].get("sex"), K["male"])
    resting, max_hr = _resting_hr(db, activity.date), _max_hr(db, activity.date.year, maxima)
    rows = (db.query(Record.timer_s, Record.hr).filter(Record.activity_id == activity.id)
            .order_by(Record.id).all())
    value, method = None, None
    if rows:
        arr = np.array(rows, dtype=float)
        t, hr = arr[:, 0], arr[:, 1]
        dt = np.clip(np.diff(t, append=t[-1]), 0, 30) / 60          # minutes until the next record
        ok = np.isfinite(hr) & np.isfinite(dt)
        if ok.sum() > 30:
            value, method = trimp(dt[ok], hr[ok], resting, max_hr, k), "records"
    if value is None and activity.avg_hr and activity.duration_s:
        value, method = trimp(np.array([activity.duration_s / 60]), np.array([activity.avg_hr]),
                              resting, max_hr, k), "average"
    activity.relative_effort = round(value) if value is not None else None
    activity.relative_effort_method = method
    db.commit()
    return activity.relative_effort


def backfill(db: Session, only_missing: bool = True) -> int:
    """Compute it for every activity with heart rate (one-off; the sync does new ones)."""
    from zachy.analytics.body import yearly_max_hr
    from zachy.analytics.profile import profile
    maxima = yearly_max_hr(db)
    k = K.get(profile(db)["effective"].get("sex"), K["male"])
    q = db.query(Activity).filter(Activity.avg_hr.isnot(None))
    if only_missing:
        q = q.filter(Activity.relative_effort.is_(None))
    n = 0
    for a in q.all():
        compute(db, a, maxima, k)
        n += 1
    return n


if __name__ == "__main__":
    import sys
    from zachy.database import SessionLocal
    print(backfill(SessionLocal(), only_missing="--all" not in sys.argv), "activities")
