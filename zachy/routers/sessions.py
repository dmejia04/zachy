from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from zachy.analytics.similar_workouts import similar_workouts
from zachy.database import get_db

router = APIRouter()


@router.get("/")
def get_sessions(db: Session = Depends(get_db)):
    """Your interval sessions grouped by their reps (analytics/similar_workouts.py)."""
    return similar_workouts(db)
