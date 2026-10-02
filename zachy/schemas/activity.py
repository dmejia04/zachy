from datetime import date as date_type
from pydantic import BaseModel


class ActivityOut(BaseModel):
    id: int
    garmin_id: str
    date: date_type
    name: str | None
    activity_type: str | None
    distance_km: float | None
    duration_s: int | None
    avg_pace: float | None
    avg_hr: float | None
    max_hr: float | None
    avg_cadence: float | None
    elevation_gain: float | None
    elevation_loss: float | None
    avg_power: float | None
    max_power: float | None
    training_effect_aerobic: float | None
    training_effect_anaerobic: float | None
    calories: int | None
    steps: int | None

    class Config:
        from_attributes = True


class YearlySummaryOut(BaseModel):
    year: int
    total_distance_km: float
    total_duration_s: float
    total_elevation_gain: float
    num_runs: int
    avg_pace: float | None
    avg_hr: float | None


class MonthlySummaryOut(BaseModel):
    month: int
    total_distance_km: float
    total_duration_s: float
    total_elevation_gain: float
    num_runs: int

class LapOut(BaseModel):
    lap_number: int | None
    distance_km: float | None
    duration_s: int | None
    avg_pace: float | None
    avg_hr: float | None
    avg_cadence: float | None
    elevation_gain: float | None

    class Config:
        from_attributes = True


class TimeseriesPointOut(BaseModel):
    seconds_elapsed: int                  # moving time (pauses removed when known)
    elapsed_s: int | None = None          # clock time since start
    distance_km: float | None = None      # cumulative
    hr: float | None = None
    pace: float | None = None             # min/km
    speed_ms: float | None = None
    cadence: float | None = None
    elevation: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    power: float | None = None
    temperature: float | None = None

    class Config:
        from_attributes = True


class ActivityDetailOut(ActivityOut):
    laps: list[LapOut]
    timeseries: list[TimeseriesPointOut]
    timeseries_source: str                # "fit" (original file) | "chart" (old 2,000-point data) | "none"
    timeseries_points: int                # points before downsampling