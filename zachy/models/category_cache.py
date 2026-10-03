from sqlalchemy import Column, Float, ForeignKey, Integer, String, Text
from zachy.database import Base


class CategoryCache(Base):
    """Stored automatic race / workout / easy guess (analytics/races.py), so lists and charts over
    years of runs don't recompute it. Cleared around new activities by the sync."""
    __tablename__ = "category_cache"

    activity_id   = Column(Integer, ForeignKey("activities.id"), primary_key=True)
    category      = Column(String, nullable=False)
    reasons       = Column(Text, nullable=True)        # JSON list
    adjusted_pace = Column(Float, nullable=True)       # personal flat-equivalent pace (races only)
    model_version = Column(String, nullable=True)      # personal grade model it was computed with
