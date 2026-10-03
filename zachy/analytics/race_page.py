"""The Races page: every race with where it started, a small elevation silhouette, and the other
editions of the same event (same start within 3 km, distance within ~15%, one per year) for
comparison.

Profiles and start points come from the FIT records and never change, so they're kept in memory.
"""

import math

import numpy as np
from sqlalchemy.orm import Session

from zachy.analytics.body import races
from zachy.analytics.elevation import display_clock, remove_jumps
from zachy.analytics.ffa import distance_key, level
from zachy.models import Activity, Record

PROFILE_POINTS = 60
PROGRESS_POINTS = 101       # time at every 1% of the distance, to compare editions
SAME_START_KM = 3.0
SAME_DISTANCE = 0.15

_track_cache: dict[int, dict] = {}


def _track(db: Session, activity_id: int) -> dict:
    """Start point, elevation silhouette and time-at-each-percent for one activity."""
    if activity_id in _track_cache:
        return _track_cache[activity_id]
    rows = (db.query(Record.timer_s, Record.elapsed_s, Record.distance_km, Record.elevation,
                     Record.latitude, Record.longitude, Record.hr)
            .filter(Record.activity_id == activity_id).order_by(Record.id).all())
    out = {"start": None, "profile": None, "progress": None, "elevation_pct": None, "hr_pct": None, "elapsed_s": None}
    if rows:
        arr = np.array([[np.nan if v is None else v for v in r] for r in rows], dtype=float)
        t, el, d, z, lat, lon, hr = arr.T
        if np.isfinite(el).sum() > 1:
            out["elapsed_s"] = float(np.nanmax(el) - np.nanmin(el))   # start to finish, stops included
        gps = np.isfinite(lat) & np.isfinite(lon)
        if gps.any():
            i = int(np.argmax(gps))
            out["start"] = [round(float(lat[i]), 5), round(float(lon[i]), 5)]
        ok = np.isfinite(d) & np.isfinite(t)
        if ok.sum() > 10:
            dd = np.maximum.accumulate(d[ok])
            total = dd[-1]
            if total > 0:
                pct = np.linspace(0, total, PROGRESS_POINTS)
                tt = t[ok] - t[ok][0]
                out["progress"] = [round(float(v)) for v in np.interp(pct, dd, tt)]
                if np.isfinite(hr[ok]).sum() > 10:
                    h = hr[ok]
                    m = np.isfinite(h)
                    out["hr_pct"] = [round(float(v)) for v in np.interp(pct, dd[m], h[m])]
                if np.isfinite(z[ok]).sum() > 10:
                    zz = remove_jumps(z[ok], display_clock(el[ok], t[ok]))
                    m = np.isfinite(zz)
                    if m.sum() > 10:
                        prof = np.interp(pct, dd[m], zz[m])
                        out["elevation_pct"] = [round(float(v)) for v in prof]
                        out["profile"] = [round(float(v)) for v in
                                          np.interp(np.linspace(0, total, PROFILE_POINTS), dd[m], zz[m])]
    _track_cache[activity_id] = out
    return out


def _km_between(a, b) -> float:
    (la1, lo1), (la2, lo2) = a, b
    x = math.radians(lo2 - lo1) * math.cos(math.radians((la1 + la2) / 2))
    y = math.radians(la2 - la1)
    return 6371 * math.hypot(x, y)


def _same_event(a: dict, b: dict) -> bool:
    if not a["start"] or not b["start"] or (a["surface"] == "trail") != (b["surface"] == "trail"):
        return False   # a road marathon and a trail from the same town are different races
    ratio = a["distance_km"] / b["distance_km"] if b["distance_km"] else 0
    return _km_between(a["start"], b["start"]) <= SAME_START_KM and abs(ratio - 1) <= SAME_DISTANCE


def _ffa(r: dict, tr: dict) -> dict | None:
    """FFA level of a road race: official time when matched to a result, else watch elapsed time."""
    res = r.get("result") or {}
    if r["surface"] != "road" or "trail" in f"{res.get('event') or ''} {res.get('race') or ''}".lower():
        return None   # road races only (a watch in road mode doesn't make a trail or a cross a road race)
    key = distance_key(r["distance_km"])
    if not key:
        return None
    official = (r.get("result") or {}).get("time_s")
    seconds = official or tr["elapsed_s"] or r["duration_s"]
    return {**level(key, seconds), "time_source": "official" if official else "watch"}


def race_list(db: Session) -> list[dict]:
    """All races, newest first, each with start, profile, and its event (editions of one race)."""
    from zachy.analytics.race_results import results_by_activity
    items = races(db)
    official = results_by_activity(db)
    for r in items:
        mine = official.get(r["id"], {})
        r["results"] = mine
        r["result"] = next((mine[s] for s in ("utmb", "betrail", "itra", "manual") if s in mine), None)
        tr = _track(db, r["id"])
        r["start"], r["profile"] = tr["start"], tr["profile"]
        r["ffa"] = _ffa(r, tr)
        a = db.get(Activity, r["id"])
        r["avg_hr"], r["relative_effort"] = a.avg_hr, a.relative_effort
    # Group editions: each race joins the first earlier race of the same event.
    items.sort(key=lambda r: r["date"])
    for i, r in enumerate(items):
        # Editions are in different years (two races at one place in a year are two events).
        r["event"] = next((p["event"] for p in items[:i] if _same_event(p, r)
                           and all(q["date"][:4] != r["date"][:4] for q in items[:i] if q["event"] == p["event"])),
                          r["id"])
    counts = {}
    for r in items:
        counts[r["event"]] = counts.get(r["event"], 0) + 1
    for r in items:
        r["editions"] = counts[r["event"]]
    return sorted(items, key=lambda r: r["date"], reverse=True)


def race_detail(db: Session, activity_id: int) -> dict | None:
    """One race and every edition of its event, with time / HR / altitude at each percent."""
    items = race_list(db)
    race = next((r for r in items if r["id"] == activity_id), None)
    if race is None:
        return None
    editions = sorted((r for r in items if r["event"] == race["event"]), key=lambda r: r["date"])
    for e in editions:
        tr = _track(db, e["id"])
        e["progress"], e["hr_pct"], e["elevation_pct"] = tr["progress"], tr["hr_pct"], tr["elevation_pct"]
    return {"race": race, "editions": editions}
