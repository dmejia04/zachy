from sqlalchemy import Column, ForeignKey, Integer, String, Text
from zachy.database import Base


class WorkoutSummary(Base):
    """Cached description of a workout's structure (analytics/workouts.py) — the FIT file it is
    read from never changes, so it's computed once. structured=0 means "no clear structure"."""
    __tablename__ = "workout_summaries"

    activity_id = Column(Integer, ForeignKey("activities.id"), primary_key=True)
    structured  = Column(Integer, nullable=False)    # 1 / 0
    type        = Column(String, nullable=True)      # Intervals, Fartlek, Hills, Tempo, Progressive
    summary     = Column(String, nullable=True)      # "12 × 400 m r 1:00 @ 3:06/km"
    warmup      = Column(String, nullable=True)
    cooldown    = Column(String, nullable=True)
    source      = Column(String, nullable=True)      # "watch workout" | "laps"
    watch_name  = Column(String, nullable=True)
    on_track    = Column(Integer, nullable=True)     # 1 = reps on an athletics track; NULL = old entry
    split_json  = Column(Text, nullable=True)        # warm-up / work / cool-down parts (workout_split)
    split_ver   = Column(Integer, nullable=True)
