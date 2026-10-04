from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from zachy.database import Base


class ActivityLink(Base):
    """A results page you linked to an activity (LiveTrail tracking, a race's results site…)."""
    __tablename__ = "activity_links"

    id = Column(Integer, primary_key=True)
    activity_id = Column(Integer, ForeignKey("activities.id"), index=True)
    url = Column(String, nullable=False)
    kind = Column(String, nullable=True)      # livetrail | web
    label = Column(String, nullable=True)


class LiveTrailData(Base):
    """A LiveTrail race read once: checkpoints, your passings, the winners' (JSON)."""
    __tablename__ = "livetrail_data"

    activity_id = Column(Integer, ForeignKey("activities.id"), primary_key=True)
    url = Column(String, nullable=False)
    data = Column(Text, nullable=False)
    fetched_at = Column(DateTime, nullable=True)
