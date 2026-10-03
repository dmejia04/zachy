from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from zachy.database import get_db
from zachy.models import Wellness

router = APIRouter()

COLUMNS = [c.name for c in Wellness.__table__.columns if c.name != "daily_fetched"]


@router.get("/")
def list_wellness(start: date | None = None, end: date | None = None, db: Session = Depends(get_db)):
    """Daily wellness rows (oldest first) between start and end, all columns. Empty values are null."""
    query = db.query(Wellness).order_by(Wellness.date)
    if start:
        query = query.filter(Wellness.date >= start)
    if end:
        query = query.filter(Wellness.date <= end)
    return [{c: getattr(w, c) for c in COLUMNS} for w in query.all()]


@router.get("/latest")
def latest_values(db: Session = Depends(get_db)):
    """For every metric, its most recent non-empty value and the date it was recorded."""
    out = {}
    for c in COLUMNS:
        if c == "date":
            continue
        row = (db.query(Wellness.date, getattr(Wellness, c))
               .filter(getattr(Wellness, c).isnot(None))
               .order_by(Wellness.date.desc()).first())
        out[c] = {"date": row[0], "value": row[1]} if row else None
    return out
