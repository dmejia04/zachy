from sqlalchemy import Column, Float, ForeignKey, Integer, String, Text
from zachy.database import Base


class RouteFingerprint(Base):
    """One run's GPS track reduced to what route detection needs (analytics/routes.py), computed once."""
    __tablename__ = "route_fingerprints"

    activity_id = Column(Integer, ForeignKey("activities.id"), primary_key=True)
    version     = Column(Integer, nullable=False)
    cells       = Column(Text, nullable=False)    # JSON [[i, j], …]: ~150 m map cells the track passes through
    sample      = Column(Text, nullable=False)    # JSON [[lat, lon], …]: ~80 points for maps and direction
    start_lat   = Column(Float, nullable=True)
    start_lon   = Column(Float, nullable=True)
    end_lat     = Column(Float, nullable=True)
    end_lon     = Column(Float, nullable=True)
    area        = Column(Float, nullable=True)    # signed area (km²): + anticlockwise loop, − clockwise
    temp_avg    = Column(Float, nullable=True)    # the watch's mean temperature (reads warm: body heat)


class Route(Base):
    """A route you run regularly: 10 runs or more on the same course."""
    __tablename__ = "routes"

    id           = Column(Integer, primary_key=True)
    name         = Column(String, nullable=True)    # yours; None = auto_name
    auto_name    = Column(String, nullable=False)
    rep_activity = Column(Integer, nullable=False)  # the run most like the others (map, distance)
    kind         = Column(String, nullable=True)    # "loop" | "out-and-back" | "point-to-point"


class RouteMember(Base):
    __tablename__ = "route_members"

    activity_id = Column(Integer, ForeignKey("activities.id"), primary_key=True)
    route_id    = Column(Integer, ForeignKey("routes.id"), nullable=False, index=True)
    reversed    = Column(Integer, nullable=False, default=0)   # loop run the other way round
