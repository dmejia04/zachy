import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from zachy.analytics.profile import profile, refresh_from_garmin, update
from zachy.database import get_db
from zachy.models import Profile

router = APIRouter()


class ProfileIn(BaseModel):
    birth_date: str | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    sex: str | None = None
    efficiency_ref_hr: float | None = None
    max_hr_ceiling: float | None = None
    utmb_url: str | None = None
    itra_url: str | None = None
    betrail_url: str | None = None
    ffa_licence: str | None = None


@router.get("/")
def get_profile(db: Session = Depends(get_db)):
    """Birth date, height, weight, sex: yours if set, else Garmin's (weight: latest scale reading)."""
    return profile(db)


@router.put("/")
def put_profile(body: ProfileIn, db: Session = Depends(get_db)):
    """Manual values: send null to fall back to Garmin's (or automatic); fields left out are kept."""
    update(db, body.model_dump(exclude_unset=True))
    return profile(db)


class PaceRaces(BaseModel):
    added: list[int] = []    # road races added to the Paces chart (activity ids)
    hidden: list[int] = []   # best races taken off it


@router.get("/pace-races")
def get_pace_races(db: Session = Depends(get_db)):
    """The Paces chart's races besides the default (your best at each distance): added and hidden."""
    p = db.get(Profile, 1)
    data = json.loads(p.pace_races_json) if p and p.pace_races_json else {}
    if isinstance(data, list):   # first version: only added races
        data = {"added": data}
    return {"added": data.get("added", []), "hidden": data.get("hidden", [])}


@router.put("/pace-races")
def put_pace_races(body: PaceRaces, db: Session = Depends(get_db)):
    """Replace the Paces chart's added and hidden races."""
    p = db.get(Profile, 1) or Profile(id=1)
    p.pace_races_json = json.dumps({"added": sorted(set(body.added)), "hidden": sorted(set(body.hidden))})
    db.merge(p)
    db.commit()
    return get_pace_races(db)


@router.post("/garmin")
def refresh_profile(db: Session = Depends(get_db)):
    """Read the profile from Garmin again."""
    try:
        refresh_from_garmin(db)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Garmin unavailable ({e.__class__.__name__})")
    return profile(db)
