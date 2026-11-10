"""Tests for known-period constrained BLS."""
import numpy as np
import pytest

from vetstar_core.pipeline import (
    BLS_KNOWN_PERIOD_TOL_FRAC,
    run_bls,
    run_bls_constrained,
    run_full_vetting,
    StarInfo,
)


def _inject_box_transit(rng, period_d=3.1, t0=1.0, depth=0.01, duration_d=0.1,
                       span_d=27.0, cadence_min=10.0, noise=0.0005):
    """Inject a clean box transit at the given ephemeris."""
    n = int(span_d * 1440.0 / cadence_min)
    t = np.linspace(0.0, span_d, n)
    f = np.ones_like(t) + rng.normal(0.0, noise, size=n)
    phase = ((t - t0) / period_d) % 1.0
    phase = np.where(phase > 0.5, phase - 1.0, phase)
    f[np.abs(phase) * period_d < duration_d / 2.0] -= depth
    fe = np.full_like(t, noise)
    return t, f, fe


def test_constrained_recovers_period_with_strong_power():
    rng = np.random.default_rng(0)
    t, f, fe = _inject_box_transit(rng, period_d=3.1)
    blind = run_bls(t, f, fe, p_min=0.5, p_max=10.0, n_periods=2000)
    constrained = run_bls_constrained(t, f, fe, known_period_days=3.1)
    assert constrained["constrained"] is True
    assert constrained["known_period_input_days"] == pytest.approx(3.1)
    assert constrained["matched_harmonic"] == "P"
    assert abs(constrained["period"] - 3.1) / 3.1 <= BLS_KNOWN_PERIOD_TOL_FRAC
    # Peak BLS power is the raw detection statistic and is comparable
    # across period grids (SDE is not — its denominator depends on grid width).
    # The constrained search should hit at least the blind peak power.
    assert constrained["power"] >= 0.9 * blind["power"]


def test_constrained_handles_two_x_alias():
    rng = np.random.default_rng(1)
    t, f, fe = _inject_box_transit(rng, period_d=3.1)
    result = run_bls_constrained(t, f, fe, known_period_days=6.2)
    assert result["matched_harmonic"] == "P/2"
    assert abs(result["period"] - 3.1) / 3.1 <= BLS_KNOWN_PERIOD_TOL_FRAC


def test_constrained_with_wrong_period_returns_low_sde():
    rng = np.random.default_rng(2)
    t, f, fe = _inject_box_transit(rng, period_d=3.1)
    result = run_bls_constrained(t, f, fe, known_period_days=10.0)
    assert result["constrained"] is True
    assert np.isfinite(result["sde"])
    # Vetstar uses SDE < 7 as the "not a clear detection" threshold in
    # make_verdict; a wrong-period constrained search should be well below.
    assert result["sde"] < 7.0


def test_constrained_output_shape_matches_run_bls_plus_new_keys():
    rng = np.random.default_rng(3)
    t, f, fe = _inject_box_transit(rng, period_d=3.1)
    blind = run_bls(t, f, fe, p_min=0.5, p_max=10.0, n_periods=2000)
    constrained = run_bls_constrained(t, f, fe, known_period_days=3.1)
    for key in ("period", "t0", "duration", "depth", "power", "sde",
                "n_transits_in_window", "_periodogram"):
        assert key in constrained, f"missing {key}"
        assert key in blind
    for key in ("constrained", "known_period_input_days", "matched_harmonic"):
        assert key in constrained


def test_run_full_vetting_propagates_known_period():
    rng = np.random.default_rng(4)
    t, f, fe = _inject_box_transit(rng, period_d=3.1)
    star = StarInfo(tic_id=0, ra=0.0, dec=0.0, tmag=10.0,
                    teff=5800.0, radius=1.0, mass=1.0)
    result = run_full_vetting(
        t, f, fe, quality=None, mom_x=None, mom_y=None,
        star=star, known_period_days=3.1,
    )
    assert result.bls.get("constrained") is True
    assert result.bls.get("known_period_input_days") == pytest.approx(3.1)


def test_run_full_vetting_falls_back_when_known_period_out_of_range():
    rng = np.random.default_rng(5)
    t, f, fe = _inject_box_transit(rng, period_d=3.1)
    span = float(t.max() - t.min())  # ~27 d
    star = StarInfo(tic_id=0, ra=0.0, dec=0.0, tmag=10.0,
                    teff=5800.0, radius=1.0, mass=1.0)
    # Pass a known period well above 0.7 * span — must fall back.
    result = run_full_vetting(
        t, f, fe, quality=None, mom_x=None, mom_y=None,
        star=star, known_period_days=span,  # = span > 0.7*span
    )
    assert result.bls.get("constrained") is not True
    assert "constrained_fallback_reason" in result.bls


# ----------------------------------------------------------------------
# Frequency grid and longer trial durations (v1.1)
# ----------------------------------------------------------------------
from vetstar_core.pipeline.detection import (  # noqa: E402
    BLS_DURATIONS_D,
    BLS_LONG_DURATION_MAX_FRAC,
    BLS_LONG_DURATIONS_D,
    bls_durations_for,
    bls_frequency_grid,
)


def test_grid_is_even_in_frequency_and_capped():
    periods = bls_frequency_grid(0.5, 19.0, span=27.0, max_points=20000)
    assert periods[0] == pytest.approx(0.5)
    assert periods[-1] == pytest.approx(19.0)
    assert np.all(np.diff(periods) > 0)
    steps = np.diff(1.0 / periods)
    assert np.allclose(steps, steps[0])
    assert len(periods) <= 20000
    # A short baseline needs fewer points than the cap.
    assert len(bls_frequency_grid(0.5, 5.0, span=7.0, max_points=20000)) < 20000


def test_long_boxes_are_only_tried_at_long_periods():
    assert list(bls_durations_for(3.0)) == list(BLS_DURATIONS_D)
    for d in BLS_LONG_DURATIONS_D:
        edge = d / BLS_LONG_DURATION_MAX_FRAC
        assert d not in bls_durations_for(edge * 0.99)
        assert d in bls_durations_for(edge)


@pytest.mark.parametrize("period_d,duration_d", [
    (0.62, 0.04), (1.7, 0.08), (3.1, 0.1), (8.2, 0.18), (12.5, 0.25),
])
def test_blind_search_recovers_period_across_the_range(period_d, duration_d):
    rng = np.random.default_rng(10)
    t, f, fe = _inject_box_transit(rng, period_d=period_d, depth=0.003,
                                   duration_d=duration_d, cadence_min=2.0,
                                   noise=0.001)
    result = run_bls(t, f, fe, p_min=0.5, p_max=27.0 * 0.7)
    assert abs(result["period"] - period_d) / period_d < 0.01
    # The old linear-in-period grid let the SDE sag below the verdict's
    # threshold of 7 at long periods, for the very same signal.
    assert result["sde"] > 10.0


def test_blind_search_fits_a_long_transit_with_a_long_box():
    rng = np.random.default_rng(11)
    t, f, fe = _inject_box_transit(rng, period_d=13.0, t0=1.3, depth=0.003,
                                   duration_d=0.7, cadence_min=2.0, noise=0.001)
    result = run_bls(t, f, fe, p_min=0.5, p_max=27.0 * 0.7)
    assert abs(result["period"] - 13.0) / 13.0 < 0.01
    assert result["duration"] > max(BLS_DURATIONS_D)
    assert result["depth"] == pytest.approx(0.003, rel=0.15)


def test_constrained_search_uses_long_boxes_too():
    rng = np.random.default_rng(12)
    t, f, fe = _inject_box_transit(rng, period_d=13.0, t0=1.3, depth=0.003,
                                   duration_d=0.7, cadence_min=2.0, noise=0.001)
    result = run_bls_constrained(t, f, fe, known_period_days=13.0)
    assert result["matched_harmonic"] == "P"
    assert abs(result["period"] - 13.0) / 13.0 <= BLS_KNOWN_PERIOD_TOL_FRAC
    assert result["duration"] > max(BLS_DURATIONS_D)


def test_plotted_periodogram_keeps_the_peak():
    rng = np.random.default_rng(13)
    t, f, fe = _inject_box_transit(rng, period_d=0.9, duration_d=0.06)
    result = run_bls(t, f, fe, p_min=0.5, p_max=10.0)
    pg = result["_periodogram"]
    assert max(pg["power"]) == pytest.approx(result["power"])
    assert pg["periods"][int(np.argmax(pg["power"]))] == pytest.approx(result["period"])


# ----------------------------------------------------------------------
# Variability guard: a high SDE from a smooth signal is not a detection
# ----------------------------------------------------------------------
from vetstar_core.pipeline.detection import bls_variability_check  # noqa: E402
from vetstar_core.pipeline.verdict import make_verdict  # noqa: E402


def _sinusoid_lc(rng, period_d, amp, span_d=27.0, cadence_min=2.0, noise=0.001):
    n = int(span_d * 1440.0 / cadence_min)
    t = np.linspace(0.0, span_d, n)
    f = 1.0 + amp * np.sin(2 * np.pi * t / period_d) + rng.normal(0.0, noise, n)
    return t, f, np.full_like(t, noise)


@pytest.mark.parametrize("rot_period_d", [1.3, 3.0, 6.3, 11.0])
@pytest.mark.parametrize("amp", [0.0005, 0.002, 0.01])
def test_guard_flags_pure_variability(rot_period_d, amp):
    rng = np.random.default_rng(50)
    t, f, fe = _sinusoid_lc(rng, rot_period_d, amp)
    bls = run_bls(t, f, fe, p_min=0.5, p_max=27.0 * 0.7)
    check = bls_variability_check(t, f, bls["period"], bls["t0"], bls["duration"])
    assert bls["sde"] > 7.0  # the SDE alone would call this a detection
    assert check["likely_variability"] is True


@pytest.mark.parametrize("period_d,duration_d", [
    (0.62, 0.04), (3.1, 0.1), (8.2, 0.18), (12.5, 0.25), (13.0, 0.7),
])
@pytest.mark.parametrize("depth,var_amp", [(0.003, 0.0), (0.0004, 0.0), (0.003, 0.0005)])
def test_guard_leaves_transits_alone(period_d, duration_d, depth, var_amp):
    rng = np.random.default_rng(51)
    t, f, fe = _inject_box_transit(rng, period_d=period_d, t0=1.3, depth=depth,
                                   duration_d=duration_d, cadence_min=2.0,
                                   noise=0.001)
    f = f + var_amp * np.sin(2 * np.pi * t / 6.3)
    bls = run_bls(t, f, fe, p_min=0.5, p_max=27.0 * 0.7)
    check = bls_variability_check(t, f, bls["period"], bls["t0"], bls["duration"])
    assert check["likely_variability"] is False
    assert check["smooth_to_box_ratio"] < 0.7


def test_verdict_does_not_trust_a_high_sde_from_variability():
    kw = dict(n_events=0, physics={"available": False}, centroid={"available": False},
              odd_even={"available": False}, secondary={"available": False},
              bls_sde=8.5)
    flagged = make_verdict(bls_variability=True, **kw)
    assert flagged["category"] == "no_signal"
    assert "bls_peak_is_variability" in flagged["flags"]
    assert make_verdict(**kw)["category"] != "no_signal"


def test_full_vetting_reports_the_variability_check():
    rng = np.random.default_rng(52)
    t, f, fe = _sinusoid_lc(rng, 6.3, 0.002)
    star = StarInfo(tic_id=0, ra=None, dec=None, tmag=10.0,
                    teff=5800.0, radius=1.0, mass=1.0)
    result = run_full_vetting(t, f, fe, quality=None, mom_x=None, mom_y=None, star=star)
    assert result.bls["likely_variability"] is True
    assert result.bls["smooth_to_box_ratio"] > 1.0
    if not result.events:
        assert result.verdict["category"] == "no_signal"
