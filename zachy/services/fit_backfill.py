"""Download and parse the original FIT file of every activity (newest first).

Resumable: activities that already have a fit_files row are skipped, so just run it again
after an interruption.

    poetry run python -m zachy.services.fit_backfill                  # everything still missing
    poetry run python -m zachy.services.fit_backfill --limit 20       # a small batch
    poetry run python -m zachy.services.fit_backfill --retry-errors   # also retry failed ones
    poetry run python -m zachy.services.fit_backfill --reparse        # re-parse local zips only
                                                                      # (no network), e.g. after
                                                                      # changing parsing code
"""

import argparse
import sys
import time

from garmin_auth import GarminAuth
from garminconnect import GarminConnectAuthenticationError, GarminConnectTooManyRequestsError
from sqlalchemy.orm import Session

from zachy.database import Base, SessionLocal, engine
from zachy.models import Activity, FitFile
from zachy.services.fit import process_activity, zip_path

MAX_RATE_LIMIT_WAITS = 6


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def pending_activities(db: Session, retry_errors: bool, reparse: bool) -> list[Activity]:
    query = db.query(Activity).outerjoin(FitFile, FitFile.activity_id == Activity.id)
    if reparse:
        query = query.filter(FitFile.status.isnot(None))
    elif retry_errors:
        query = query.filter((FitFile.activity_id.is_(None)) | (FitFile.status == "error"))
    else:
        query = query.filter(FitFile.activity_id.is_(None))
    activities = query.order_by(Activity.date.desc(), Activity.id.desc()).all()
    if reparse:
        activities = [a for a in activities if zip_path(a.garmin_id).exists()]
    return activities


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, help="process at most N activities")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds to wait between downloads")
    parser.add_argument("--retry-errors", action="store_true", help="also retry activities that failed")
    parser.add_argument("--reparse", action="store_true", help="re-parse already downloaded files, no network")
    args = parser.parse_args()

    Base.metadata.create_all(engine)
    db = SessionLocal()
    todo = pending_activities(db, args.retry_errors, args.reparse)[: args.limit]
    log(f"{len(todo)} activities to process")
    if not todo:
        return 0

    client = None if args.reparse else GarminAuth().login()
    counts: dict[str, int] = {}
    started = time.time()
    i = 0
    waits = 0

    while i < len(todo):
        activity = todo[i]
        try:
            fit = process_activity(client, db, activity, redownload=False)
        except GarminConnectTooManyRequestsError:
            waits += 1
            if waits > MAX_RATE_LIMIT_WAITS:
                log("Garmin keeps rate-limiting — stopping. Run again later to resume.")
                return 1
            pause = 60 * 2 ** (waits - 1)   # 1, 2, 4, 8, 16, 32 min
            log(f"Rate-limited by Garmin, waiting {pause // 60} min…")
            db.rollback()
            time.sleep(pause)
            continue
        except GarminConnectAuthenticationError:
            log("Garmin session expired, logging in again…")
            db.rollback()
            client = GarminAuth().login()
            continue

        waits = 0
        i += 1
        counts[fit.status] = counts.get(fit.status, 0) + 1
        rate = (time.time() - started) / i
        eta_min = rate * (len(todo) - i) / 60
        log(f"[{i}/{len(todo)}] {activity.date} {(activity.name or '')[:30]:30} "
            f"{fit.status:10} {fit.n_records or 0:>7} rec  ETA {eta_min:.0f} min"
            + (f"  {fit.error}" if fit.error else ""))

        if client is not None and i < len(todo):
            time.sleep(args.delay)

    log(f"Done in {(time.time() - started) / 60:.1f} min: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
