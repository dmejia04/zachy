"""Road race plans: the predicted time from your critical speed, splits every 1, 2, 5 or 10 km.

Your level comes from your road races, as in the Paces tab: by default your best flat race at each
standard distance (you can take races off and add others there). Through the ones lasting 2–90 min
runs the critical-speed line, distance = CS × time + D′, so a race of distance D takes
(D − D′) / CS. Past one hour the line gets too optimistic (fuel, fatigue: a marathon falls below
it), so from there on the time grows as Riegel's (D / D₆₀)^k, k fitted on the same races (≈ 1.06).

With a GPX, the slopes count as in trail plans: each 25 m costs its slope's factor from your race
model (1 on the flat), at the same flat speed. Without one, the course is flat and there is no map.

The outlook scales the time (±10%); the strategy moves time between the start and the end at the
same total, as in trail plans but gentler (at the ends, halves ~6% apart; 0 = even pace).
"""

import json
import math
import time

import numpy as np
from sqlalchemy.orm import Session

from zachy.analytics.gap_race import cost_race, race_model
from zachy.models import Profile, RacePlan

STANDARD_M = (1500, 1609.34, 3000, 5000, 10000, 15000, 16093.4, 20000, 21097.5, 25000, 30000, 42195)
FFA_M = {"5k": 5000, "10k": 10000, "15k": 15000, "20k": 20000, "half": 21097.5, "marathon": 42195}
CS_MIN_S, CS_MAX_S = 120, 90 * 60
ROAD_STRATEGY_K = 0.12
ROAD_OUTLOOK = 10
SPLITS_KM = (1, 2, 5, 10)


def road_nutrition_suggested(hours: float) -> dict:
    """Starting points for a road race (sports-nutrition guidance): under an hour nothing is
    needed; up to ~1¼ h a little (or a mouth rinse); 60 g/h up to 2½ h, 75 g/h beyond. Fluid from
    the aid stations' cups, ~400–600 ml/h (more in the heat)."""
    carbs = 0 if hours < 1 else 30 if hours < 1.25 else 60 if hours < 2.5 else 75
    return {"carbs_gh": carbs, "fluid_mlh": 0 if hours < 1 else 500}
_fit_cache: dict = {}


def road_races(db: Session) -> list[dict]:
    """Your road races at the standard distances (official time when known, else the watch's)."""
    # As the races page, but only the road races' tracks are read (the whole list is slow cold).
    from zachy.analytics.race_page import _ffa, _track, races
    from zachy.analytics.race_results import results_by_activity
    official = results_by_activity(db)
    out = []
    for r in races(db):
        if r.get("surface") in ("trail", "cross"):
            continue
        mine = official.get(r["id"], {})
        r["result"] = next((mine[s] for s in ("utmb", "betrail", "itra", "manual") if s in mine), None)
        ffa = _ffa(r, _track(db, r["id"])) or {}
        d = FFA_M.get(ffa.get("distance")) or next((s for s in STANDARD_M if abs(r["distance_km"] * 1000 / s - 1) <= 0.03), None)
        t = (r.get("result") or {}).get("time_s") or ffa.get("time_s") or r.get("duration_s")
        if d and t:
            out.append({"id": r["id"], "d": d, "t": float(t), "date": r["date"], "flat": not r.get("terrain") or r["terrain"] == "flat"})
    return out


def chart_races(db: Session) -> list[dict]:
    """The Paces chart's races: your best flat race per distance, minus the ones you took off, plus the ones you added."""
    p = db.get(Profile, 1)
    sel = json.loads(p.pace_races_json) if p and p.pace_races_json else {}
    added, hidden = set(sel.get("added", [])), set(sel.get("hidden", []))
    allr = road_races(db)
    best: dict[float, dict] = {}
    for x in allr:
        if x["flat"] and (x["d"] not in best or x["t"] < best[x["d"]]["t"]):
            best[x["d"]] = x
    ids = {x["id"] for x in best.values()}
    return [x for x in best.values() if x["id"] not in hidden] + [x for x in allr if x["id"] in added and x["id"] not in ids]


def road_fit(db: Session) -> dict | None:
    """Critical speed and D′ (races of 2–90 min), Riegel k (all the chart's races), and how far
    the races land from the prediction (spread of log time)."""
    if _fit_cache.get("at", 0) > time.time() - 120:
        return _fit_cache["fit"]
    races = chart_races(db)
    fit = None
    cs_races = [r for r in races if CS_MIN_S <= r["t"] <= CS_MAX_S]
    if len(cs_races) >= 2 and len({r["d"] for r in cs_races}) >= 2:
        t = np.array([r["t"] for r in cs_races]); d = np.array([r["d"] for r in cs_races])
        cs, dprime = np.polyfit(t, d, 1)
        if cs > 0:
            k = 1.06
            if len({r["d"] for r in races}) >= 2:
                k = float(np.polyfit(np.log([r["d"] for r in races]), np.log([r["t"] for r in races]), 1)[0])
                k = min(max(k, 1.03), 1.15)
            fit = {"cs": float(cs), "dprime": float(dprime), "k": k, "n": len(cs_races), "n_races": len(races),
                   "shortest_s": float(t.min()), "longest_s": float(t.max())}
            res = [math.log(r["t"] / flat_time(fit, r["d"])) for r in races]
            fit["spread"] = float(np.std(res)) if len(res) > 2 else 0.03
            fit["spread"] = min(max(fit["spread"], 0.02), 0.08)
    _fit_cache.update(at=time.time(), fit=fit)
    return fit


def flat_time(fit: dict, d: float) -> float:
    """Seconds for d metres on the flat: the critical-speed line up to one hour, Riegel beyond."""
    d60 = fit["cs"] * 3600 + fit["dprime"]
    if d <= d60:
        return max(d - fit["dprime"], d * 0.25) / fit["cs"]
    return 3600 * (d / d60) ** fit["k"]


def flat_course(distance_km: float) -> list[list]:
    """A course without GPX: flat, no map."""
    n = int(distance_km * 1000 // 25)
    km = [round(i * 0.025, 4) for i in range(n + 1)]
    if km[-1] < distance_km:
        km.append(round(distance_km, 4))
    return [[k, 0.0, None, None] for k in km]


def predict_road(db: Session, plan: RacePlan) -> dict:
    from zachy.analytics.planner import _slopes, sections
    course = json.loads(plan.course)
    km = np.array([c[0] for c in course]); z = np.array([c[1] for c in course])
    has_gpx = course[0][2] is not None
    dist_m = float(km[-1]) * 1000
    fit = road_fit(db)
    if plan.flat_pace:
        flat_s, source = plan.flat_pace * 60 * km[-1], "yours"
    elif fit:
        flat_s, source = flat_time(fit, dist_m), "critical speed"
    else:
        raise ValueError("Needs two road races of 2–90 min at different distances (Paces tab), or type your target pace")
    step = np.diff(km, append=km[-1]) * 1000
    model = race_model(db) if has_gpx else None
    cost = cost_race(np.clip(_slopes(km, z), -0.45, 0.45), model) if model else np.ones(len(km))
    steps = step * np.nan_to_num(cost, nan=1.0)
    effort = float(steps.sum())
    base = flat_s * effort / dist_m                       # the same flat speed over the slopes
    moving = base * (1 + (plan.outlook or 0) / 100)
    dt = steps * moving / effort
    k = -ROAD_STRATEGY_K * (plan.strategy or 0)
    if k:
        w = dt * np.exp(k * (km / km[-1] - 0.5))
        dt = w * dt.sum() / w.sum()
    t_cum = np.concatenate([[0], np.cumsum(dt[:-1])])
    at = lambda x: float(np.interp(x, km, t_cum))
    split = plan.split_km if plan.split_km in SPLITS_KM else 1
    marks = list(np.arange(0, km[-1], split)) + [float(km[-1])]
    if len(marks) > 2 and marks[-1] - marks[-2] < 0.05:
        marks.pop(-2)
    splits = []
    for a, b in zip(marks, marks[1:]):
        m = (km >= a) & (km <= b)
        dz = np.diff(z[m]) if m.sum() > 1 else np.array([0.0])
        splits.append({"from_km": float(a), "to_km": float(b), "km": float(b - a), "time_s": at(b) - at(a), "at_s": at(b),
                       "pace": (at(b) - at(a)) / 60 / (b - a), "gain": float(dz[dz > 0].sum()), "loss": float(-dz[dz < 0].sum())})
    half = at(km[-1] / 2)
    dz = np.diff(z)
    thin = max(1, len(course) // 800)
    return {
        "id": plan.id, "kind": "road", "name": plan.name, "race_date": plan.race_date.isoformat() if plan.race_date else None,
        "gpx_name": plan.gpx_name, "has_gpx": has_gpx, "distance_km": float(km[-1]),
        "gain": float(dz[dz > 0].sum()), "loss": float(-dz[dz < 0].sum()), "alt_min": float(z.min()), "alt_max": float(z.max()),
        "effort_km": effort / 1000, "moving_s": moving, "stops_s": 0, "total_s": moving,
        "outlook": plan.outlook or 0, "strategy": plan.strategy or 0, "outlook_max": ROAD_OUTLOOK,
        "base": {"moving_s": base, "total_s": base, "pace": base / 60 / km[-1], "flat_s": flat_s, "flat_pace": flat_s / 60 / km[-1]},
        "pace": moving / 60 / km[-1], "flat_pace": moving / 60 / (effort / 1000),
        "halves": {"first_s": half, "second_s": moving - half},
        "level": {"source": source, "flat_pace_set": plan.flat_pace, "spread": fit["spread"] if fit and source != "yours" else None,
                  **({k2: fit[k2] for k2 in ("cs", "dprime", "k", "n", "n_races", "shortest_s")} if fit else {})},
        "nutrition": _road_nutrition(plan, moving / 3600),
        "split_km": split, "splits": splits, "start_time": plan.start_time,
        "aid": [], "legs": [],
        "sections": [{**{k: v for k, v in x.items() if k not in ("start", "end")}, "time_s": at(x["end_km"]) - at(x["start_km"]),
                      "pace": (at(x["end_km"]) - at(x["start_km"])) / 60 / x["km"] if x["km"] else None}
                     for x in sections(km, z, _slopes(km, z))] if has_gpx else [],
        "profile": [[c[0], c[1]] for c in course[::thin]] + [[course[-1][0], course[-1][1]]] if has_gpx else [],
        "line": [[c[2], c[3]] for c in course[::max(1, len(course) // 400)]] if has_gpx else [],
    }


def _road_nutrition(plan: RacePlan, hours: float) -> dict:
    """Your targets (or the suggested ones) and the products you picked from the fuel library;
    the page spreads them over the race."""
    saved = json.loads(plan.nutrition) if plan.nutrition else {}
    items = saved.pop("items", [])
    saved.pop("fuel", None)
    mine = {k: saved[k] for k in ("carbs_gh", "fluid_mlh") if k in saved}
    sug = road_nutrition_suggested(hours)
    return {"targets": {**sug, **mine}, "suggested": sug, "custom": bool(mine), "items": items}
