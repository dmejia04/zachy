"""Official race results from results sites, matched to your activities.

UTMB (utmb.world): your public runner page embeds every result as JSON (__NEXT_DATA__): date,
race and event names, distance, climb, time, overall / gender rank, DNF, and the stages of stage
races. Read once on demand, stored in race_results. The UTMB score of each race is only in the
page when you're signed in: those are saved separately (set_scores) and kept by later imports.

Matching a result to the activities recorded that day:
- one race: a run within a day of the official date whose distance is within 20% (GPS reads short
  in the mountains, courses change);
- a stage race: one run per stage on consecutive days, each matched by its own distance
  (within 3 days of the listed date, else within a week: the listed date is sometimes wrong);
- a DNF: the longest run that day that's shorter than the course.
Before that, a result whose official time equals another site's matched result (to the second)
takes the same activities: sites sometimes list another date or distance for the same race.
A matched activity counts as a race (analytics/races.py) unless you set its category yourself.
"""

import json
import re
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from zachy.analytics.terrain import FOOT_TYPES
from zachy.models import Activity, CategoryCache, Profile, RaceResult

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh) Zachy personal running tracker"}
SAME_DISTANCE = 0.20
STAGE_WINDOW_DAYS = 3


def _seconds(t: str | None) -> int | None:
    if not t or ":" not in t:
        return None
    parts = [int(p) for p in t.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def fetch_utmb(url: str) -> tuple[list[dict], dict]:
    """(results, {category: UTMB index}) from the runner page."""
    r = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=30)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        raise ValueError("No results data on this page")
    page = json.loads(m.group(1))["props"]["pageProps"]
    indexes = {i["piCategory"]: i["index"] for i in page.get("performanceIndexes") or []}
    return page["results"]["results"], indexes


def fetch_itra_index(url: str) -> int | None:
    """Your ITRA Performance Index, from the page description (the results themselves are
    encrypted by ITRA and the per-race scores are for subscribers: not read)."""
    r = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=30)
    r.raise_for_status()
    m = re.search(r"ITRA Performance Index is (\d+)", r.text)
    return int(m.group(1)) if m else None


def fetch_betrail_levels(runner_id: int) -> dict | None:
    """Your current Betrail levels (public runner data): ultra and short score (shown /100) and
    your ultra rank among men in France."""
    r = httpx.get(f"https://www.betrail.run/api/runner/{runner_id}", headers={**HEADERS, "Accept": "application/json"},
                  timeout=30)
    r.raise_for_status()
    sc = next((x for x in r.json()["body"].get("scores") or [] if x.get("year") == "CURRENT"), None)
    if not sc:
        return None
    rank = sc.get("rank") or {}
    pct = lambda v: round(v / 100, 2) if v else None
    return {"runner_id": runner_id, "ultra": pct(sc.get("btu_score")), "short": pct(sc.get("bts_score")),
            "ultra_rank": rank.get("rank_btu_gender"), "ultra_total": rank.get("total_btu_gender"),
            "races": sc.get("nb_races"), "races_ultra": sc.get("nb_races_ultra")}


def save_indexes(db: Session, **found) -> dict:
    p = db.get(Profile, 1)
    data = json.loads(p.indexes_json or "{}")
    data.update({k: v for k, v in found.items() if v is not None}, fetched_at=datetime.now().isoformat(timespec="minutes"))
    p.indexes_json = json.dumps(data)
    db.commit()
    return data


def import_utmb(db: Session, url: str) -> dict:
    """Read the UTMB runner page and store / update its results, then match them."""
    rows, indexes = fetch_utmb(url)
    save_indexes(db, utmb=indexes)
    for x in rows:
        r = db.query(RaceResult).filter_by(source="utmb", key=x["uri"]).first() or RaceResult(source="utmb", key=x["uri"])
        r.date = date.fromisoformat(x["dateIso"])
        r.event, r.race = x.get("eventName"), x.get("raceName")
        r.distance_km = float(x["distance"]) if x.get("distance") else None
        r.elevation_gain = x.get("elevationGain")
        r.time_s, r.dnf = _seconds(x.get("time")), bool(x.get("isDnf"))
        r.rank, r.total = x.get("rank"), x.get("totalRanked")
        r.rank_gender, r.total_gender = x.get("rankGender"), x.get("totalRankedGender")
        r.score = x.get("index") if x.get("index") is not None else r.score   # scores need a login: keep saved ones
        r.url = url
        r.raw = json.dumps(x)
        r.fetched_at = datetime.now()
        db.add(r)
    db.commit()
    return {"results": len(rows), **match_all(db)}


def _runs(db: Session, start: date, end: date) -> list[Activity]:
    return (db.query(Activity).filter(Activity.activity_type.in_(FOOT_TYPES), Activity.distance_km >= 1,
                                      Activity.date.between(start, end)).all())


def _same_duration(a: Activity, r: RaceResult) -> bool:
    """The official time (start to finish) is the watch's moving time or a bit more (stops at aid
    stations), never much less."""
    if not r.time_s or not a.duration_s:
        return True
    return 0.93 <= r.time_s / a.duration_s <= 1.4


def _match(db: Session, r: RaceResult, taken: set[int]) -> list[int]:
    stages = (json.loads(r.raw or "{}").get("stages") or []) if r.raw else []
    if len(stages) > 1:
        # Each stage on its own day, every stage found (one may be missing from 3 stages up), and
        # the days consecutive. The listed date is sometimes off by days: look wider if needed.
        for window in (STAGE_WINDOW_DAYS, 7):
            runs = [a for a in _runs(db, r.date - timedelta(days=window), r.date + timedelta(days=window))
                    if a.id not in taken]
            picked = []
            for st in stages:
                km = float(st.get("distance") or 0)
                free = [a for a in runs if a not in picked and a.date not in {p.date for p in picked}
                        and km and abs(a.distance_km / km - 1) <= SAME_DISTANCE]
                if free:
                    picked.append(min(free, key=lambda a: abs(a.distance_km / km - 1)))
            days = sorted(a.date for a in picked)
            enough = len(picked) == len(stages) or (len(stages) >= 3 and len(picked) >= len(stages) - 1)
            if picked and enough and (days[-1] - days[0]).days <= len(stages):
                return [a.id for a in sorted(picked, key=lambda a: a.date)]
        return []
    runs = [a for a in _runs(db, r.date - timedelta(days=1), r.date + timedelta(days=1)) if a.id not in taken]
    if not r.distance_km:
        return []
    if r.dnf:   # the run stopped early: the longest one that day, shorter than the course
        part = [a for a in runs if 0.1 <= a.distance_km / r.distance_km < 1 - SAME_DISTANCE / 2]
        return [max(part, key=lambda a: a.distance_km).id] if part else []
    near = [a for a in runs if abs(a.distance_km / r.distance_km - 1) <= SAME_DISTANCE and _same_duration(a, r)]
    if near:
        return [min(near, key=lambda a: abs(a.distance_km / r.distance_km - 1)).id]
    # A stage race listed as one result (no stage details): the longest run of each of 2-4
    # consecutive days from the listed date, adding up to the race distance.
    for days in (2, 3, 4):
        picked = []
        for k in range(days):
            day = [a for a in _runs(db, r.date + timedelta(days=k), r.date + timedelta(days=k)) if a.id not in taken]
            if not day:
                break
            picked.append(max(day, key=lambda a: a.distance_km))
        if len(picked) == days and abs(sum(a.distance_km for a in picked) / r.distance_km - 1) <= SAME_DISTANCE * 0.75:
            return [a.id for a in picked]
    return []


def _same_time(r: RaceResult, results: list[RaceResult]) -> list[int]:
    """The activities of another site's result with the same official time (to the second, within
    two months): the same race, even when this site lists another date or distance."""
    if not r.time_s:
        return []
    for o in results:
        if o.source != r.source and o.time_s and abs(o.time_s - r.time_s) <= 2 and o.activity_ids \
                and o.match != "rejected" and abs((o.date - r.date).days) <= 62:
            return json.loads(o.activity_ids)
    return []


def match_all(db: Session) -> dict:
    """(Re)match every result not confirmed or rejected by you. Clears the category cache of the
    activities involved, so they're shown as races."""
    results = db.query(RaceResult).order_by(RaceResult.date).all()
    # One result per activity and site (the same race can have a UTMB and a Betrail result).
    taken = {(r.source, i) for r in results if r.match == "confirmed" for i in json.loads(r.activity_ids or "[]")}
    touched, matched = set(), 0
    for r in results:
        if r.match in ("confirmed", "rejected"):
            continue
        before = set(json.loads(r.activity_ids or "[]"))
        ids = _same_time(r, results) or _match(db, r, {i for src, i in taken if src == r.source})
        taken |= {(r.source, i) for i in ids}
        r.activity_ids = json.dumps(ids) if ids else None
        r.match = "auto" if ids else None
        matched += bool(ids)
        touched |= before | set(ids)
    if touched:
        db.query(CategoryCache).filter(CategoryCache.activity_id.in_(touched)).delete(synchronize_session=False)
    db.commit()
    return {"matched": matched, "unmatched": len(results) - matched}


SOURCE_ORDER = ("utmb", "betrail", "itra", "manual")   # manual: the rank you typed   # which site's result leads when several match


def results_by_activity(db: Session) -> dict[int, dict[str, dict]]:
    """activity id -> {site: its official result}, for every matched result."""
    out: dict[int, dict[str, dict]] = {}
    for r in db.query(RaceResult).filter(RaceResult.activity_ids.isnot(None), RaceResult.match != "rejected"):
        ids = json.loads(r.activity_ids)
        for i in ids:
            out.setdefault(i, {})[r.source] = {**as_dict(r), "stage": ids.index(i) + 1 if len(ids) > 1 else None,
                                               "stages": len(ids)}
    return out


def import_rows(db: Session, source: str, rows: list[dict], url: str | None = None) -> dict:
    """Store results read from a signed-in results page (keys as in RaceResult), then match."""
    for x in rows:
        r = db.query(RaceResult).filter_by(source=source, key=str(x["key"])).first() or RaceResult(source=source, key=str(x["key"]))
        r.date = date.fromisoformat(x["date"])
        for f in ("event", "race", "distance_km", "elevation_gain", "time_s", "rank", "total", "rank_gender",
                  "total_gender", "score"):
            setattr(r, f, x.get(f))
        r.dnf, r.url, r.raw, r.fetched_at = bool(x.get("dnf")), url, json.dumps(x), datetime.now()
        db.add(r)
    db.commit()
    return {"results": len(rows), **match_all(db)}


def as_dict(r: RaceResult) -> dict:
    return {"id": r.id, "source": r.source, "date": r.date.isoformat(), "event": r.event, "race": r.race,
            "distance_km": r.distance_km, "elevation_gain": r.elevation_gain, "time_s": r.time_s, "dnf": r.dnf,
            "rank": r.rank, "total": r.total, "rank_gender": r.rank_gender, "total_gender": r.total_gender,
            "score": r.score, "url": r.url, "match": r.match,
            "activity_ids": json.loads(r.activity_ids) if r.activity_ids else []}


def has_official_result(db: Session, activity_id: int) -> RaceResult | None:
    for r in db.query(RaceResult).filter(RaceResult.activity_ids.isnot(None), RaceResult.match != "rejected"):
        if activity_id in json.loads(r.activity_ids):
            return r
    return None


def set_scores(db: Session, source: str, scores: dict[str, float | None]) -> int:
    """Per-race scores read from your signed-in results page ({site key: score})."""
    n = 0
    for r in db.query(RaceResult).filter(RaceResult.source == source, RaceResult.key.in_(list(scores))):
        if scores[r.key] is not None:
            r.score, n = scores[r.key], n + 1
    db.commit()
    return n


def official_for(by_site: dict[str, dict] | None) -> dict | None:
    """One activity's official results merged: names and ranks from the leading site (UTMB, then
    Betrail, then ITRA), and every site's score."""
    if not by_site:
        return None
    lead = next(by_site[s] for s in SOURCE_ORDER if s in by_site)
    clean = lambda t: (t or "").strip() or None
    return {"event": clean(lead["event"]), "race": clean(lead["race"]), "rank": lead["rank"], "total": lead["total"],
            "rank_gender": lead["rank_gender"], "dnf": lead["dnf"], "time_s": lead["time_s"],
            "stage": lead.get("stage"), "stages": lead.get("stages"),
            "scores": {s: r["score"] for s, r in by_site.items() if r["score"] is not None}}


def performance_table(db: Session) -> list[dict]:
    """Every official result from every site, one row per race: the sites' results of a race share
    their matched activities (or, unmatched, the same official time). Newest first."""
    groups: list[dict] = []
    for r in sorted(db.query(RaceResult).filter(RaceResult.source != "manual",
                                                RaceResult.match.is_(None) | (RaceResult.match != "rejected")),
                    key=lambda r: (r.date, r.source)):
        ids = json.loads(r.activity_ids) if r.activity_ids else []
        g = next((g for g in groups
                  if (ids and set(ids) & set(g["activity_ids"]))
                  or (not ids and not g["activity_ids"] and r.time_s and g["time_s"] and abs(g["time_s"] - r.time_s) <= 2)), None)
        if g is None:
            g = {"sites": {}, "activity_ids": ids, "time_s": r.time_s}
            groups.append(g)
        g["sites"][r.source] = as_dict(r)
        g["activity_ids"] = sorted(set(g["activity_ids"]) | set(ids))
    out = []
    for g in groups:
        lead = next(g["sites"][s] for s in SOURCE_ORDER if s in g["sites"])
        ranked = next((g["sites"][s] for s in SOURCE_ORDER if s in g["sites"] and g["sites"][s]["total"]), lead)
        acts = sorted((db.get(Activity, i) for i in g["activity_ids"]), key=lambda x: x.date)
        a = acts[0] if acts else None   # a stage race starts on its first day
        out.append({
            "date": min(x["date"] for x in g["sites"].values()) if not a else a.date.isoformat(),
            "event": (lead["event"] or "").strip(), "race": (lead["race"] or "").strip(),
            "distance_km": lead["distance_km"], "elevation_gain": lead["elevation_gain"],
            "time_s": lead["time_s"], "dnf": lead["dnf"],
            "rank": ranked["rank"], "total": ranked["total"], "rank_gender": ranked["rank_gender"],
            "total_gender": ranked["total_gender"],
            "scores": {s: x["score"] for s, x in g["sites"].items()},
            "urls": {s: x["url"] for s, x in g["sites"].items()},
            "activity_id": a.id if a else None, "activity_ids": g["activity_ids"],
        })
    return sorted(out, key=lambda x: x["date"], reverse=True)


def set_manual_rank(db: Session, activity: Activity, rank: int | None, total: int | None) -> dict | None:
    """Your place (and the number of finishers) typed for a race with no imported result, e.g. a
    cross country. rank None removes it."""
    key = f"activity-{activity.id}"
    r = db.query(RaceResult).filter_by(source="manual", key=key).first()
    if rank is None:
        if r:
            db.delete(r)
            db.commit()
        return None
    r = r or RaceResult(source="manual", key=key)
    r.date, r.rank, r.total = activity.date, rank, total
    r.distance_km, r.time_s = activity.distance_km, None
    r.activity_ids, r.match, r.fetched_at = json.dumps([activity.id]), "confirmed", datetime.now()
    db.add(r)
    db.commit()
    return as_dict(r)
