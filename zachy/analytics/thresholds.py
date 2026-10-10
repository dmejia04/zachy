"""Your thresholds from your own runs: aerobic (AeT, LT1), anaerobic (AnT, LT2) and vVO2max.

- AnT pace: the speed you can hold for about an hour, from your critical speed (Paces tab):
  (CS × 3600 + D′) / 3600. AnT heart rate: Friel's field test — the average heart rate over the
  last 20 minutes of your flat races of 30–75 minutes in the last 18 months (the median of them).
- AeT heart rate: the heart-rate drift test (Uphill Athlete). On a steady run below AeT, the
  pace-to-heart-rate ratio barely drifts between the first and second half (< 5%); above it, it
  drifts more. From your flat easy and long runs of 60–150 min in the last 12 months: the drift
  against the run's average heart rate, a line through them, and the heart rate where it reaches
  5%. Kept within 80–95% of the AnT heart rate.
- AeT pace (and AnT pace check): from your heart rate–speed line on flat runs of the last 6 months.
- vVO2max: the speed of a ~6-minute all-out effort. Model: Daniels' velocity at VO2max for your
  best VDOT among the Paces chart's races of the last 12 months (else all). Measured: your best
  6-minute stretch on a flat race or workout of the last 12 months — a lower bound unless one was
  run all out.
"""

import time
from datetime import date, timedelta

import numpy as np
from sqlalchemy.orm import Session

from zachy.models import Activity, ActivityOverride, Record

RUNS = ("running", "track_running", "street_running", "treadmill_running")
DRIFT_AT_AET = 0.05
_cache: dict = {}


def _records(db: Session, activity_id: int) -> np.ndarray:
    rows = (db.query(Record.timer_s, Record.distance_km, Record.hr)
            .filter(Record.activity_id == activity_id).order_by(Record.id).all())
    a = np.array([[np.nan if v is None else v for v in r] for r in rows], dtype=float) if rows else np.empty((0, 3))
    return a[np.isfinite(a[:, 0]) & np.isfinite(a[:, 1])] if len(a) else a


def _category(db: Session, a: Activity) -> str | None:
    from zachy.analytics.races import category_status
    o = db.get(ActivityOverride, a.id)
    c = category_status(db, a, o.category if o else None)
    return c["category"] if c else None


def _flat(a: Activity) -> bool:
    return bool(a.distance_km) and (a.elevation_gain or 0) / a.distance_km < 10


def thresholds(db: Session) -> dict:
    if _cache.get("at", 0) > time.time() - 600:
        return _cache["out"]
    from zachy.analytics.road_plan import road_fit
    today = date.today()
    acts = (db.query(Activity).filter(Activity.activity_type.in_(RUNS), Activity.date >= today - timedelta(days=548))
            .order_by(Activity.date).all())
    cats = {a.id: _category(db, a) for a in acts}
    fit = road_fit(db)

    # AnT heart rate: last 20 min of flat races of 30–75 min.
    lthr = []
    for a in acts:
        if cats[a.id] == "race" and _flat(a) and a.duration_s and 1800 <= a.duration_s <= 4500:
            r = _records(db, a.id)
            if len(r) > 100 and np.isfinite(r[:, 2]).sum() > 100:
                last = r[r[:, 0] >= r[-1, 0] - 1200]
                hr = last[:, 2][np.isfinite(last[:, 2])]
                if len(hr) > 50:
                    lthr.append({"id": a.id, "date": a.date.isoformat(), "name": a.name, "hr": float(hr.mean())})
    ant_hr = float(np.median([x["hr"] for x in lthr])) if lthr else None

    # AeT heart rate: the drift on steady flat easy runs.
    year_ago = today - timedelta(days=365)
    drift = []
    for a in acts:
        if (a.date < year_ago or cats[a.id] not in ("easy", "long") or not _flat(a) or not a.duration_s
                or not 3600 <= a.duration_s <= 9000):
            continue
        r = _records(db, a.id)
        ok = np.isfinite(r[:, 2]) if len(r) else []
        if len(r) < 300 or ok.sum() < 300:
            continue
        r = r[ok]
        r = r[r[:, 0] >= 600]                     # the first 10 min: warming up
        if len(r) < 200:
            continue
        mid = (r[0, 0] + r[-1, 0]) / 2
        halves = [r[r[:, 0] < mid], r[r[:, 0] >= mid]]
        eff = []
        for h in halves:
            dt = h[-1, 0] - h[0, 0]
            if dt <= 0:
                break
            eff.append((h[-1, 1] - h[0, 1]) * 1000 / dt / h[:, 2].mean())
        if len(eff) == 2 and eff[0] > 0:
            drift.append({"id": a.id, "date": a.date.isoformat(), "hr": float(r[:, 2].mean()), "drift": float((eff[0] - eff[1]) / eff[0])})
    aet_hr, aet_src = None, None
    clean = [d for d in drift if abs(d["drift"]) <= 0.15]        # stops, GPS jumps: not drift
    if len(clean) >= 8:
        x = np.array([d["hr"] for d in clean]); y = np.array([d["drift"] for d in clean])
        keep = np.ones(len(x), bool)
        for _ in range(2):                                        # robust: refit without the outliers
            b, c = np.polyfit(x[keep], y[keep], 1)
            res = y - (b * x + c)
            keep = np.abs(res) <= 2 * res[keep].std()
        if b > 0:
            aet_hr, aet_src = (DRIFT_AT_AET - c) / b, "drift"
    if ant_hr and (aet_hr is None or not 0.80 * ant_hr <= aet_hr <= 0.95 * ant_hr):
        aet_hr = (min(max(aet_hr, 0.80 * ant_hr), 0.95 * ant_hr) if aet_hr else 0.89 * ant_hr)
        aet_src = "drift, kept within 80–95% of AnT" if aet_src else "89% of the AnT heart rate"

    # Heart rate → speed on flat runs of the last 6 months.
    six = today - timedelta(days=182)
    pts = [(a.avg_hr, a.distance_km * 1000 / a.duration_s) for a in acts
           if a.date >= six and _flat(a) and "treadmill" not in (a.name or "").lower() and a.avg_hr and a.duration_s and a.distance_km and a.distance_km >= 5
           and a.activity_type != "treadmill_running"]
    hr_speed = None
    if len(pts) >= 10:
        hx, sv = np.array(pts, float).T
        k, i0 = np.polyfit(hx, sv, 1)
        if k > 0:
            hr_speed = {"slope": float(k), "intercept": float(i0), "n": len(pts)}
    speed_at = lambda hr: hr_speed["slope"] * hr + hr_speed["intercept"] if hr_speed and hr else None

    ant_speed = (fit["cs"] * 3600 + fit["dprime"]) / 3600 if fit else speed_at(ant_hr)

    # vVO2max: best 6 min on a flat race or workout of the last 12 months.
    best = None
    for a in acts:
        if (a.date < year_ago or cats[a.id] not in ("race", "workout") or not _flat(a)
                or a.activity_type == "treadmill_running" or "treadmill" in (a.name or "").lower()):
            continue
        r = _records(db, a.id)
        if len(r) < 400:
            continue
        t, d = r[:, 0], np.maximum.accumulate(r[:, 1])
        ok = t[-1] - t[0] >= 360
        if not ok:
            continue
        d6 = np.interp(t + 360, t, d) - d
        i = int(np.argmax(np.where(t + 360 <= t[-1], d6, 0)))
        sp = d6[i] * 1000 / 360
        cap = (_vvo2max_model(db).get("model") or 7) * 1.1   # GPS glitches: not 10%+ faster than the model
        if sp < cap and (best is None or sp > best["speed"]):
            best = {"speed": float(sp), "id": a.id, "date": a.date.isoformat(), "name": a.name, "at_km": float(d[i])}
    out = {
        "aet": {"hr": round(aet_hr) if aet_hr else None, "speed": speed_at(aet_hr), "source": aet_src,
                "n": len(clean), "runs": clean[-60:]},
        "ant": {"hr": round(ant_hr) if ant_hr else None, "speed": ant_speed,
                "source": "critical speed: the pace for one hour" if fit else "heart rate–speed line",
                "speed_from_hr": speed_at(ant_hr), "races": lthr},
        "vvo2max": {"measured": best, **_vvo2max_model(db)},
        "hr_speed": hr_speed,
    }
    _cache.update(at=time.time(), out=out)
    return out


def _vdot(d: float, t: float) -> float:
    """Daniels & Gilbert: VO2 cost of the speed over the share of VO2max held for that time."""
    v, m = d / (t / 60), t / 60
    cost = -4.60 + 0.182258 * v + 0.000104 * v * v
    share = 0.8 + 0.1894393 * np.exp(-0.012778 * m) + 0.2989558 * np.exp(-0.1932605 * m)
    return float(cost / share)


def _vvo2max_model(db: Session) -> dict:
    from zachy.analytics.road_plan import chart_races
    races = chart_races(db)
    if not races:
        return {"model": None}
    recent = [r for r in races if r["date"] >= (date.today() - timedelta(days=365)).isoformat()] or races
    best = max(recent, key=lambda r: _vdot(r["d"], r["t"]))
    vd = _vdot(best["d"], best["t"])
    v = (-0.182258 + np.sqrt(0.182258 ** 2 + 4 * 0.000104 * (vd + 4.60))) / (2 * 0.000104)   # m/min
    return {"model": float(v / 60), "vdot": round(vd, 1), "from": best}
