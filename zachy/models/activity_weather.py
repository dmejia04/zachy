from sqlalchemy import Column, DateTime, ForeignKey, Integer, Text

from zachy.database import Base


class ActivityWeather(Base):
    """Garmin's weather for an activity (nearest station at the start), fetched once.
    data is the converted JSON, or NULL when Garmin has none."""
    __tablename__ = "activity_weather"

    activity_id = Column(Integer, ForeignKey("activities.id"), primary_key=True)
    data = Column(Text, nullable=True)
    fetched_at = Column(DateTime, nullable=False)
