from sqlalchemy import Column, Float, ForeignKey, Integer, String
from zachy.database import Base


class BestEffort(Base):
    """Fastest stretch of a given distance inside one activity (from its FIT records)."""
    __tablename__ = "best_efforts"

    activity_id = Column(Integer, ForeignKey("activities.id"), primary_key=True)
    key         = Column(String, primary_key=True)    # "5k", "10k", "half", "marathon"
    duration_s  = Column(Float, nullable=False)       # moving time for exactly that distance
    start_km    = Column(Float, nullable=False)       # where in the activity it started
