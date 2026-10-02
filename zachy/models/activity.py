from sqlalchemy import Column, Integer, Float, String, Date
from sqlalchemy.orm import relationship
from zachy.database import Base


class Activity(Base):
    __tablename__ = "activities"

    id                        = Column(Integer, primary_key=True)
    garmin_id                 = Column(String, unique=True, index=True)
    date                      = Column(Date, index=True)
    name                      = Column(String, nullable=True)
    activity_type             = Column(String, nullable=True)
    distance_km               = Column(Float, nullable=True)
    duration_s                = Column(Integer, nullable=True)
    avg_pace                  = Column(Float, nullable=True)
    avg_hr                    = Column(Float, nullable=True)
    max_hr                    = Column(Float, nullable=True)
    avg_cadence               = Column(Float, nullable=True)
    elevation_gain            = Column(Float, nullable=True)
    elevation_loss            = Column(Float, nullable=True)
    avg_power                 = Column(Float, nullable=True)
    max_power                 = Column(Float, nullable=True)
    training_effect_aerobic   = Column(Float, nullable=True)
    training_effect_anaerobic = Column(Float, nullable=True)
    calories                  = Column(Integer, nullable=True)
    steps                     = Column(Integer, nullable=True)
    notes                     = Column(String, nullable=True)

    laps       = relationship("Lap", back_populates="activity", cascade="all, delete")
    timeseries = relationship("Timeseries", back_populates="activity", cascade="all, delete")