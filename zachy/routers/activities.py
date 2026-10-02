from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from zachy.database import get_db
from zachy.models import Activity, Lap, Timeseries
from zachy.schemas.activity import ActivityOut, ActivityDetailOut

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


@router.get("/{activity_id}/detail", response_model=ActivityDetailOut)
def get_activity_detail(
    activity_id: int,
    downsample: int = Query(5, ge=1, le=60),
    db: Session = Depends(get_db),
):
    """Activity with its laps and (downsampled) second-by-second timeseries."""
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    laps = (
        db.query(Lap)
        .filter(Lap.activity_id == activity_id)
        .order_by(Lap.lap_number)
        .all()
    )

    points = (
        db.query(Timeseries)
        .filter(Timeseries.activity_id == activity_id)
        .order_by(Timeseries.seconds_elapsed)
        .all()
    )
    points = points[::downsample]

    return {
        **{c.name: getattr(activity, c.name) for c in Activity.__table__.columns},
        "laps": laps,
        "timeseries": points,
    }