"""Personal records at the classic distances.

- 5 km, 10 km, half, marathon: best efforts — the fastest stretch of exactly that distance inside
  any run (analytics/best_efforts.py), so a 5 km record can be a split of a 10 km race. By time.
- 50 km, 100 km, 100 miles: whole activities whose distance falls in the range (a "100 km" trail
  can be 105 km), ranked by personal flat-equivalent pace (analytics/gap.py), so courses with very
  different climbing compare fairly. Runs without FIT data fall back to their actual pace.
Treadmill runs are left out. Each entry carries the activity's race / workout / easy category,
so the page can show races only.
"""

from sqlalchemy.orm import Session

from zachy.analytics.gap import adjusted_paces
from zachy.analytics.races import category_status
from zachy.analytics.terrain import FOOT_TYPES, place_from_name
from zachy.models import Activity, ActivityOverride, BestEffort

BEST_EFFORTS = [("5k", "5 km", 5.0), ("10k", "10 km", 10.0), ("half", "Half marathon", 21.0975),
                ("marathon", "Marathon", 42.195)]
DISTANCES = [
    # whole-activity records: key, label, target km, accepted range (km)
    ("50k", "50 km", 50.0, (47.0, 56.0)),
    ("100k", "100 km", 100.0, (95.0, 118.0)),
    ("100mi", "100 miles", 160.9, (150.0, 185.0)),
]
RECORD_TYPES = FOOT_TYPES - {"treadmill_running"}
KEEP = 10   # per distance: enough to still show a podium with "races only"


def _entry(db: Session, a: Activity, overrides: dict) -> dict:
    status = category_status(db, a, overrides.get(a.id))
    gain = a.elevation_gain or 0.0
    return {
        "id": a.id, "date": a.date.isoformat(), "name": a.name, "place": place_from_name(a.name),
        "distance_km": round(a.distance_km, 2), "duration_s": a.duration_s,
        "pace": round(a.duration_s / 60 / a.distance_km, 3),
        "elevation_gain": round(gain), "km_effort": round(a.distance_km + gain / 100, 1),
        "avg_hr": a.avg_hr, "category": status["category"] if status else None,
    }


def records(db: Session) -> list[dict]:
    overrides = {o.activity_id: o.category for o in db.query(ActivityOverride)}
    out = []
    for key, label, km in BEST_EFFORTS:
        rows = (db.query(BestEffort, Activity).join(Activity, Activity.id == BestEffort.activity_id)
                .filter(BestEffort.key == key).order_by(BestEffort.duration_s).limit(KEEP).all())
        entries = []
        for effort, a in rows:
            e = _entry(db, a, overrides)
            # The record is the effort, not the whole activity: its own time, pace and position.
            e.update({"activity_distance_km": e["distance_km"], "distance_km": km,
                      "duration_s": effort.duration_s, "pace": round(effort.duration_s / 60 / km, 3),
                      "start_km": effort.start_km, "whole_activity": a.distance_km <= km * 1.05})
            entries.append(e)
        count = db.query(BestEffort).filter(BestEffort.key == key).count()
        out.append({"key": key, "label": label, "target_km": km, "kind": "best_effort",
                    "count": count, "top": entries})
    for key, label, target, (lo, hi) in DISTANCES:
        runs = (
            db.query(Activity)
            .filter(Activity.activity_type.in_(RECORD_TYPES),
                    Activity.distance_km.between(lo, hi), Activity.duration_s > 0)
            .all()
        )
        entries = []
        for a in runs:
            e = _entry(db, a, overrides)
            gap = adjusted_paces(db, a)
            e["adjusted_pace"] = (gap or {}).get("personal") or (gap or {}).get("minetti")
            e["adjusted_method"] = "personal" if (gap or {}).get("personal") else ("minetti" if gap else None)
            entries.append(e)
        # Fastest flat-equivalent pace first; no FIT data -> ranked by actual pace.
        entries.sort(key=lambda e: e["adjusted_pace"] or e["pace"])
        out.append({"key": key, "label": label, "target_km": target, "range_km": [lo, hi],
                    "kind": "activity", "count": len(runs), "top": entries[:KEEP]})
    return out
