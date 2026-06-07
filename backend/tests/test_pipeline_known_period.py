"""Tests for known-period constrained BLS."""
import numpy as np
import pytest

from backend.app.pipeline import (
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
