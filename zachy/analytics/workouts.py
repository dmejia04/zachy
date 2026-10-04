"""Describe a workout from its laps: "Intervals · 12 × 300 m r 0:55 @ 3:05/km".

Laps come from the original FIT file. Two cases:
- A structured workout run from the watch: laps are labelled warm-up / active / recovery /
  cool-down (and the planned steps are in the file), so the roles are known.
- Lap-button sessions: every lap is just "interval", so roles are inferred — reps are the fast
  laps, recoveries the slow ones between them, warm-up / cool-down the slow laps around the set.

Reps are described by distance or by time, whichever is more regular, rounded so lap-button lag
doesn't matter (401 m -> 400 m, 2:02 -> 2:00). Repeating patterns are grouped ("2 × (30/45/60 s)").
Type: Hills (reps climb, recoveries come back down), Fartlek (time reps), Intervals (distance
reps), Tempo (8 min+ hard blocks), Progressive (each lap faster than the last). "Track …" when
the reps were run on an athletics track: their GPS points stay inside a ~170 × 90 m oval.
"""

import io
import warnings

import fitdecode
import numpy as np

from sqlalchemy.orm import Session

from zachy.models import Activity, ActivityOverride, FitFile, Record, WorkoutSummary
from zachy.services.fit import fit_bytes_from_zip, zip_path

WORK, REST, WARMUP, COOLDOWN = "work", "rest", "warmup", "cooldown"
FIT_ROLES = {"active": WORK, "interval": None, "recovery": REST, "rest": REST,
             "warmup": WARMUP, "cooldown": COOLDOWN}
MIN_SPEED_RATIO = 1.2      # fast laps must be clearly faster than slow ones to call it intervals
HILL_GRADE = 0.04          # reps climbing 4%+ on average, with recoveries going back down
TRACK_P90_M = 85           # on a track, 90% of the reps' GPS points lie within ~85 m of the centre
TRACK_MAX_M = 100          # … and none further than ~100 m (a 400 m road rep reaches far beyond)


# ---------- reading ----------

def read_fit_laps(garmin_id: str) -> tuple[list[dict], str | None]:
    """Laps (distance m, time s, speed m/s, ascent/descent m, FIT role, planned step label) and
    the workout name. When the lap follows a planned step of fixed time or distance, "planned" is
    that target ("30 s", "1 km"), which is exact where measured laps carry lap-button lag."""
    data = fit_bytes_from_zip(zip_path(garmin_id).read_bytes())
    laps, name, steps = [], None, {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with fitdecode.FitReader(io.BytesIO(data)) as reader:
            for frame in reader:
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue
                if frame.name == "lap":
                    v = lambda k: frame.get_value(k, fallback=None)
                    dist, secs = v("total_distance") or 0.0, v("total_timer_time") or 0.0
                    laps.append({
                        "distance": float(dist), "time": float(secs),
                        "speed": float(dist / secs) if secs else 0.0,
                        "ascent": float(v("total_ascent") or 0), "descent": float(v("total_descent") or 0),
                        "fit_role": FIT_ROLES.get(str(v("intensity")) if v("intensity") is not None else "interval"),
                        "step": v("wkt_step_index"),
                    })
                elif frame.name == "workout_step":
                    v = lambda k: frame.get_value(k, fallback=None)
                    kind = str(v("duration_type"))
                    if kind == "time" and v("duration_time"):
                        steps[v("message_index")] = fmt_time(float(v("duration_time")))
                    elif kind == "distance" and v("duration_distance"):
                        steps[v("message_index")] = fmt_distance(round_distance(float(v("duration_distance"))))
                elif frame.name == "workout":
                    name = frame.get_value("wkt_name", fallback=None)
    for lap in laps:
        lap["planned"] = steps.get(lap.pop("step"))
    return laps, name


# ---------- rounding & formatting ----------

def round_time(s: float) -> float:
    step = 5 if s <= 60 else 15 if s <= 300 else 30 if s <= 600 else 60
    return max(step, round(s / step) * step)


def round_distance(m: float) -> float:
    step = 50 if m < 1000 else 100 if m < 5000 else 500
    return max(step, round(m / step) * step)


def fmt_time(s: float) -> str:
    s = int(round(s))
    if s < 60:
        return f"{s} s"
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def fmt_distance(m: float) -> str:
    return f"{m / 1000:g} km" if m >= 1000 else f"{int(m)} m"


def fmt_pace(speed: float) -> str:
    if speed <= 0:
        return "—"
    total = round(1000 / speed)
    return f"{total // 60}:{total % 60:02d}/km"


def _cv(values) -> float:
    a = np.asarray(values, float)
    return float(a.std() / a.mean()) if len(a) and a.mean() > 0 else 9.9


def _repeats(labels: list[str]) -> str | None:
    """The whole list as k copies of a block: "12 × 300 m", "2 × (3:00/2:00/1:00)"."""
    n = len(labels)
    for p in range(1, n // 2 + 1):
        if n % p == 0 and labels == labels[:p] * (n // p):
            block = labels[:p]
            return f"{n // p} × {block[0]}" if p == 1 else f"{n // p} × ({'/'.join(block)})"
    return None


def group(labels: list[str]) -> str:
    """["300 m"] * 12 -> "12 × 300 m"; a repeating block, possibly after a different start ->
    "8:00 + 2 × (3:00/2:00/1:00)"; else run-length "3:00 + 5:00 + 3 × 4:00"."""
    n = len(labels)
    whole = _repeats(labels)
    if whole:
        return whole
    for start in range(1, n - 3):   # a prefix, then a block repeated 2+ times to the end
        rest = _repeats(labels[start:])
        if rest and not rest.startswith(f"{n - start} × "):   # repeated block, not just run-length
            return " + ".join(labels[:start]) + " + " + rest
    out, i = [], 0   # run-length: "3 × 1 km + 4 × 400 m"
    while i < n:
        j = i
        while j < n and labels[j] == labels[i]:
            j += 1
        out.append(f"{j - i} × {labels[i]}" if j - i > 1 else labels[i])
        i = j
    return " + ".join(out)


# ---------- roles ----------

def assign_roles(laps: list[dict]) -> list[str] | None:
    """Role per lap, from the watch when it labelled them, else inferred from lap speed."""
    if any(l["fit_role"] for l in laps):
        return [l["fit_role"] or WORK for l in laps]
    usable = [l for l in laps if l["time"] >= 10 and l["speed"] > 0]
    if len(usable) < 3:
        return None
    # Two speed groups (1-D k-means): reps are the fast group.
    speeds = np.array([l["speed"] for l in usable])
    lo, hi = speeds.min(), speeds.max()
    for _ in range(20):
        cut = (lo + hi) / 2
        fast, slow = speeds[speeds >= cut], speeds[speeds < cut]
        if not len(fast) or not len(slow):
            return None
        lo, hi = slow.mean(), fast.mean()
    if hi / lo < MIN_SPEED_RATIO:
        return None
    cut = (lo + hi) / 2
    roles = [WORK if l["speed"] >= cut and l["time"] >= 10 else REST for l in laps]
    first = roles.index(WORK)
    last = len(roles) - 1 - roles[::-1].index(WORK)
    for i in range(first):
        roles[i] = WARMUP
    for i in range(last + 1, len(roles)):
        roles[i] = COOLDOWN
    return roles


def progressive_laps(laps: list[dict]) -> list[dict]:
    """Laps of about the same length (a short leftover lap at the end is ignored)."""
    median = float(np.median([l["time"] for l in laps]))
    return [l for l in laps if l["time"] >= 0.5 * median and l["time"] >= 60]


def is_progressive(laps: list[dict]) -> bool:
    main = progressive_laps(laps)
    if len(main) < 4:
        return False
    speeds = [l["speed"] for l in main]
    return all(b > a for a, b in zip(speeds, speeds[1:])) and speeds[-1] / speeds[0] >= 1.1


# ---------- description ----------

def reps_on_track(laps: list[dict], roles: list[str], positions) -> bool:
    """positions: rows of (timer_s, lat, lon). True if the reps stayed inside a track-sized oval."""
    if positions is None or not len(positions):
        return False
    pos = np.asarray(positions, float)
    times = np.array([l["time"] for l in laps])
    ends = np.cumsum(times)
    mask = np.zeros(len(pos), bool)
    for start, end, role in zip(ends - times, ends, roles):
        if role == WORK:
            mask |= (pos[:, 0] >= start) & (pos[:, 0] < end)
    work_distance = sum(l["distance"] for l, r in zip(laps, roles) if r == WORK)
    if mask.sum() < 30 or work_distance < 800:
        return False
    p = pos[mask]
    lat0, lon0 = p[:, 1].mean(), p[:, 2].mean()
    x = (p[:, 2] - lon0) * np.cos(np.radians(lat0)) * 111_320
    y = (p[:, 1] - lat0) * 111_320
    r = np.hypot(x, y)
    return np.percentile(r, 90) <= TRACK_P90_M and r.max() <= TRACK_MAX_M


def describe(laps: list[dict], watch_name: str | None = None, positions=None,
             track_activity: bool = False) -> dict | None:
    """{"type", "summary", "warmup", "cooldown", "source", "watch_name", "on_track"} or None."""
    if len(laps) < 3:
        return None
    source = "watch workout" if any(l["fit_role"] for l in laps) else "laps"

    if source == "laps" and is_progressive(laps):
        main = progressive_laps(laps)
        times = [round_time(l["time"]) for l in main]
        reps = group([fmt_time(t) for t in times]) if _cv(times) < 0.1 else f"{len(main)} laps"
        return {"type": "Progressive", "summary": f"{reps}, {fmt_pace(main[0]['speed'])} → {fmt_pace(main[-1]['speed'])}",
                "warmup": None, "cooldown": None, "source": source, "watch_name": watch_name, "on_track": False}

    roles = assign_roles(laps)
    if not roles or WORK not in roles:
        return None
    work = [l for l, r in zip(laps, roles) if r == WORK]
    rest = [l for l, r in zip(laps, roles) if r == REST]
    wu = sum(l["distance"] for l, r in zip(laps, roles) if r == WARMUP)
    cd = sum(l["distance"] for l, r in zip(laps, roles) if r == COOLDOWN)
    work_speed = sum(l["distance"] for l in work) / max(1, sum(l["time"] for l in work))

    # Reps: the planned step targets when the watch has them, else distance or time — whichever
    # is more regular. Too varied to list (strides, mixed sessions) -> a range.
    by_distance = _cv([l["distance"] for l in work]) <= _cv([l["time"] for l in work])
    if all(l.get("planned") for l in work):
        labels = [l["planned"] for l in work]
        by_distance = labels[0].endswith("m")
    else:
        labels = [fmt_distance(round_distance(l["distance"])) if by_distance else fmt_time(round_time(l["time"]))
                  for l in work]
    reps = group(labels)
    planned = all(l.get("planned") for l in work)
    if reps.count("+") >= (9 if planned else 3):   # exact planned steps can be listed further
        values = [l["distance"] if by_distance else l["time"] for l in work]
        fmt = (lambda v: fmt_distance(round_distance(v))) if by_distance else (lambda v: fmt_time(round_time(v)))
        reps = f"{len(work)} reps of {fmt(min(values))}–{fmt(max(values))}"


    # Recovery: average, as distance if that's the regular part (e.g. 400 m float), else time.
    rest_text = ""
    if rest:
        if _cv([l["distance"] for l in rest]) < _cv([l["time"] for l in rest]) and np.mean([l["distance"] for l in rest]) >= 200:
            rest_text = f" r {fmt_distance(round_distance(np.mean([l['distance'] for l in rest])))}"
        else:
            rest_text = f" r {fmt_time(round_time(np.mean([l['time'] for l in rest])))}"
        rest_speed = sum(l["distance"] for l in rest) / max(1, sum(l["time"] for l in rest))
        if rest_speed < 0.5:
            rest_text += " standing"

    # Type. On a track it's never hills (any "climb" there is altimeter noise).
    on_track = track_activity or reps_on_track(laps, roles, positions)
    climb = sum(l["ascent"] for l in work) / max(1, sum(l["distance"] for l in work))
    comes_down = sum(l["descent"] for l in rest) >= 0.5 * sum(l["ascent"] for l in work) if rest else False
    per_rep = sum(l["ascent"] for l in work) / len(work)
    if not on_track and climb >= HILL_GRADE and per_rep >= 8 and comes_down:
        kind = "Hills"
        summary = f"{reps} up (+{per_rep:.0f} m each, {climb:.0%}){rest_text}, jog down"
    else:
        if all(l["time"] >= 480 for l in work):   # 8 min+ hard blocks
            kind = "Tempo"
        else:
            kind = "Intervals" if by_distance else "Fartlek"
        summary = f"{reps}{rest_text} @ {fmt_pace(work_speed)}"
        if on_track:
            kind = f"Track {kind.lower()}"
    return {
        "type": kind, "summary": summary, "on_track": on_track,
        "warmup": fmt_distance(round_distance(wu)) if wu >= 300 else None,
        "cooldown": fmt_distance(round_distance(cd)) if cd >= 300 else None,
        "source": source, "watch_name": watch_name,
    }


def describe_activity(db: Session, activity: Activity) -> dict | None:
    laps, name = read_fit_laps(activity.garmin_id)
    positions = (db.query(Record.timer_s, Record.latitude, Record.longitude)
                 .filter(Record.activity_id == activity.id, Record.latitude.isnot(None)).all())
    return describe(laps, name, positions, track_activity=activity.activity_type == "track_running")


def cached_workout(db: Session, activity: Activity) -> dict | None:
    """describe_activity(), computed once per activity and kept in workout_summaries; with your own
    title / description instead when you've written one (activity_overrides)."""
    auto = _guessed_workout(db, activity)
    o = db.get(ActivityOverride, activity.id)
    if not o or not (o.workout_type or o.workout_summary):
        return auto
    base = auto or {"warmup": None, "cooldown": None, "source": None, "watch_name": None, "on_track": False}
    return {**base, "type": o.workout_type or (auto or {}).get("type") or "Workout", "summary": o.workout_summary or "",
            "manual": True, "auto": {"type": auto["type"], "summary": auto["summary"]} if auto else None}


def _guessed_workout(db: Session, activity: Activity) -> dict | None:
    row = db.get(WorkoutSummary, activity.id)
    if row is None or (row.structured and row.on_track is None):   # missing, or from before track detection
        fit = db.get(FitFile, activity.id)
        if not fit or fit.status != "ok":
            return None
        d = describe_activity(db, activity)
        if d:
            d["on_track"] = int(d["on_track"])
        row = WorkoutSummary(activity_id=activity.id, structured=1 if d else 0, **(d or {}))
        db.merge(row)
        db.commit()
        row = db.get(WorkoutSummary, activity.id)
    if not row.structured:
        return None
    out = {c: getattr(row, c) for c in ("type", "summary", "warmup", "cooldown", "source", "watch_name")}
    return {**out, "on_track": bool(row.on_track)}
