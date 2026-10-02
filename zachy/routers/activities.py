from datetime import date
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from zachy.database import get_db
from zachy.models import Activity, Lap, Record, Timeseries
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

    return query.offset(offset).limit(limit).all()


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
    if not timeseries:
        timeseries, source = chart_timeseries(db, activity), "chart"
    if not timeseries:
        source = "none"
    total = len(timeseries)
    step = max(1, ceil(total / max_points))

    return {
        **{c.name: getattr(activity, c.name) for c in Activity.__table__.columns},
        "laps": laps,
        # Thin evenly, but always keep the final point (exact end distance/time).
        "timeseries": timeseries[::step] + ([timeseries[-1]] if total and (total - 1) % step else []),
        "timeseries_source": source,
        "timeseries_points": total,
    }


def fit_timeseries(db: Session, activity_id: int) -> list[dict]:
    rows = (
        db.query(Record.timer_s, Record.elapsed_s, Record.distance_km, Record.speed_ms, Record.hr,
                 Record.cadence, Record.elevation, Record.latitude, Record.longitude,
                 Record.power, Record.temperature)
        .filter(Record.activity_id == activity_id)
        .order_by(Record.id)
        .all()
    )
    return [
        {
            "seconds_elapsed": r.timer_s, "elapsed_s": r.elapsed_s, "distance_km": r.distance_km,
            "speed_ms": r.speed_ms,
            "pace": round(1000 / r.speed_ms / 60, 3) if r.speed_ms else None,
            "hr": r.hr, "cadence": r.cadence, "elevation": r.elevation,
            "latitude": r.latitude, "longitude": r.longitude,
            "power": r.power, "temperature": r.temperature,
        }
        for r in rows
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