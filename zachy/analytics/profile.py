"""Your profile (birth date, height, weight, sex): what you typed, else what Garmin has.

Weight: the latest scale reading in the wellness data comes before the profile value.
Used for the plausible max heart rate: 220 - age/2 (a generous ceiling — the classic 220 - age
estimate can be 10-20 bpm low for trained runners), 200 when the age is unknown.
"""

import json
from datetime import date, datetime

from sqlalchemy.orm import Session

from zachy.models import Profile, Wellness

FIELDS = ("birth_date", "height_cm", "weight_kg", "sex")
SETTINGS = ("efficiency_ref_hr", "max_hr_ceiling")
LINKS = ("utmb_url", "itra_url", "betrail_url", "ffa_licence")       # your pages on results sites   # computed automatically unless you set them
DEFAULT_MAX_HR = 200


def _row(db: Session) -> Profile:
    row = db.get(Profile, 1)
    if row is None:
        row = Profile(id=1)
        db.add(row)
        db.commit()
    return row


def refresh_from_garmin(db: Session, client=None) -> None:
    if client is None:
        from zachy.services.garmin_client import get_client
        client = get_client()
    data = client.get_user_profile().get("userData", {})
    weight = data.get("weight")
    garmin = {
        "birth_date": data.get("birthDate"),
        "height_cm": data.get("height"),
        "weight_kg": round(weight / 1000, 1) if weight else None,   # Garmin gives grams
        "sex": (data.get("gender") or "").lower() or None,
        "lthr": data.get("lactateThresholdHeartRate"),
    }
    row = _row(db)
    row.garmin_json = json.dumps(garmin)
    row.garmin_fetched_at = datetime.now()
    db.commit()


def profile(db: Session) -> dict:
    row = _row(db)
    garmin = json.loads(row.garmin_json) if row.garmin_json else {}
    manual = {f: getattr(row, f) for f in FIELDS}
    manual["birth_date"] = manual["birth_date"].isoformat() if manual["birth_date"] else None
    effective = {f: manual[f] if manual[f] is not None else garmin.get(f) for f in FIELDS}
    source = {f: "manual" if manual[f] is not None else ("garmin" if garmin.get(f) is not None else None) for f in FIELDS}
    scale = (db.query(Wellness.date, Wellness.weight_kg).filter(Wellness.weight_kg.isnot(None))
             .order_by(Wellness.date.desc()).first())
    if manual["weight_kg"] is None and scale:
        effective["weight_kg"], source["weight_kg"] = round(scale.weight_kg, 1), f"scale {scale.date.isoformat()}"
    age = None
    if effective["birth_date"]:
        b = date.fromisoformat(effective["birth_date"])
        t = date.today()
        age = t.year - b.year - ((t.month, t.day) < (b.month, b.day))
    # Settings: yours, else automatic.
    from zachy.analytics.body import aerobic_efficiency
    ceiling_auto = _age_ceiling(date.today().year, effective["birth_date"])
    reference = aerobic_efficiency(db)[0]
    for f, auto in (("max_hr_ceiling", ceiling_auto), ("efficiency_ref_hr", reference)):
        manual[f] = getattr(row, f)
        effective[f] = manual[f] if manual[f] is not None else auto
        source[f] = "manual" if manual[f] is not None else "auto"
    links = {f: getattr(row, f) for f in LINKS}
    return {"effective": effective, "source": source, "manual": manual, "garmin": garmin, "age": age, "links": links,
            "max_hr_ceiling": effective["max_hr_ceiling"], "max_hr_ceiling_auto": ceiling_auto,
            "garmin_fetched_at": row.garmin_fetched_at.isoformat() if row.garmin_fetched_at else None}


def update(db: Session, values: dict) -> None:
    row = _row(db)
    for f in FIELDS + SETTINGS + LINKS:
        if f in values:
            v = values[f]
            if f == "birth_date" and v:
                v = date.fromisoformat(v)
            setattr(row, f, v if v not in ("", None) else None)
    db.commit()


def max_hr_ceiling(db: Session, year: int) -> float:
    """Highest believable heart rate in a given year: yours if set in the profile, else
    220 - age/2 (200 without an age)."""
    p = _row(db)
    if p.max_hr_ceiling:
        return p.max_hr_ceiling
    birth_date = p.birth_date.isoformat() if p.birth_date else (json.loads(p.garmin_json or "{}").get("birth_date"))
    return _age_ceiling(year, birth_date)


def _age_ceiling(year: int, birth_date: str | None) -> float:
    if not birth_date:
        return DEFAULT_MAX_HR
    return round(220 - (year - int(birth_date[:4])) / 2, 1)
