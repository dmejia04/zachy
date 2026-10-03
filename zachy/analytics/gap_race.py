"""Race GAP model: the slope cost curve that makes your trail races the flattest.

Idea: in a race you hold a roughly constant effort, so a good grade-adjusted pace should turn a
race into an almost flat line. Unlike the heart-rate model (gap.py, physiological cost), this
learns what a slope costs you *when racing* — walking the steep climbs, how you descend.

1. Races: trail races on mountain terrain of 1.5 to 6 h (moving), with FIT records and heart rate.
2. Segments of 200 m: slope (altitude smoothed over ~50 m), speed, mean heart rate, how far into
   the race. Left out: the first 1.5 km (start), stops and recording gaps, walking-pace-or-slower
   moments on gentle ground, segments whose heart rate is far from the race's (a queue, a fall).
3. Fit, by least squares on the logarithm of speed:
       log speed = race level + f(slope) + β·(HR − race HR)/race HR + γ·(share of the race done)
   f is a smooth curve (values every 5% of slope, joined linearly, with a penalty on bends) and
   f(0) = 0; the cost factor is exp(−f): how many flat metres one metre at that slope is worth.
   The race level absorbs each race's intensity; β and γ take out pushing harder and fatigue.
4. Checks, comparing every model (km-effort, Minetti, Strava-like, heart-rate, race):
   - flatness: per race, the spread of the grade-adjusted pace (std of its logarithm, in %),
     the race model being refitted without that race (leave one race out);
   - road check: each model's flat-equivalent distance of a race, run through Riegel from your
     best road half / marathon of the previous 12 months, against your real time: the more
     consistent (lower spread), the better the model predicts.
"""

import json
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from zachy.analytics.elevation import moving_elevation
from zachy.models import Activity, BestEffort, GapModel, Record

MODEL_ID = 2                        # gap_models row (1 = heart-rate model)
MIN_S, MAX_S = 1.5 * 3600, 6 * 3600
SEGMENT_M = 200
SKIP_START_M = 1500
KNOTS = np.arange(-0.40, 0.4001, 0.05)
SMOOTHING = 3.0                     # penalty on bends of the curve
HR_BAND = 0.12                      # segments with HR more than 12% off the race's are left out
GAP_S = 30


def candidate_races(db: Session) -> list[dict]:
    """Trail races on mountain terrain, 1.5-6 h of moving time."""
    from zachy.analytics.body import races
    return [r for r in races(db) if r["surface"] == "trail" and r["terrain"] == "mountain"
            and MIN_S <= r["duration_s"] <= MAX_S]


def segments(db: Session, activity_id: int) -> pd.DataFrame | None:
    """200 m pieces of one race: grade, speed (m/s), HR, share of the race done."""
    rows = (db.query(Record.timer_s, Record.elapsed_s, Record.distance_km, Record.elevation, Record.hr)
            .filter(Record.activity_id == activity_id).order_by(Record.id).all())
    r = pd.DataFrame(rows, columns=["t", "el", "d", "z", "hr"]).astype(float).dropna(subset=["t", "d", "z"])
    if len(r) < 600 or r.hr.notna().sum() < len(r) / 2:
        return None
    r["z"] = moving_elevation(r.z.to_numpy(), r.t.to_numpy(), r.el.to_numpy())
    r["d"] = np.maximum.accumulate(r.d.to_numpy()) * 1000
    # Recording gaps or watch pauses: where they happened (segments across them are left out).
    gap = (r.t.diff() > GAP_S) | ((r.el.diff() - r.t.diff()) > 5)
    gap_pos = r.d[gap].to_numpy()
    r = r[np.append(True, np.diff(r.d.to_numpy()) > 0)]
    d, t, z, hr = r.d.to_numpy(), r.t.to_numpy(), r.z.to_numpy(), r.hr.to_numpy()
    total = d[-1]
    if total < 5000:
        return None
    # Altitude on a 10 m grid, smoothed over ~50 m.
    grid = np.arange(d[0], d[-1], 10.0)
    zg = pd.Series(np.interp(grid, d, z)).rolling(5, center=True, min_periods=1).mean().to_numpy()
    edges = np.arange(SKIP_START_M, total - SEGMENT_M, SEGMENT_M)
    t_at = np.interp(edges, d, t)
    t_end = np.interp(edges + SEGMENT_M, d, t)
    z_at = np.interp(edges, grid, zg)
    z_end = np.interp(edges + SEGMENT_M, grid, zg)
    hr_series = pd.Series(hr, index=d).interpolate(limit_area="inside")
    out = []
    for a, ta, te, za, ze in zip(edges, t_at, t_end, z_at, z_end):
        dt = te - ta
        if dt <= 0 or ((gap_pos >= a) & (gap_pos <= a + SEGMENT_M)).any():
            continue
        h = hr_series[(hr_series.index >= a) & (hr_series.index <= a + SEGMENT_M)].mean()
        out.append({"grade": (ze - za) / SEGMENT_M, "speed": SEGMENT_M / dt, "hr": h, "done": a / total})
    s = pd.DataFrame(out).dropna()
    if s.empty:
        return None
    race_hr = s.hr.median()
    s = s[(s.grade.abs() <= 0.45) & (s.speed.between(0.25, 6.5))
          & ((s.hr / race_hr - 1).abs() <= HR_BAND)
          & ~((s.grade.abs() < 0.08) & (s.speed < 1.4))]     # standing / walking on gentle ground
    s = s.assign(hr_rel=s.hr / race_hr - 1)
    return s if len(s) >= 30 else None


def _basis(g: np.ndarray) -> np.ndarray:
    """Linear 'hat' functions at the knots: f(g) = basis @ values."""
    g = np.clip(g, KNOTS[0], KNOTS[-1])
    B = np.zeros((len(g), len(KNOTS)))
    step = KNOTS[1] - KNOTS[0]
    pos = (g - KNOTS[0]) / step
    i = np.minimum(np.floor(pos).astype(int), len(KNOTS) - 2)
    w = pos - i
    B[np.arange(len(g)), i] = 1 - w
    B[np.arange(len(g)), i + 1] += w
    return B


def fit(data: dict[int, pd.DataFrame], smoothing: float = SMOOTHING) -> dict:
    """Penalised least squares: per-race intercepts, curve values at the knots (0 at the flat),
    β (heart rate) and γ (fatigue). Returns the knot values and coefficients."""
    ids = list(data)
    df = pd.concat([d.assign(race=k) for k, d in data.items()], ignore_index=True)
    B = _basis(df.grade.to_numpy())
    zero = int(np.argmin(np.abs(KNOTS)))
    B = np.delete(B, zero, axis=1)                              # f(0) = 0
    R = (df.race.to_numpy()[:, None] == np.array(ids)[None, :]).astype(float)
    X = np.hstack([R, B, df[["hr_rel", "done"]].to_numpy()])
    y = np.log(df.speed.to_numpy())
    # Penalty on second differences of the curve (re-inserting the fixed 0 at the flat).
    nk = len(KNOTS)
    D = np.diff(np.eye(nk), n=2, axis=0)
    D = np.delete(D, zero, axis=1)
    P = np.zeros((X.shape[1], X.shape[1]))
    s = slice(len(ids), len(ids) + nk - 1)
    P[s, s] = smoothing * len(df) / 1000 * D.T @ D
    for _ in range(2):                                          # refit without gross outliers
        coef = np.linalg.solve(X.T @ X + P, X.T @ y)
        res = y - X @ coef
        keep = np.abs(res) <= 3 * res.std()
        X, y = X[keep], y[keep]
    f = np.insert(coef[s], zero, 0.0)
    return {"knots": KNOTS.tolist(), "f": f.tolist(), "beta_hr": float(coef[-2]), "gamma_fatigue": float(coef[-1]),
            "residual_pct": float(np.std(y - X @ coef) * 100)}


def cost_race(g, model: dict | None):
    """Cost factor per slope from the race model (exp(−f)); 1 on the flat."""
    if not model:
        return np.full_like(np.asarray(g, float), np.nan)
    return np.exp(-np.interp(np.clip(g, KNOTS[0], KNOTS[-1]), model["knots"], model["f"]))


def _flatness(s: pd.DataFrame, cost) -> float:
    """Spread (%) of the grade-adjusted speed over a race: std of log(speed × cost), after taking
    out (within the race, the same way for every model) what heart rate and fatigue explain —
    pushing harder or tiring isn't the slope's fault."""
    v = np.log(s.speed.to_numpy() * cost(s.grade.to_numpy()))
    X = np.column_stack([np.ones(len(s)), s.hr_rel.to_numpy(), s.done.to_numpy()])
    res = v - X @ np.linalg.lstsq(X, v, rcond=None)[0]
    return float(np.std(res) * 100)


def _road_reference(db: Session, day) -> tuple[float, float] | None:
    """Best road half or marathon effort in the 12 months before (km, seconds)."""
    best = None
    for key, km in (("half", 21.0975), ("marathon", 42.195)):
        row = (db.query(BestEffort.duration_s).join(Activity, Activity.id == BestEffort.activity_id)
               .filter(BestEffort.key == key, Activity.date.between(day - timedelta(days=365), day))
               .order_by(BestEffort.duration_s).first())
        if row:
            # compare on equal footing: Riegel-equivalent marathon time
            t42 = row[0] * (42.195 / km) ** 1.06
            if best is None or t42 < best[1]:
                best = (42.195, t42)
    return best


def evaluate_and_store(db: Session) -> dict:
    """Fit the race model on all selected races, check every model, store the result."""
    from zachy.analytics.gap import costs, personal_model
    hr_model = personal_model(db)
    races = candidate_races(db)
    data, info = {}, {}
    for r in races:
        s = segments(db, r["id"])
        if s is not None:
            data[r["id"]], info[r["id"]] = s, r
    if len(data) < 5:
        raise ValueError(f"Only {len(data)} usable races (need 5)")
    model = fit(data)

    fixed = {m: (lambda g, m=m: costs(g, hr_model)[m]) for m in ("km_effort", "minetti", "strava", "personal")}
    per_race, road = [], {m: [] for m in [*fixed, "race"]}
    for aid, s in data.items():
        loro = fit({k: v for k, v in data.items() if k != aid})        # without this race
        cost_loro = lambda g, mdl=loro: cost_race(g, mdl)
        flat = {m: round(_flatness(s, c), 2) for m, c in fixed.items()}
        flat["race"] = round(_flatness(s, cost_loro), 2)
        r = info[aid]
        row = {"id": aid, "date": r["date"], "name": r["name"], "place": r.get("place"),
               "distance_km": r["distance_km"], "elevation_gain": r["elevation_gain"], "duration_s": r["duration_s"],
               "segments": int(len(s)), "flatness": flat}
        # Road check: flat-equivalent distance (whole race profile) → Riegel from the road reference.
        ref = _road_reference(db, datetime.fromisoformat(r["date"]).date())
        if ref:
            from zachy.analytics.gap import profile
            g = profile(db, db.get(Activity, aid))
            if g is not None:
                errs = {}
                for m, c in {**fixed, "race": cost_loro}.items():
                    eq_km = r["distance_km"] * float(np.mean(c(g)))
                    predicted = ref[1] * (eq_km / ref[0]) ** 1.06
                    errs[m] = round(math.log(r["duration_s"] / predicted) * 100, 1)
                    road[m].append(errs[m])
                row["road_error"] = errs
        per_race.append(row)

    summary = {}
    for m in [*fixed, "race"]:
        f = [p["flatness"][m] for p in per_race]
        e = road[m]
        summary[m] = {"flatness_median": round(float(np.median(f)), 2),
                      "wins": int(sum(1 for p in per_race if min(p["flatness"], key=p["flatness"].get) == m)),
                      "road_bias": round(float(np.mean(e)), 1) if e else None,
                      "road_spread": round(float(np.std(e)), 1) if len(e) > 2 else None, "road_races": len(e)}
    grades = np.arange(-40, 41)
    out = {**model, "races": sorted(per_race, key=lambda p: p["date"], reverse=True), "summary": summary,
           "curve": {"grades": grades.tolist(), "factors": np.round(cost_race(grades / 100, model), 4).tolist()},
           "segments": int(sum(len(s) for s in data.values())), "n_races": len(data),
           "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    db.query(GapModel).filter(GapModel.id == MODEL_ID).delete()
    db.add(GapModel(id=MODEL_ID, data=json.dumps(out)))
    db.commit()
    return out


def race_model(db: Session) -> dict | None:
    row = db.get(GapModel, MODEL_ID)
    return json.loads(row.data) if row else None


def race_profile(db: Session, activity_id: int) -> dict | None:
    """One race's 200 m segments with the grade-adjusted pace under every model (for the chart)."""
    from zachy.analytics.gap import costs, personal_model
    s = segments(db, activity_id)
    model = race_model(db)
    if s is None or not model:
        return None
    c = costs(s.grade.to_numpy(), personal_model(db))
    c["race"] = cost_race(s.grade.to_numpy(), model)
    out = {"done": s.done.round(4).tolist(), "grade": (s.grade * 100).round(1).tolist(),
           "pace": (1000 / s.speed / 60).round(3).tolist(), "hr": s.hr.round().tolist(), "gap": {}}
    for m, cost in c.items():
        if np.isnan(cost).all():
            continue
        out["gap"][m] = (1000 / (s.speed.to_numpy() * cost) / 60).round(3).tolist()
    return out
