from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from zachy.analytics.gap import curves, fit_personal
from zachy.analytics.gap_race import evaluate_and_store, race_profile
from zachy.database import get_db

router = APIRouter()


@router.get("/models")
def gap_models(db: Session = Depends(get_db)):
    """The grade-cost curves (km-effort, Minetti, Strava-like, heart-rate, race model) from −40% to +40%."""
    return curves(db)


@router.post("/models/personal")
def refit_personal(db: Session = Depends(get_db)):
    """Refit the personal curve from all runs (takes ~30 s)."""
    model = fit_personal(db)
    return {k: model[k] for k in ("windows", "runs", "fitted_at")}


@router.post("/models/race")
def refit_race(db: Session = Depends(get_db)):
    """Refit the race model from your trail races and re-check every model (takes ~10 s)."""
    try:
        m = evaluate_and_store(db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {k: m[k] for k in ("n_races", "segments", "summary", "fitted_at")}


@router.get("/race/{activity_id}")
def race_gap_profile(activity_id: int, db: Session = Depends(get_db)):
    """One race's 200 m segments: actual pace and grade-adjusted pace under every model."""
    out = race_profile(db, activity_id)
    if out is None:
        raise HTTPException(status_code=404, detail="Not usable for the race model")
    return out
