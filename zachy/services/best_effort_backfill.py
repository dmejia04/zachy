"""Compute best efforts (5 km, 10 km, half, marathon) for every run with FIT records.

    poetry run python -m zachy.services.best_effort_backfill
"""

import time

from zachy.analytics.best_efforts import compute_for_activity, eligible
from zachy.database import Base, SessionLocal, add_missing_columns, engine
from zachy.models import BestEffort


def main() -> None:
    Base.metadata.create_all(engine)
    add_missing_columns()
    db = SessionLocal()
    done = {a for (a,) in db.query(BestEffort.activity_id).distinct()}
    todo = [a for a in eligible(db).all() if a.id not in done]
    print(f"{len(todo)} runs to process", flush=True)
    started = time.time()
    for i, a in enumerate(todo, 1):
        compute_for_activity(db, a)
        if i % 200 == 0 or i == len(todo):
            print(f"{time.strftime('%H:%M:%S')} [{i}/{len(todo)}] {time.time() - started:.0f}s", flush=True)


if __name__ == "__main__":
    main()
