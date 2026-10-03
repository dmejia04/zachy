"""Runs a full incremental sync for the "Sync" button and records it as a SyncRun."""

from datetime import datetime, timezone

from garmin_auth import GarminAuth

from zachy.database import SessionLocal
from zachy.models import SyncRun
from zachy.analytics.best_efforts import EFFORT_TYPES
from zachy.analytics.best_efforts import compute_for_activity as compute_best_efforts
from zachy.analytics.races import auto_category, clear_category_cache_around
from zachy.analytics.workouts import cached_workout
from zachy.services.fit import process_activity
from zachy.services.sync import sync_new_activities
from zachy.services.wellness import sync_recent as sync_recent_wellness
from zachy.services.sync_laps_timeseries import RUNNING_TYPES, fetch_laps_and_timeseries


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def run_sync(run_id: int) -> None:
    """New activities -> original FIT file (+ laps for runs) -> recent wellness days."""
    db = SessionLocal()
    run = db.get(SyncRun, run_id)
    try:
        client = GarminAuth().login()

        new = sync_new_activities(client, db)
        run.new_activities = len(new)
        db.commit()
        for day in sorted({a.date for a in new}):
            clear_category_cache_around(db, day)   # race guesses compare runs within a year

        for activity in new:
            if activity.activity_type in RUNNING_TYPES:
                fetch_laps_and_timeseries(client, db, activity, include_timeseries=False)
                db.commit()
            if process_activity(client, db, activity).status == "ok":
                run.new_details += 1
                db.commit()
                if auto_category(db, activity)[0] == "workout":
                    cached_workout(db, activity)   # describe it now so the list stays fast
                if activity.activity_type in EFFORT_TYPES:
                    compute_best_efforts(db, activity)   # 5 km / 10 km / half / marathon records

        run.new_body_metrics = sync_recent_wellness(client, db, log=lambda *_: None)
        run.status = "ok"
    except Exception as e:
        db.rollback()
        run.status = "error"
        run.error = f"{type(e).__name__}: {e}"[:500]
    finally:
        run.finished_at = utcnow()
        db.commit()
        db.close()
