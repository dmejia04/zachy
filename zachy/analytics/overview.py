"""Overview analytics — running-focused summaries and trends."""

import pandas as pd
from sqlalchemy.orm import Session
from zachy.models import Activity

RUNNING_TYPES = [
    "running",
    "trail_running",
    "track_running",
    "treadmill_running",
    "ultra_run",
]

EXCLUDED_TYPES = [
    "incident_detected",
]


def get_running_activities_df(db: Session) -> pd.DataFrame:
    """Load all running-type activities into a DataFrame, excluding junk entries."""
    activities = (
        db.query(Activity)
        .filter(Activity.activity_type.in_(RUNNING_TYPES))
        .filter(~Activity.activity_type.in_(EXCLUDED_TYPES))
        .all()
    )

    records = [
        {
            "date": a.date,
            "name": a.name,
            "activity_type": a.activity_type,
            "distance_km": a.distance_km,
            "duration_s": a.duration_s,
            "avg_pace": a.avg_pace,
            "avg_hr": a.avg_hr,
            "max_hr": a.max_hr,
            "elevation_gain": a.elevation_gain,
            "calories": a.calories,
        }
        for a in activities
    ]

    df = pd.DataFrame(records)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def get_all_activities_df(db: Session) -> pd.DataFrame:
    """Load every activity (all sports), excluding junk entries — for cross-training context."""
    activities = (
        db.query(Activity)
        .filter(~Activity.activity_type.in_(EXCLUDED_TYPES))
        .all()
    )

    records = [
        {
            "date": a.date,
            "activity_type": a.activity_type,
            "distance_km": a.distance_km,
            "duration_s": a.duration_s,
        }
        for a in activities
    ]

    df = pd.DataFrame(records)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def yearly_summary(db: Session) -> pd.DataFrame:
    """Running distance, time, and elevation per year."""
    df = get_running_activities_df(db)
    if df.empty:
        return df

    df["year"] = df["date"].dt.year
    summary = df.groupby("year").agg(
        total_distance_km=("distance_km", "sum"),
        total_duration_s=("duration_s", "sum"),
        total_elevation_gain=("elevation_gain", "sum"),
        num_runs=("distance_km", "count"),
        avg_pace=("avg_pace", "mean"),
        avg_hr=("avg_hr", "mean"),
    ).reset_index()

    return summary


def monthly_summary(db: Session, year: int) -> pd.DataFrame:
    """Running distance, time, and elevation per month for one year."""
    df = get_running_activities_df(db)
    if df.empty:
        return df

    df = df[df["date"].dt.year == year]
    if df.empty:
        return df

    df["month"] = df["date"].dt.month
    summary = df.groupby("month").agg(
        total_distance_km=("distance_km", "sum"),
        total_duration_s=("duration_s", "sum"),
        total_elevation_gain=("elevation_gain", "sum"),
        num_runs=("distance_km", "count"),
    ).reset_index()

    # Fill in months with no runs as zero, so all 12 months always show
    all_months = pd.DataFrame({"month": range(1, 13)})
    summary = all_months.merge(summary, on="month", how="left").fillna(0)
    summary["num_runs"] = summary["num_runs"].astype(int)

    return summary