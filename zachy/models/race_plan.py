from sqlalchemy import Column, Date, DateTime, Float, Integer, String, Text
from zachy.database import Base


class RacePlan(Base):
    """A race you plan (Race planner): the course from a GPX file, its aid stations, your settings."""
    __tablename__ = "race_plans"

    id         = Column(Integer, primary_key=True)
    name       = Column(String, nullable=False)
    kind       = Column(String, nullable=True)    # trail (None) | road
    split_km   = Column(Float, nullable=True)     # road: splits every 1, 2, 5 or 10 km
    race_date  = Column(Date, nullable=True)
    gpx_name   = Column(String, nullable=True)
    course     = Column(Text, nullable=False)     # JSON [[km, altitude m, lat, lon], …] every ~25 m
    aid        = Column(Text, nullable=True)      # JSON [{km, name, stop_min}, …]
    flat_pace  = Column(Float, nullable=True)     # your flat-equivalent pace (min/km); None = from your races
    start_time = Column(String, nullable=True)    # "06:00": clock times in the plan
    outlook    = Column(Float, nullable=True)     # % on the predicted time: −20 optimistic … +20 pessimistic (road ±10)
    strategy   = Column(Float, nullable=True)     # −1 aggressive (fast start) … +1 conservative (strong finish); 0 = your usual race
    nutrition  = Column(Text, nullable=True)      # JSON {carbs_gh, fluid_mlh, sodium_mgh, gel_g, flask_ml}; None = suggested
    created_at = Column(DateTime, nullable=False)
