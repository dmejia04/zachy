from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from zachy.analytics.profile import profile, refresh_from_garmin, update
from zachy.database import get_db

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


@router.post("/garmin")
def refresh_profile(db: Session = Depends(get_db)):
    """Read the profile from Garmin again."""
    try:
        refresh_from_garmin(db)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Garmin unavailable ({e.__class__.__name__})")
    return profile(db)
