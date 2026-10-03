from sqlalchemy import Boolean, Column, Date, DateTime, Float, Integer, String, Text

from zachy.database import Base


class RaceResult(Base):
    """An official result from a results site (UTMB, later ITRA / Betrail), matched to the
    activity (or activities, for stage races) recorded that day."""
    __tablename__ = "race_results"

    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False)          # utmb | itra | betrail
    key = Column(String, nullable=False, index=True)  # the site's id for this result
    date = Column(Date, nullable=False)
    event = Column(String, nullable=True)             # "Festival Des Templiers"
    race = Column(String, nullable=True)              # "Endurance Trail"
    distance_km = Column(Float, nullable=True)
    elevation_gain = Column(Float, nullable=True)
    time_s = Column(Integer, nullable=True)
    dnf = Column(Boolean, default=False)
    rank = Column(Integer, nullable=True)
    total = Column(Integer, nullable=True)
    rank_gender = Column(Integer, nullable=True)
    total_gender = Column(Integer, nullable=True)
    score = Column(Float, nullable=True)              # UTMB index / ITRA points / Betrail score
    url = Column(String, nullable=True)
    raw = Column(Text, nullable=True)                 # the site's JSON for this result
    activity_ids = Column(Text, nullable=True)        # JSON list; NULL = no Garmin activity found
    match = Column(String, nullable=True)             # auto | confirmed | rejected
    fetched_at = Column(DateTime, nullable=True)
