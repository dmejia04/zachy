from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from zachy.database import Base


class FitFile(Base):
    """The original FIT file downloaded for an activity, and what came out of parsing it."""
    __tablename__ = "fit_files"

    activity_id   = Column(Integer, ForeignKey("activities.id"), primary_key=True)
    garmin_id     = Column(String, index=True)
    status        = Column(String, nullable=False)   # ok | no_records | no_fit | not_found | error
    sport         = Column(String, nullable=True)    # from the FIT session message
    n_records     = Column(Integer, default=0)
    fields        = Column(String, nullable=True)    # comma-separated Parquet columns
    downloaded_at = Column(DateTime, nullable=True)  # naive UTC
    error         = Column(String, nullable=True)


class Record(Base):
    """One FIT `record` message (usually one per second). The full set of fields — running
    dynamics, developer fields, etc. — lives in data/parquet/<garmin_id>.parquet."""
    __tablename__ = "records"

    id          = Column(Integer, primary_key=True)
    activity_id = Column(Integer, ForeignKey("activities.id"), index=True)
    elapsed_s   = Column(Integer)                 # clock time since start, pauses included
    timer_s     = Column(Integer)                 # moving time: pauses removed
    distance_km = Column(Float, nullable=True)    # watch's own cumulative distance
    speed_ms    = Column(Float, nullable=True)
    hr          = Column(Float, nullable=True)
    cadence     = Column(Float, nullable=True)    # steps/min on foot, rpm on the bike
    elevation   = Column(Float, nullable=True)
    latitude    = Column(Float, nullable=True)
    longitude   = Column(Float, nullable=True)
    power       = Column(Float, nullable=True)
    temperature = Column(Float, nullable=True)
