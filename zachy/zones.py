"""Training zones, entered manually as absolute limits per metric, for a 3- or 5-zone model.

For each metric and model, `boundaries` holds the n-1 limits between zones, in the metric's
own unit: bpm, W, steps/min, and min/km for pace. Zone 1 is the easiest, so limits increase
for HR/power/cadence and decrease for pace (faster = smaller number). A metric with no limits
is simply not set. Use `zones_for(db, day)` to get the zones that applied on a given date.
"""

from datetime import date

from sqlalchemy.orm import Session

from zachy.models import ZoneSettings

METRICS = ["hr", "pace", "power", "cadence"]
MODELS = (3, 5)
# Pace limits get smaller as zones get harder; all other metrics get bigger.
DESCENDING = {"pace"}

ZONE_NAMES = {
    3: ["Easy", "Moderate", "Hard"],
    5: ["Recovery", "Endurance", "Tempo", "Threshold", "VO2max"],
}
# Cadence isn't an intensity: its zones are about form, around a target cadence.
CADENCE_ZONE_NAMES = {
    3: ["Low", "Target", "High"],
    5: ["Very low", "Low", "Target", "High", "Very high"],
}


def zone_names(metric: str, zone_model: int) -> list[str]:
    return (CADENCE_ZONE_NAMES if metric == "cadence" else ZONE_NAMES)[zone_model]


def defaults() -> dict:
    return {"zone_names": {m: {str(k): zone_names(m, k) for k in MODELS} for m in METRICS}}


def validate(zone_model: int, data: dict) -> dict:
    """Clean a settings payload; raises ValueError with a readable message."""
    if zone_model not in MODELS:
        raise ValueError("zone_model must be 3 or 5")
    boundaries = {}
    for m in METRICS:
        boundaries[m] = {}
        for model in MODELS:
            given = ((data.get("boundaries") or {}).get(m) or {}).get(str(model)) or []
            if all(v in (None, "") for v in given):
                boundaries[m][str(model)] = []          # metric not set for this model
                continue
            if len(given) != model - 1 or any(v in (None, "") for v in given):
                raise ValueError(f"{m}: the {model}-zone model needs all {model - 1} limits")
            values = [float(v) for v in given]
            pairs = list(zip(values, values[1:]))
            ordered = all(b < a for a, b in pairs) if m in DESCENDING else all(b > a for a, b in pairs)
            if values[0] <= 0 or not ordered:
                direction = "decrease (faster)" if m in DESCENDING else "increase"
                raise ValueError(f"{m}: limits must be positive and {direction} from zone 1 up")
            boundaries[m][str(model)] = values
    return {"boundaries": boundaries}


def settings_for(db: Session, day: date) -> ZoneSettings | None:
    """The settings in effect on `day` (latest valid_from <= day); for days before your first
    set, the earliest set (the next one) — better than no zones at all."""
    return (
        db.query(ZoneSettings)
        .filter(ZoneSettings.valid_from <= day)
        .order_by(ZoneSettings.valid_from.desc())
        .first()
    ) or db.query(ZoneSettings).order_by(ZoneSettings.valid_from).first()


def compute_zones(zone_model: int, data: dict) -> dict:
    """Zone ranges per metric that has limits set for this model.

    {"hr": [{"zone": 1, "name": "Recovery", "min": None, "max": 146}, ...], ...}
    For pace (min/km), "min" is the slow end and "max" the fast end of the zone.
    """
    out = {}
    for m in METRICS:
        cuts = data["boundaries"].get(m, {}).get(str(zone_model)) or []
        if not cuts:
            continue
        edges = [None] + cuts + [None]
        out[m] = [
            {"zone": i + 1, "name": name, "min": edges[i], "max": edges[i + 1]}
            for i, name in enumerate(zone_names(m, zone_model))
        ]
    return out


def zones_for(db: Session, day: date) -> dict | None:
    """Zones in effect on `day`, or None if no settings exist yet."""
    s = settings_for(db, day)
    if s is None:
        return None
    return {"valid_from": s.valid_from.isoformat(), "zone_model": s.zone_model,
            "zones": compute_zones(s.zone_model, s.data),
            # Both models, so a page can switch between 3 and 5 zones.
            "by_model": {str(m): compute_zones(m, s.data) for m in MODELS}}
