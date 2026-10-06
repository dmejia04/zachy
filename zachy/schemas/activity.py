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
    relative_effort: float | None = None
    category: str | None = None   # race | workout | easy — automatic guess or your override (analytics/races.py)
    place: str | None = None      # from the activity name ("Toulouse Running" -> Toulouse)
    surface: str | None = None    # road | trail | treadmill | track (analytics/terrain.py)
    terrain: str | None = None    # flat | rolling | mountain
    workout: dict | None = None   # {"type", "summary", ...} for workouts (analytics/workouts.py)
    official: dict | None = None  # official result (UTMB / ITRA / Betrail) matched to it (analytics/race_results.py)

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
    steps: int = 0          # all steps that month (watch, every day), not only running

class LapOut(BaseModel):
    lap_number: int | None
    distance_km: float | None
    duration_s: int | None
    avg_pace: float | None
    avg_hr: float | None
    avg_cadence: float | None
    elevation_gain: float | None
    max_hr: float | None = None           # computed from FIT records when available
    elevation_loss: float | None = None   # computed from FIT records when available
    avg_power: float | None = None        # computed from FIT records when available
    gap_pace: float | None = None         # personal grade-adjusted pace (min/km)
    gap_race_pace: float | None = None    # race-model grade-adjusted pace (min/km)

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
    peaks: dict | None = None             # 30 s extremes + altitude/gradient extremes (analytics/peaks.py)
    terrain: dict | None = None           # flat/rolling/mountain, m/km, km-effort, trail/road (analytics/terrain.py)
    category: dict | None = None          # race/workout/easy, auto guess, source, reasons (analytics/races.py)
    place: str | None = None              # from the activity name ("Toulouse Running" -> Toulouse)
    gap: dict | None = None               # flat-equivalent pace per method (analytics/gap.py)
    shoe: dict | None = None              # {"shoe": {...}, "source": "manual" | "default"} (analytics/shoes.py)
    route: dict | None = None             # the regular route it's on: name, nth time, time rank (analytics/routes.py)