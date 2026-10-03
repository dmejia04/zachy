from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from zachy.analytics.body import aerobic_efficiency, daily_volume, races, yearly
from zachy.database import get_db

router = APIRouter()


@router.get("/races")
def get_races(db: Session = Depends(get_db)):
    """All races with surface, terrain, distance, time, actual and flat-equivalent pace."""
    return races(db)


@router.get("/efficiency")
def get_efficiency(db: Session = Depends(get_db)):
    """Aerobic efficiency of flat easy runs (speed per heartbeat; also as pace at your typical easy HR)."""
    reference, runs = aerobic_efficiency(db)
    return {"reference_hr": reference, "runs": runs}


@router.get("/yearly")
def get_yearly(db: Session = Depends(get_db)):
    """Per year: total steps, average resting HR and aerobic efficiency (pace at 140 bpm)."""
    return yearly(db)


@router.get("/volume")
def get_volume(db: Session = Depends(get_db)):
    """Running distance per day, for the training volume chart."""
    return daily_volume(db)
