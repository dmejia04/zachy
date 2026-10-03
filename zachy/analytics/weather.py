"""Weather at the start of an activity, from Garmin (nearest weather station, one observation).

Garmin returns °F and mph; stored converted to °C and km/h. Fetched once per activity and cached
(also "none" when Garmin has nothing, so it isn't asked again).
"""

import json
from datetime import datetime

from sqlalchemy.orm import Session

from zachy.models import Activity, ActivityWeather

_client = None


def _garmin():
    global _client
    if _client is None:
        from zachy.services.garmin_client import get_client
        _client = get_client()
    return _client


def _convert(w: dict) -> dict | None:
    if not w or w.get("temp") is None:
        return None
    c = lambda f: None if f is None else round((f - 32) * 5 / 9, 1)
    kmh = lambda mph: None if mph is None else round(mph * 1.609344)
    return {
        "temp": c(w.get("temp")), "feels_like": c(w.get("apparentTemp")), "dew_point": c(w.get("dewPoint")),
        "humidity": w.get("relativeHumidity"), "wind_kmh": kmh(w.get("windSpeed")),
        "gust_kmh": kmh(w.get("windGust")), "wind_dir": (w.get("windDirectionCompassPoint") or "").upper() or None,
        "description": (w.get("weatherTypeDTO") or {}).get("desc"),
        "station": (w.get("weatherStationDTO") or {}).get("name"),
    }


def activity_weather(db: Session, activity: Activity, client=None) -> dict | None:
    """Cached weather, fetched from Garmin the first time. Network errors aren't cached."""
    row = db.get(ActivityWeather, activity.id)
    if row is None:
        raw = (client or _garmin()).get_activity_weather(activity.garmin_id)
        data = _convert(raw)
        row = ActivityWeather(activity_id=activity.id, data=json.dumps(data) if data else None,
                              fetched_at=datetime.now())
        db.add(row)
        db.commit()
    return json.loads(row.data) if row.data else None
