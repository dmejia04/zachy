from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from zachy.analytics.routes import detect, route_detail, route_list
from zachy.database import get_db
from zachy.models import Route

router = APIRouter()


@router.get("/")
def get_routes(db: Session = Depends(get_db)):
    """The routes you run regularly (10 runs or more on the same course), most run first."""
    return route_list(db)


@router.post("/detect")
def detect_routes(db: Session = Depends(get_db)):
    """Find the routes again from all your runs' GPS tracks (the first time reads every track: ~1 min)."""
    return detect(db)


@router.get("/{route_id}")
def get_route(route_id: int, db: Session = Depends(get_db)):
    out = route_detail(db, route_id)
    if out is None:
        raise HTTPException(status_code=404, detail="Route not found")
    return out


class NameIn(BaseModel):
    name: str | None   # your name for the route; empty = the automatic one


@router.put("/{route_id}/name")
def rename_route(route_id: int, body: NameIn, db: Session = Depends(get_db)):
    r = db.get(Route, route_id)
    if not r:
        raise HTTPException(status_code=404, detail="Route not found")
    r.name = (body.name or "").strip()[:80] or None
    db.commit()
    return {"id": r.id, "name": r.name or r.auto_name, "custom": bool(r.name)}
