"""FFA performance levels (barèmes, men, all ages from juniors to masters) for road races.

The French athletics federation ranks every performance from IA (international) through
N1-N4 (national), IR1-IR4 (inter-regional), R1-R6 (regional) to D1-D7 (departmental); each
level is a time to beat for its distance. Table: men's barème as published by the FFA
("Barème Hommes au 1er janvier 2010", still the reference grid). Colombia has no such grid.

A road race gets the level of its nominal distance (5 km, 10 km, 15 km, 20 km, half, marathon)
when the GPS distance is within a few percent; the time is the official one when known, else
the watch's elapsed time (FFA levels need a measured, labelled course: this is indicative).
"""

LEVELS = [("IA", 40), ("IB", 35), ("N1", 30), ("N2", 28), ("N3", 26), ("N4", 24), ("IR1", 21), ("IR2", 20),
          ("IR3", 19), ("IR4", 18), ("R1", 15), ("R2", 14), ("R3", 13), ("R4", 12), ("R5", 11), ("R6", 10),
          ("D1", 8), ("D2", 7), ("D3", 6), ("D4", 5), ("D5", 4), ("D6", 3), ("D7", 2)]


def _t(s: str) -> int:
    h, rest = (s.split("h") if "h" in s else ("0", s))
    m, sec = rest.split("'")
    return int(h) * 3600 + int(m) * 60 + int(sec or 0)


_GRID = {
    "5k": "13'10 13'25 13'45 14'00 14'20 14'40 15'00 15'20 15'40 16'00 16'20 16'40 17'00 17'20 17'40 18'00 18'20 18'40 19'10 19'40 20'10 21'00 22'00",
    "10k": "27'50 28'15 29'00 29'45 30'30 31'15 32'00 32'45 33'30 34'15 35'00 35'45 36'30 37'15 38'00 38'45 39'30 40'30 41'30 43'00 45'00 47'00 50'00",
    "15k": "43'30 44'00 44'30 45'40 46'50 48'00 49'10 50'20 51'30 52'40 53'50 55'00 56'10 57'20 58'30 59'45 1h01'00 1h03'00 1h05'00 1h07'00 1h11'00 1h14'00 1h18'00",
    "20k": "58'30 59'15 1h01'00 1h02'15 1h03'30 1h05'00 1h07'00 1h08'30 1h10'00 1h11'30 1h13'00 1h14'30 1h16'00 1h18'00 1h19'30 1h22'30 1h25'30 1h29'00 1h32'00 1h36'00 1h40'00 1h45'00 1h50'00",
    "half": "1h01'30 1h02'15 1h04'00 1h05'30 1h07'00 1h08'30 1h10'30 1h12'30 1h14'00 1h15'30 1h17'30 1h19'00 1h20'30 1h22'30 1h24'00 1h27'00 1h30'00 1h34'00 1h37'00 1h41'00 1h45'00 1h50'00 1h55'00",
    "marathon": "2h10'00 2h12'30 2h15'00 2h20'00 2h24'00 2h28'00 2h32'00 2h36'00 2h40'00 2h44'00 2h48'00 2h53'00 2h58'00 3h03'00 3h08'00 3h14'00 3h20'00 3h26'00 3h34'00 3h42'00 3h50'00 4h00'00 4h10'00",
}
GRID = {k: [_t(x) for x in v.split()] for k, v in _GRID.items()}
DISTANCES = {"5k": 5.0, "10k": 10.0, "15k": 15.0, "20k": 20.0, "half": 21.0975, "marathon": 42.195}
LABELS = {"5k": "5 km", "10k": "10 km", "15k": "15 km", "20k": "20 km", "half": "half marathon", "marathon": "marathon"}
TOLERANCE = 0.04   # GPS distance within 4% of the nominal one


def distance_key(km: float) -> str | None:
    return next((k for k, d in DISTANCES.items() if abs(km / d - 1) <= TOLERANCE), None)


def level(key: str, seconds: float) -> dict:
    """{"level", "points", "limit_s", "next": {"level", "limit_s", "gap_s"}} — level None below D7."""
    limits = GRID[key]
    i = next((i for i, lim in enumerate(limits) if seconds <= lim), None)
    out = {"distance": key, "label": LABELS[key], "time_s": round(seconds),
           "level": LEVELS[i][0] if i is not None else None, "points": LEVELS[i][1] if i is not None else 0,
           "limit_s": limits[i] if i is not None else None}
    j = (i if i is not None else len(limits)) - 1   # the next level up
    if j >= 0:
        out["next"] = {"level": LEVELS[j][0], "limit_s": limits[j], "gap_s": round(seconds - limits[j])}
    return out


def table() -> dict:
    return {"levels": [{"level": l, "points": p} for l, p in LEVELS], "grid": GRID, "labels": LABELS}
