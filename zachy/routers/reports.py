from fastapi import APIRouter, Depends
from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from zachy.models import Wellness

from zachy.database import get_db
from zachy.analytics.overview import yearly_summary, monthly_summary, get_running_activities_df
from zachy.schemas.activity import YearlySummaryOut, MonthlySummaryOut

router = APIRouter()

@router.get("/monthly/{year}", response_model=list[MonthlySummaryOut])
def get_monthly_summary(year: int, db: Session = Depends(get_db)):
    """Month-by-month running distance, time, and elevation for one year."""
    df = monthly_summary(db, year)
    rows = df.to_dict(orient="records") if not df.empty else []
    # Steps per month from the daily wellness data (a year can have steps but no runs).
    steps = dict(db.query(extract("month", Wellness.date), func.sum(Wellness.steps))
                 .filter(extract("year", Wellness.date) == year).group_by(extract("month", Wellness.date)).all())
    if not rows and not steps:
        return []
    if not rows:
        rows = [{"month": m, "total_distance_km": 0, "total_duration_s": 0, "total_elevation_gain": 0, "num_runs": 0}
                for m in range(1, 13)]
    for r in rows:
        r["steps"] = int(steps.get(r["month"]) or 0)
    return rows


@router.get("/yearly", response_model=list[YearlySummaryOut])
def get_yearly_summary(db: Session = Depends(get_db)):
    """Year-by-year running distance, run count, avg pace, avg HR."""
    df = yearly_summary(db)
    return df.to_dict(orient="records")


@router.get("/overview")
def get_overview(db: Session = Depends(get_db)):
    """High-level lifetime stats — total distance, total runs, date range."""
    df = get_running_activities_df(db)

    if df.empty:
        return {"total_runs": 0, "total_distance_km": 0, "date_range": None}

    return {
        "total_runs": len(df),
        "total_distance_km": round(df["distance_km"].sum(), 1),
        "avg_pace": round(df["avg_pace"].mean(), 2) if df["avg_pace"].notna().any() else None,
        "avg_hr": round(df["avg_hr"].mean(), 1) if df["avg_hr"].notna().any() else None,
        "date_range": {
            "earliest": df["date"].min().date().isoformat(),
            "latest": df["date"].max().date().isoformat(),
        },
    }