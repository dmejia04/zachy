from sqlalchemy import Column, Integer, String, DateTime
from zachy.database import Base


class SyncRun(Base):
    """One press of the "Sync" button: when it ran, how it ended, and what it brought in."""
    __tablename__ = "sync_runs"

    id               = Column(Integer, primary_key=True)
    started_at       = Column(DateTime, nullable=False)   # naive UTC
    finished_at      = Column(DateTime, nullable=True)    # naive UTC
    status           = Column(String, nullable=False)     # running | ok | error
    new_activities   = Column(Integer, default=0)
    new_details      = Column(Integer, default=0)         # activities whose FIT file was stored
    new_body_metrics = Column(Integer, default=0)
    error            = Column(String, nullable=True)
