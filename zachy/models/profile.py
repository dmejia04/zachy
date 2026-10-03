from sqlalchemy import Column, Date, DateTime, Float, Integer, String, Text

from zachy.database import Base


class Profile(Base):
    """You: one row. Manual values (columns) win over what Garmin says (garmin_json)."""
    __tablename__ = "profile"

    id = Column(Integer, primary_key=True)
    birth_date = Column(Date, nullable=True)
    height_cm = Column(Float, nullable=True)
    weight_kg = Column(Float, nullable=True)
    sex = Column(String, nullable=True)           # male | female
    garmin_json = Column(Text, nullable=True)     # last profile read from Garmin
    garmin_fetched_at = Column(DateTime, nullable=True)
