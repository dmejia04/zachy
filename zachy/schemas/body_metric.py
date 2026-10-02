from datetime import date as date_type
from pydantic import BaseModel


class BodyMetricOut(BaseModel):
    date: date_type
    hrv: float | None
    weight_kg: float | None
    vo2max: float | None
    resting_hr: float | None
    sleep_hours: float | None

    class Config:
        from_attributes = True