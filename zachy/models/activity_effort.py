from sqlalchemy import Column, DateTime, ForeignKey, Integer, Text

from zachy.database import Base


class ActivityEffort(Base):
    """Garmin's effort figures for an activity (training effect, load, RPE, feel, stamina),
    from the activity details, fetched once. data is JSON."""
    __tablename__ = "activity_effort"

    activity_id = Column(Integer, ForeignKey("activities.id"), primary_key=True)
    data = Column(Text, nullable=True)
    fetched_at = Column(DateTime, nullable=False)
