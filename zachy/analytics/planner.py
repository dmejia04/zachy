"""Race planner: a course from a GPX file, cut into climbs, descents and flats, with a predicted time.

The prediction uses your race model (gap_race.py), fitted on your trail races:
    log speed = race level + f(slope) + γ·(share of the race done)
so a stretch of course takes  length × cost(slope) × exp(−γ·share) / level  (cost = exp(−f)).
Summed over the course, that is its "effort distance" E (flat metres, fatigue included), and the
moving time is E / level.

Your level depends on how long the race is (you run a 3 h race faster than a 15 h one): each of
your past trail races gives one (its own effort distance over its moving time), and a line through
log(level) against log(moving time) gives the level for any duration. The planned race's time
then solves  T = E / level(T)  (closed form). Aid station stops are added on top.

Two settings per plan: the outlook scales the moving time (−20% optimistic … +20% pessimistic: how
good the day is), and the strategy moves effort between the start and the end at the same total
time: each stretch's time × exp(k·(share done − ½)), rescaled to the same total, k = −STRATEGY_K ×
strategy (aggressive −1: fast start, slower end; conservative +1: easier start, stronger end; 0:
your usual race, the model's own fatigue).
"""

import json
import math
import re
from datetime import date, datetime

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from zachy.analytics.elevation import moving_elevation
from zachy.analytics.gap_race import cost_race, race_model
from zachy.models import Activity, RacePlan, Record

STEP_M = 25            # course resolution
SLOPE_M = 200          # slope measured over this (as the model's 200 m segments)
CLIMB = 0.03           # above +3% = climb, below −3% = descent
MIN_SECTION_M = 400    # shorter pieces join their neighbours
STRATEGY_K = 0.5       # at the ends of the strategy slider, halves ~25% apart
_level_cache: dict = {}


# ---------- course ----------

def parse_gpx(text: str) -> list[list[float]]:
    """[[lat, lon, ele], …] from a GPX file's track points (or route points)."""
    pts = re.findall(r'<(?:trkpt|rtept)\s+[^>]*?lat="([-\d.]+)"[^>]*?lon="([-\d.]+)"[^>]*?(?:/>|>(.*?)</(?:trkpt|rtept)>)', text, re.S)
    if not pts:   # lon before lat
        pts = [(la, lo, body) for lo, la, body in
               re.findall(r'<(?:trkpt|rtept)\s+[^>]*?lon="([-\d.]+)"[^>]*?lat="([-\d.]+)"[^>]*?(?:/>|>(.*?)</(?:trkpt|rtept)>)', text, re.S)]
    out = []
    for la, lo, body in pts:
        m = re.search(r"<ele>([-\d.]+)</ele>", body or "")
        out.append([float(la), float(lo), float(m.group(1)) if m else None])
    return out


def course_from_gpx(text: str) -> list[list[float]]:
    """The course every 25 m: [km, altitude (smoothed over ~75 m), lat, lon]."""
    pts = parse_gpx(text)
    if len(pts) < 10:
        raise ValueError("No track in this GPX file")
    if sum(p[2] is not None for p in pts) < len(pts) / 2:
        raise ValueError("This GPX file has no altitude")
    lat, lon = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
    ele = pd.Series([p[2] for p in pts], dtype=float).interpolate(limit_direction="both").to_numpy()
    dx = np.radians(np.diff(lon)) * np.cos(np.radians((lat[1:] + lat[:-1]) / 2))
    dy = np.radians(np.diff(lat))
    d = np.concatenate([[0], np.cumsum(6371000 * np.hypot(dx, dy))])
    keep = np.append(True, np.diff(d) > 0)
    d, lat, lon, ele = d[keep], lat[keep], lon[keep], ele[keep]
    grid = np.arange(0, d[-1], STEP_M)
    z = pd.Series(np.interp(grid, d, ele)).rolling(3, center=True, min_periods=1).mean().to_numpy()
    return [[round(g / 1000, 4), round(float(zz), 1), round(float(a), 6), round(float(o), 6)]
            for g, zz, a, o in zip(grid, z, np.interp(grid, d, lat), np.interp(grid, d, lon))]


def _slopes(km: np.ndarray, z: np.ndarray) -> np.ndarray:
    """Slope at each course point, measured over ~200 m around it."""
    h = max(1, int(SLOPE_M / STEP_M / 2))
    zp = np.concatenate([np.full(h, z[0]), z, np.full(h, z[-1])])
    kp = np.concatenate([km[0] - np.arange(h, 0, -1) * STEP_M / 1000, km, km[-1] + np.arange(1, h + 1) * STEP_M / 1000])
    return (zp[2 * h:] - zp[:-2 * h]) / ((kp[2 * h:] - kp[:-2 * h]) * 1000)


def sections(km: np.ndarray, z: np.ndarray, g: np.ndarray) -> list[dict]:
    """Climbs (> +3%), descents (< −3%) and flats; pieces under 400 m join their neighbours."""
    kind = np.where(g > CLIMB, "climb", np.where(g < -CLIMB, "descent", "flat"))
    runs = []
    for i, k in enumerate(kind):
        if runs and runs[-1][0] == k:
            runs[-1][2] = i
        else:
            runs.append([k, i, i])
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for j, (k, a, b) in enumerate(runs):
            if (km[b] - km[a]) * 1000 < MIN_SECTION_M:
                # Join the longer neighbour.
                left = runs[j - 1] if j > 0 else None
                right = runs[j + 1] if j + 1 < len(runs) else None
                target = left if right is None or (left and (km[left[2]] - km[left[1]]) >= (km[right[2]] - km[right[1]])) else right
                target[1], target[2] = min(target[1], a), max(target[2], b)
                runs.pop(j)
                changed = True
                break
        merged = []
        for r in runs:
            if merged and merged[-1][0] == r[0]:
                merged[-1][2] = r[2]
            else:
                merged.append(r)
        runs = merged
    out = []
    for k, a, b in runs:
        b2 = min(b + 1, len(km) - 1)
        dz = np.diff(z[a:b2 + 1])
        out.append({"type": k, "start": a, "end": b2, "start_km": float(km[a]), "end_km": float(km[b2]),
                    "km": float(km[b2] - km[a]), "gain": float(dz[dz > 0].sum()), "loss": float(-dz[dz < 0].sum())})
    return out


# ---------- your level ----------

def _effort_steps(km: np.ndarray, z: np.ndarray, model: dict) -> np.ndarray:
    """Flat-equivalent metres of each step (slope cost × fatigue for its share of the race)."""
    g = _slopes(km, z)
    step = np.diff(km, append=km[-1]) * 1000
    done = km / km[-1] if km[-1] else km
    return step * cost_race(np.clip(g, -0.45, 0.45), model) * np.exp(-model["gamma_fatigue"] * done)


def _race_effort(db: Session, activity_id: int, model: dict) -> float | None:
    rows = (db.query(Record.timer_s, Record.elapsed_s, Record.distance_km, Record.elevation)
            .filter(Record.activity_id == activity_id).order_by(Record.id).all())
    r = pd.DataFrame(rows, columns=["t", "el", "d", "z"]).astype(float).dropna(subset=["t", "d", "z"])
    if len(r) < 300:
        return None
    z = moving_elevation(r.z.to_numpy(), r.t.to_numpy(), r.el.to_numpy())
    d = np.maximum.accumulate(r.d.to_numpy())
    grid = np.arange(0, d[-1], STEP_M / 1000)
    zg = pd.Series(np.interp(grid, d, z)).rolling(3, center=True, min_periods=1).mean().to_numpy()
    return float(_effort_steps(grid, zg, model).sum())


def level_model(db: Session) -> dict | None:
    """Your race level (flat-equivalent speed, m/s) against race duration, from your trail races:
    log(level) = a + b·log(moving hours)."""
    from zachy.analytics.body import races
    model = race_model(db)
    if not model:
        return None
    key = model["fitted_at"]
    if key in _level_cache:
        return _level_cache[key]
    pts = []
    for r in races(db):
        if r["surface"] != "trail" or r["terrain"] not in ("mountain", "rolling") or not r["duration_s"] or r["duration_s"] < 3600:
            continue
        e = _race_effort(db, r["id"], model)
        if e:
            pts.append({"id": r["id"], "date": r["date"], "name": r.get("place") or r["name"], "km": r["distance_km"],
                        "gain": r["elevation_gain"], "hours": r["duration_s"] / 3600, "level": e / r["duration_s"]})
    if len(pts) < 3:
        return None
    # Your normal race day: stage-race days (another race within 3 days) and bad days (> 1.5 σ
    # slower than the fit) left out; the outlook slider covers the rest.
    days = [date.fromisoformat(str(p["date"])[:10]) for p in pts]
    single = np.array([not any(i != j and abs((days[i] - days[j]).days) <= 3 for j in range(len(pts))) for i in range(len(pts))])
    if single.sum() < 3:
        single[:] = True
    x = np.log([p["hours"] for p in pts])
    y = np.log([p["level"] for p in pts])
    keep = single.copy()
    for _ in range(2):
        b, a = np.polyfit(x[keep], y[keep], 1)
        res = y - (a + b * x)
        keep = single & (res > -1.5 * res[keep].std())
    b, a = np.polyfit(x[keep], y[keep], 1)
    res = y - (a + b * x)
    for p, k in zip(pts, keep):
        p["used"] = bool(k)
    out = {"a": float(a), "b": float(b), "spread": float(res[keep].std()), "races": pts, "n_used": int(keep.sum()),
           "gamma": model["gamma_fatigue"]}
    _level_cache[key] = out
    return out


# ---------- the plan ----------

def predict(db: Session, plan: RacePlan) -> dict:
    if plan.kind == "road":
        from zachy.analytics.road_plan import predict_road
        return predict_road(db, plan)
    course = json.loads(plan.course)
    km = np.array([c[0] for c in course])
    z = np.array([c[1] for c in course])
    model = race_model(db)
    if not model:
        raise ValueError("No race model yet: fit it in the GAP page (Race model)")
    steps = _effort_steps(km, z, model)
    effort = float(steps.sum())                            # flat-equivalent metres
    lv = level_model(db)
    if plan.flat_pace:
        level, source = 1000 / (plan.flat_pace * 60), "yours"
        moving = effort / level
    elif lv:
        # T = E / (e^a · T^b), T in hours → T = (E / (3600·e^a))^(1/(1+b))
        hours = (effort / (3600 * math.exp(lv["a"]))) ** (1 / (1 + lv["b"]))
        moving = hours * 3600
        level, source = effort / moving, "your races"
    else:
        raise ValueError("Not enough trail races to set your level: type your flat-equivalent pace")
    base_moving = moving                                           # the model's own prediction
    moving *= 1 + (plan.outlook or 0) / 100                        # how good the day is
    level = effort / moving
    dt = steps / level
    k = -STRATEGY_K * (plan.strategy or 0)                         # how the effort is spread
    if k:
        done = km / km[-1]
        w = dt * np.exp(k * (done - 0.5))
        dt = w * dt.sum() / w.sum()
    t_cum = np.concatenate([[0], np.cumsum(dt[:-1])])   # moving time at each course point
    half = float(np.interp(km[-1] / 2, km, t_cum))
    aid = sorted(json.loads(plan.aid or "[]"), key=lambda s: s["km"])
    def at_km(k):
        return float(np.interp(k, km, t_cum))
    stops_before = lambda k: sum((s.get("stop_min") or 0) * 60 for s in aid if s["km"] < k - 1e-6)
    secs = sections(km, z, _slopes(km, z))
    for s in secs:
        t0, t1 = t_cum[s["start"]], t_cum[s["end"]]
        s["time_s"] = float(t1 - t0)
        s["pace"] = s["time_s"] / 60 / s["km"] if s["km"] else None
        s["grade"] = (z[s["end"]] - z[s["start"]]) / (s["km"] * 1000) * 100 if s["km"] else 0
        s["arrive_s"] = float(t1 + stops_before(s["end_km"]))
        s["alt_start"], s["alt_end"] = float(z[s["start"]]), float(z[s["end"]])
        del s["start"], s["end"]
    stations = [{**s, "arrive_s": at_km(s["km"]) + stops_before(s["km"]),
                 "leave_s": at_km(s["km"]) + stops_before(s["km"]) + (s.get("stop_min") or 0) * 60,
                 "alt": float(np.interp(s["km"], km, z))} for s in aid]
    # Between aid stations: distance, climb, time.
    marks = [0.0] + [s["km"] for s in aid] + [float(km[-1])]
    legs = []
    for a, b in zip(marks, marks[1:]):
        m = (km >= a) & (km <= b)
        dz = np.diff(z[m]) if m.sum() > 1 else np.array([0.0])
        legs.append({"from_km": a, "to_km": b, "km": b - a, "gain": float(dz[dz > 0].sum()), "loss": float(-dz[dz < 0].sum()),
                     "time_s": at_km(b) - at_km(a)})
    dz = np.diff(z)
    stops = sum((s.get("stop_min") or 0) * 60 for s in aid)
    thin = max(1, len(course) // 800)
    # Longest climb, descent and flat (by distance).
    for t in ("climb", "descent", "flat"):
        of = [s for s in secs if s["type"] == t and (t != "flat" or s["km"] >= 1)]   # a flat under 1 km isn't worth a tag
        if of:
            max(of, key=lambda s: s["km"])["longest"] = True
    nutrition = _nutrition(plan, legs, (moving + stops) / 3600)
    return {
        "id": plan.id, "kind": "trail", "name": plan.name, "race_date": plan.race_date.isoformat() if plan.race_date else None,
        "gpx_name": plan.gpx_name, "has_gpx": True, "distance_km": float(km[-1]), "gain": float(dz[dz > 0].sum()), "loss": float(-dz[dz < 0].sum()),
        "alt_min": float(z.min()), "alt_max": float(z.max()), "effort_km": effort / 1000,
        "moving_s": moving, "stops_s": stops, "total_s": moving + stops,
        "outlook": plan.outlook or 0, "strategy": plan.strategy or 0,
        "base": {"moving_s": base_moving, "total_s": base_moving + stops, "flat_pace": base_moving / 60 / (effort / 1000),
                 "pace": base_moving / 60 / float(km[-1])},
        "pace": moving / 60 / float(km[-1]), "flat_pace": moving / 60 / (effort / 1000),
        "scores": estimate_scores(db, effort, (moving + stops) / 3600),
        "halves": {"first_s": half, "second_s": float(t_cum[-1] - half)},
        "level": {"speed": level, "flat_pace": 1000 / level / 60, "source": source, "flat_pace_set": plan.flat_pace,
                  "spread": lv["spread"] if lv and source != "yours" else None, "n_races": lv["n_used"] if lv else 0,
                  "fatigue_pct": round((1 - math.exp(model["gamma_fatigue"])) * 100, 1)},
        "sections": secs, "aid": stations, "legs": legs, "start_time": plan.start_time, "nutrition": nutrition,
        "profile": [[c[0], c[1]] for c in course[::thin]] + [[course[-1][0], course[-1][1]]],
        "line": [[c[2], c[3]] for c in course[::max(1, len(course) // 400)]],
    }


# ---------- the sites' scores (UTMB index, ITRA, Betrail): estimated from your own results ----------

SITES = ("utmb", "itra", "betrail")
_score_cache: dict = {}


def score_models(db: Session) -> dict:
    """Per site, from your scored results matched to a race activity: log(score) = a +
    b·log(flat-equivalent speed) + c·log(hours), the speed from the race model as for the time
    prediction (the scores grow with how fast you cover the effort; c: how the site treats long
    races). Gross outliers (> 2.5 σ) left out, refitted."""
    from zachy.models import RaceResult
    lv = level_model(db)
    if not lv:
        return {}
    by_id = {p["id"]: p for p in lv["races"]}
    rows = (db.query(RaceResult).filter(RaceResult.score > 0, RaceResult.source.in_(SITES), RaceResult.dnf.isnot(True),
                                        RaceResult.activity_ids.isnot(None)).all())
    key = (len(rows), max((r.fetched_at for r in rows if r.fetched_at), default=None), lv["a"])
    if _score_cache.get("key") == key:
        return _score_cache["models"]
    models = {}
    for site in SITES:
        pts = []
        for r in rows:
            p = r.source == site and next((by_id[i] for i in json.loads(r.activity_ids or "[]") if i in by_id), None)
            if p:
                pts.append((p["level"], p["hours"], r.score))
        if len(pts) < 8:
            continue
        lvl, h, sc = (np.array(v, float) for v in zip(*pts))
        X = np.column_stack([np.ones(len(lvl)), np.log(lvl), np.log(h)])
        y = np.log(sc)
        keep = np.ones(len(y), bool)
        for _ in range(2):
            coef, *_ = np.linalg.lstsq(X[keep], y[keep], rcond=None)
            res = y - X @ coef
            keep = np.abs(res) <= 2.5 * res[keep].std()
        models[site] = {"coef": coef.tolist(), "error": float(res[keep].std()), "n": int(keep.sum())}
    _score_cache.update(key=key, models=models)
    return models


def estimate_scores(db: Session, effort_m: float, hours: float) -> dict:
    out = {}
    for site, m in score_models(db).items():
        a, b, c = m["coef"]
        out[site] = {"value": math.exp(a + b * math.log(effort_m / (hours * 3600)) + c * math.log(hours)),
                     "error_pct": round(m["error"] * 100), "n": m["n"]}
    return out


# ---------- nutrition ----------

def suggested_nutrition(hours: float) -> dict:
    """Per-hour starting points from common sports-nutrition guidance, by race length: carbs ~30-60
    g/h up to 2.5 h, 60-90 g/h beyond (what a trained gut takes); fluid ~500-750 ml/h; sodium
    ~300-600 mg/h. To adjust to what you've trained, the heat, and your sweat."""
    carbs = 40 if hours < 1.25 else 60 if hours < 2.5 else 75 if hours < 8 else 70
    return {"carbs_gh": carbs, "fluid_mlh": 600, "sodium_mgh": 450, "gel_g": 25, "flask_ml": 500}


def _nutrition(plan: RacePlan, legs: list[dict], hours: float) -> dict:
    """What to carry on each leg between aid stations, from per-hour targets (yours or suggested)."""
    saved = json.loads(plan.nutrition) if plan.nutrition else {}
    fuel = saved.pop("fuel", {})            # quantities per leg: {"leg index": {"item id": portions}}
    mine = saved or None
    n = {**suggested_nutrition(hours), **(mine or {})}
    out = []
    for leg in legs:
        h = leg["time_s"] / 3600
        carbs, fluid, sodium = n["carbs_gh"] * h, n["fluid_mlh"] * h, n["sodium_mgh"] * h
        out.append({"from_km": leg["from_km"], "to_km": leg["to_km"], "time_s": leg["time_s"],
                    "carbs_g": round(carbs), "gels": math.ceil(carbs / n["gel_g"] - 0.15) if n["gel_g"] else None,
                    "fluid_ml": round(fluid / 50) * 50, "flasks": math.ceil(fluid / n["flask_ml"] - 0.1) if n["flask_ml"] else None,
                    "sodium_mg": round(sodium / 50) * 50})
    total = {k: sum(x[k] or 0 for x in out) for k in ("carbs_g", "gels", "fluid_ml", "sodium_mg")}
    return {"targets": n, "custom": bool(mine), "suggested": suggested_nutrition(hours), "legs": out, "total": total, "fuel": fuel}


def plan_list(db: Session) -> list[dict]:
    out = []
    for p in db.query(RacePlan).order_by(RacePlan.created_at.desc()):
        c = json.loads(p.course)
        z = np.array([x[1] for x in c])
        dz = np.diff(z)
        out.append({"id": p.id, "kind": p.kind or "trail", "has_gpx": c[0][2] is not None, "name": p.name, "race_date": p.race_date.isoformat() if p.race_date else None,
                    "distance_km": c[-1][0], "gain": float(dz[dz > 0].sum()), "aid": len(json.loads(p.aid or "[]")),
                    "line": [[x[2], x[3]] for x in c[::max(1, len(c) // 120)]] if c[0][2] is not None else [],
                    "profile": [round(float(v)) for v in np.interp(np.linspace(0, c[-1][0], 60), [x[0] for x in c], z)]})
    return out


def _gpx_title(gpx: str, gpx_name: str | None) -> str:
    """The race's name: the GPX's own <name>, else the file name tidied ("utmb-2026_ccc.gpx" → "Utmb 2026 ccc")."""
    m = re.search(r"<name>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</name>", gpx, re.S)
    if m and m.group(1).strip():
        return m.group(1).strip()[:100]
    base = re.sub(r"\.gpx$", "", gpx_name or "", flags=re.I)
    base = re.sub(r"[-_]+", " ", base).strip()
    return (base[:1].upper() + base[1:])[:100] if base else "Race"


def create(db: Session, name: str, gpx_name: str | None, gpx: str | None, kind: str = "trail",
           distance_km: float | None = None) -> RacePlan:
    """Trail: the GPX is needed. Road: the GPX is optional (the course is then flat, at
    distance_km); with a GPX, its own distance counts."""
    if kind == "road":
        from zachy.analytics.road_plan import flat_course
        if gpx:
            course = course_from_gpx(gpx)            # the GPX's own distance
        elif distance_km and 0.4 <= distance_km <= 120:
            course = flat_course(distance_km)
        else:
            raise ValueError("Choose the race distance or load its GPX")
        title = name.strip() or (_gpx_title(gpx, gpx_name) if gpx else f"Road race {distance_km:g} km")
        plan = RacePlan(name=title, kind="road", gpx_name=gpx_name if gpx else None, course=json.dumps(course), aid="[]",
                        split_km=1, created_at=datetime.now())
        db.add(plan)
        db.commit()
        return plan
    if not gpx:
        raise ValueError("A trail plan needs the race's GPX")
    plan = RacePlan(name=name.strip() or _gpx_title(gpx, gpx_name), gpx_name=gpx_name, course=json.dumps(course_from_gpx(gpx)),
                    aid="[]", created_at=datetime.now())
    db.add(plan)
    db.commit()
    return plan
