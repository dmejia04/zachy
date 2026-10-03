from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from zachy.config import settings
from zachy.database import Base, add_missing_columns, engine
from zachy.models import Activity, Lap, Timeseries, BodyMetric, SyncRun
from zachy.routers import activities, app_config, body, gap, records, reports, body_metrics, profile, sync, wellness, zones

Base.metadata.create_all(bind=engine)
add_missing_columns()

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(activities.router, prefix="/activities", tags=["activities"])
app.include_router(reports.router, prefix="/reports", tags=["reports"])
app.include_router(body_metrics.router, prefix="/body-metrics", tags=["body-metrics"])
app.include_router(sync.router, prefix="/sync", tags=["sync"])
app.include_router(zones.router, prefix="/zones", tags=["zones"])
app.include_router(app_config.router, prefix="/config", tags=["config"])
app.include_router(wellness.router, prefix="/wellness", tags=["wellness"])
app.include_router(records.router, prefix="/records", tags=["records"])
app.include_router(gap.router, prefix="/gap", tags=["gap"])
app.include_router(body.router, prefix="/body", tags=["body"])
app.include_router(profile.router, prefix="/profile", tags=["profile"])


@app.get("/")
def health_check():
    return {"status": "ok", "app": settings.app_name}