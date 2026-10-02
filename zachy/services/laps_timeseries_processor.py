"""Transforms raw Garmin lap splits and detail metrics into Zachy records."""

from datetime import datetime, timezone


def process_laps(activity_db_id: int, splits_raw: dict) -> list[dict]:
    """Map Garmin's lapDTOs into Lap records for one activity."""
    laps = []
    for lap in splits_raw.get("lapDTOs", []):
        distance_m = lap.get("distance") or 0
        duration_s = lap.get("duration") or 0

        avg_pace = None
        if distance_m > 0:
            avg_pace = round((duration_s / (distance_m / 1000)) / 60, 2)

        laps.append({
            "activity_id": activity_db_id,
            "lap_number": lap.get("lapIndex"),
            "distance_km": round(distance_m / 1000, 3) if distance_m else None,
            "duration_s": round(duration_s) if duration_s else None,
            "avg_pace": avg_pace,
            "avg_hr": lap.get("averageHR"),
            "avg_cadence": lap.get("averageRunCadence"),
            "elevation_gain": lap.get("elevationGain"),
        })
    return laps


def process_timeseries(activity_db_id: int, details_raw: dict) -> list[dict]:
    """Map Garmin's activityDetailMetrics (column-oriented) into Timeseries records."""
    descriptors = {d["key"]: d["metricsIndex"] for d in details_raw.get("metricDescriptors", [])}
    rows = details_raw.get("activityDetailMetrics", [])

    def get(metrics_list, key):
        idx = descriptors.get(key)
        if idx is None or idx >= len(metrics_list):
            return None
        return metrics_list[idx]

    records = []
    for row in rows:
        m = row.get("metrics", [])

        ts_ms = get(m, "directTimestamp")
        timestamp = None
        if ts_ms:
            timestamp = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

        speed_mps = get(m, "directSpeed")
        pace = None
        if speed_mps and speed_mps > 0:
            pace = round((1000 / speed_mps) / 60, 2)  # min/km

        records.append({
            "activity_id": activity_db_id,
            "seconds_elapsed": round(get(m, "sumElapsedDuration") or 0),
            "timestamp": timestamp,
            "hr": get(m, "directHeartRate"),
            "pace": pace,
            "cadence": get(m, "directRunCadence"),
            "elevation": get(m, "directElevation"),
            "latitude": get(m, "directLatitude"),
            "longitude": get(m, "directLongitude"),
            "power": get(m, "directPower"),
        })

    return records