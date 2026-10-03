from sqlalchemy import Boolean, Column, Date, DateTime, Float, Integer, String
from zachy.database import Base


class Wellness(Base):
    """Everything Garmin knows about one day (sleep, HRV, body, fitness, training, daily stats).
    Units are converted to readable ones; the raw responses live in data/wellness/raw/."""
    __tablename__ = "wellness"

    date = Column(Date, primary_key=True)

    # Sleep (night ending on this date)
    sleep_s          = Column(Integer)
    deep_s           = Column(Integer)
    light_s          = Column(Integer)
    rem_s            = Column(Integer)
    awake_s          = Column(Integer)
    sleep_score      = Column(Integer)
    sleep_quality    = Column(String)      # EXCELLENT | GOOD | FAIR | POOR
    sleep_need_min   = Column(Integer)
    sleep_start      = Column(DateTime)    # local time
    sleep_end        = Column(DateTime)    # local time
    sleep_avg_hr     = Column(Float)
    sleep_resp       = Column(Float)       # breaths/min
    sleep_spo2       = Column(Float)       # %
    sleep_bb_change  = Column(Integer)     # body battery gained overnight
    skin_temp_c      = Column(Float)       # deviation, if the watch measures it

    # HRV (overnight, ms)
    hrv_night        = Column(Float)
    hrv_weekly       = Column(Float)
    hrv_5min_high    = Column(Float)
    hrv_status       = Column(String)      # BALANCED | UNBALANCED | LOW | POOR
    hrv_base_low     = Column(Float)       # balanced range
    hrv_base_high    = Column(Float)

    # Heart
    resting_hr       = Column(Float)
    min_hr           = Column(Float)
    max_hr           = Column(Float)

    # Body composition (last weigh-in of the day)
    weight_kg        = Column(Float)
    bmi              = Column(Float)
    body_fat_pct     = Column(Float)
    body_water_pct   = Column(Float)
    muscle_mass_kg   = Column(Float)
    bone_mass_kg     = Column(Float)

    # Fitness (set on the days Garmin updated them)
    vo2max           = Column(Float)
    vo2max_cycling   = Column(Float)
    race_5k_s        = Column(Integer)
    race_10k_s       = Column(Integer)
    race_half_s      = Column(Integer)
    race_marathon_s  = Column(Integer)
    hill_score       = Column(Integer)
    hill_strength    = Column(Integer)
    hill_endurance   = Column(Integer)
    endurance_score  = Column(Integer)
    lt_hr            = Column(Float)       # lactate threshold heart rate
    lt_speed_ms      = Column(Float)       # lactate threshold speed (m/s)
    ftp_running_w    = Column(Float)       # running functional threshold power

    # Training
    readiness_score  = Column(Integer)
    readiness_level  = Column(String)
    recovery_time_min = Column(Integer)
    acute_load       = Column(Float)
    chronic_load     = Column(Float)
    acwr             = Column(Float)       # acute:chronic workload ratio
    training_status  = Column(String)      # PRODUCTIVE, MAINTAINING, RECOVERY, …
    load_aerobic_low  = Column(Float)      # 4-week load by focus
    load_aerobic_high = Column(Float)
    load_anaerobic    = Column(Float)

    # Daily activity
    steps            = Column(Integer)
    step_goal        = Column(Integer)
    distance_km      = Column(Float)
    floors_up        = Column(Float)
    total_kcal       = Column(Float)
    active_kcal      = Column(Float)
    bmr_kcal         = Column(Float)
    intensity_moderate_min = Column(Integer)
    intensity_vigorous_min = Column(Integer)
    active_s         = Column(Integer)
    highly_active_s  = Column(Integer)
    sedentary_s      = Column(Integer)

    # Stress & body battery
    stress_avg       = Column(Integer)
    stress_max       = Column(Integer)
    stress_low_s     = Column(Integer)
    stress_medium_s  = Column(Integer)
    stress_high_s    = Column(Integer)
    stress_rest_s    = Column(Integer)
    bb_high          = Column(Integer)
    bb_low           = Column(Integer)
    bb_charged       = Column(Integer)
    bb_drained       = Column(Integer)
    bb_at_wake       = Column(Integer)

    # Breathing & blood oxygen (whole day)
    spo2_avg         = Column(Float)
    spo2_low         = Column(Float)
    resp_waking      = Column(Float)
    resp_low         = Column(Float)
    resp_high        = Column(Float)

    # Per-day endpoints already fetched for this date (range endpoints are cheap to redo)
    daily_fetched    = Column(Boolean, default=False)
