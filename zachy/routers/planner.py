import json
import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from zachy.analytics.planner import create, level_model, plan_list, predict
from zachy.database import get_db
from zachy.models import Profile, RacePlan

router = APIRouter()


class NewPlan(BaseModel):
    name: str = ""
    gpx_name: str | None = None
    gpx: str | None = None          # the GPX file's text (needed for trail)
    kind: str = "trail"             # trail | road
    distance_km: float | None = None  # road without GPX


class AidStation(BaseModel):
    km: float
    name: str = ""
    stop_min: float = 2


class PlanIn(BaseModel):
    name: str | None = None
    race_date: str | None = None
    aid: list[AidStation] | None = None
    flat_pace: float | None = None     # min/km; 0 = back to your races
    clear_date: bool = False
    start_time: str | None = None      # "06:00"; "" = none
    outlook: float | None = None       # % on the time: −20 optimistic … +20 pessimistic
    strategy: float | None = None      # −1 aggressive … +1 conservative
    nutrition: dict | None = None      # {carbs_gh, fluid_mlh, sodium_mgh, gel_g, flask_ml}; {} = suggested
    fuel: dict | None = None           # portions per leg: {"0": {"3": 2, …}, …}
    split_km: float | None = None      # road: 1, 2, 5 or 10
    fuel_items: list[int] | None = None  # road: the fuel library products you'll take


def _get(db: Session, plan_id: int) -> RacePlan:
    p = db.get(RacePlan, plan_id)
    if not p:
        raise HTTPException(status_code=404, detail="Plan not found")
    return p


def _predict(db: Session, p: RacePlan) -> dict:
    try:
        return predict(db, p)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/")
def list_plans(db: Session = Depends(get_db)):
    return plan_list(db)


@router.post("/")
def new_plan(body: NewPlan, db: Session = Depends(get_db)):
    """A new plan from a GPX file (its text): the course, cut into climbs, descents and flats."""
    try:
        p = create(db, body.name, body.gpx_name, body.gpx, "road" if body.kind == "road" else "trail", body.distance_km)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _predict(db, p)


class FuelItem(BaseModel):
    id: int | None = None
    name: str
    type: str = "gel"          # gel | drink | bar | food
    carbs_g: float = 0
    fluid_ml: float = 0
    sodium_mg: float = 0


@router.get("/fuel")
def get_fuel(db: Session = Depends(get_db)):
    """Your fuel library: gels, drinks, bars… with carbs, fluid and sodium per portion."""
    p = db.get(Profile, 1)
    return json.loads(p.fuel_json) if p and p.fuel_json else []


@router.put("/fuel")
def put_fuel(items: list[FuelItem], db: Session = Depends(get_db)):
    p = db.get(Profile, 1) or Profile(id=1)
    used = [i.id for i in items if i.id]
    nxt = max(used, default=0) + 1
    out = []
    for i in items:
        if not i.name.strip():
            continue
        if not i.id:
            i.id, nxt = nxt, nxt + 1
        out.append({**i.model_dump(), "name": i.name.strip()[:60]})
    p.fuel_json = json.dumps(out)
    db.merge(p)
    db.commit()
    return out


@router.get("/level")
def get_level(db: Session = Depends(get_db)):
    """Your race level against race duration, from your trail races (what the prediction uses)."""
    return level_model(db)


@router.get("/{plan_id}")
def get_plan(plan_id: int, db: Session = Depends(get_db)):
    return _predict(db, _get(db, plan_id))


@router.put("/{plan_id}")
def edit_plan(plan_id: int, body: PlanIn, db: Session = Depends(get_db)):
    p = _get(db, plan_id)
    if body.name is not None and body.name.strip():
        p.name = body.name.strip()[:100]
    if body.race_date:
        p.race_date = date.fromisoformat(body.race_date)
    if body.clear_date:
        p.race_date = None
    if body.aid is not None:
        p.aid = json.dumps([a.model_dump() for a in sorted(body.aid, key=lambda a: a.km)])
    if body.flat_pace is not None:
        p.flat_pace = body.flat_pace or None
    if body.outlook is not None:
        lim = 10.0 if p.kind == "road" else 20.0
        p.outlook = max(-lim, min(lim, body.outlook)) or None
    if body.split_km is not None and body.split_km in (1, 2, 5, 10):
        p.split_km = body.split_km
    if body.strategy is not None:
        p.strategy = max(-1.0, min(1.0, body.strategy)) or None
    if body.start_time is not None:
        if body.start_time and not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", body.start_time):
            raise HTTPException(status_code=422, detail="Start time like 06:00")
        p.start_time = body.start_time or None
    if body.nutrition is not None or body.fuel is not None or body.fuel_items is not None:
        saved = json.loads(p.nutrition) if p.nutrition else {}
        if body.fuel_items is not None:
            saved["items"] = sorted(set(body.fuel_items))
            if not saved["items"]:
                saved.pop("items")
        if body.nutrition is not None:   # targets (the fuel picked per leg / the products are kept)
            keys = ("carbs_gh", "fluid_mlh", "sodium_mgh", "gel_g", "flask_ml")
            saved = {"fuel": saved.get("fuel", {}), **({"items": saved["items"]} if saved.get("items") else {}),
                     **{k: float(body.nutrition[k]) for k in keys if body.nutrition.get(k) not in (None, "")}}
        if body.fuel is not None:
            saved["fuel"] = {str(leg): {str(i): float(q) for i, q in items.items() if q and float(q) > 0}
                             for leg, items in body.fuel.items()}
            saved["fuel"] = {k: v for k, v in saved["fuel"].items() if v}
        if not saved.get("fuel"):
            saved.pop("fuel", None)
        p.nutrition = json.dumps(saved) if saved else None
    db.commit()
    return _predict(db, p)


@router.delete("/{plan_id}")
def delete_plan(plan_id: int, db: Session = Depends(get_db)):
    p = db.get(RacePlan, plan_id)
    if p:
        db.delete(p)
        db.commit()
    return plan_list(db)
