from sqlalchemy import Column, Float, ForeignKey, Integer, String
from zachy.database import Base


class ActivityOverride(Base):
    """Your corrections to an activity, kept apart from the data synced from Garmin."""
    __tablename__ = "activity_overrides"

    activity_id = Column(Integer, ForeignKey("activities.id"), primary_key=True)
    surface     = Column(String, nullable=True)   # "trail" | "road" | None = automatic
    category    = Column(String, nullable=True)   # "race" | "workout" | "long" | "easy" | None = automatic
    workout_type    = Column(String, nullable=True)   # your workout title ("Fartlek"); None = the guess
    workout_summary = Column(String, nullable=True)   # and its description ("6 × 3 min hills")
    shoe_id         = Column(Integer, nullable=True)  # the pair you ran in (None = the default pair); on a split workout: the work
    shoe_easy_id    = Column(Integer, nullable=True)  # split workout: the pair for the warm-up and cool-down (None = easy default)
    shoe2_id        = Column(Integer, nullable=True)  # shoes changed during the run: the second pair…
    shoe2_from_km   = Column(Float, nullable=True)    # …from this km on
