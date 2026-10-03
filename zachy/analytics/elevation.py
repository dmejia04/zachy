"""Altimeter clean-up shared by every calculation on the FIT altitude.

Nobody climbs at 5 m/s, so altitude changing faster than that (ALTITUDE_JUMP_MS) is a sensor
artefact. A burst of such steps (consecutive samples) is one jump, handled one of two ways:
- catch-up: the altitude was stuck (unchanged, or not recorded) before the jump, long enough for
  the change to be plausible when spread over that stretch — the altimeter froze and then
  caught up. The change is spread over the stuck stretch (linear interpolation);
- recalibration: otherwise (e.g. +40 m in the first second of a ride, while the altimeter
  converges to the GPS altitude). The step is dropped and the profile rebuilt from the
  remaining changes.
The rebuilt profile is then placed at the level that agrees with the raw altitude most of the
time (median offset), so a leftover error stays on the short side of the track.

Jumps are measured in elapsed time: a big change across a pause is real — you resumed somewhere
else (a ski lift, a shuttle back up the hill) — and is kept in the altitude itself (profile,
highest / lowest point). For climbing done while moving (gain / loss, slopes, vertical speed),
moving_elevation() then also takes out what changed during pauses, so a lift ride isn't counted
as a climb. A clean track comes back unchanged.
"""

import numpy as np

ALTITUDE_JUMP_MS = 5.0   # altitude changing faster than 5 m/s = altimeter artefact
STUCK_M = 0.5            # consecutive samples within this are "the altimeter didn't move"


def remove_jumps(elevation, time_s) -> np.ndarray:
    """`elevation` with altimeter jumps (faster than ALTITUDE_JUMP_MS per `time_s`) repaired.
    Missing values stay missing."""
    z = np.array(elevation, dtype=float)
    ok = np.isfinite(z)
    if ok.sum() < 2:
        return z
    zs = z[ok]
    dt = np.diff(np.asarray(time_s, dtype=float)[ok])
    dt = np.where(np.isfinite(dt), np.maximum(dt, 1.0), 1.0)   # duplicate / reset timestamps
    clock = np.concatenate([[0.0], np.cumsum(dt)])
    jump = np.abs(np.diff(zs)) > ALTITUDE_JUMP_MS * dt          # step i: sample i → i + 1
    if not jump.any():
        return z

    fixed, dropped = zs.copy(), np.zeros(len(jump), bool)
    handled = []                                    # (start, end) of what each jump changed
    edges = np.diff(np.concatenate([[0], jump.astype(int), [0]]))
    for a, b in zip(np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0]):
        # Jump from sample a to sample b; was the altitude stuck before it?
        s = a
        while s > 0 and abs(zs[s - 1] - zs[a]) <= STUCK_M:
            s -= 1
        p = s - 1                                   # last sample before the stuck stretch
        if p >= 0 and abs(zs[b] - zs[p]) <= ALTITUDE_JUMP_MS * (clock[b] - clock[p]):
            # Stuck ever since an earlier jump, and now back near the level before it: that
            # earlier jump was a glitch, not a step. Undo what was done for it.
            for lo, hi in handled:
                if hi > p:
                    fixed[lo:hi], dropped[lo:hi] = zs[lo:hi], False
            fixed[p + 1:b] = np.interp(clock[p + 1:b], [clock[p], clock[b]], [zs[p], zs[b]])
            handled.append((p + 1, b))
        else:
            dropped[a:b] = True
            handled.append((a, b))

    steps = np.where(dropped, 0.0, np.diff(fixed))
    rebuilt = np.concatenate([[0.0], np.cumsum(steps)])
    z[ok] = rebuilt + np.median(zs - rebuilt)
    return z


def moving_elevation(elevation, timer_s, elapsed_s=None) -> np.ndarray:
    """Altitude for climbing done while moving: altimeter jumps repaired, then any change still
    faster than ALTITUDE_JUMP_MS of moving time — a move during a pause — taken out, and the
    profile rebuilt from the remaining changes."""
    z = remove_jumps(elevation, display_clock(elapsed_s, timer_s))
    ok = np.isfinite(z)
    if ok.sum() < 2:
        return z
    dz = np.diff(z[ok])
    dt = np.diff(np.asarray(timer_s, dtype=float)[ok])
    dt = np.where(np.isfinite(dt), np.maximum(dt, 1.0), 1.0)
    carried = np.abs(dz) > ALTITUDE_JUMP_MS * dt
    if carried.any():
        dz[carried] = 0
        z[ok] = z[ok][0] + np.concatenate([[0.0], np.cumsum(dz)])
    return z


def display_clock(elapsed_s, timer_s) -> np.ndarray:
    """Elapsed time when the track has it everywhere (pauses then explain real moves), else
    moving time."""
    elapsed = np.asarray([] if elapsed_s is None else elapsed_s, dtype=float)
    return elapsed if len(elapsed) and np.isfinite(elapsed).all() else np.asarray(timer_s, dtype=float)
