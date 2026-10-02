"""Transforms raw Garmin activity JSON into clean records for the database."""

from datetime import datetime


def process_activity(raw: dict) -> dict:
    """Map a raw Garmin activity dict to Zachy's Activity table shape."""

    distance_m = raw.get("distance") or 0
    duration_s = raw.get("duration") or 0

    # Pace in min/km (only meaningful if distance > 0)
    avg_pace = None
    if distance_m > 0:
        pace_s_per_km = duration_s / (distance_m / 1000)
        avg_pace = round(pace_s_per_km / 60, 2)  # minutes per km

    start_time = raw.get("startTimeLocal")
    activity_date = None
    if start_time:
        activity_date = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S").date()

    return {
        "garmin_id": str(raw.get("activityId")),
        "date": activity_date,
        "name": raw.get("activityName"),
        "activity_type": raw.get("activityType", {}).get("typeKey"),
        "distance_km": round(distance_m / 1000, 3) if distance_m else None,
        "duration_s": round(duration_s) if duration_s else None,
        "avg_pace": avg_pace,
        "avg_hr": raw.get("averageHR"),
        "max_hr": raw.get("maxHR"),
        "avg_cadence": raw.get("averageRunningCadenceInStepsPerMinute"),
        "elevation_gain": raw.get("elevationGain"),
        "elevation_loss": raw.get("elevationLoss"),
        "avg_power": raw.get("avgPower"),
        "max_power": raw.get("maxPower"),
        "training_effect_aerobic": raw.get("aerobicTrainingEffect"),
        "training_effect_anaerobic": raw.get("anaerobicTrainingEffect"),
        "calories": round(raw["calories"]) if raw.get("calories") is not None else None,
        "steps": raw.get("steps"),
    }


def process_activities(raw_list: list[dict]) -> list[dict]:
    """Process a list of raw activities."""
    return [process_activity(a) for a in raw_list]
