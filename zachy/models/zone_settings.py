from sqlalchemy import JSON, Column, Date, Integer
from zachy.database import Base


class ZoneSettings(Base):
    """Manually set zone limits, effective from `valid_from` until the next set."""
    __tablename__ = "zone_settings"

    id         = Column(Integer, primary_key=True)
    valid_from = Column(Date, unique=True, index=True, nullable=False)
    zone_model = Column(Integer, nullable=False)   # 3 or 5
    # {"boundaries": {"hr": {"3": [146, 172], "5": [...]}, "pace": {...}, ...}}
    # absolute limits between zones, in each metric's unit — see zachy/zones.py
    data       = Column(JSON, nullable=False)
