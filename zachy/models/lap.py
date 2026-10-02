from sqlalchemy import Column, Integer, Float, ForeignKey
from sqlalchemy.orm import relationship
from zachy.database import Base


class Lap(Base):
    __tablename__ = "laps"

    id             = Column(Integer, primary_key=True)
    activity_id    = Column(Integer, ForeignKey("activities.id"), index=True)
    lap_number     = Column(Integer)
    distance_km    = Column(Float, nullable=True)
    duration_s     = Column(Integer, nullable=True)
    avg_pace       = Column(Float, nullable=True)
    avg_hr         = Column(Float, nullable=True)
    avg_cadence    = Column(Float, nullable=True)
    elevation_gain = Column(Float, nullable=True)

    activity = relationship("Activity", back_populates="laps")