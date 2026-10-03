from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from zachy.analytics.body import aerobic_efficiency, races
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
