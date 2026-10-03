from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from zachy.analytics.records import records
from zachy.database import get_db

router = APIRouter()


@router.get("/")
def get_records(db: Session = Depends(get_db)):
    """Fastest activities at 5 km, 10 km, half, marathon, 50 km, 100 km and 100 miles."""
    return records(db)
