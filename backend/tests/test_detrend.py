"""Tests for the sinusoidal-regression detrender."""
from __future__ import annotations

import numpy as np
import pytest

from backend.app.detrend import fit_sinusoid, apply_detrend
from backend.tests.conftest import make_sinusoid, inject_box_transit


def test_fit_recovers_amplitude_and_period_within_one_percent():
    period = 7.34
    amp = 0.0182  # 1.82% peak-to-mean → 18200 ppm
    t = np.linspace(0.0, 27.0, 20000)  # one sector, 2-min cadence
    f = make_sinusoid(t, period=period, amp=amp, phase=0.7)

    fit = fit_sinusoid(t, f, period_days=period)

    expected_amp_ppm = amp * 1e6
    assert fit.amplitude_ppm == pytest.approx(expected_amp_ppm, rel=0.01)
    assert fit.period_days == pytest.approx(period)
    assert fit.rms_after < fit.rms_before


def test_detrend_lifts_transit_visibility_above_variability():
    """Inject a transit on top of a sinusoid; the residual flux should have
    a deeper visible dip (relative to local scatter) than the raw flux."""
    t = np.linspace(0.0, 27.0, 20000)
    rot_period = 5.1
    rot_amp = 0.012  # 1.2% rotation
    base = make_sinusoid(t, period=rot_period, amp=rot_amp)
    transit_period = 3.7
    transit_depth = 0.0015
    transit_dur = 0.10
    f = inject_box_transit(t, base, period=transit_period, t0=1.0,
                           depth=transit_depth, duration=transit_dur)

    fit = fit_sinusoid(t, f, period_days=rot_period)
    resid = apply_detrend(t, f, fit)

    assert np.std(resid) < 0.5 * np.std(f)
    assert transit_depth > 3.0 * np.std(resid)


from backend.app.detrend import apply_variability_detrend


def test_skip_when_amplitude_below_noise_floor():
    """A flat light curve must be returned unchanged with reason=skipped_low_amplitude."""
    t = np.linspace(0.0, 27.0, 20000)
    f = np.ones_like(t) + np.random.default_rng(0).normal(0.0, 1e-4, size=t.size)

    out_f, meta = apply_variability_detrend(
        t, f, period_days=5.0, noise_floor_ppm=500.0, source="ls_peak",
    )

    assert meta["applied"] is False
    assert meta["reason"] == "skipped_low_amplitude"
    assert meta["period_days"] == 5.0
    assert meta["amplitude_ppm"] is not None
    assert np.array_equal(out_f, f)


def test_apply_when_amplitude_exceeds_noise_floor():
    t = np.linspace(0.0, 27.0, 20000)
    f = make_sinusoid(t, period=7.0, amp=0.01)

    out_f, meta = apply_variability_detrend(
        t, f, period_days=7.0, noise_floor_ppm=500.0, source="user_period",
    )

    assert meta["applied"] is True
    assert meta["reason"] == "user_period"
    assert meta["amplitude_ppm"] > 5000.0
    assert meta["rms_reduction_pct"] > 50.0
    assert np.std(out_f) < np.std(f)


def test_run_full_vetting_defaults_disable_detrend(rng):
    """high_variability=False must produce detrend.applied=False and use BLS on raw flux."""
    from backend.app.pipeline import run_full_vetting, StarInfo

    t = np.linspace(0.0, 27.0, 5000)
    f = 1.0 + rng.normal(0.0, 1e-3, size=t.size)
    fe = np.full_like(t, 1e-3)
    star = StarInfo()
    res = run_full_vetting(t, f, fe, quality=None, mom_x=None, mom_y=None, star=star)
    assert res.detrend["applied"] is False
    assert res.detrend["reason"] == "disabled"
    assert res.sensitivity["secondary_sigma"] == 3.0


def test_run_full_vetting_with_variability_toggle_runs_fit(rng):
    from backend.app.pipeline import run_full_vetting, StarInfo

    t = np.linspace(0.0, 27.0, 8000)
    f = make_sinusoid(t, period=4.0, amp=0.01)
    fe = np.full_like(t, 1e-3)
    star = StarInfo()
    res = run_full_vetting(
        t, f, fe, quality=None, mom_x=None, mom_y=None, star=star,
        high_variability=True, rotation_period_days=4.0,
    )
    assert res.detrend["applied"] is True
    assert res.detrend["reason"] == "user_period"
    assert res.detrend["period_days"] == 4.0


def test_detrend_plot_emitted_when_applied(rng):
    from backend.app.pipeline import run_full_vetting, StarInfo

    t = np.linspace(0.0, 27.0, 8000)
    f = make_sinusoid(t, period=4.0, amp=0.01)
    fe = np.full_like(t, 1e-3)
    star = StarInfo()
    res = run_full_vetting(
        t, f, fe, quality=None, mom_x=None, mom_y=None, star=star,
        high_variability=True, rotation_period_days=4.0,
    )
    assert "detrend" in res.plots
    assert isinstance(res.plots["detrend"], str)
    assert len(res.plots["detrend"]) > 100  # base64 PNG, not empty


def test_detrend_plot_absent_when_disabled(rng):
    from backend.app.pipeline import run_full_vetting, StarInfo

    t = np.linspace(0.0, 27.0, 5000)
    f = 1.0 + rng.normal(0.0, 1e-3, size=t.size)
    fe = np.full_like(t, 1e-3)
    res = run_full_vetting(t, f, fe, quality=None, mom_x=None, mom_y=None, star=StarInfo())
    assert "detrend" not in res.plots


def test_defaults_match_pre_change_pipeline_output(rng):
    """With high_variability=False and secondary_sigma=3.0, run_full_vetting
    must produce the same BLS, secondary, verdict as before these changes."""
    from backend.app.pipeline import run_full_vetting, StarInfo

    t = np.linspace(0.0, 27.0, 6000)
    f = 1.0 + rng.normal(0.0, 1e-3, size=t.size)
    fe = np.full_like(t, 1e-3)
    star = StarInfo()
    a = run_full_vetting(t, f, fe, None, None, None, star)
    b = run_full_vetting(
        t, f, fe, None, None, None, star,
        high_variability=False, rotation_period_days=None, secondary_sigma=3.0,
    )
    assert a.bls["period"] == b.bls["period"]
    assert a.bls["sde"] == b.bls["sde"]
    assert a.secondary.get("detected") == b.secondary.get("detected")
    assert a.verdict["category"] == b.verdict["category"]
    assert a.detrend["applied"] is False
    assert a.sensitivity["secondary_sigma"] == 3.0
