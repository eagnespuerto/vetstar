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

def test_compute_observables_matches_paczynski_at_u0_small():
    """For u0 = 0.1, Paczynski's peak magnification is ~10.03; check ours
    matches, and check derived scalars are internally consistent."""
    from app.microlensing import compute_observables
    obs = compute_observables(
        {"t0": 1220.0, "tE": 5.0, "u0": 0.1, "f_s": 1.0, "f_b": 0.0},
        {"t0": 0.001, "tE": 0.02, "u0": 0.001, "f_s": 0.01, "f_b": 0.01},
    )
    # A_peak at u0=0.1: (0.01+2) / (0.1 * sqrt(0.01+4)) = 2.01 / (0.1 * 2.00249) ≈ 10.037
    assert obs["peak_magnification"] == pytest.approx(10.037, abs=0.01)
    # No blend → observed peak = intrinsic peak
    assert obs["peak_magnification_observed"] == pytest.approx(obs["peak_magnification"], rel=1e-6)
    # Δm = -2.5·log10(A) ≈ -2.5·log10(10.037) ≈ -2.504
    assert obs["peak_brightening_mag"] == pytest.approx(-2.504, abs=0.01)
    # Source fraction = 1 (no blend)
    assert obs["source_flux_fraction"] == pytest.approx(1.0, abs=1e-9)
    # Einstein-crossing duration = 2*tE*sqrt(1 - u0²) = 2*5*sqrt(0.99) ≈ 9.9499
    assert obs["einstein_crossing_duration_d"] == pytest.approx(9.9499, abs=0.01)
    # BJD offset applied
    assert obs["t0_bjd"] == pytest.approx(1220.0 + 2457000.0, abs=1e-6)
    # FWHM should be finite and positive
    assert obs["magnification_fwhm_d"] > 0.0


def test_compute_observables_handles_wide_impact_parameter():
    """u0 > 1 means the source never enters the Einstein ring — crossing
    duration should be zero, but A_peak is still finite (just small)."""
    from app.microlensing import compute_observables
    obs = compute_observables(
        {"t0": 1500.0, "tE": 20.0, "u0": 1.5, "f_s": 0.7, "f_b": 0.3},
        {"t0": 0.1, "tE": 0.5, "u0": 0.01, "f_s": 0.05, "f_b": 0.05},
    )
    assert obs["einstein_crossing_duration_d"] == 0.0
    assert obs["peak_magnification"] > 1.0
    # Blend fraction should be 0.3 / 1.0 = 0.3 (source frac = 0.7)
    assert obs["source_flux_fraction"] == pytest.approx(0.7, abs=1e-6)
    assert obs["blend_flux_fraction"] == pytest.approx(0.3, abs=1e-6)


def test_analyze_response_includes_observables():
    """Full pipeline should emit the observables block."""
    t = np.linspace(1200.0, 1240.0, 400)
    from app.microlensing import pspl_flux
    m = pspl_flux(t, 1220.0, 5.0, 0.15, 1.0, 0.0)
    flux, ferr = _make_synthetic(t, m, noise_ppm=200.0, seed=10)
    out = analyze_event(t, flux, ferr, 1200.0, 1240.0, 1220.0)
    obs = out["observables"]
    assert obs is not None
    assert obs["einstein_timescale_d"] == pytest.approx(5.0, rel=0.05)
    assert obs["impact_parameter_u0"] == pytest.approx(0.15, abs=0.02)
    # Peak magnification for u0 ≈ 0.15 is ~6.7
    assert obs["peak_magnification"] == pytest.approx(6.7, abs=0.5)


# ---------------------------------------------------------------------------
# ExoFOP planet rows — microlensing-derivable subset
# ---------------------------------------------------------------------------

def test_exofop_rows_report_required_fields():
    from app.microlensing import (
        compute_exofop_planet_rows, compute_observables, compute_planet_predictions,
    )
    pspl = {"t0": 1220.0, "tE": 5.0, "u0": 0.15, "f_s": 1.0, "f_b": 0.0}
    err = {"t0": 0.001, "tE": 0.02, "u0": 0.001, "f_s": 0.01, "f_b": 0.01}
    obs = compute_observables(pspl, err)
    pp = compute_planet_predictions(pspl)
    rows = compute_exofop_planet_rows(obs, pp)
    labels = [r["label"] for r in rows]
    # The 4 required fields — matches the transit convention of marking t0/tE
    # equivalents as required.
    assert "Peak time t0" in labels
    assert "Einstein timescale tE" in labels
    assert "Impact parameter u0" in labels
    # Radius-ratio row uses the same label the transit tab does — R_planet/R_star.
    assert "R_planet/R_star" in labels
    # Semi-major axis is reported as the projected minimum, matching microlensing
    # convention (single-lens can't measure the true orbital a).
    assert any(r["label"].startswith("Semi-major") for r in rows)
    # Un-derivable rows (period, eccentricity) come back with None values — the
    # frontend renders them as "—" without pretending we fit them.
    period_row = next(r for r in rows if r["label"] == "Orbital Period")
    assert period_row["value"] is None


def test_exofop_rows_include_host_star_radius_from_mass_relation():
    """R_star for the fiducial K-dwarf lens should land near 0.3 R_sun for
    the default 0.3 M_sun bulge-lens prior — verify the M–R interpolation
    stays consistent."""
    from app.microlensing import (
        compute_exofop_planet_rows, compute_observables, compute_planet_predictions,
    )
    pspl = {"t0": 1500.0, "tE": 20.0, "u0": 0.3, "f_s": 0.8, "f_b": 0.2}
    err = {k: 0.01 for k in pspl}
    obs = compute_observables(pspl, err)
    pp = compute_planet_predictions(pspl)
    rows = compute_exofop_planet_rows(obs, pp)
    r_star_row = next(r for r in rows if r["label"].startswith("Host (lens) radius"))
    # 0.3 M_sun in the Pecaut–Mamajek table maps to ~0.30 R_sun.
    assert r_star_row["value"] == pytest.approx(0.30, abs=0.05)


# ---------------------------------------------------------------------------
# Joint TESS + Gaia fit (Harris+2026 workflow)
# ---------------------------------------------------------------------------

def _make_synthetic_joint(t_tess, t_gaia_jd, true_t0, true_tE, true_u0,
                            gaia_baseline_mag=17.0, noise_ppm_tess=200.0,
                            noise_mmag_gaia=10.0, seed=42):
    """Return TESS (flux, err) and Gaia (mag, err) arrays for a shared PSPL."""
    from app.microlensing import pspl_flux
    rng = np.random.default_rng(seed)
    # TESS: normalised flux (source only, no blend)
    model_tess = pspl_flux(t_tess, true_t0, true_tE, true_u0, 1.0, 0.0)
    sigma_t = noise_ppm_tess * 1e-6
    tess_flux = model_tess + rng.normal(0, sigma_t, t_tess.size)
    tess_ferr = np.full_like(t_tess, sigma_t)
    # Gaia: mag centred on baseline, brightened by magnification.
    # G(t) = G_baseline - 2.5 log10(A(t))
    t_gaia_btjd = t_gaia_jd - 2_457_000.0
    from app.microlensing import pspl_magnification
    A = pspl_magnification(t_gaia_btjd, true_t0, true_tE, true_u0)
    gaia_mag_true = gaia_baseline_mag - 2.5 * np.log10(A)
    sigma_g = noise_mmag_gaia * 1e-3
    gaia_mag = gaia_mag_true + rng.normal(0, sigma_g, t_gaia_jd.size)
    gaia_merr = np.full_like(t_gaia_jd, sigma_g)
    return tess_flux, tess_ferr, gaia_mag, gaia_merr


def test_joint_fit_recovers_shared_pspl_params():
    """Synthetic two-band PSPL → joint fit should recover t0/tE/u0."""
    from app.microlensing import analyze_event_joint
    # TESS: 400 dense points over 40 days centered on peak
    t_tess = np.linspace(1200.0, 1240.0, 400)
    # Gaia: 60 sparse points over 5 years (2016-2021), covering the event
    t_gaia_jd = np.linspace(2_457_400.0, 2_459_400.0, 60)
    true_t0, true_tE, true_u0 = 1220.0, 5.0, 0.15
    tess_flux, tess_ferr, gaia_mag, gaia_merr = _make_synthetic_joint(
        t_tess, t_gaia_jd, true_t0, true_tE, true_u0,
        gaia_baseline_mag=17.5, seed=1,
    )
    out = analyze_event_joint(
        tess_time=t_tess, tess_flux=tess_flux, tess_flux_err=tess_ferr,
        gaia_time_jd=t_gaia_jd, gaia_mag=gaia_mag, gaia_mag_err=gaia_merr,
        t_start=1200.0, t_end=1240.0, t0_guess=1220.0,
    )
    jf = out["joint_fit"]
    assert jf["success"] is True
    assert jf["params"]["t0"] == pytest.approx(true_t0, abs=0.05)
    assert jf["params"]["tE"] == pytest.approx(true_tE, rel=0.1)
    assert jf["params"]["u0"] == pytest.approx(true_u0, abs=0.03)
    # Both bands should contribute meaningful chi2 (order-N points each).
    assert jf["chi2_tess"] > 0.0
    assert jf["chi2_gaia"] > 0.0
    assert jf["n_tess"] == 400
    # Observables + planet predictions come through from the joint fit
    assert out["observables"] is not None
    assert out["planet_predictions"] is not None
    # Sanity: baseline Gaia mag recovered near the truth (17.5).
    assert out["window"]["gaia_baseline_mag"] == pytest.approx(17.5, abs=0.3)


def test_joint_fit_rejects_too_few_gaia_points():
    from app.microlensing import analyze_event_joint
    t_tess = np.linspace(1200.0, 1240.0, 200)
    tess_flux = np.ones_like(t_tess)
    tess_ferr = np.full_like(t_tess, 1e-3)
    # Only 3 Gaia points
    with pytest.raises(ValueError, match="Gaia"):
        analyze_event_joint(
            tess_time=t_tess, tess_flux=tess_flux, tess_flux_err=tess_ferr,
            gaia_time_jd=np.array([2457100.0, 2457200.0, 2457300.0]),
            gaia_mag=np.array([17.0, 17.0, 17.0]),
            gaia_mag_err=np.array([0.01, 0.01, 0.01]),
            t_start=1200.0, t_end=1240.0, t0_guess=1220.0,
        )


def test_joint_fit_response_keys_stable():
    """Downstream consumers depend on a stable response shape."""
    from app.microlensing import analyze_event_joint
    t_tess = np.linspace(1200.0, 1240.0, 400)
    t_gaia_jd = np.linspace(2_457_400.0, 2_459_400.0, 60)
    tess_flux, tess_ferr, gaia_mag, gaia_merr = _make_synthetic_joint(
        t_tess, t_gaia_jd, 1220.0, 5.0, 0.15, seed=2,
    )
    out = analyze_event_joint(
        tess_time=t_tess, tess_flux=tess_flux, tess_flux_err=tess_ferr,
        gaia_time_jd=t_gaia_jd, gaia_mag=gaia_mag, gaia_mag_err=gaia_merr,
        t_start=1200.0, t_end=1240.0, t0_guess=1220.0,
    )
    for k in ("verdict", "window", "joint_fit", "observables", "planet_predictions",
              "tess_time_windowed", "gaia_time_btjd", "notes"):
        assert k in out, f"missing key: {k}"
    for k in ("params", "chi2_tess", "chi2_gaia", "bic",
              "model_flux_tess", "model_flux_gaia"):
        assert k in out["joint_fit"], f"missing joint_fit key: {k}"


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
