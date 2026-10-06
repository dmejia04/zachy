"""Routes you run regularly: runs on the same course (10 or more), found from their GPS tracks.

Each run's track becomes the set of ~100 m map cells it passes through (its fingerprint, computed
once). Two runs are on the same course when their distances are within 5% and each one's cells
lie on the other's track (90%, allowing one cell of GPS wobble). Runs are grouped in date
order: each joins the first group whose first run it matches; then every run must also match the
group's most typical run, so a group can't drift. Groups of 10 runs or more are
routes; the run most like the others represents the route (map, distance). A loop run the other
way round stays in the route, flagged "reversed" (its climbs differ).

Per run on a route: time, pace, heart rate, efficiency (metres per heartbeat: distance over
heartbeats, comparable between easy runs on the same course: more = fitter), temperature.
"""

import json
import math
from collections import Counter

from sqlalchemy.orm import Session

from zachy.analytics.terrain import terrain_class
from zachy.models import Activity, ActivityWeather, Record, Route, RouteFingerprint, RouteMember

VERSION = 2
CELL_M = 100
MIN_RUNS = 10
MIN_KM = 2.0
SAME_DISTANCE = 0.05
SAME_TRACK = 0.90
SAMPLE_POINTS = 80
RUN_TYPES = ("running", "trail_running", "street_running", "track_running", "ultra_run")


def _cell(lat: float, lon: float) -> tuple[int, int]:
    dlat = CELL_M / 111_320
    return int(math.floor(lat / dlat)), int(math.floor(lon / (dlat / max(math.cos(math.radians(lat)), 0.2))))


def _fingerprint(db: Session, a: Activity) -> RouteFingerprint | None:
    rows = (db.query(Record.latitude, Record.longitude, Record.temperature)
            .filter(Record.activity_id == a.id, Record.latitude.isnot(None)).order_by(Record.id).all())
    if len(rows) < 60:
        return None
    pts = [(r[0], r[1]) for r in rows]
    cells = sorted({_cell(*p) for p in pts})
    step = max(1, len(pts) // SAMPLE_POINTS)
    sample = pts[::step] + [pts[-1]]
    lat0 = sum(p[0] for p in sample) / len(sample)
    kx, ky = 111.32 * math.cos(math.radians(lat0)), 111.32   # km per degree
    area = sum((sample[i][1] * kx) * (sample[i + 1][0] * ky) - (sample[i + 1][1] * kx) * (sample[i][0] * ky)
               for i in range(len(sample) - 1)) / 2
    temps = [r[2] for r in rows if r[2] is not None]
    return RouteFingerprint(activity_id=a.id, version=VERSION, cells=json.dumps(cells),
                            sample=json.dumps([[round(p[0], 5), round(p[1], 5)] for p in sample]),
                            start_lat=pts[0][0], start_lon=pts[0][1], end_lat=pts[-1][0], end_lon=pts[-1][1],
                            area=area, temp_avg=sum(temps) / len(temps) if temps else None)


def fingerprints(db: Session) -> dict[int, RouteFingerprint]:
    """Every run's fingerprint, computing the missing ones (about 0.03 s each)."""
    have = {f.activity_id: f for f in db.query(RouteFingerprint).filter(RouteFingerprint.version == VERSION)}
    todo = (db.query(Activity).filter(Activity.activity_type.in_(RUN_TYPES), Activity.distance_km >= MIN_KM,
                                      Activity.id.notin_(list(have) or [0])).all())
    for i, a in enumerate(todo, 1):
        db.query(RouteFingerprint).filter(RouteFingerprint.activity_id == a.id).delete()
        f = _fingerprint(db, a)
        if f is None:   # no GPS (treadmill…): remember, so it isn't read again
            f = RouteFingerprint(activity_id=a.id, version=VERSION, cells="[]", sample="[]")
        db.add(f)
        have[a.id] = f
        if i % 200 == 0:
            db.commit()
    db.commit()
    return {k: f for k, f in have.items() if f.cells != "[]"}


def _dilate(cells: set) -> set:
    return {(i + di, j + dj) for i, j in cells for di in (-1, 0, 1) for dj in (-1, 0, 1)}


def _km_between(lat1, lon1, lat2, lon2) -> float:
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    return 6371 * math.hypot(x, math.radians(lat2 - lat1))


class _Track:
    def __init__(self, a: Activity, f: RouteFingerprint):
        self.id, self.date, self.km = a.id, a.date, a.distance_km
        self.cells = {tuple(c) for c in json.loads(f.cells)}
        self.wide = _dilate(self.cells)
        self.f = f

    def same_course(self, other: "_Track") -> bool:
        if abs(self.km / other.km - 1) > SAME_DISTANCE:
            return False
        if not self.cells & other.wide:
            return False
        return (len(self.cells & other.wide) / len(self.cells) >= SAME_TRACK
                and len(other.cells & self.wide) / len(other.cells) >= SAME_TRACK)


def _kind(f: RouteFingerprint, km: float) -> str:
    if f.start_lat is None:
        return "loop"
    closed = _km_between(f.start_lat, f.start_lon, f.end_lat, f.end_lon) < 0.4
    if not closed:
        return "point-to-point"
    # A loop encloses ground; an out-and-back retraces itself (tiny area for its length).
    return "loop" if abs(f.area or 0) > 0.02 * km * km else "out-and-back"


def detect(db: Session) -> list[dict]:
    """Group all runs into courses; keep those run 10 times or more as routes. Route ids and the
    names you gave are kept across detections (a new group takes the id of the old route it
    shares most runs with)."""
    from zachy.analytics.terrain import place_from_name
    fps = fingerprints(db)
    acts = {a.id: a for a in db.query(Activity).filter(Activity.id.in_(list(fps)))}
    tracks = sorted((_Track(acts[i], f) for i, f in fps.items() if acts.get(i) and acts[i].distance_km),
                    key=lambda t: (t.date, t.id))
    groups: list[list[_Track]] = []
    for t in tracks:
        g = next((g for g in groups if g[0].same_course(t)), None)
        if g is None:
            groups.append([t])
        else:
            g.append(t)
    # Second pass: each run must also match the group's most typical run (not only its first one),
    # so a group can't drift run by run.
    refined = []
    for g in groups:
        if len(g) < MIN_RUNS:
            continue
        probe = g if len(g) <= 40 else g[:: len(g) // 40]
        rep_t = max(probe, key=lambda t: sum(len(t.cells & o.wide) for o in probe))
        g = [t for t in g if t is rep_t or rep_t.same_course(t)]
        if len(g) >= MIN_RUNS:
            refined.append(g)
    groups = refined

    old_members = {m.activity_id: m.route_id for m in db.query(RouteMember)}
    old_routes = {r.id: r for r in db.query(Route)}
    db.query(RouteMember).delete()
    used = set()
    for g in sorted(groups, key=len, reverse=True):
        # The run most like the others (the sample keeps it quick on big groups).
        probe = g if len(g) <= 40 else g[:: len(g) // 40]
        rep = max(probe, key=lambda t: sum(len(t.cells & o.wide) for o in probe))
        votes = Counter(old_members[t.id] for t in g if t.id in old_members and old_members[t.id] not in used)
        rid = votes.most_common(1)[0][0] if votes else None
        place = place_from_name(acts[rep.id].name) or "Route"
        kind = _kind(rep.f, rep.km)
        auto = f"{place} · {rep.km:.1f} km {kind}"
        route = old_routes.get(rid) if rid else None
        if route is None:
            route = Route(auto_name=auto, rep_activity=rep.id, kind=kind)
            db.add(route)
            db.flush()
        else:
            route.auto_name, route.rep_activity, route.kind = auto, rep.id, kind
        used.add(route.id)
        # Direction of a loop: the way most runs go round it; the others are "reversed".
        sign = 1 if sum(1 if (t.f.area or 0) > 0 else -1 for t in g) >= 0 else -1
        for t in g:
            rev = kind == "loop" and (t.f.area or 0) * sign < 0
            db.add(RouteMember(activity_id=t.id, route_id=route.id, reversed=int(rev)))
    for rid, r in old_routes.items():   # routes no longer found
        if rid not in used:
            db.delete(r)
    db.commit()
    return route_list(db)


def _weather_temps(db: Session, ids: list[int]) -> dict[int, float]:
    out = {}
    for w in db.query(ActivityWeather).filter(ActivityWeather.activity_id.in_(ids), ActivityWeather.data.isnot(None)):
        t = json.loads(w.data).get("temp")
        if t is not None:
            out[w.activity_id] = t
    return out


def _runs(db: Session, route_id: int) -> list[dict]:
    from zachy.analytics.races import category_status
    from zachy.models import ActivityOverride
    members = {m.activity_id: m for m in db.query(RouteMember).filter(RouteMember.route_id == route_id)}
    acts = db.query(Activity).filter(Activity.id.in_(list(members))).order_by(Activity.date).all()
    fps = {f.activity_id: f for f in db.query(RouteFingerprint).filter(RouteFingerprint.activity_id.in_(list(members)))}
    overrides = {o.activity_id: o for o in db.query(ActivityOverride).filter(ActivityOverride.activity_id.in_(list(members)))}
    weather = _weather_temps(db, list(members))
    out = []
    for a in acts:
        cat = category_status(db, a, getattr(overrides.get(a.id), "category", None))
        eff = a.distance_km * 1000 / (a.avg_hr * a.duration_s / 60) if a.avg_hr and a.duration_s else None
        f = fps.get(a.id)
        out.append({"id": a.id, "date": a.date.isoformat(), "distance_km": a.distance_km, "duration_s": a.duration_s,
                    "pace": a.duration_s / 60 / a.distance_km if a.duration_s and a.distance_km else None,
                    "avg_hr": a.avg_hr, "elevation_gain": a.elevation_gain, "efficiency": round(eff, 3) if eff else None,
                    "category": cat["category"] if cat else None, "reversed": bool(members[a.id].reversed),
                    "temp": weather.get(a.id), "temp_watch": round(f.temp_avg, 1) if f and f.temp_avg is not None else None})
    return out


def _summary(db: Session, r: Route, runs: list[dict]) -> dict:
    rep = db.get(Activity, r.rep_activity)
    f = db.get(RouteFingerprint, r.rep_activity)
    timed = [x for x in runs if x["duration_s"]]
    best = min(timed, key=lambda x: x["duration_s"], default=None)
    effs = [x for x in runs if x["efficiency"]]
    best_eff = max(effs, key=lambda x: x["efficiency"], default=None)
    return {"id": r.id, "name": r.name or r.auto_name, "auto_name": r.auto_name, "custom": bool(r.name), "kind": r.kind,
            "distance_km": rep.distance_km if rep else None, "elevation_gain": rep.elevation_gain if rep else None,
            "terrain": terrain_class((rep.elevation_gain or 0) / rep.distance_km) if rep and rep.distance_km else None,
            "runs": len(runs), "first": runs[0]["date"] if runs else None, "last": runs[-1]["date"] if runs else None,
            "best": best and {k: best[k] for k in ("id", "date", "duration_s", "pace")},
            "best_efficiency": best_eff and {k: best_eff[k] for k in ("id", "date", "efficiency")},
            "line": json.loads(f.sample) if f else []}


def route_list(db: Session) -> list[dict]:
    """Every route, most run first."""
    out = [_summary(db, r, _runs(db, r.id)) for r in db.query(Route)]
    return sorted(out, key=lambda x: -x["runs"])


def route_detail(db: Session, route_id: int) -> dict | None:
    """One route: its summary, every run on it, and each run's line for the map."""
    r = db.get(Route, route_id)
    if not r:
        return None
    runs = _runs(db, route_id)
    lines = {f.activity_id: json.loads(f.sample) for f in
             db.query(RouteFingerprint).filter(RouteFingerprint.activity_id.in_([x["id"] for x in runs]))}
    return {**_summary(db, r, runs), "list": runs, "lines": lines}


def activity_route(db: Session, activity_id: int) -> dict | None:
    """The route an activity is on: its name, which time on it this was, the time's rank, and the
    efficiency against the route's average."""
    m = db.get(RouteMember, activity_id)
    if not m:
        return None
    r = db.get(Route, m.route_id)
    runs = _runs(db, m.route_id)
    me = next((x for x in runs if x["id"] == activity_id), None)
    if not r or not me:
        return None
    nth = [x["id"] for x in runs].index(activity_id) + 1
    times = sorted(x["duration_s"] for x in runs if x["duration_s"])
    effs = [x["efficiency"] for x in runs if x["efficiency"]]
    avg_eff = sum(effs) / len(effs) if effs else None
    return {"id": r.id, "name": r.name or r.auto_name, "runs": len(runs), "nth": nth,
            "time_rank": times.index(me["duration_s"]) + 1 if me["duration_s"] in times else None,
            "efficiency_vs_avg": round((me["efficiency"] / avg_eff - 1) * 100, 1) if me["efficiency"] and avg_eff else None,
            "reversed": me["reversed"]}


def assign_new(db: Session, activity: Activity) -> None:
    """After a sync: put a new run on the route it matches (no full detection needed)."""
    if activity.activity_type not in RUN_TYPES or (activity.distance_km or 0) < MIN_KM or db.get(RouteMember, activity.id):
        return
    f = db.get(RouteFingerprint, activity.id) or _fingerprint(db, activity)
    if f is None:
        return
    db.merge(f)
    t = _Track(activity, f)
    for r in db.query(Route):
        rep, rf = db.get(Activity, r.rep_activity), db.get(RouteFingerprint, r.rep_activity)
        if rep and rf and rep.distance_km and _Track(rep, rf).same_course(t):
            loop_sign = (rf.area or 0) > 0
            db.add(RouteMember(activity_id=activity.id, route_id=r.id,
                               reversed=int(r.kind == "loop" and ((f.area or 0) > 0) != loop_sign)))
            break
    db.commit()
