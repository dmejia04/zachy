from sqlalchemy import Column, Integer, Float, Date
from zachy.database import Base


class BodyMetric(Base):
    __tablename__ = "body_metrics"

    id           = Column(Integer, primary_key=True)
    date         = Column(Date, unique=True, index=True)
    hrv          = Column(Float, nullable=True)
    weight_kg    = Column(Float, nullable=True)
    vo2max       = Column(Float, nullable=True)
    resting_hr   = Column(Float, nullable=True)
    sleep_hours  = Column(Float, nullable=True)