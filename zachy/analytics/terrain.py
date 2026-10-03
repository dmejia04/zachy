"""Trail or road, and how hilly: classification of foot runs.

- Terrain from elevation gain per km: flat (< 10 m/km), rolling (10–30), mountain (≥ 30).
  30 m/km is where this history best matches Garmin's own trail/road labels.
- Surface (road / trail / track / treadmill): treadmill and track activities as recorded; track
  when a workout's reps stayed inside a track oval (GPS, analytics/workouts.py); otherwise Garmin's
  label (the watch mode you chose), promoted to "trail" on mountain terrain (catches runs where the
  watch was left in road mode). Your choice on the activity page (activity_overrides) wins.
- km-effort (ITRA): distance + 1 km per 100 m of climbing, to compare hilly and flat runs.
"""

FOOT_TYPES = {"running", "trail_running", "ultra_run", "track_running", "treadmill_running"}
TRAIL_TYPES = {"trail_running", "ultra_run"}
SURFACES = ("road", "trail", "track", "treadmill", "cross")
# Cross country: laps of a short loop in a park or field, so the whole course fits in a small area.
CROSS_MAX_FOOTPRINT_KM = 1.2     # diagonal of the area the GPS track covers
CROSS_DISTANCE_KM = (3.0, 15.0)
ROLLING_M_PER_KM = 10
MOUNTAIN_M_PER_KM = 30


# Garmin's default activity names are "<Place> <Sport>" — these are the sport parts.
SPORT_SUFFIXES = sorted([
    "Trail Running", "Ultra Running", "Treadmill Running", "Track Running", "Running",
    "Road Cycling", "Mountain Biking", "Gravel Cycling", "Indoor Cycling", "Virtual Cycling", "Cycling",
    "Walking", "Hiking", "Strength", "Cardio", "Yoga", "Pool Swim", "Open Water", "Swimming",
    "Mobility", "Skiing", "Backcountry Skiing", "Resort Skiing",
], key=len, reverse=True)


def place_from_name(name: str | None) -> str | None:
    """"Toulouse Running" -> "Toulouse", "Paris - AS42" -> "Paris", "Treadmill Running" -> None."""
    if not name:
        return None
    if " - " in name:
        return name.split(" - ", 1)[0].strip() or None
    for suffix in SPORT_SUFFIXES:
        if name.endswith(" " + suffix):
            return name[: -len(suffix) - 1].strip() or None
    return None


def terrain_class(gain_per_km: float) -> str:
    if gain_per_km >= MOUNTAIN_M_PER_KM:
        return "mountain"
    if gain_per_km >= ROLLING_M_PER_KM:
        return "rolling"
    return "flat"


def classify(activity_type: str | None, distance_km: float | None, elevation_gain: float | None,
             override: str | None = None, detected_track: bool = False, detected_cross: bool = False) -> dict | None:
    """{"terrain", "gain_per_km", "km_effort", "surface", "surface_source", "auto_surface"}
    or None if not a run. auto_surface is what the rules decide, ignoring any override."""
    if activity_type not in FOOT_TYPES or not distance_km or distance_km < 0.5:
        return None
    gain = elevation_gain or 0.0
    gain_per_km = gain / distance_km
    terrain = terrain_class(gain_per_km)

    if activity_type in ("treadmill_running", "track_running"):
        auto, auto_source = activity_type.split("_")[0], "garmin"    # treadmill / track
    elif detected_track:
        auto, auto_source = "track", "gps"
    elif detected_cross and activity_type not in TRAIL_TYPES and terrain != "mountain":
        auto, auto_source = "cross", "gps"
    elif activity_type in TRAIL_TYPES:
        auto, auto_source = "trail", "garmin"
    elif terrain == "mountain":
        auto, auto_source = "trail", "elevation"
    else:
        auto, auto_source = "road", "garmin"
    if override in SURFACES:
        surface, source = override, "manual"
    else:
        surface, source = auto, auto_source

    return {
        "terrain": terrain,
        "gain_per_km": round(gain_per_km, 1),
        "km_effort": round(distance_km + gain / 100, 1),
        "surface": surface,
        "surface_source": source,
        "auto_surface": auto,
    }


def course_footprint(db, activity) -> float | None:
    """Diagonal (km) of the area the GPS track covers, computed once and stored on the activity.
    A cross country (laps of a 1.5-3 km loop) stays within ~1 km; a road 10 km spreads over
    several. None without GPS."""
    import math
    from zachy.models import Record
    if activity.footprint_km is None:
        rows = (db.query(Record.latitude, Record.longitude)
                .filter(Record.activity_id == activity.id, Record.latitude.isnot(None)).all())
        if len(rows) < 20:
            activity.footprint_km = -1
        else:
            lats, lons = [r[0] for r in rows], [r[1] for r in rows]
            k = math.cos(math.radians(sum(lats) / len(lats)))
            activity.footprint_km = round(math.hypot((max(lats) - min(lats)) * 110.57,
                                                     (max(lons) - min(lons)) * 111.32 * k), 2)
        db.commit()
    return activity.footprint_km if activity.footprint_km >= 0 else None


def looks_like_cross(db, activity, category: str | None) -> bool:
    """A race of 3-15 km whose whole course fits in a small area (only races: an easy run looping
    around a park isn't a cross country)."""
    lo, hi = CROSS_DISTANCE_KM
    if category != "race" or not activity.distance_km or not lo <= activity.distance_km <= hi:
        return False
    fp = course_footprint(db, activity)
    return fp is not None and fp < CROSS_MAX_FOOTPRINT_KM
