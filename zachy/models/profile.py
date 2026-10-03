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
    efficiency_ref_hr = Column(Float, nullable=True)   # aerobic efficiency reference; NULL = automatic
    max_hr_ceiling = Column(Float, nullable=True)      # highest believable HR; NULL = 220 - age/2
    utmb_url = Column(String, nullable=True)      # runner page on utmb.world (official results)
    itra_url = Column(String, nullable=True)
    betrail_url = Column(String, nullable=True)
    ffa_licence = Column(String, nullable=True)   # French athletics licence number (reference only)
    indexes_json = Column(Text, nullable=True)    # your overall UTMB / ITRA indexes, from those pages
    garmin_json = Column(Text, nullable=True)     # last profile read from Garmin
    garmin_fetched_at = Column(DateTime, nullable=True)
