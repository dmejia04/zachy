"""Transforms raw Garmin daily wellness data into a BodyMetric record."""


def process_body_metrics(day: str, hrv: dict, weight: dict, maxm: list, stats: dict) -> dict:
    """Combine HRV, weight, VO2max, and stats for a single day into one record."""

    hrv_value = None
    if hrv and "hrvSummary" in hrv:
        hrv_value = hrv["hrvSummary"].get("lastNightAvg")

    weight_kg = None
    if weight and weight.get("dateWeightList"):
        weight_g = weight["dateWeightList"][0].get("weight")
        if weight_g:
            weight_kg = round(weight_g / 1000, 2)

    vo2max = None
    if maxm and len(maxm) > 0:
        generic = maxm[0].get("generic") or {}
        vo2max = generic.get("vo2MaxPreciseValue") or generic.get("vo2MaxValue")

    resting_hr = stats.get("restingHeartRate") if stats else None
    sleep_seconds = stats.get("sleepingSeconds") if stats else None
    sleep_hours = round(sleep_seconds / 3600, 2) if sleep_seconds else None

    return {
        "date": day,
        "hrv": hrv_value,
        "weight_kg": weight_kg,
        "vo2max": vo2max,
        "resting_hr": resting_hr,
        "sleep_hours": sleep_hours,
    }