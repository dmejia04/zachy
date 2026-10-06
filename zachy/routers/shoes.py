from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from zachy.analytics.shoes import SURFACES, USES, shoes_with_km
from zachy.database import get_db
from zachy.models import ActivityOverride, Shoe

router = APIRouter()


class ShoeIn(BaseModel):
    brand: str
    model: str
    version: str | None = None
    pair: int | None = None
    surface: str = "road"
    use: str = "easy"
    is_default: bool = False
    since: str | None = None
    retired: str | None = None
    max_km: float | None = None


def _save(db: Session, shoe: Shoe, body: ShoeIn) -> list[dict]:
    if not body.brand.strip() or not body.model.strip():
        raise HTTPException(status_code=422, detail="Brand and model are needed")
    if body.surface not in SURFACES or body.use not in USES:
        raise HTTPException(status_code=422, detail=f"surface in {SURFACES}, use in {USES}")
    shoe.brand, shoe.model = body.brand.strip(), body.model.strip()
    shoe.version, shoe.pair = (body.version or "").strip() or None, body.pair or None
    shoe.surface, shoe.use, shoe.is_default = body.surface, body.use, int(body.is_default)
    shoe.since = date.fromisoformat(body.since) if body.since else None
    shoe.max_km = body.max_km if body.max_km and body.max_km > 0 else None
    if body.retired is not None:
        shoe.retired = date.fromisoformat(body.retired) if body.retired else None
    db.add(shoe)
    db.flush()
    if shoe.is_default:   # one default per surface and use among the pairs in use (retired ones keep their past runs)
        db.query(Shoe).filter(Shoe.id != shoe.id, Shoe.surface == shoe.surface, Shoe.use == shoe.use,
                              Shoe.retired.is_(None)).update({"is_default": 0})
    db.commit()
    return shoes_with_km(db)


@router.get("/")
def list_shoes(db: Session = Depends(get_db)):
    """Your shoes with the km and runs on each (runs you put them on, and those they were the default for)."""
    return shoes_with_km(db)


@router.post("/")
def add_shoe(body: ShoeIn, db: Session = Depends(get_db)):
    return _save(db, Shoe(), body)


@router.put("/{shoe_id}")
def edit_shoe(shoe_id: int, body: ShoeIn, db: Session = Depends(get_db)):
    shoe = db.get(Shoe, shoe_id)
    if not shoe:
        raise HTTPException(status_code=404, detail="Shoe not found")
    return _save(db, shoe, body)


@router.post("/{shoe_id}/retire")
def retire_shoe(shoe_id: int, db: Session = Depends(get_db)):
    """Retire a pair today: it moves to the retired list and stops being put on new runs."""
    shoe = db.get(Shoe, shoe_id)
    if not shoe:
        raise HTTPException(status_code=404, detail="Shoe not found")
    shoe.retired = date.today()
    db.commit()
    return shoes_with_km(db)


@router.post("/{shoe_id}/unretire")
def unretire_shoe(shoe_id: int, db: Session = Depends(get_db)):
    """Put a retired pair back in use."""
    shoe = db.get(Shoe, shoe_id)
    if not shoe:
        raise HTTPException(status_code=404, detail="Shoe not found")
    shoe.retired = None
    if shoe.is_default:   # back in use: it is the default again, the other one stops being
        db.query(Shoe).filter(Shoe.id != shoe.id, Shoe.surface == shoe.surface, Shoe.use == shoe.use,
                              Shoe.retired.is_(None)).update({"is_default": 0})
    db.commit()
    return shoes_with_km(db)


@router.delete("/{shoe_id}")
def delete_shoe(shoe_id: int, db: Session = Depends(get_db)):
    shoe = db.get(Shoe, shoe_id)
    if shoe:
        db.query(ActivityOverride).filter(ActivityOverride.shoe_id == shoe_id).update({"shoe_id": None})
        db.delete(shoe)
        db.commit()
    return shoes_with_km(db)
