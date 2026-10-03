from sqlalchemy import Column, ForeignKey, Integer, String
from zachy.database import Base


class ActivityOverride(Base):
    """Your corrections to an activity, kept apart from the data synced from Garmin."""
    __tablename__ = "activity_overrides"

    activity_id = Column(Integer, ForeignKey("activities.id"), primary_key=True)
    surface     = Column(String, nullable=True)   # "trail" | "road" | None = automatic
    category    = Column(String, nullable=True)   # "race" | "workout" | "long" | "easy" | None = automatic
