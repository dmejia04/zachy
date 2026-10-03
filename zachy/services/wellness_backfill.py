"""Download all daily wellness history from Garmin into the `wellness` table.

Range sources (sleep, HRV, weight, VO2max, race predictions, hill/endurance score, lactate
threshold) are fetched a year at a time; daily stats, training readiness and training status
one request per day, newest first. Resumable: days already fetched are skipped.

    poetry run python -m zachy.services.wellness_backfill
    poetry run python -m zachy.services.wellness_backfill --start 2022-01-01
    poetry run python -m zachy.services.wellness_backfill --skip-ranges     # only per-day sources
"""

import argparse
import sys
import time
from datetime import date, timedelta

from garmin_auth import GarminAuth
from garminconnect import GarminConnectAuthenticationError, GarminConnectTooManyRequestsError

from zachy.database import Base, SessionLocal, engine
from zachy.models import Wellness
from zachy.services.wellness import sync_day, sync_ranges

MAX_RATE_LIMIT_WAITS = 6


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2018, 6, 1),
                        help="first day to fetch (default 2018-06-01)")
    parser.add_argument("--delay", type=float, default=0.5, help="seconds between per-day requests")
    parser.add_argument("--skip-ranges", action="store_true", help="skip the range sources")
    args = parser.parse_args()

    Base.metadata.create_all(engine)
    db = SessionLocal()
    client = GarminAuth().login()
    today = date.today()
    waits = 0

    def with_backoff(fn):
        nonlocal client, waits
        while True:
            try:
                result = fn()
                waits = 0
                return result
            except GarminConnectTooManyRequestsError:
                db.rollback()
                waits += 1
                if waits > MAX_RATE_LIMIT_WAITS:
                    raise
                pause = 60 * 2 ** (waits - 1)
                log(f"Rate-limited by Garmin, waiting {pause // 60} min…")
                time.sleep(pause)
            except GarminConnectAuthenticationError:
                db.rollback()
                log("Garmin session expired, logging in again…")
                client = GarminAuth().login()

    try:
        if not args.skip_ranges:
            log(f"Range sources {args.start} → {today}")
            with_backoff(lambda: sync_ranges(client, db, args.start, today, log=log))

        done = {d for (d,) in db.query(Wellness.date).filter(Wellness.daily_fetched.is_(True))}
        days = [today - timedelta(days=i) for i in range((today - args.start).days + 1)]
        todo = [d for d in days if d not in done]
        log(f"Per-day sources: {len(todo)} days to fetch")
        started = time.time()
        for i, d in enumerate(todo, 1):
            try:
                with_backoff(lambda: sync_day(client, db, d))
            except GarminConnectTooManyRequestsError:
                log("Garmin keeps rate-limiting — stopping. Run again later to resume.")
                return 1
            except Exception as e:   # one bad day shouldn't stop the run; it stays unfetched
                db.rollback()
                log(f"[{i}/{len(todo)}] {d} error: {type(e).__name__}: {str(e)[:150]}")
                continue
            if i % 25 == 0 or i == len(todo):
                eta = (time.time() - started) / i * (len(todo) - i) / 60
                log(f"[{i}/{len(todo)}] {d}  ETA {eta:.0f} min")
            time.sleep(args.delay)
    finally:
        db.close()

    log("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
