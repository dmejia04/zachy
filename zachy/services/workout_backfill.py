"""Describe every workout once (fills workout_summaries) so the activity list stays fast.

    poetry run python -m zachy.services.workout_backfill
"""

import time

from zachy.analytics.races import FOOT_TYPES, auto_category
from zachy.analytics.workouts import cached_workout
from zachy.database import Base, SessionLocal, add_missing_columns, engine
from zachy.models import Activity, ActivityOverride, FitFile, WorkoutSummary


def main() -> None:
    Base.metadata.create_all(engine)
    add_missing_columns()
    db = SessionLocal()
    done = {a for (a,) in db.query(WorkoutSummary.activity_id)}
    runs = (db.query(Activity).join(FitFile, FitFile.activity_id == Activity.id)
            .filter(Activity.activity_type.in_(FOOT_TYPES), FitFile.status == "ok")
            .order_by(Activity.date.desc()).all())
    overrides = {o.activity_id: o.category for o in db.query(ActivityOverride)}
    todo = [a for a in runs if a.id not in done
            and (overrides.get(a.id) or auto_category(db, a)[0]) == "workout"]
    print(f"{len(todo)} workouts to describe", flush=True)
    started = time.time()
    for i, a in enumerate(todo, 1):
        cached_workout(db, a)
        if i % 25 == 0 or i == len(todo):
            print(f"{time.strftime('%H:%M:%S')} [{i}/{len(todo)}] ETA "
                  f"{(time.time() - started) / i * (len(todo) - i) / 60:.0f} min", flush=True)


if __name__ == "__main__":
    main()
