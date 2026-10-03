from sqlalchemy import Column, Integer, Text
from zachy.database import Base


class GapModel(Base):
    """The fitted personal grade-cost curve (analytics/gap.py), stored as JSON. One row."""
    __tablename__ = "gap_models"

    id   = Column(Integer, primary_key=True)
    data = Column(Text, nullable=False)
