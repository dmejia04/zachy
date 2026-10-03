"""Run-based series for the Body tab: races over time, and aerobic efficiency on flat easy runs."""

from sqlalchemy.orm import Session

from zachy.analytics.gap import adjusted_paces, personal_model
from zachy.analytics.races import FOOT_TYPES, category_status
from zachy.analytics.terrain import classify, place_from_name
from zachy.models import Activity, ActivityOverride, CategoryCache

EFFICIENCY_TYPES = {"running", "treadmill_running", "track_running", "trail_running"}
EFFICIENCY_HR = 140   # every run is normalised to this heart rate
OUTLIER_SHARE = 0.12   # more than 12% off the neighbouring runs' median

def races(db: Session) -> list[dict]:
    """Every race (automatic guess or your choice) with surface, terrain, distance, time, actual pace
    and personal flat-equivalent pace — so road and trail races can be compared on one chart."""
    overrides = {o.activity_id: o for o in db.query(ActivityOverride)}
    model = personal_model(db)
    version = model and model["fitted_at"]
    out = []
    for a in db.query(Activity).filter(Activity.activity_type.in_(FOOT_TYPES), Activity.distance_km >= 1,
                                       Activity.duration_s > 0).order_by(Activity.date):
        o = overrides.get(a.id)
        status = category_status(db, a, getattr(o, "category", None))
        if not status or status["category"] != "race":
            continue
        t = classify(a.activity_type, a.distance_km, a.elevation_gain, getattr(o, "surface", None))
        # Flat-equivalent pace is slow to compute: stored with the category, per model version.
        cache = db.get(CategoryCache, a.id)
        if cache is not None and (cache.adjusted_pace is None or cache.model_version != version):
            gap = adjusted_paces(db, a) or {}
            cache.adjusted_pace = gap.get("personal") or gap.get("minetti")
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
    normalised to 140 bpm with your own speed-vs-heart-rate slope: a run at 120 bpm is credited
    with the extra speed you'd have had at 140. Returns (reference_hr, runs)."""
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
        if not status or status["category"] != "easy":            # steady easy efforts only
            continue
        speed = a.distance_km * 1000 / (a.duration_s / 60)         # m/min
        ef = speed / a.avg_hr
        out.append({"id": a.id, "date": a.date.isoformat(), "ef": round(ef, 3),
                    "avg_hr": a.avg_hr,
                    "pace": round(a.duration_s / 60 / a.distance_km, 3), "distance_km": round(a.distance_km, 1)})
    if not out:
        return EFFICIENCY_HR, out
    # Odd runs (HR strap glitch, snow, a run with a friend) sit far from their neighbours: flagged,
    # so the trend, best/worst and the fit below ignore them.
    ratios = [r["ef"] for r in out]
    for i, r in enumerate(out):
        around = sorted(ratios[max(0, i - 15):i + 16])
        r["outlier"] = abs(around[len(around) // 2] / r["ef"] - 1) > OUTLIER_SHARE
    # How much faster you run per extra beat, from your own runs: the slope of speed vs heart rate
    # within each year (so getting fitter over the years doesn't bias it).
    slope = _speed_per_beat([r for r in out if not r["outlier"]])
    for r in out:
        speed = 1000 / r["pace"] + slope * (EFFICIENCY_HR - r["avg_hr"])   # m/min at the reference HR
        r["pace_at_hr"] = round(1000 / speed, 3)
    return EFFICIENCY_HR, out


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
