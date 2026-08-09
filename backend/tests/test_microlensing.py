"""Tests for Module A — microlensing model-comparison classifier.

Exercises the end-to-end analyzer against synthetic PSPL, flare, and
flat-noise light curves and checks the verdict + parameter recovery.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.microlensing import (
    analyze_event,
    flare_flux,
    pspl_flux,
    pspl_magnification,
    symmetry_score,
)


# ---------------------------------------------------------------------------
# Model-level sanity
# ---------------------------------------------------------------------------

def test_pspl_magnification_peaks_at_t0():
    t = np.linspace(-5, 5, 501)
    A = pspl_magnification(t, t0=0.0, tE=1.0, u0=0.1)
    assert A.argmax() == np.argmin(np.abs(t))  # peak at t0
    assert A.min() >= 1.0 - 1e-6                # A >= 1 everywhere
    assert A.max() > 5.0                        # u0=0.1 gives a strong peak


def test_pspl_flux_baseline_unity_no_blend():
    t = np.array([-1e6, 0.0, 1e6])
    f = pspl_flux(t, t0=0.0, tE=1.0, u0=0.5, f_s=1.0, f_b=0.0)
    # Far from t0 → A → 1 → flux → 1
    assert f[0] == pytest.approx(1.0, abs=1e-6)
    assert f[2] == pytest.approx(1.0, abs=1e-6)
    assert f[1] > 1.0  # peak brighter than baseline


def test_flare_template_shape_is_asymmetric():
    t = np.linspace(-2.0, 6.0, 801)
    f = flare_flux(t, t_peak=0.0, amplitude=1.0, fwhm=1.0, baseline=1.0)
    # Rise: quicker than decay → integral of decay side > integral of rise side.
    left = f[(t >= -1.0) & (t < 0.0)] - 1.0
    right = f[(t > 0.0) & (t <= 5.0)] - 1.0
    assert right.sum() > left.sum() * 1.5  # decay dominates


# ---------------------------------------------------------------------------
# End-to-end analyzer
# ---------------------------------------------------------------------------

def _make_synthetic(t, model_flux, noise_ppm=200.0, seed=0):
    """Add Gaussian white noise to model_flux; return arrays."""
    rng = np.random.default_rng(seed)
    sigma = noise_ppm * 1e-6
    flux = model_flux + rng.normal(0.0, sigma, size=t.size)
    flux_err = np.full_like(t, sigma)
    return flux, flux_err


def test_analyze_recovers_pspl_verdict_and_params():
    """Synthetic PSPL light curve → verdict 'microlensing', tE and u0 recovered."""
    t = np.linspace(1200.0, 1240.0, 400)
    true_t0, true_tE, true_u0 = 1220.0, 5.0, 0.15
    model = pspl_flux(t, true_t0, true_tE, true_u0, f_s=1.0, f_b=0.0)
    flux, ferr = _make_synthetic(t, model, noise_ppm=200.0, seed=1)

    out = analyze_event(t, flux, ferr, t_start=1200.0, t_end=1240.0, t0_guess=1220.0)

    assert out["verdict"] == "microlensing", (
        f"expected microlensing, got {out['verdict']}; "
        f"delta_bic={out['delta_bic']}"
    )
    assert out["confidence"] > 0.5

    pspl = out["models"]["pspl"]["params"]
    assert pspl["t0"] == pytest.approx(true_t0, abs=0.1)
    assert pspl["tE"] == pytest.approx(true_tE, rel=0.15)
    assert pspl["u0"] == pytest.approx(true_u0, abs=0.05)

    # A good PSPL fit leaves noise-dominated residuals; the symmetry score
    # should therefore be near zero (uncorrelated wings) — NOT strongly
    # negative (that would indicate leftover asymmetric residual, i.e. a bad fit).
    score = out["symmetry_score"]
    assert np.isnan(score) or abs(score) < 0.5


def test_analyze_flags_flare_over_pspl():
    """Synthetic flare → verdict 'flare' (BIC prefers Davenport template)."""
    t = np.linspace(1200.0, 1210.0, 300)
    model = flare_flux(t, t_peak=1201.5, amplitude=0.02, fwhm=0.4, baseline=1.0)
    flux, ferr = _make_synthetic(t, model, noise_ppm=150.0, seed=2)

    out = analyze_event(t, flux, ferr, t_start=1200.0, t_end=1210.0, t0_guess=1201.5)

    # Either 'flare' (flare BIC lowest) or 'ambiguous' is acceptable — a
    # perfect PSPL fit can BIC-tie a low-cadence flare — but 'microlensing'
    # is not.
    assert out["verdict"] in ("flare", "ambiguous"), (
        f"expected flare/ambiguous, got {out['verdict']}; "
        f"delta_bic={out['delta_bic']}"
    )
    assert out["delta_bic"]["flare_minus_pspl"] < 0 or out["verdict"] == "ambiguous"


def test_analyze_returns_null_on_flat_noise():
    """Pure noise around 1.0 → null model wins."""
    t = np.linspace(1200.0, 1210.0, 300)
    flat = np.ones_like(t)
    flux, ferr = _make_synthetic(t, flat, noise_ppm=200.0, seed=3)

    out = analyze_event(t, flux, ferr, t_start=1200.0, t_end=1210.0, t0_guess=1205.0)

    assert out["verdict"] == "null", (
        f"expected null, got {out['verdict']}; delta_bic={out['delta_bic']}"
    )


def test_symmetry_score_symmetric_signal():
    t = np.linspace(-5.0, 5.0, 201)
    residuals = np.exp(-t * t)   # perfectly even about 0
    assert symmetry_score(t, residuals, t0=0.0) == pytest.approx(1.0, abs=0.05)


def test_symmetry_score_asymmetric_signal():
    t = np.linspace(-5.0, 5.0, 201)
    residuals = np.where(t < 0.0, 0.0, np.exp(-t))  # one-sided decay
    # Left wing is zero → std ≈ 0 → returns NaN; that's acceptable behaviour.
    score = symmetry_score(t, residuals, t0=0.0)
    assert np.isnan(score) or abs(score) < 0.3


def test_analyze_rejects_tiny_window():
    t = np.linspace(0.0, 10.0, 100)
    flux = np.ones_like(t)
    ferr = np.full_like(t, 1e-3)
    with pytest.raises(ValueError, match="Need at least"):
        analyze_event(t, flux, ferr, t_start=4.99, t_end=5.01, t0_guess=5.0)


# ---------------------------------------------------------------------------
# MulensModel parity — protects against regressions in the closed-form PSPL
# by pinning it to the reference implementation. Skipped when MulensModel
# isn't installed (it's a heavy optional dep — the runtime path is
# closed-form and doesn't need it).
# ---------------------------------------------------------------------------

def test_pspl_matches_mulensmodel_reference():
    mm = pytest.importorskip("MulensModel")
    from app.microlensing import pspl_magnification

    t = np.linspace(1200.0, 1240.0, 400)
    for t0, tE, u0 in [(1220.0, 5.0, 0.15), (1500.0, 20.0, 0.5), (2000.0, 1.5, 0.02)]:
        ours = pspl_magnification(t, t0=t0, tE=tE, u0=u0)
        ref = mm.Model({"t_0": t0, "u_0": u0, "t_E": tE}).get_magnification(t)
        # Bit-level agreement for a point-source point-lens is expected
        # because Paczynski's closed form is exactly what MulensModel returns
        # for the no-parallax / no-finite-source case.
        assert np.allclose(ours, ref, atol=1e-12, rtol=1e-12), (
            f"PSPL mismatch at (t0={t0}, tE={tE}, u0={u0}): "
            f"max |diff| = {np.abs(ours - ref).max():.2e}"
        )
