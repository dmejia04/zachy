from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from zachy.database import get_db
from zachy.models import Activity, SyncRun
from zachy.services.sync_runner import run_sync, utcnow

router = APIRouter()

# A run still marked "running" after this long was interrupted (e.g. the server restarted).
STALE_AFTER = timedelta(minutes=30)


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def _run_out(run: SyncRun | None):
    if run is None:
        return None
    return {
        "id": run.id,
        "status": run.status,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "new_activities": run.new_activities,
        "new_details": run.new_details,
        "new_body_metrics": run.new_body_metrics,
        "error": run.error,
    }


def _expire_stale_runs(db: Session):
    stale = (
        db.query(SyncRun)
        .filter(SyncRun.status == "running", SyncRun.started_at < utcnow() - STALE_AFTER)
        .all()
    )
    for run in stale:
        run.status = "error"
        run.error = "Interrupted"
        run.finished_at = run.finished_at or utcnow()
    if stale:
        db.commit()


@router.get("/status")
def sync_status(db: Session = Depends(get_db)):
    """Latest sync run, latest successful one, and the date of the newest stored activity."""
    _expire_stale_runs(db)
    last = db.query(SyncRun).order_by(SyncRun.id.desc()).first()
    last_ok = (
        db.query(SyncRun).filter(SyncRun.status == "ok").order_by(SyncRun.id.desc()).first()
    )
    latest = db.query(func.max(Activity.date)).scalar()
    return {
        "last_run": _run_out(last),
        "last_success": _run_out(last_ok),
        "latest_activity_date": latest.isoformat() if latest else None,
    }


@router.post("/", status_code=202)
def start_sync(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Start a sync in the background. Poll GET /sync/status to follow it."""
    _expire_stale_runs(db)
    if db.query(SyncRun).filter(SyncRun.status == "running").first():
        raise HTTPException(status_code=409, detail="A sync is already running")

    run = SyncRun(started_at=utcnow(), status="running",
                  new_activities=0, new_details=0, new_body_metrics=0)
    db.add(run)
    db.commit()
    background_tasks.add_task(run_sync, run.id)
    return _run_out(run)
