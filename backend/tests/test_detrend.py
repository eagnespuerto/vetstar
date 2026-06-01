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
