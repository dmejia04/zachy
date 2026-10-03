"""Race, workout or easy run: automatic guess for foot runs, from signals in your own history.

Race — any of:

1. Effort: speed in km-effort per hour (so trails compare with roads) at least 97% of your best
   for a similar effort length within a year, with average HR at least 87% of that year's max
   (max = 95th percentile of the year's runs, so a sensor spike doesn't skew it).
2. Ultra: very long (km-effort ≥ 80), or long (≥ 60) at a racing HR (≥ 70% of the year's max),
   and isolated — no other big day just before or after (multi-day trips are training).
3. Name: a race word (trail, marathon, semi, sky, "63 km"…) in a name you gave it, with no
   training word (base, threshold, fartlek, taper, spé…) — you rename workouts too.
4. Standard distance: 5 km, 10 km, half or marathon (within 3%), your single fastest at that
   distance within 6 months, at a racing HR (≥ 78% of the year's max) — unless the laps show an
   interval session.

Workout (if not a race): a workout word in the name, or a structured session — 5+ laps that are
mostly not round-km auto laps (lap button / structured workout: 400 m, 2 km, 1:00…). Trail runs
are left out of the lap rule: manual laps at aid stations or summits are common there.

Easy: everything else.

Your choice on the activity page always wins (activity_overrides.category).
"""

import re
from datetime import timedelta

from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from zachy.analytics.terrain import FOOT_TYPES
from zachy.models import Activity, Lap

RACE_TYPES = FOOT_TYPES - {"treadmill_running"}
STANDARD_DISTANCES = [("5 km", 5.0), ("10 km", 10.0), ("half marathon", 21.0975), ("marathon", 42.195)]
DEFAULT_NAME = re.compile(r"^.+ (Running|Trail Running|Ultra Running|Track Running)$")
RACE_WORDS = re.compile(r"(trail|ultra|marathon|semi|half|course|race|cross|corrida|sky|challenge|charity"
                        r"|\b\d+\s?k(m)?\b)", re.I)
# Words from your own session names (you rename training sessions as well as races).
WORKOUT_WORDS = re.compile(
    r"(vma|seuil|fractionn|interval|tempo|séance|seance|r\d+'|\d+\s?[x*]\s?\d+"
    r"|threshold|sprint|fartlek|tap+er|tap+ering|af+ut|\bsp[eé]\b|\bas\s?\d+|\bwo\b|\bwu\b|hill|c[oô]tes"
    r"|progress|allure|balay|satuc|\bs\d\b|vo2|phasing|ludique|treadmill)", re.I)
EASY_WORDS = re.compile(r"(\bbase\b|easy|recovery|r[ée]cup|footing|shake|mise.en.jambe|\bsl\b|confinement)", re.I)


def effort_km(a: Activity) -> float:
    return (a.distance_km or 0) + (a.elevation_gain or 0) / 100


def _effort_speed(a: Activity) -> float | None:
    return effort_km(a) / (a.duration_s / 3600) if a.duration_s else None


def name_signal(name: str | None) -> str | None:
    """Why the name looks like a race, or None. Garmin's default names ("<Place> Trail Running")
    contain "trail" too, so only names you changed count."""
    if not name or DEFAULT_NAME.match(name) or WORKOUT_WORDS.search(name) or EASY_WORDS.search(name):
        return None
    event = name.split(" - ", 1)[1] if " - " in name else name
    return "race name" if RACE_WORDS.search(event) else None


_max_hr_cache: dict[int, float | None] = {}


def robust_max_hr(db: Session, year: int) -> float | None:
    """The year's max HR, ignoring sensor spikes: 95th percentile of the runs' max HR."""
    if year not in _max_hr_cache:
        values = sorted(v for (v,) in db.query(Activity.max_hr)
                        .filter(Activity.activity_type.in_(RACE_TYPES), Activity.max_hr.isnot(None),
                                extract("year", Activity.date) == year))
        _max_hr_cache[year] = values[int(0.95 * (len(values) - 1))] if values else None
    return _max_hr_cache[year]


def auto_race(db: Session, a: Activity) -> tuple[bool, list[str]]:
    """(is_race, reasons) — reasons explain a race verdict."""
    if a.activity_type not in RACE_TYPES or not a.distance_km or a.distance_km < 5 or not a.duration_s:
        return False, []
    reasons = []

    named = name_signal(a.name)
    if named:
        reasons.append(named)
    if a.name and (WORKOUT_WORDS.search(a.name) or EASY_WORDS.search(a.name)):
        return False, []   # a named training session (VMA, 3x400, Base…) is not a race even if fast

    # Effort vs your best for a similar effort length, ±1 year.
    e, speed = effort_km(a), _effort_speed(a)
    window = (
        db.query(Activity)
        .filter(Activity.activity_type.in_(RACE_TYPES), Activity.duration_s > 0,
                Activity.date.between(a.date - timedelta(days=365), a.date + timedelta(days=365)))
        .all()
    )
    similar = [x for x in window if 0.8 * e <= effort_km(x) <= 1.25 * e]
    best = max((_effort_speed(x) or 0) for x in similar) if similar else None
    year_max_hr = robust_max_hr(db, a.date.year)
    rel_hr = a.avg_hr / year_max_hr if a.avg_hr and year_max_hr else None
    if best and speed and rel_hr and speed / best >= 0.97 and rel_hr >= 0.87:
        reasons.append(f"effort {speed / best:.0%} of your best, HR {rel_hr:.0%} of max")

    # Standard road distance run near your best pace for it (that year).
    for label, target in STANDARD_DISTANCES:
        if abs(a.distance_km - target) / target > 0.03:
            continue
        same = [x for x in window if x.distance_km and abs(x.distance_km - target) / target <= 0.03
                and abs((x.date - a.date).days) <= 182]
        best_pace = min(x.duration_s / x.distance_km for x in same)
        pace = a.duration_s / a.distance_km
        racing_hr = rel_hr is None or rel_hr >= 0.78
        if pace <= best_pace and racing_hr and not structured_laps(db, a):
            reasons.append(f"your fastest {label} in 6 months")
        break

    # Ultra: long and isolated (no other big day the day before or after).
    if e >= 60:
        neighbours = [x for x in window
                      if x.id != a.id and abs((x.date - a.date).days) <= 1 and effort_km(x) >= 30]
        if not neighbours and (e >= 80 or (rel_hr and rel_hr >= 0.70)):
            reasons.append(f"ultra effort ({e:.0f} km-effort), not part of a multi-day trip")

    return bool(reasons), reasons


CATEGORIES = ("race", "workout", "easy")


def structured_laps(db: Session, a: Activity, min_laps: int = 5) -> bool:
    """Mostly irregular laps (not round-km auto laps): a structured / interval session."""
    laps = db.query(Lap.distance_km).filter(Lap.activity_id == a.id).order_by(Lap.lap_number).all()
    laps = [d for (d,) in laps][:-1]   # the last lap is usually a leftover
    if len(laps) < min_laps - 1:
        return False
    auto = [d for d in laps if d and round(d) >= 1 and abs(d - round(d)) < 0.02]
    return len(auto) / len(laps) < 0.5


def auto_category(db: Session, a: Activity) -> tuple[str, list[str]]:
    """("race" | "workout" | "easy", reasons)."""
    is_race, reasons = auto_race(db, a)
    if is_race:
        return "race", reasons
    if a.name and not DEFAULT_NAME.match(a.name):
        if EASY_WORDS.search(a.name):
            return "easy", ["easy-run name"]
        if WORKOUT_WORDS.search(a.name):
            return "workout", ["workout name"]
    if a.activity_type not in ("trail_running", "ultra_run") and structured_laps(db, a):
        return "workout", ["structured laps (intervals or workout steps)"]
    return "easy", []


def category_status(db: Session, a: Activity, override: str | None) -> dict | None:
    if a.activity_type not in FOOT_TYPES:
        return None
    auto, reasons = auto_category(db, a)
    if override in CATEGORIES:
        return {"category": override, "auto": auto, "source": "manual", "reasons": reasons}
    return {"category": auto, "auto": auto, "source": "auto", "reasons": reasons}
