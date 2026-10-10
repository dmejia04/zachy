"""Shoes (Profile → Equipment): which pair an activity was run in, and the km on each pair.

An activity's shoes are the pair you picked for it (activity_overrides.shoe_id), else the default
pair for its surface (road / cross / trail; track and treadmill count as road) and use (easy run,
workout or race; a long run counts as easy) that was in use on that day (between its "since" and
"retired" dates).
"""

from datetime import date

from sqlalchemy.orm import Session

from zachy.models import Activity, ActivityOverride, Shoe

RUNNING = ("running", "trail_running", "track_running", "treadmill_running", "ultra_run", "street_running", "virtual_run")
SURFACES = ("road", "cross", "trail")
USES = ("easy", "workout", "race")


def shoe_use(category: str | None) -> str:
    return category if category in ("workout", "race") else "easy"   # long runs: easy shoes


def shoe_surface(surface: str | None) -> str:
    return surface if surface in ("cross", "trail") else "road"


# The usual life of a pair when you don't set one: racing shoes (light foams) wear sooner.
USUAL_MAX_KM = {"race": 400, "workout": 600, "easy": 700}


def as_dict(s: Shoe) -> dict:
    return {"id": s.id, "brand": s.brand, "model": s.model, "version": s.version, "pair": s.pair,
            "surface": s.surface, "use": s.use, "is_default": bool(s.is_default),
            "since": s.since.isoformat() if s.since else None, "retired": s.retired.isoformat() if s.retired else None,
            "max_km": s.max_km, "life_km": s.max_km or USUAL_MAX_KM.get(s.use, 700)}


def _in_use(s: Shoe, day) -> bool:
    return (not s.since or s.since <= day) and (not s.retired or day <= s.retired)


def default_shoe(shoes: list[Shoe], day, surface: str | None, category: str | None) -> Shoe | None:
    """The default pair in use that day; a retired default keeps the runs of its own time (the
    latest "since" first, then the retired pair over a newer one without a date)."""
    use = shoe_use(category)
    fits = [s for s in shoes if s.is_default and s.surface == shoe_surface(surface) and s.use == use and _in_use(s, day)]
    return max(fits, key=lambda s: (s.since or date.min, s.retired is not None), default=None)


def _easy_km(split: dict | None) -> float:
    return sum(p["km"] for p in (split or {}).get("parts", []) if p["category"] == "easy")


def shoe_for(db: Session, activity: Activity, surface: str | None, category: str | None, shoes: list[Shoe] | None = None,
             split: dict | None = None) -> dict | None:
    """{"shoe": {...}, "source": "manual" | "default"} for a run, None when no pair fits. On a
    workout with a warm-up / cool-down (split), "shoe" is the pair for the work and "easy" the
    pair for the easy running around it, with "km": {"work", "easy"}."""
    if activity.activity_type not in RUNNING:
        return None
    shoes = shoes if shoes is not None else db.query(Shoe).all()
    o = db.get(ActivityOverride, activity.id)

    def pick(manual_id, cat):
        s = next((s for s in shoes if s.id == manual_id), None) if manual_id else None
        if s:
            return {"shoe": as_dict(s), "source": "manual"}
        s = default_shoe(shoes, activity.date, surface, cat)
        return {"shoe": as_dict(s), "source": "default"} if s else None
    main = pick(o.shoe_id if o else None, category)
    easy = _easy_km(split) if category == "workout" else 0
    total = activity.distance_km or 0
    if o and o.shoe2_id and o.shoe2_from_km and not easy:   # changed shoes during the run
        s2 = next((s for s in shoes if s.id == o.shoe2_id), None)
        if s2:
            at = min(max(o.shoe2_from_km, 0.0), total)
            return {**(main or {"shoe": None, "source": None}), "change": {"shoe": as_dict(s2), "from_km": at},
                    "km": {"first": round(at, 2), "second": round(total - at, 2)}}
    if not easy:
        return main
    return {**(main or {"shoe": None, "source": None}), "easy": pick(o.shoe_easy_id if o else None, "easy"),
            "km": {"work": round(max(0.0, total - easy), 2), "easy": round(easy, 2)}}


def shoes_with_km(db: Session) -> list[dict]:
    """Every pair with its km and runs: the runs you put it on, plus those it was the default for.
    A workout with a warm-up / cool-down counts those km on its easy pair, the rest on the work pair."""
    from zachy.analytics.races import category_status
    from zachy.analytics.terrain import classify, looks_like_cross
    from zachy.analytics.workouts import cached_split, cached_workout
    shoes = db.query(Shoe).order_by(Shoe.retired.isnot(None), Shoe.surface, Shoe.use, Shoe.brand, Shoe.model).all()
    totals = {s.id: [0.0, 0] for s in shoes}
    if shoes:
        overrides = {o.activity_id: o for o in db.query(ActivityOverride)}
        manual = [i for i, o in overrides.items() if o.shoe_id or o.shoe_easy_id or o.shoe2_id]
        first = min((s.since for s in shoes if s.since), default=None)
        q = db.query(Activity).filter(Activity.activity_type.in_(RUNNING))
        if first and not any(not s.since for s in shoes if s.is_default):
            q = q.filter((Activity.date >= first) | Activity.id.in_(manual))
        for a in q:
            o = overrides.get(a.id)
            cat = category_status(db, a, getattr(o, "category", None))
            category = cat["category"] if cat else None
            t = classify(a.activity_type, a.distance_km, a.elevation_gain, getattr(o, "surface", None),
                         detected_cross=looks_like_cross(db, a, category))
            surface = t["surface"] if t else None
            split = cached_split(db, a, cached_workout(db, a)) if category == "workout" else None
            easy = _easy_km(split)
            work_id = (o.shoe_id if o and o.shoe_id else None) or getattr(default_shoe(shoes, a.date, surface, category), "id", None)
            uses = [(work_id, (a.distance_km or 0) - easy)]
            if not easy and o and o.shoe2_id and o.shoe2_from_km:   # changed shoes during the run
                at = min(max(o.shoe2_from_km, 0.0), a.distance_km or 0)
                uses = [(work_id, at), (o.shoe2_id, (a.distance_km or 0) - at)]
            if easy:
                easy_id = (o.shoe_easy_id if o and o.shoe_easy_id else None) or getattr(default_shoe(shoes, a.date, surface, "easy"), "id", None)
                uses.append((easy_id, easy))
            for sid, km in uses:
                if sid in totals:
                    totals[sid][0] += max(0.0, km)
            for sid in {sid for sid, _ in uses}:
                if sid in totals:
                    totals[sid][1] += 1
    return [{**as_dict(s), "km": round(totals[s.id][0], 1), "runs": totals[s.id][1]} for s in shoes]
