import numpy as np

from zachy.analytics.elevation import moving_elevation, remove_jumps

T = np.arange(20.0)


def test_clean_track_unchanged():
    z = 100 + 0.5 * T
    z[3] = np.nan
    np.testing.assert_array_equal(remove_jumps(z, T), z)


def test_recalibration_step_dropped_at_the_true_level():
    # +40 m in the first second of a ride: the start was wrong, the rest is right.
    z = np.r_[110.0, np.full(19, 150.0)]
    np.testing.assert_allclose(remove_jumps(z, T), 150.0)


def test_catch_up_after_a_freeze_is_spread():
    # Descending, then the altimeter sticks at 120 for 8 s and catches up by -20 m.
    z = np.r_[124.0, 122.0, np.full(8, 120.0), 100.0 - np.arange(10.0)]
    fixed = remove_jumps(z, T)
    assert fixed[0] == 124 and fixed[-1] == z[-1]
    assert np.abs(np.diff(fixed)).max() <= 5


def test_glitch_that_comes_back_is_erased():
    z = np.full(20, 1054.0)
    z[8:14] = 1018.0
    np.testing.assert_allclose(remove_jumps(z, T), 1054.0)


def test_move_during_a_pause_kept_in_altitude_but_not_climbing():
    # A shuttle back up the hill: +350 m during a 15-minute pause (moving time stands still).
    timer = np.r_[np.arange(10.0), np.arange(9.0, 19.0)]
    elapsed = np.r_[np.arange(10.0), 900 + np.arange(10.0)]
    z = np.r_[700 - np.arange(10.0), 1050 - np.arange(10.0)]
    np.testing.assert_array_equal(remove_jumps(z, elapsed), z)
    climbing = moving_elevation(z, timer, elapsed)
    np.testing.assert_allclose(np.diff(climbing), np.r_[np.full(9, -1.0), 0, np.full(9, -1.0)])
