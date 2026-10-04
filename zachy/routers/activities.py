from datetime import date
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from zachy.database import get_db
import numpy as np
import numpy as np
import pandas as pd

from zachy.analytics.elevation import display_clock, remove_jumps
from zachy.analytics.gap import adjusted_paces, split_paces
from zachy.analytics.peaks import elevation_extremes, sustained_extremes
from zachy.analytics.splits import km_splits, lap_stats, load_track
from zachy.analytics.races import CATEGORIES, category_status
from zachy.analytics.terrain import SURFACES, classify, looks_like_cross, place_from_name
from zachy.analytics.effort import activity_effort
from zachy.analytics.race_results import official_for, results_by_activity
from zachy.analytics.weather import activity_weather
from zachy.analytics.workouts import cached_workout
from zachy.models import FitFile
from pydantic import BaseModel

from zachy.models import Activity, ActivityOverride, CategoryCache, Lap, Record, Timeseries
from zachy.schemas.activity import ActivityOut, ActivityDetailOut, TimeseriesPointOut

router = APIRouter()

@router.get("/", response_model=list[ActivityOut])
def list_activities(
    limit: int = Query(50, le=500),
    offset: int = 0,
    activity_type: str | None = None,
    year: int | None = None,
    month: int | None = Query(None, ge=1, le=12),
    db: Session = Depends(get_db),
):
    """List activities, most recent first. Optionally filter by activity_type, year and month."""
    query = db.query(Activity).order_by(Activity.date.desc(), Activity.id.desc())

    if activity_type:
        query = query.filter(Activity.activity_type == activity_type)

    if year is not None:
        if month is not None:
            start = date(year, month, 1)
            end = date(year + month // 12, month % 12 + 1, 1)
        else:
            start, end = date(year, 1, 1), date(year + 1, 1, 1)
        query = query.filter(Activity.date >= start, Activity.date < end)

    activities = query.offset(offset).limit(limit).all()
    overrides = {o.activity_id: o for o in db.query(ActivityOverride)
                 .filter(ActivityOverride.activity_id.in_([a.id for a in activities]))}
    out = []
    official = results_by_activity(db)
    for a in activities:
        row = ActivityOut.model_validate(a).model_dump()
        override = overrides.get(a.id)
        status = category_status(db, a, getattr(override, "category", None))
        row["category"] = status["category"] if status else None
        row["workout"] = cached_workout(db, a) if row["category"] == "workout" else None
        t = classify(a.activity_type, a.distance_km, a.elevation_gain, getattr(override, "surface", None),
                     detected_track=bool(row["workout"] and row["workout"]["on_track"]),
                     detected_cross=looks_like_cross(db, a, row["category"]))
        row["surface"], row["terrain"] = (t["surface"], t["terrain"]) if t else (None, None)
        row["place"] = place_from_name(a.name)
        row["official"] = official_for(official.get(a.id))
        out.append(row)
    return out


@router.get("/months")
def list_activity_months(db: Session = Depends(get_db)):
    """Number of activities per (year, month), most recent first — for the year/month pickers."""
    year = extract("year", Activity.date)
    month = extract("month", Activity.date)
    rows = (
        db.query(year.label("year"), month.label("month"), func.count(Activity.id).label("count"))
        .group_by(year, month)
        .order_by(year.desc(), month.desc())
        .all()
    )
    return [{"year": int(r.year), "month": int(r.month), "count": r.count} for r in rows]


@router.get("/{activity_id}", response_model=ActivityOut)
def get_activity(activity_id: int, db: Session = Depends(get_db)):
    """Get a single activity by its internal id."""
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    return activity


def cumulative_distances(points: list[Timeseries], total_km: float | None) -> list[float]:
    """Distance covered (km) at each point, integrating speed (1 / pace) over time.
    Only for the old chart timeseries, which has no distance; FIT records carry the real one.

    Garmin's stored pace is per point, so the sum drifts a little; when the activity's
    official distance is known, scale so the last point lands exactly on it.
    """
    distances = []
    km = 0.0
    prev_s = None
    for p in points:
        if prev_s is not None and p.pace:
            km += (p.seconds_elapsed - prev_s) / 60 / p.pace   # minutes / (min per km)
        prev_s = p.seconds_elapsed
        distances.append(km)

    if total_km and km > 0:
        scale = total_km / km
        distances = [d * scale for d in distances]
    return [round(d, 3) for d in distances]


@router.get("/{activity_id}/detail", response_model=ActivityDetailOut)
def get_activity_detail(
    activity_id: int,
    max_points: int = Query(2000, ge=100, le=200_000),
    db: Session = Depends(get_db),
):
    """Activity with its laps and timeseries, thinned to at most `max_points` points.
    Uses the original FIT records when available, else the older chart timeseries."""
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    laps = (
        db.query(Lap)
        .filter(Lap.activity_id == activity_id)
        .order_by(Lap.lap_number)
        .all()
    )

    timeseries, source = fit_timeseries(db, activity_id), "fit"
    if timeseries:
        laps = laps_with_fit_stats(db, laps, timeseries, activity)
    else:
        timeseries, source = chart_timeseries(db, activity), "chart"
    if not timeseries:
        source = "none"
    total = len(timeseries)
    step = max(1, ceil(total / max_points))
    peaks = compute_peaks(timeseries) if timeseries else None

    return {
        **{c.name: getattr(activity, c.name) for c in Activity.__table__.columns},
        "laps": laps,
        # Thin evenly, but always keep the final point (exact end distance/time).
        "timeseries": timeseries[::step] + ([timeseries[-1]] if total and (total - 1) % step else []),
        "timeseries_source": source,
        "timeseries_points": total,
        "peaks": peaks,
        "terrain": activity_terrain(db, activity),
        "category": (category := activity_category(db, activity)),
        "place": place_from_name(activity.name),
        "official": official_for(results_by_activity(db).get(activity.id)),
        "gap": adjusted_paces(db, activity) if source == "fit" else None,
        # Workout structure (cached), so the page header can say "Track intervals · 12 × 400 m…".
        "workout": cached_workout(db, activity) if category and category["category"] == "workout" else None,
    }


@router.get("/{activity_id}/workout")
def get_workout(activity_id: int, db: Session = Depends(get_db)):
    """What the workout was, read from the laps of the original FIT file — e.g.
    {"type": "Intervals", "summary": "12 × 400 m r 1:00 @ 3:06/km", "warmup": "10.5 km", ...}.
    null when the laps show no structure."""
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    fit = db.get(FitFile, activity_id) if activity else None
    if not fit or fit.status != "ok":
        raise HTTPException(status_code=404, detail="No FIT file for this activity")
    return cached_workout(db, activity)


def activity_category(db: Session, activity: Activity) -> dict | None:
    override = db.get(ActivityOverride, activity.id)
    return category_status(db, activity, override.category if override else None)


class CategoryIn(BaseModel):
    category: str | None   # "race" | "workout" | "long" | "easy", or null = back to the automatic guess


@router.put("/{activity_id}/category")
def set_category(activity_id: int, body: CategoryIn, db: Session = Depends(get_db)):
    """Set race / workout / easy for one activity (null = automatic again)."""
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    if body.category not in (None, *CATEGORIES):
        raise HTTPException(status_code=422, detail="category must be race, workout, easy or null")
    override = db.get(ActivityOverride, activity_id) or ActivityOverride(activity_id=activity_id)
    override.category = body.category
    db.merge(override)
    db.commit()
    return activity_category(db, activity)


def activity_terrain(db: Session, activity: Activity) -> dict | None:
    override = db.get(ActivityOverride, activity.id)
    category = category_status(db, activity, override.category if override else None)
    workout = cached_workout(db, activity) if category and category["category"] == "workout" else None
    return classify(activity.activity_type, activity.distance_km, activity.elevation_gain,
                    override.surface if override else None,
                    detected_track=bool(workout and workout["on_track"]),
                    detected_cross=looks_like_cross(db, activity, category and category["category"]))


class WorkoutTitleIn(BaseModel):
    type: str | None = None      # your title ("Fartlek"); both empty = back to the guess
    summary: str | None = None   # and the description ("6 × 3 min hills")


@router.put("/{activity_id}/workout")
def set_workout_title(activity_id: int, body: WorkoutTitleIn, db: Session = Depends(get_db)):
    """Write a workout's title and description yourself (both empty = the guess again)."""
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    override = db.get(ActivityOverride, activity_id) or ActivityOverride(activity_id=activity_id)
    override.workout_type = (body.type or "").strip()[:60] or None
    override.workout_summary = (body.summary or "").strip()[:200] or None
    db.merge(override)
    db.commit()
    return cached_workout(db, activity)


class SurfaceIn(BaseModel):
    surface: str | None   # "road", "trail", "track", "treadmill", "cross", or null to go back to automatic


@router.put("/{activity_id}/surface")
def set_surface(activity_id: int, body: SurfaceIn, db: Session = Depends(get_db)):
    """Override trail/road for one activity (null = automatic again)."""
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    if body.surface not in (None, *SURFACES):
        raise HTTPException(status_code=422, detail=f"surface must be one of {SURFACES} or null")
    override = db.get(ActivityOverride, activity_id) or ActivityOverride(activity_id=activity_id)
    override.surface = body.surface
    db.merge(override)
    db.query(CategoryCache).filter(CategoryCache.activity_id == activity_id).delete()   # cross = race
    db.commit()
    return activity_terrain(db, activity)


def compute_peaks(timeseries: list[dict]) -> dict:
    """Sustained extremes on the full-resolution track (before thinning for the charts)."""
    track = pd.DataFrame(timeseries).rename(columns={"seconds_elapsed": "timer_s"})
    cols = ["timer_s", "elapsed_s", "distance_km", "speed_ms", "pace", "hr", "cadence", "power", "elevation",
            "temperature"]
    track = track.reindex(columns=cols).astype(float)
    if track["speed_ms"].isna().all() and track["pace"].notna().any():   # old chart data: pace only
        track["speed_ms"] = 1000 / (track["pace"] * 60)
    out = {**sustained_extremes(track), "elevation": elevation_extremes(track)}
    # Total time including stops, time-weighted mean altitude and the watch's temperature range.
    if track["elapsed_s"].notna().any():
        out["elapsed_s"] = float(track["elapsed_s"].max() - track["elapsed_s"].min())
    dt = track["timer_s"].diff().clip(lower=0, upper=30).shift(-1).fillna(0)
    z = track["elevation"]
    if z.notna().any() and (dt[z.notna()] > 0).any():
        out["alt_mean"] = float((z * dt)[z.notna()].sum() / dt[z.notna()].sum())
    temp = track["temperature"]
    if temp.notna().any():
        out["temperature"] = {"min": float(temp.min()), "max": float(temp.max()), "mean": float(temp.mean())}
    return out


@router.get("/{activity_id}/weather")
def get_activity_weather(activity_id: int, db: Session = Depends(get_db)):
    """Temperature, feels-like, humidity and wind at the start (Garmin, nearest station). Cached."""
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    try:
        return activity_weather(db, activity)
    except Exception:
        raise HTTPException(status_code=503, detail="Garmin weather unavailable")


@router.get("/{activity_id}/effort")
def get_activity_effort(activity_id: int, db: Session = Depends(get_db)):
    """Training effect (aerobic / anaerobic, label, load), your RPE and feel, stamina. Cached."""
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    try:
        return activity_effort(db, activity)
    except Exception:
        raise HTTPException(status_code=503, detail="Garmin unavailable")


@router.get("/{activity_id}/dynamics")
def get_activity_dynamics(activity_id: int, db: Session = Depends(get_db)):
    """Running dynamics, stamina, performance condition, Garmin GAP and body battery, per point and
    per lap, read from the original FIT file (first time only)."""
    from zachy.analytics.dynamics import dynamics
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    out = dynamics(activity.garmin_id)
    if out is None:
        raise HTTPException(status_code=404, detail="No FIT file for this activity")
    return out


@router.get("/{activity_id}/gait")
def get_activity_gait(activity_id: int, db: Session = Depends(get_db)):
    """Running / walking / standing: seconds of each, and where along the distance."""
    from zachy.analytics.gap import gait
    out = gait(db, activity_id)
    if out is None:
        raise HTTPException(status_code=404, detail="No cadence data for this activity")
    return out


class LinkIn(BaseModel):
    url: str


@router.get("/{activity_id}/links")
def get_links(activity_id: int, db: Session = Depends(get_db)):
    """Results pages linked to this activity."""
    from zachy.analytics.livetrail import links
    return links(db, activity_id)


@router.post("/{activity_id}/links")
def add_link(activity_id: int, body: LinkIn, db: Session = Depends(get_db)):
    """Link a results page; a LiveTrail runner link also gets its race data read and saved."""
    from zachy.analytics.livetrail import save_link
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    try:
        return save_link(db, activity, body.url.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not read the page ({e.__class__.__name__})")


@router.delete("/{activity_id}/links/{link_id}")
def delete_link(activity_id: int, link_id: int, db: Session = Depends(get_db)):
    from zachy.analytics.livetrail import _group, links
    from zachy.models import ActivityLink, LiveTrailData
    link = db.get(ActivityLink, link_id)
    if link and link.activity_id in _group(db, activity_id):   # a stage race's link is on one of its stages
        if link.kind == "livetrail":
            db.query(LiveTrailData).filter_by(activity_id=link.activity_id).delete()
        db.delete(link)
        db.commit()
    return links(db, activity_id)


@router.get("/{activity_id}/livetrail")
def get_livetrail(activity_id: int, overall: bool = False, db: Session = Depends(get_db)):
    """Checkpoint sections from the linked LiveTrail page (null when none). A stage race: this
    activity's stage, or overall=true for every stage."""
    from zachy.analytics.livetrail import livetrail
    return livetrail(db, activity_id, overall)


@router.get("/{activity_id}/splits")
def get_activity_splits(
    activity_id: int,
    km: float = Query(1.0, gt=0.09, le=50),
    db: Session = Depends(get_db),
):
    """Splits every `km` kilometres from the FIT records: time, pace, HR, cadence, power,
    elevation gain and loss (calibrated to the activity's official totals)."""
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    track = load_track(db, activity_id)
    if track.empty:
        raise HTTPException(status_code=404, detail="No FIT records for this activity")
    splits = km_splits(track, activity, km)
    for s, gap, gap_race in zip(splits, split_paces(db, activity, splits), split_paces(db, activity, splits, "race")):
        s["gap_pace"], s["gap_race_pace"] = gap, gap_race   # flat-equivalent pace of that km, both models
    return {"split_km": km, "splits": splits}


def laps_with_fit_stats(db: Session, laps: list[Lap], timeseries: list[dict], activity: Activity) -> list[dict]:
    """Laps with elevation gain/loss, max HR, power and cadence recomputed from the FIT track (same
    method as the km splits), and their personal grade-adjusted pace. Garmin's own lap cadence
    averages in stopped time, so it's replaced."""
    track = pd.DataFrame(timeseries).reindex(
        columns=["seconds_elapsed", "elapsed_s", "elevation", "power", "cadence", "hr"]).rename(
        columns={"seconds_elapsed": "timer_s"}).astype(float)
    stats = lap_stats(track, activity, [lap.duration_s for lap in laps])
    out = []
    for i, lap in enumerate(laps):
        row = {c: getattr(lap, c) for c in ("lap_number", "distance_km", "duration_s", "avg_pace",
                                            "avg_hr", "avg_cadence", "elevation_gain")}
        if i < len(stats):
            row.update({k: v for k, v in stats[i].items() if v is not None})
        out.append(row)
    # Grade-adjusted pace per lap: each lap as a stretch of distance, like the km splits.
    ends = np.cumsum([r["distance_km"] or 0 for r in out])
    pseudo = [{"end_km": float(e), "distance_km": r["distance_km"] or 0, "duration_s": r["duration_s"]}
              for e, r in zip(ends, out)]
    for r, gap, gap_race in zip(out, split_paces(db, activity, pseudo), split_paces(db, activity, pseudo, "race")):
        r["gap_pace"], r["gap_race_pace"] = gap, gap_race
    return out


def fit_timeseries(db: Session, activity_id: int) -> list[dict]:
    rows = (
        db.query(Record.timer_s, Record.elapsed_s, Record.distance_km, Record.speed_ms, Record.hr,
                 Record.cadence, Record.elevation, Record.latitude, Record.longitude,
                 Record.power, Record.temperature)
        .filter(Record.activity_id == activity_id)
        .order_by(Record.id)
        .all()
    )
    # Altimeter recalibration jumps and the barometer's catch-up during pauses removed on the
    # full-resolution track (real moves during a pause kept).
    elevation = remove_jumps([r.elevation for r in rows],
                             display_clock([r.elapsed_s for r in rows], [r.timer_s for r in rows]),
                             [r.timer_s for r in rows])
    return [
        {
            "seconds_elapsed": r.timer_s, "elapsed_s": r.elapsed_s, "distance_km": r.distance_km,
            "speed_ms": r.speed_ms,
            "pace": round(1000 / r.speed_ms / 60, 3) if r.speed_ms else None,
            "hr": r.hr, "cadence": r.cadence,
            "elevation": None if np.isnan(z) else float(z),
            "latitude": r.latitude, "longitude": r.longitude,
            "power": r.power, "temperature": r.temperature,
        }
        for r, z in zip(rows, elevation)
    ]


def chart_timeseries(db: Session, activity: Activity) -> list[dict]:
    points = (
        db.query(Timeseries)
        .filter(Timeseries.activity_id == activity.id)
        .order_by(Timeseries.seconds_elapsed)
        .all()
    )
    # Distance is computed on every point before thinning, for accuracy.
    distances = cumulative_distances(points, activity.distance_km)
    return [
        {**TimeseriesPointOut.model_validate(p).model_dump(), "distance_km": d}
        for p, d in zip(points, distances)
    ]