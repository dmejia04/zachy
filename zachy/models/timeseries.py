from sqlalchemy import Column, Integer, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from zachy.database import Base


class Timeseries(Base):
    __tablename__ = "timeseries"

    id              = Column(Integer, primary_key=True)
    activity_id     = Column(Integer, ForeignKey("activities.id"), index=True)
    seconds_elapsed = Column(Integer)
    timestamp       = Column(DateTime, index=True, nullable=True)
    hr              = Column(Float, nullable=True)
    pace            = Column(Float, nullable=True)
    cadence         = Column(Float, nullable=True)
    elevation       = Column(Float, nullable=True)
    latitude        = Column(Float, nullable=True)
    longitude       = Column(Float, nullable=True)
    power           = Column(Float, nullable=True)

    activity = relationship("Activity", back_populates="timeseries")