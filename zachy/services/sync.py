"""Syncs activities from Garmin Connect into the local database."""

from garmin_auth import GarminAuth
from sqlalchemy.orm import Session
from zachy.database import Base, engine, SessionLocal
from zachy.models import Activity, Lap, Timeseries, BodyMetric
from zachy.services.activity_processor import process_activities


def sync_all_activities(batch_size: int = 100):
    auth = GarminAuth()
    client = auth.login()

    db: Session = SessionLocal()
    total_new = 0
    total_skipped = 0
    start = 0

    try:
        while True:
            print(f"Fetching activities {start} to {start + batch_size}...")
            raw_activities = client.get_activities(start, batch_size)

            if not raw_activities:
                print("No more activities — done.")
                break

            processed = process_activities(raw_activities)

            for data in processed:
                exists = db.query(Activity).filter_by(garmin_id=data["garmin_id"]).first()
                if exists:
                    total_skipped += 1
                    continue

                activity = Activity(**data)
                db.add(activity)
                total_new += 1

            db.commit()
            print(f"  -> saved {len(processed)} in this batch "
                  f"(running total: {total_new} new, {total_skipped} skipped)")

            start += batch_size

        print(f"\nDone! Total new: {total_new}, total skipped: {total_skipped}")

    finally:
        db.close()


def sync_new_activities(client, db: Session, batch_size: int = 20) -> list[Activity]:
    """Fetch the newest activities page by page, stopping at the first page that has
    activities we already store. Returns the newly added activities (committed)."""
    new: list[Activity] = []
    start = 0
    while True:
        raw_activities = client.get_activities(start, batch_size)
        if not raw_activities:
            break

        known = 0
        for data in process_activities(raw_activities):
            if db.query(Activity).filter_by(garmin_id=data["garmin_id"]).first():
                known += 1
                continue
            activity = Activity(**data)
            db.add(activity)
            new.append(activity)
        db.commit()

        if known:  # caught up with what's already in the database
            break
        start += batch_size

    return new


if __name__ == "__main__":
    Base.metadata.create_all(engine)
    sync_all_activities(batch_size=100)