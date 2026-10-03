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


@router.get("/")
def get_profile(db: Session = Depends(get_db)):
    """Birth date, height, weight, sex: yours if set, else Garmin's (weight: latest scale reading)."""
    return profile(db)


@router.put("/")
def put_profile(body: ProfileIn, db: Session = Depends(get_db)):
    """Manual values; send null (or leave a field out) to fall back to Garmin's."""
    update(db, body.model_dump())
    return profile(db)


@router.post("/garmin")
def refresh_profile(db: Session = Depends(get_db)):
    """Read the profile from Garmin again."""
    try:
        refresh_from_garmin(db)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Garmin unavailable ({e.__class__.__name__})")
    return profile(db)
