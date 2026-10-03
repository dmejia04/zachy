from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from zachy.analytics.gap import curves, fit_personal
from zachy.database import get_db

router = APIRouter()


@router.get("/models")
def gap_models(db: Session = Depends(get_db)):
    """The four grade-cost curves (km-effort, Minetti, Strava-like, personal) from −40% to +40%."""
    return curves(db)


@router.post("/models/personal")
def refit_personal(db: Session = Depends(get_db)):
    """Refit the personal curve from all runs (takes ~30 s)."""
    model = fit_personal(db)
    return {k: model[k] for k in ("windows", "runs", "fitted_at")}
