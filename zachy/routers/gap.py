from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from zachy.analytics.gap import curves, fit_personal, speed_vs_slope
from zachy.analytics.gap_garmin import fit_garmin
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


@router.get("/speed-slope/{activity_id}")
def speed_slope(activity_id: int, db: Session = Depends(get_db)):
    """Speed against slope every 10 s of a run, with cadence; run / walk time and the switch zone."""
    out = speed_vs_slope(db, activity_id)
    if out is None:
        raise HTTPException(status_code=404, detail="No cadence or GPS data for this activity")
    return out


@router.post("/models/garmin")
def refit_garmin(db: Session = Depends(get_db)):
    """Rebuild Garmin's curve from your recent hilly runs' FIT files (takes several minutes)."""
    try:
        m = fit_garmin(db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {k: m[k] for k in ("runs", "seconds", "fitted_at")}
