from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from zachy.database import get_db
from zachy.models import ZoneSettings
from zachy.zones import compute_zones, defaults, validate, zones_for

router = APIRouter()


class ZoneSettingsIn(BaseModel):
    zone_model: int
    # {"hr": {"3": [146, 172], "5": [146, 155, 163, 172]}, "pace": {"5": [5.45, 4.83, ...]}, ...}
    boundaries: dict[str, dict[str, list[float | None]]] = {}


def _out(s: ZoneSettings) -> dict:
    return {
        "valid_from": s.valid_from.isoformat(),
        "zone_model": s.zone_model,
        **s.data,
        "zones": compute_zones(s.zone_model, s.data),
    }


@router.get("/")
def list_zone_settings(db: Session = Depends(get_db)):
    """All saved zone sets (newest first) plus the zone names."""
    sets = db.query(ZoneSettings).order_by(ZoneSettings.valid_from.desc()).all()
    return {"sets": [_out(s) for s in sets], "defaults": defaults()}


@router.get("/on/{day}")
def zones_on(day: date, db: Session = Depends(get_db)):
    """The zones that applied on a given date, or null if none were set yet."""
    return zones_for(db, day)


@router.put("/{valid_from}")
def save_zone_settings(valid_from: date, body: ZoneSettingsIn, db: Session = Depends(get_db)):
    """Create or replace the threshold set that starts on `valid_from`."""
    try:
        data = validate(body.zone_model, body.model_dump())
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    s = db.query(ZoneSettings).filter(ZoneSettings.valid_from == valid_from).first()
    if s is None:
        s = ZoneSettings(valid_from=valid_from)
        db.add(s)
    s.zone_model = body.zone_model
    s.data = data
    db.commit()
    return _out(s)


@router.delete("/{valid_from}", status_code=204)
def delete_zone_settings(valid_from: date, db: Session = Depends(get_db)):
    s = db.query(ZoneSettings).filter(ZoneSettings.valid_from == valid_from).first()
    if s is None:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(s)
    db.commit()
