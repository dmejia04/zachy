"""Daily wellness data from Garmin: sleep, HRV, body composition, fitness, training, daily stats.

Range endpoints (one request covers up to a year) fill most columns; three endpoints only exist
per day (daily stats, training readiness, training status). Every raw response is kept in
data/wellness/raw/<source>/ so more fields can be extracted later without re-downloading.
"""

import gzip
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from zachy.models import Wellness

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "wellness" / "raw"


# ---------- helpers ----------

def save_raw(source: str, key: str, payload) -> None:
    path = RAW_DIR / source / f"{key}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, default=str)


def _day(db: Session, d, cache: dict) -> Wellness:
    """The Wellness row for a date (created if needed), cached for the current batch."""
    d = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
    if d not in cache:
        cache[d] = db.get(Wellness, d) or Wellness(date=d)
        db.add(cache[d])
    return cache[d]


def _set(row: Wellness, **values) -> None:
    """Set only the values Garmin actually provided (None and 0-placeholders are skipped)."""
    for k, v in values.items():
        if v is not None:
            setattr(row, k, v)


def _nz(v, scale=1.0, digits=None):
    """Garmin uses 0 for 'not measured' in many fields."""
    if v in (None, 0, 0.0):
        return None
    v = v * scale
    return round(v, digits) if digits is not None else v


def _local_ms(ms):
    # Garmin's "local" millis are local wall-clock time encoded as if it were UTC.
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(tzinfo=None) if ms else None


def chunks(start: date, end: date, days: int):
    """[start, end] split into consecutive windows of at most `days` days."""
    while start <= end:
        stop = min(end, start + timedelta(days=days - 1))
        yield start, stop
        start = stop + timedelta(days=1)


# ---------- range sources ----------

def apply_sleep(db, payload, cache):
    for item in payload or []:
        v = item.get("values") or {}
        if not v.get("totalSleepTimeInSeconds"):
            continue
        _set(_day(db, item["calendarDate"], cache),
             sleep_s=v.get("totalSleepTimeInSeconds"), deep_s=v.get("deepTime"),
             light_s=v.get("lightTime"), rem_s=v.get("remTime"), awake_s=v.get("awakeTime"),
             sleep_score=v.get("sleepScore"), sleep_quality=v.get("sleepScoreQuality"),
             sleep_need_min=v.get("sleepNeed"),
             sleep_start=_local_ms(v.get("localSleepStartTimeInMillis")),
             sleep_end=_local_ms(v.get("localSleepEndTimeInMillis")),
             sleep_avg_hr=_nz(v.get("avgHeartRate")), sleep_resp=_nz(v.get("respiration")),
             sleep_spo2=_nz(v.get("spO2")), sleep_bb_change=v.get("bodyBatteryChange"),
             skin_temp_c=_nz(v.get("skinTempC")))


def apply_hrv(db, payload, cache):
    for s in (payload or {}).get("hrvSummaries") or []:
        base = s.get("baseline") or {}
        _set(_day(db, s["calendarDate"], cache),
             hrv_night=s.get("lastNightAvg"), hrv_weekly=s.get("weeklyAvg"),
             hrv_5min_high=s.get("lastNight5MinHigh"), hrv_status=s.get("status"),
             hrv_base_low=base.get("balancedLow"), hrv_base_high=base.get("balancedUpper"))


def apply_rhr(db, payload, cache):
    for item in payload or []:
        if item.get("value"):
            _set(_day(db, item["calendarDate"], cache), resting_hr=item["value"])


def apply_body_comp(db, payload, cache):
    latest = {}   # calendarDate -> last weigh-in of that day
    for w in (payload or {}).get("dateWeightList") or []:
        d = w.get("calendarDate")
        if d and (d not in latest or (w.get("timestampGMT") or 0) >= (latest[d].get("timestampGMT") or 0)):
            latest[d] = w
    for d, w in latest.items():
        _set(_day(db, d, cache),
             weight_kg=_nz(w.get("weight"), 0.001, 2), bmi=_nz(w.get("bmi"), 1, 1),
             body_fat_pct=_nz(w.get("bodyFat"), 1, 1), body_water_pct=_nz(w.get("bodyWater"), 1, 1),
             muscle_mass_kg=_nz(w.get("muscleMass"), 0.001, 2), bone_mass_kg=_nz(w.get("boneMass"), 0.001, 2))


def apply_max_metrics(db, payload, cache):
    for item in payload or []:
        for key, col in (("generic", "vo2max"), ("cycling", "vo2max_cycling")):
            m = item.get(key) or {}
            v = m.get("vo2MaxPreciseValue") or m.get("vo2MaxValue")
            if v and m.get("calendarDate"):
                _set(_day(db, m["calendarDate"], cache), **{col: v})


def apply_race(db, payload, cache):
    for r in payload or []:
        if r.get("calendarDate") and r.get("time5K"):
            _set(_day(db, r["calendarDate"], cache),
                 race_5k_s=r.get("time5K"), race_10k_s=r.get("time10K"),
                 race_half_s=r.get("timeHalfMarathon"), race_marathon_s=r.get("timeMarathon"))


def apply_hill(db, payload, cache):
    for h in (payload or {}).get("hillScoreDTOList") or []:
        if h.get("overallScore"):
            _set(_day(db, h["calendarDate"], cache), hill_score=h["overallScore"],
                 hill_strength=h.get("strengthScore"), hill_endurance=h.get("enduranceScore"))


def apply_endurance(db, payload, cache):
    # Garmin returns weekly groups keyed by the week's first day.
    for d, g in ((payload or {}).get("groupMap") or {}).items():
        if g and g.get("groupAverage"):
            _set(_day(db, d, cache), endurance_score=g["groupAverage"])


def apply_lactate(db, payload, cache):
    def running(series):
        return [x for x in series or [] if x.get("series") in (None, "running")]
    for x in running((payload or {}).get("heart_rate")):
        _set(_day(db, x["from"], cache), lt_hr=x.get("value"))
    for x in running((payload or {}).get("speed")):
        # Garmin reports this speed in tenths of m/s (0.45 -> 4.5 m/s = 3:42/km).
        _set(_day(db, x["from"], cache), lt_speed_ms=_nz(x.get("value"), 10, 3))
    for x in running((payload or {}).get("power")):
        _set(_day(db, x["from"], cache), ftp_running_w=x.get("value"))


RANGE_SOURCES = {
    # name: (max days per request, fetch(client, start, end), apply)
    "sleep":       (365, lambda c, s, e: c.get_sleep_daily(s, e), apply_sleep),
    "hrv":         (365, lambda c, s, e: c.get_hrv_data_range(s, e), apply_hrv),
    "rhr":         (365, lambda c, s, e: c.get_rhr_daily(s, e), apply_rhr),
    "body_comp":   (365, lambda c, s, e: c.get_body_composition(s, e), apply_body_comp),
    "max_metrics": (365, lambda c, s, e: c.get_max_metrics_range(s, e), apply_max_metrics),
    "race":        (365, lambda c, s, e: c.get_race_predictions(s, e, "daily"), apply_race),
    "hill":        (365, lambda c, s, e: c.get_hill_score(s, e), apply_hill),
    "endurance":   (90,  lambda c, s, e: c.get_endurance_score(s, e), apply_endurance),
    "lactate":     (365, lambda c, s, e: c.get_lactate_threshold(latest=False, start_date=s, end_date=e),
                    apply_lactate),
}


def sync_ranges(client, db: Session, start: date, end: date, log=print) -> None:
    """Fetch every range source for [start, end] and store it. Commits per request."""
    for name, (max_days, fetch, apply) in RANGE_SOURCES.items():
        for s, e in chunks(start, end, max_days):
            payload = fetch(client, s.isoformat(), e.isoformat())
            save_raw(name, f"{s}_{e}", payload)
            cache: dict = {}
            apply(db, payload, cache)
            db.commit()
        log(f"  {name}: {start} → {end}")


# ---------- per-day sources ----------

def apply_stats(row: Wellness, s: dict) -> None:
    if not s or not s.get("calendarDate"):
        return
    _set(row,
         steps=s.get("totalSteps"), step_goal=s.get("dailyStepGoal"),
         distance_km=_nz(s.get("totalDistanceMeters"), 0.001, 2),
         floors_up=_nz(s.get("floorsAscended"), 1, 1),
         total_kcal=_nz(s.get("totalKilocalories")), active_kcal=_nz(s.get("activeKilocalories")),
         bmr_kcal=_nz(s.get("bmrKilocalories")),
         intensity_moderate_min=s.get("moderateIntensityMinutes"),
         intensity_vigorous_min=s.get("vigorousIntensityMinutes"),
         active_s=s.get("activeSeconds"), highly_active_s=s.get("highlyActiveSeconds"),
         sedentary_s=s.get("sedentarySeconds"),
         min_hr=_nz(s.get("minHeartRate")), max_hr=_nz(s.get("maxHeartRate")),
         stress_avg=s.get("averageStressLevel") if (s.get("averageStressLevel") or -1) >= 0 else None,
         stress_max=s.get("maxStressLevel") if (s.get("maxStressLevel") or -1) >= 0 else None,
         stress_low_s=s.get("lowStressDuration"), stress_medium_s=s.get("mediumStressDuration"),
         stress_high_s=s.get("highStressDuration"), stress_rest_s=s.get("restStressDuration"),
         bb_high=s.get("bodyBatteryHighestValue"), bb_low=s.get("bodyBatteryLowestValue"),
         bb_charged=s.get("bodyBatteryChargedValue"), bb_drained=s.get("bodyBatteryDrainedValue"),
         bb_at_wake=s.get("bodyBatteryAtWakeTime"),
         spo2_avg=_nz(s.get("averageSpo2")), spo2_low=_nz(s.get("lowestSpo2")),
         resp_waking=_nz(s.get("avgWakingRespirationValue")),
         resp_low=_nz(s.get("lowestRespirationValue")), resp_high=_nz(s.get("highestRespirationValue")))
    if row.resting_hr is None:
        _set(row, resting_hr=_nz(s.get("restingHeartRate")))


def apply_readiness(row: Wellness, items) -> None:
    # Several readings per day; the first one (morning, after sleep) is the one to trend.
    items = [x for x in items or [] if x.get("score") is not None]
    if not items:
        return
    r = min(items, key=lambda x: x.get("timestamp") or "")
    _set(row, readiness_score=r.get("score"), readiness_level=r.get("level"),
         recovery_time_min=r.get("recoveryTime"))


def apply_training_status(row: Wellness, payload, day: date) -> None:
    status = ((payload or {}).get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData") or {}
    for s in status.values():
        if s.get("calendarDate") != day.isoformat():
            continue   # "most recent" can be an older day; only keep exact matches
        acute = s.get("acuteTrainingLoadDTO") or {}
        phrase = s.get("trainingStatusFeedbackPhrase") or ""
        _set(row, training_status=phrase.rsplit("_", 1)[0] or None,
             acute_load=acute.get("dailyTrainingLoadAcute"),
             chronic_load=acute.get("dailyTrainingLoadChronic"),
             acwr=acute.get("dailyAcuteChronicWorkloadRatio"))
        break
    balance = ((payload or {}).get("mostRecentTrainingLoadBalance") or {}).get("metricsTrainingLoadBalanceDTOMap") or {}
    for b in balance.values():
        if b.get("calendarDate") == day.isoformat():
            _set(row, load_aerobic_low=b.get("monthlyLoadAerobicLow"),
                 load_aerobic_high=b.get("monthlyLoadAerobicHigh"),
                 load_anaerobic=b.get("monthlyLoadAnaerobic"))
            break


def sync_day(client, db: Session, day: date) -> None:
    """Fetch the three per-day sources for one date and store them. Commits."""
    key = day.isoformat()
    stats = client.get_stats(key)
    readiness = client.get_training_readiness(key)
    status = client.get_training_status(key)
    for name, payload in (("stats", stats), ("readiness", readiness), ("training_status", status)):
        save_raw(name, key, payload)
    row = _day(db, day, {})
    apply_stats(row, stats)
    apply_readiness(row, readiness)
    apply_training_status(row, status, day)
    row.daily_fetched = True
    db.commit()


# ---------- incremental (sync button) ----------

FIRST_SYNC_DAYS = 30


def sync_recent(client, db: Session, log=print) -> int:
    """From the day before the newest fully-fetched day through today (so a day synced while
    still in progress gets completed). Returns the number of days refreshed."""
    latest = db.query(func.max(Wellness.date)).filter(Wellness.daily_fetched.is_(True)).scalar()
    today = date.today()
    start = latest - timedelta(days=1) if latest else today - timedelta(days=FIRST_SYNC_DAYS - 1)
    sync_ranges(client, db, start, today, log=log)
    d = start
    while d <= today:
        sync_day(client, db, d)
        d += timedelta(days=1)
    return (today - start).days + 1
