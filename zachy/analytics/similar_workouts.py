"""Similar workouts: your interval sessions grouped by their reps ("400 m reps": 10 × 400 m and
12 × 400 m together), each with its reps, recovery, the spread of the reps, heart rate and a score
of the execution.

Reps and recoveries come from the laps (the fast group = reps, analytics/workouts.py). A session
joins a group when its reps are all about the same: by distance (within 5% of each other, the
group by the rounded distance) or by time (within 8%). Hills are a group of their own.

Execution score (0–100): 60% regularity — how close the reps are to each other (the spread of
their pace: 0% = 100, 8% or more = 0) — and 40% finishing strong — the last third of the reps
against the first third (2% faster or more = 100, even = 50, 2% slower or more = 0).
"""

import time

import numpy as np
from sqlalchemy.orm import Session

from zachy.analytics.workouts import (REST, WORK, _cv, assign_roles, fmt_distance, fmt_time, round_distance,
                                      round_time)
from zachy.models import Activity, Lap, WorkoutSummary

SAME_DISTANCE = 0.05
SAME_TIME = 0.08
_cache: dict = {}


def _session(a: Activity, laps: list[Lap], kind: str | None, on_track: bool) -> dict | None:
    rows = [{"distance": (l.distance_km or 0) * 1000, "time": float(l.duration_s or 0), "hr": l.avg_hr,
             "ascent": l.elevation_gain or 0, "descent": 0.0, "fit_role": None} for l in laps]
    for r in rows:
        r["speed"] = r["distance"] / r["time"] if r["time"] else 0.0
    if len(rows) < 4:
        return None
    roles = assign_roles(rows)
    if not roles:
        return None
    work = [r for r, x in zip(rows, roles) if x == WORK]
    rest = [r for r, x in zip(rows, roles) if x == REST]
    if len(work) < 3:
        return None
    by_distance = _cv([r["distance"] for r in work]) <= _cv([r["time"] for r in work])
    values = np.array([r["distance"] if by_distance else r["time"] for r in work])
    if (values.max() - values.min()) / np.median(values) > (SAME_DISTANCE if by_distance else SAME_TIME) * 2:
        return None                                      # mixed reps (pyramids, ladders): no single group
    unit = round_distance(float(np.median(values))) if by_distance else round_time(float(np.median(values)))
    hills = kind == "Hills"
    key = f"{'hills' if hills else 'd' if by_distance else 't'}:{unit:g}"
    label = f"{fmt_distance(unit) if by_distance else fmt_time(unit)} {'hill ' if hills else ''}reps"
    paces = np.array([r["time"] / 60 / (r["distance"] / 1000) for r in work if r["distance"] > 0])
    times = np.array([r["time"] for r in work])
    hrs = [r["hr"] for r in work if r["hr"]]
    # Score: regularity of the reps' pace, and the last third against the first.
    spread = float(paces.std() / paces.mean()) if len(paces) > 1 else 0.0
    third = max(1, len(paces) // 3)
    finish = float((paces[:third].mean() - paces[-third:].mean()) / paces.mean())      # > 0: faster at the end
    reg_score = max(0.0, 1 - spread / 0.08)
    fin_score = min(1.0, max(0.0, 0.5 + finish / 0.04))
    rest_text = None
    if rest:
        rd, rt = [r["distance"] for r in rest], [r["time"] for r in rest]
        rest_text = (fmt_distance(round_distance(float(np.mean(rd)))) if _cv(rd) < _cv(rt) and np.mean(rd) >= 200
                     else fmt_time(round_time(float(np.mean(rt)))))
    return {
        "id": a.id, "date": a.date.isoformat(), "name": a.name, "key": key, "label": label, "by": "distance" if by_distance else "time",
        "unit": unit, "reps": len(work), "rest": rest_text, "on_track": on_track,
        "rep_time": {"min": float(times.min()), "max": float(times.max()), "avg": float(times.mean())},
        "rep_pace": {"min": float(paces.min()), "max": float(paces.max()), "avg": float(paces.mean())},
        "hr": {"min": float(min(hrs)), "max": float(max(hrs)), "avg": float(np.mean(hrs))} if hrs else None,
        "spread_pct": round(spread * 100, 1), "finish_pct": round(finish * 100, 1),
        "score": round(100 * (0.6 * reg_score + 0.4 * fin_score)),
        "score_parts": {"regularity": round(100 * reg_score), "finish": round(100 * fin_score)},
        "paces": [round(float(p), 3) for p in paces],
    }


def similar_workouts(db: Session) -> list[dict]:
    """Groups of 2+ sessions with the same reps, most sessions first."""
    n = db.query(WorkoutSummary).filter(WorkoutSummary.structured == 1).count()
    if _cache.get("n") == n and _cache.get("at", 0) > time.time() - 600:
        return _cache["groups"]
    rows = db.query(WorkoutSummary).filter(WorkoutSummary.structured == 1).all()
    ids = [r.activity_id for r in rows]
    meta = {r.activity_id: r for r in rows}
    laps: dict[int, list[Lap]] = {}
    for l in db.query(Lap).filter(Lap.activity_id.in_(ids)).order_by(Lap.activity_id, Lap.lap_number):
        laps.setdefault(l.activity_id, []).append(l)
    # Temperature: the weather at the time (Open-Meteo), else the watch's average.
    from sqlalchemy import func
    from zachy.analytics.routes import _weather_temps
    from zachy.models import Record
    temps = _weather_temps(db, ids)
    watch = dict(db.query(Record.activity_id, func.avg(Record.temperature))
                 .filter(Record.activity_id.in_(ids), Record.temperature.isnot(None)).group_by(Record.activity_id).all())
    groups: dict[str, dict] = {}
    for a in db.query(Activity).filter(Activity.id.in_(ids)).order_by(Activity.date):
        m = meta[a.id]
        if m.type in ("Progressive", "Tempo"):
            continue
        s = _session(a, laps.get(a.id, []), m.type, bool(m.on_track))
        if not s:
            continue
        s["temp"] = temps.get(a.id)
        s["temp_watch"] = round(float(watch[a.id]), 1) if watch.get(a.id) is not None else None
        g = groups.setdefault(s["key"], {"key": s["key"], "label": s["label"], "by": s["by"], "unit": s["unit"], "sessions": []})
        g["sessions"].append(s)
    out = []
    for g in groups.values():
        if len(g["sessions"]) < 2:
            continue
        ss = g["sessions"]
        g.update(count=len(ss), first=ss[0]["date"], last=ss[-1]["date"],
                 best=max(ss, key=lambda x: (x["score"], -x["rep_pace"]["avg"]))["id"],
                 fastest=min(ss, key=lambda x: x["rep_pace"]["avg"])["id"])
        out.append(g)
    out.sort(key=lambda g: (-g["count"], g["key"]))
    _cache.update(n=n, at=time.time(), groups=out)
    return out


def similar_to(db: Session, activity_id: int) -> dict | None:
    """The group a workout belongs to (for its page)."""
    for g in similar_workouts(db):
        for i, s in enumerate(g["sessions"]):
            if s["id"] == activity_id:
                return {"key": g["key"], "label": g["label"], "count": g["count"], "score": s["score"],
                        "rank": 1 + sorted(g["sessions"], key=lambda x: x["rep_pace"]["avg"]).index(s)}
    return None
