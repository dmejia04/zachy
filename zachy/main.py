from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from zachy.config import settings
from zachy.database import Base, engine
from zachy.models import Activity, Lap, Timeseries, BodyMetric, SyncRun
from zachy.routers import activities, reports, body_metrics, sync

Base.metadata.create_all(bind=engine)

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


@app.get("/")
def health_check():
    return {"status": "ok", "app": settings.app_name}