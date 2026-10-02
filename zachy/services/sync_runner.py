"""Runs a full incremental sync for the "Sync" button and records it as a SyncRun."""

from datetime import date, datetime, timedelta, timezone

from garmin_auth import GarminAuth
from sqlalchemy import func
from sqlalchemy.orm import Session

from zachy.database import SessionLocal
from zachy.models import BodyMetric, SyncRun
from zachy.services.fit import process_activity
from zachy.services.sync import sync_new_activities
from zachy.services.sync_body_metrics import sync_body_metrics
from zachy.services.sync_laps_timeseries import RUNNING_TYPES, fetch_laps_and_timeseries

# With no body metrics stored at all, go back this far on the first sync.
BODY_METRICS_FIRST_SYNC_DAYS = 30


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def body_metrics_days_to_sync(db: Session) -> int:
    """Days to fetch (counting back from today): from the day before the newest stored day, so a
    day that was synced while still in progress gets completed, through today."""
    latest = db.query(func.max(BodyMetric.date)).scalar()
    if latest is None:
        return BODY_METRICS_FIRST_SYNC_DAYS
    start = latest - timedelta(days=1)
    return (date.today() - start).days + 1


def run_sync(run_id: int) -> None:
    """New activities -> original FIT file (+ laps for runs) -> recent body metrics."""
    db = SessionLocal()
    run = db.get(SyncRun, run_id)
    try:
        client = GarminAuth().login()

        new = sync_new_activities(client, db)
        run.new_activities = len(new)
        db.commit()

        for activity in new:
            if activity.activity_type in RUNNING_TYPES:
                fetch_laps_and_timeseries(client, db, activity, include_timeseries=False)
                db.commit()
            if process_activity(client, db, activity).status == "ok":
                run.new_details += 1
                db.commit()

        run.new_body_metrics = sync_body_metrics(
            days_back=body_metrics_days_to_sync(db), client=client, refresh_existing=True
        )
        run.status = "ok"
    except Exception as e:
        db.rollback()
        run.status = "error"
        run.error = f"{type(e).__name__}: {e}"[:500]
    finally:
        run.finished_at = utcnow()
        db.commit()
        db.close()
