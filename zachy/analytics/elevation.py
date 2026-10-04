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

Except the barometer catching up during a pause: the altimeter is smoothed, so on a fast descent
(or climb) the recorded altitude lags the real one, and when you stop the watch it catches up —
the whole lag shows as one step at the restart, at the same place. Told from a real move by being
small, in the direction you were already going, and the size of a plausible lag (step ÷ vertical
speed before the pause = a few seconds to a minute); spread back over the stretch before the
pause, where it built up (fix_pause_lag).
"""

import numpy as np

ALTITUDE_JUMP_MS = 5.0   # altitude changing faster than 5 m/s = altimeter artefact
STUCK_M = 0.5            # consecutive samples within this are "the altimeter didn't move"
PAUSE_S = 20             # a stop of the watch this long or more
LAG_MAX_M = 30           # a catch-up step is at most this…
LAG_S = (2, 60)          # …and the lag it means (step ÷ vertical speed before) within this
VZ_WINDOW_S = 20         # vertical speed before the pause, over this much moving time


def fix_pause_lag(elevation, timer_s, elapsed_s) -> np.ndarray:
    """`elevation` with the barometer's catch-up during pauses spread back over the stretch before
    each pause (3 × the lag, 15 s to 150 s of moving time), so the step at the restart goes away.
    Needs both clocks (pauses are where elapsed time runs and moving time doesn't)."""
    z = np.array(elevation, dtype=float)
    if elapsed_s is None or timer_s is None:
        return z
    t, el = np.asarray(timer_s, dtype=float), np.asarray(elapsed_s, dtype=float)
    if len(t) != len(z) or not (np.isfinite(t).all() and np.isfinite(el).all()):
        return z
    ok = np.nonzero(np.isfinite(z))[0]
    for k in range(1, len(ok)):
        p, i = ok[k - 1], ok[k]
        if (el[i] - el[p]) - (t[i] - t[p]) < PAUSE_S:
            continue
        step = z[i] - z[p]
        if abs(step) < 1 or abs(step) > LAG_MAX_M:
            continue
        before = ok[(ok <= p) & (t[ok] >= t[p] - VZ_WINDOW_S)]
        span = t[p] - t[before[0]] if len(before) else 0
        if span < VZ_WINDOW_S / 2:
            continue
        vz = (z[p] - z[before[0]]) / span
        if vz == 0 or np.sign(vz) != np.sign(step) or not LAG_S[0] <= step / vz <= LAG_S[1]:
            continue                                   # not a lag: a real move, kept as it is
        w = min(max(3 * step / vz, 15), 150)
        j = ok[(ok <= p) & (t[ok] > t[p] - w)]
        z[j] += step * (t[j] - (t[p] - w)) / w         # 0 at the start of the stretch, the whole step at the pause
    return z


def remove_jumps(elevation, time_s, timer_s=None) -> np.ndarray:
    """`elevation` with altimeter jumps (faster than ALTITUDE_JUMP_MS per `time_s`) repaired, and,
    given the moving time too (time_s then elapsed), the barometer's catch-up during pauses.
    Missing values stay missing."""
    z = fix_pause_lag(elevation, timer_s, time_s) if timer_s is not None else np.array(elevation, dtype=float)
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
    z = remove_jumps(elevation, display_clock(elapsed_s, timer_s), timer_s)
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
