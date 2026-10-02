"""Syncs daily body metrics (HRV, weight, VO2max, resting HR) from Garmin."""

from datetime import date as date_cls, timedelta
from garmin_auth import GarminAuth
from sqlalchemy.orm import Session

from zachy.database import Base, engine, SessionLocal
from zachy.models import Activity, Lap, Timeseries, BodyMetric
from zachy.services.body_metrics import process_body_metrics


def sync_body_metrics(days_back: int = 30, client=None, refresh_existing: bool = False) -> int:
    """Fetch body metrics for the last `days_back` days (today included). Days already stored are
    skipped, or re-fetched and overwritten when `refresh_existing` is set (a day synced in the middle
    of the day is incomplete). Returns the number of days added."""
    if client is None:
        client = GarminAuth().login()

    db: Session = SessionLocal()
    new_count = 0
    skipped_count = 0
    error_count = 0

    try:
        for i in range(days_back):
            day_obj = date_cls.today() - timedelta(days=i)
            day = day_obj.isoformat()

            exists = db.query(BodyMetric).filter_by(date=day_obj).first()
            if exists and not refresh_existing:
                skipped_count += 1
                continue

            try:
                hrv = client.get_hrv_data(day)
            except Exception:
                hrv = None
            try:
                weight = client.get_body_composition(day)
            except Exception:
                weight = None
            try:
                maxm = client.get_max_metrics(day)
            except Exception:
                maxm = None
            try:
                stats = client.get_stats(day)
            except Exception:
                stats = None

            try:
                data = process_body_metrics(day, hrv, weight, maxm, stats)
                data["date"] = day_obj  # ensure it's a real date object, not a string
                if exists:
                    for key, value in data.items():
                        setattr(exists, key, value)
                else:
                    db.add(BodyMetric(**data))
                    new_count += 1
            except Exception as e:
                print(f"  Error processing {day}: {e}")
                error_count += 1

            if i % 10 == 0:
                print(f"  ...processed {i}/{days_back} days")

        db.commit()
        print(f"\nDone. New: {new_count}, skipped: {skipped_count}, errors: {error_count}")
        return new_count

    finally:
        db.close()


if __name__ == "__main__":
    Base.metadata.create_all(engine)
    sync_body_metrics(days_back=365 * 8)  # ~8 years, matching your activity history