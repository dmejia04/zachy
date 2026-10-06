from sqlalchemy import Column, Date, Float, Integer, String
from zachy.database import Base


class Shoe(Base):
    """A pair of running shoes you entered (Profile → Equipment)."""
    __tablename__ = "shoes"

    id         = Column(Integer, primary_key=True)
    brand      = Column(String, nullable=False)
    model      = Column(String, nullable=False)
    version    = Column(String, nullable=True)    # "3", "v2"…
    pair       = Column(Integer, nullable=True)   # 2 = your second pair of the same shoe
    surface    = Column(String, nullable=False)   # "road" | "cross" | "trail"
    use        = Column(String, nullable=False)   # "easy" | "workout" | "race"
    is_default = Column(Integer, nullable=False, default=0)   # the default for its surface and use
    since      = Column(Date, nullable=True)      # in use from (defaults apply from that day)
    retired    = Column(Date, nullable=True)      # retired on (no longer a default after it)
    max_km     = Column(Float, nullable=True)     # its expected life; None = the usual for its use
