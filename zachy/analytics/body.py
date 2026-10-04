"""Run-based series for the Body tab: races over time, and aerobic efficiency on flat easy runs."""

from sqlalchemy.orm import Session

from zachy.analytics.gap import adjusted_paces
from zachy.analytics.gap_race import race_model
from zachy.analytics.races import FOOT_TYPES, category_status
from zachy.analytics.terrain import classify, looks_like_cross, place_from_name
from zachy.models import Activity, ActivityOverride, CategoryCache

EFFICIENCY_TYPES = {"running", "treadmill_running", "track_running", "trail_running"}
OUTLIER_SHARE = 0.12   # more than 12% off the neighbouring runs' median

def races(db: Session) -> list[dict]:
    """Every race (automatic guess or your choice) with surface, terrain, distance, time, actual pace
    and personal flat-equivalent pace — so road and trail races can be compared on one chart."""
    overrides = {o.activity_id: o for o in db.query(ActivityOverride)}
    model = race_model(db)   # races: flat-equivalent pace from the race model (gap_race.py)
    version = model and f"race {model['fitted_at']}"
    out = []
    for a in db.query(Activity).filter(Activity.activity_type.in_(FOOT_TYPES), Activity.distance_km >= 1,
                                       Activity.duration_s > 0).order_by(Activity.date):
        o = overrides.get(a.id)
        status = category_status(db, a, getattr(o, "category", None))
        if not status or status["category"] != "race":
            continue
        t = classify(a.activity_type, a.distance_km, a.elevation_gain, getattr(o, "surface", None),
                     detected_cross=looks_like_cross(db, a, "race"))
        # Flat-equivalent pace is slow to compute: stored with the category, per model version.
        cache = db.get(CategoryCache, a.id)
        if cache is not None and (cache.adjusted_pace is None or cache.model_version != version):
            gap = adjusted_paces(db, a) or {}
            cache.adjusted_pace = gap.get("race") or gap.get("personal") or gap.get("minetti")
            cache.model_version = version
            db.commit()
        out.append({
            "id": a.id, "date": a.date.isoformat(), "name": a.name, "place": place_from_name(a.name),
            "distance_km": round(a.distance_km, 2), "duration_s": a.duration_s,
            "elevation_gain": round(a.elevation_gain or 0), "pace": round(a.duration_s / 60 / a.distance_km, 3),
            "adjusted_pace": cache.adjusted_pace if cache else None,
            "surface": t["surface"] if t else None, "terrain": t["terrain"] if t else None,
            "km_effort": t["km_effort"] if t else None,
        })
    return out

def aerobic_efficiency(db: Session) -> tuple[int, list[dict]]:
    """Efficiency factor (metres per minute per heartbeat) of flat, easy, steady runs: same effort
    over time -> are you faster for the same heart rate? Every run (any HR in 105-165) is
    normalised to a reference heart rate (efficiency_reference) with your own speed-vs-heart-rate
    slope: a run at 120 bpm is credited with the extra speed you'd have had at 130.
    Returns (reference_hr, runs)."""
    overrides = {o.activity_id: o.category for o in db.query(ActivityOverride)}
    out = []
    runs = (db.query(Activity)
            .filter(Activity.activity_type.in_(EFFICIENCY_TYPES), Activity.distance_km >= 5,
                    Activity.duration_s >= 1800, Activity.avg_hr.between(105, 165))
            .order_by(Activity.date))
    for a in runs:
        if (a.elevation_gain or 0) / a.distance_km >= 10:          # flat runs only
            continue
        status = category_status(db, a, overrides.get(a.id))
        if not status or status["category"] not in ("easy", "long"):   # steady easy efforts only
            continue
        speed = a.distance_km * 1000 / (a.duration_s / 60)         # m/min
        ef = speed / a.avg_hr
        out.append({"id": a.id, "date": a.date.isoformat(), "ef": round(ef, 3),
                    "avg_hr": a.avg_hr,
                    "pace": round(a.duration_s / 60 / a.distance_km, 3), "distance_km": round(a.distance_km, 1)})
    if not out:
        return 0, out
    # Odd runs (HR strap glitch, snow, a run with a friend) sit far from their neighbours: flagged,
    # so the trend, best/worst and the fit below ignore them.
    ratios = [r["ef"] for r in out]
    for i, r in enumerate(out):
        around = sorted(ratios[max(0, i - 15):i + 16])
        r["outlier"] = abs(around[len(around) // 2] / r["ef"] - 1) > OUTLIER_SHARE
    # How much faster you run per extra beat, from your own runs: the slope of speed vs heart rate
    # within each year (so getting fitter over the years doesn't bias it).
    slope = _speed_per_beat([r for r in out if not r["outlier"]])
    reference = efficiency_reference(db, out)
    for r in out:
        speed = 1000 / r["pace"] + slope * (reference - r["avg_hr"])   # m/min at the reference HR
        r["pace_at_hr"] = round(1000 / speed, 3)
    return reference, out


def efficiency_reference(db: Session, runs: list[dict]) -> int:
    """The heart rate every run is normalised to: yours if set in the profile, else your typical
    easy-run heart rate (the median), so runs are adjusted by only a few beats."""
    from zachy.models import Profile
    p = db.get(Profile, 1)
    if p and p.efficiency_ref_hr:
        return int(p.efficiency_ref_hr)
    return auto_efficiency_reference(runs)


def auto_efficiency_reference(runs: list[dict]) -> int:
    hrs = sorted(r["avg_hr"] for r in runs if not r.get("outlier"))
    return round(hrs[len(hrs) // 2]) if hrs else 130


def _speed_per_beat(runs: list[dict]) -> float:
    """Least-squares slope (m/min per bpm) of speed against heart rate, each year centred on its
    own mean. Falls back to the proportional assumption (speed / HR) with too few runs."""
    by_year = {}
    for r in runs:
        by_year.setdefault(r["date"][:4], []).append((r["avg_hr"], 1000 / r["pace"]))
    num = den = 0.0
    for pts in by_year.values():
        mh = sum(h for h, _ in pts) / len(pts)
        mv = sum(v for _, v in pts) / len(pts)
        num += sum((h - mh) * (v - mv) for h, v in pts)
        den += sum((h - mh) ** 2 for h, _ in pts)
    if len(runs) < 30 or den == 0:
        return sum(r["ef"] for r in runs) / max(len(runs), 1)
    return num / den


def yearly(db: Session) -> list[dict]:
    """Per year: total steps (and days the watch counted), average resting HR, and aerobic
    efficiency — the median pace at 140 bpm of that year's flat easy runs (odd runs left out)."""
    from zachy.models import Wellness
    years: dict[int, dict] = {}
    for day, steps, rhr in db.query(Wellness.date, Wellness.steps, Wellness.resting_hr):
        y = years.setdefault(day.year, {"steps": 0, "step_days": 0, "rhr": []})
        if steps:
            y["steps"] += steps
            y["step_days"] += 1
        if rhr:
            y["rhr"].append(rhr)
    max_hr = yearly_max_hr(db)
    reference, runs = aerobic_efficiency(db)
    paces: dict[int, list[float]] = {}
    for r in runs:
        if not r["outlier"]:
            paces.setdefault(int(r["date"][:4]), []).append(r["pace_at_hr"])
    out = []
    for year in sorted(set(years) | set(paces) | set(max_hr)):
        y = years.get(year, {"steps": 0, "step_days": 0, "rhr": []})
        p = sorted(paces.get(year, []))
        out.append({
            "year": year, "steps": y["steps"] or None, "step_days": y["step_days"],
            "avg_daily_steps": round(y["steps"] / y["step_days"]) if y["step_days"] else None,
            "resting_hr": round(sum(y["rhr"]) / len(y["rhr"]), 1) if y["rhr"] else None,
            "resting_hr_days": len(y["rhr"]),
            "efficiency_pace": p[len(p) // 2] if p else None, "efficiency_runs": len(p),
            "reference_hr": reference,
            **max_hr.get(year, {"max_hr": None}),
        })
    return out


_max_hr_cache: dict = {}   # recomputed when activities or the birth date change
SUSTAINED_HR_S = 10   # a max heart rate has to be held this long (10 s median) to count


def _sustained_max_hr(db: Session, activity_id: int) -> float | None:
    """Highest 10-second median heart rate in the FIT records: single-sample spikes don't count."""
    import pandas as pd
    from zachy.models import Record
    hr = [h for (h,) in db.query(Record.hr).filter(Record.activity_id == activity_id).order_by(Record.id)]
    s = pd.Series(hr, dtype=float).dropna()
    if len(s) < SUSTAINED_HR_S:
        return None
    return float(s.rolling(SUSTAINED_HR_S).median().max())


def yearly_max_hr(db: Session) -> dict[int, dict]:
    """Highest believable heart rate of each year, from runs (wrist readings on bikes and in the gym
    lock onto cadence or glitch too often). Rules:
    - held for 10 s (median of the FIT records), so one-second spikes don't count;
    - under the ceiling 220 - age/2 (analytics/profile.py); higher is a glitch;
    - one run standing more than 10 bpm above every other that year is dropped as a spike.
    Runs without FIT records use the watch's max."""
    from sqlalchemy import func
    from zachy.analytics.profile import max_hr_ceiling
    from zachy.analytics.terrain import FOOT_TYPES
    from zachy.models import Profile
    p = db.get(Profile, 1)
    key = (tuple(db.query(func.count(Activity.id), func.max(Activity.id)).one()),
           p and (p.birth_date, p.max_hr_ceiling, p.garmin_json))
    if _max_hr_cache.get("key") == key:
        return _max_hr_cache["value"]
    by_year: dict[int, list] = {}
    for a in db.query(Activity).filter(Activity.max_hr.isnot(None), Activity.activity_type.in_(FOOT_TYPES)):
        by_year.setdefault(a.date.year, []).append(a)
    out = {}
    for year, acts in by_year.items():
        ceiling = max_hr_ceiling(db, year)
        values = []
        for a in sorted(acts, key=lambda a: -a.max_hr)[:20]:   # the top candidates are enough
            v = _sustained_max_hr(db, a.id)
            v = a.max_hr if v is None else v
            if v <= ceiling:
                values.append((v, a))
        values.sort(key=lambda x: -x[0])
        while len(values) >= 2 and values[0][0] - values[1][0] > 10:
            values.pop(0)
        if values:
            v, a = values[0]
            out[year] = {"max_hr": round(v), "max_hr_date": a.date.isoformat(), "max_hr_activity": a.id,
                         "max_hr_ceiling": ceiling}
    _max_hr_cache.update(key=key, value=out)
    return out


def daily_volume(db: Session) -> list[dict]:
    """Running distance, moving time and climb per day (all foot activities, treadmill included)."""
    from sqlalchemy import func
    from zachy.analytics.terrain import FOOT_TYPES
    rows = (db.query(Activity.date, func.sum(Activity.distance_km), func.sum(Activity.duration_s),
                     func.sum(Activity.elevation_gain))
            .filter(Activity.activity_type.in_(FOOT_TYPES), Activity.distance_km > 0)
            .group_by(Activity.date).order_by(Activity.date))
    return [{"date": d.isoformat(), "km": round(km, 2), "secs": secs or 0, "gain": round(gain or 0)}
            for d, km, secs, gain in rows]
