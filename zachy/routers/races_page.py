import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from zachy.analytics.ffa import table as ffa_table
from zachy.analytics.race_page import race_detail, race_list
from zachy.analytics.race_results import as_dict, fetch_betrail_levels, set_manual_rank, fetch_itra_index, performance_table, import_rows, import_utmb, match_all, save_indexes, set_scores
from zachy.database import get_db
from zachy.models import Activity, CategoryCache, Profile, RaceResult

router = APIRouter()


@router.get("/")
def get_races(db: Session = Depends(get_db)):
    """Every race, newest first: start point, elevation silhouette, event and its edition count."""
    return race_list(db)


@router.post("/results/import")
def import_results(db: Session = Depends(get_db)):
    """Read your results pages (UTMB for now; links in the profile) and match them to activities."""
    p = db.get(Profile, 1)
    if not p or not p.utmb_url:
        raise HTTPException(status_code=400, detail="Add your UTMB runner page in the Profile first")
    try:
        out = import_utmb(db, p.utmb_url)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not read the UTMB page ({e.__class__.__name__})")
    if p.itra_url:
        try:
            save_indexes(db, itra=fetch_itra_index(p.itra_url))
        except Exception:
            pass   # the UTMB results are what matters; the ITRA index can wait
    runner = (json.loads(p.indexes_json or "{}").get("betrail") or {}).get("runner_id")
    if runner:
        try:
            save_indexes(db, betrail=fetch_betrail_levels(runner))
        except Exception:
            pass
    return out


@router.get("/performance")
def get_performance(db: Session = Depends(get_db)):
    """All official results of all sites, one row per race, with each site's score."""
    return performance_table(db)


@router.get("/ffa")
def get_ffa():
    """The FFA road performance grid (men): levels, points and time limits per distance."""
    return ffa_table()


@router.get("/indexes")
def get_indexes(db: Session = Depends(get_db)):
    """Your overall UTMB indexes (general, 20k, 50k, 100k, 100m) and ITRA Performance Index."""
    p = db.get(Profile, 1)
    return json.loads(p.indexes_json) if p and p.indexes_json else {}


@router.get("/results")
def get_results(db: Session = Depends(get_db)):
    """Every official result, newest first, with the activities it's matched to."""
    return [as_dict(r) for r in db.query(RaceResult).order_by(RaceResult.date.desc())]


class RowsIn(BaseModel):
    source: str
    url: str | None = None
    rows: list[dict]


@router.post("/results/rows")
def post_rows(body: RowsIn, db: Session = Depends(get_db)):
    """Results read from a signed-in results page (Betrail), stored and matched."""
    return import_rows(db, body.source, body.rows, body.url)


class ScoresIn(BaseModel):
    source: str
    scores: dict[str, float | None]     # site key -> score


@router.post("/results/scores")
def post_scores(body: ScoresIn, db: Session = Depends(get_db)):
    """Per-race scores read from your signed-in results page."""
    return {"updated": set_scores(db, body.source, body.scores)}


class MatchIn(BaseModel):
    action: str                         # confirm | reject | reset
    activity_ids: list[int] | None = None


@router.post("/results/{result_id}/match")
def set_match(result_id: int, body: MatchIn, db: Session = Depends(get_db)):
    """Confirm or reject a match (or give the right activities), or go back to automatic."""
    r = db.get(RaceResult, result_id)
    if not r:
        raise HTTPException(status_code=404, detail="Result not found")
    before = set(json.loads(r.activity_ids or "[]"))
    if body.action == "confirm":
        if body.activity_ids is not None:
            r.activity_ids = json.dumps(body.activity_ids) if body.activity_ids else None
        r.match = "confirmed"
    elif body.action == "reject":
        r.match = "rejected"
    else:
        r.match = None
    db.query(CategoryCache).filter(CategoryCache.activity_id.in_(before | set(body.activity_ids or []))).delete(synchronize_session=False)
    db.commit()
    match_all(db)
    return as_dict(r)


class RankIn(BaseModel):
    rank: int | None = None       # None removes it
    total: int | None = None      # number of finishers, if known


@router.put("/{activity_id}/rank")
def put_rank(activity_id: int, body: RankIn, db: Session = Depends(get_db)):
    """Your place in a race that has no imported result (e.g. a cross country)."""
    a = db.get(Activity, activity_id)
    if not a:
        raise HTTPException(status_code=404, detail="Activity not found")
    return set_manual_rank(db, a, body.rank, body.total)


@router.get("/{activity_id}")
def get_race(activity_id: int, db: Session = Depends(get_db)):
    """One race with all editions of the same event, for comparison."""
    out = race_detail(db, activity_id)
    if out is None:
        raise HTTPException(status_code=404, detail="Not a race")
    return out
