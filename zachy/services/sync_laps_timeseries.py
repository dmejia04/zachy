"""Syncs laps and timeseries for existing running activities that don't have them yet."""

from sqlalchemy.orm import Session
from garmin_auth import GarminAuth

from zachy.database import Base, engine, SessionLocal
from zachy.models import Activity, Lap, Timeseries, BodyMetric
from zachy.services.laps_timeseries_processor import process_laps, process_timeseries

RUNNING_TYPES = [
    "running",
    "trail_running",
    "track_running",
    "treadmill_running",
    "ultra_run",
]


def fetch_laps_and_timeseries(
    client, db: Session, activity: Activity, include_timeseries: bool = True
) -> tuple[int, int]:
    """Download and store laps (+ the 2,000-point chart timeseries unless disabled — the FIT
    file is the better source for that). Returns (laps, points); caller commits."""
    garmin_id = int(activity.garmin_id)

    laps = []
    try:
        splits = client.get_activity_splits(garmin_id)
        laps = process_laps(activity.id, splits)
        for lap_data in laps:
            db.add(Lap(**lap_data))
    except Exception as e:
        print(f"  Laps error: {e}")

    timeseries = []
    if not include_timeseries:
        return len(laps), 0
    try:
        details = client.get_activity_details(garmin_id)
        if details is None:
            print("  No detail metrics for this activity type — skipping timeseries.")
        else:
            timeseries = process_timeseries(activity.id, details)
            for ts_data in timeseries:
                db.add(Timeseries(**ts_data))
    except Exception as e:
        print(f"  Timeseries error: {e}")

    return len(laps), len(timeseries)


def sync_laps_and_timeseries(limit: int = 50):
    auth = GarminAuth()
    client = auth.login()

    db: Session = SessionLocal()

    activities = (
        db.query(Activity)
        .outerjoin(Lap)
        .filter(Lap.id.is_(None))
        .filter(Activity.activity_type.in_(RUNNING_TYPES))
        .order_by(Activity.date.desc())
        .limit(limit)
        .all()
    )

    print(f"Found {len(activities)} running activities without laps/timeseries.")

    for i, activity in enumerate(activities):
        print(f"[{i+1}/{len(activities)}] {activity.date} - {activity.name}")
        n_laps, n_points = fetch_laps_and_timeseries(client, db, activity)
        print(f"  -> {n_laps} laps, {n_points} timeseries points")
        db.commit()

    db.close()
    print("\nDone.")


if __name__ == "__main__":
    Base.metadata.create_all(engine)
    sync_laps_and_timeseries(limit=2300)