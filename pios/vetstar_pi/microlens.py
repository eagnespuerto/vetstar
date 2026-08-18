"""Microlensing model-comparison classifier — compact Pi port of
``backend/app/microlensing.py``.

Same three-way BIC fit (PSPL / Davenport 2014 flare / null) and same
observables/planet-prediction derivations. Single-band only; the joint
TESS+Gaia branch and MulensModel finite-source / parallax hooks are
dropped for the Pi port.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np
from scipy.optimize import least_squares


# ---------------------------------------------------------------------------
# Model functions
# ---------------------------------------------------------------------------
def pspl_magnification(t, t0, tE, u0):
    tau = (t - t0) / tE
    u = np.sqrt(u0 * u0 + tau * tau)
    u_safe = np.where(u > 1e-12, u, 1e-12)
    return (u_safe * u_safe + 2.0) / (u_safe * np.sqrt(u_safe * u_safe + 4.0))


def pspl_flux(t, t0, tE, u0, f_s, f_b):
    return f_s * pspl_magnification(t, t0, tE, u0) + f_b


_FLARE_RISE = (1.941, -0.175, -2.246, -1.125)
_FLARE_DECAY = ((0.6890, -1.600), (0.3030, -0.2783))


def flare_template(t_norm):
    out = np.zeros_like(t_norm, dtype=float)
    rise = (t_norm >= -1.0) & (t_norm <= 0.0)
    decay = t_norm > 0.0
    tr = t_norm[rise]
    a1, a2, a3, a4 = _FLARE_RISE
    out[rise] = 1.0 + a1 * tr + a2 * tr ** 2 + a3 * tr ** 3 + a4 * tr ** 4
    td = t_norm[decay]
    (b1, k1), (b2, k2) = _FLARE_DECAY
    out[decay] = b1 * np.exp(k1 * td) + b2 * np.exp(k2 * td)
    return out


def flare_flux(t, t_peak, amplitude, fwhm, baseline=1.0):
    fw = max(abs(fwhm), 1e-9)
    return baseline + amplitude * flare_template((t - t_peak) / fw)


# ---------------------------------------------------------------------------
# Fits
# ---------------------------------------------------------------------------
@dataclass
class FitResult:
    name: str
    params: Dict[str, float]
    param_err: Dict[str, float] = field(default_factory=dict)
    chi2: float = 0.0
    chi2_red: float = 0.0
    bic: float = 0.0
    n_params: int = 0
    n_points: int = 0
    model_flux: np.ndarray = None
    success: bool = True
    message: str = ""


def _bic(chi2, k, n):
    if n <= 0:
        return float("inf")
    return chi2 + k * np.log(n)


def _param_errors(res, names):
    try:
        J = res.jac
        n, k = J.shape
        if n <= k:
            return {name: float("nan") for name in names}
        var = float(2.0 * res.cost / max(n - k, 1))
        cov = np.linalg.pinv(J.T @ J) * var
        errs = np.sqrt(np.clip(np.diag(cov), 0.0, np.inf))
        return {name: float(errs[i]) for i, name in enumerate(names)}
    except Exception:
        return {name: float("nan") for name in names}


def fit_pspl(t, flux, flux_err, t0_guess, window_width) -> FitResult:
    p0 = np.array([t0_guess, max(window_width / 4.0, 1e-3), 0.3, 0.8, 0.2])
    names = ["t0", "tE", "u0", "f_s", "f_b"]
    lo = [t0_guess - window_width, 1e-4, 1e-3, 0.0, 0.0]
    hi = [t0_guess + window_width, window_width * 5.0, 5.0, 5.0, 5.0]

    def resid(p):
        t0, tE, u0, fs, fb = p
        return (flux - pspl_flux(t, t0, tE, u0, fs, fb)) / flux_err

    try:
        res = least_squares(resid, p0, bounds=(lo, hi), max_nfev=5000)
    except Exception as e:
        return FitResult("pspl", dict(zip(names, p0)), success=False,
                         message=f"PSPL fit failed: {e}",
                         n_params=len(p0), n_points=len(t),
                         chi2=float("inf"), bic=float("inf"))
    chi2 = float(np.sum(resid(res.x) ** 2))
    n = len(t)
    k = len(p0)
    return FitResult(
        name="pspl",
        params={n_: float(v) for n_, v in zip(names, res.x)},
        param_err=_param_errors(res, names),
        chi2=chi2, chi2_red=chi2 / max(n - k, 1),
        bic=_bic(chi2, k, n), n_params=k, n_points=n,
        model_flux=pspl_flux(t, *res.x),
        success=res.success, message=str(res.message),
    )


def fit_flare(t, flux, flux_err, t_peak_guess, window_width) -> FitResult:
    amp_guess = max(float(np.max(flux) - 1.0), 1e-4)
    fwhm_guess = max(window_width / 6.0, 1e-3)
    names = ["t_peak", "amplitude", "fwhm"]
    lo = [t_peak_guess - window_width, 1e-5, 1e-4]
    hi = [t_peak_guess + window_width, 5.0, window_width * 2.0]
    amp_guess = min(max(amp_guess, lo[1] * 1.01), hi[1] * 0.99)
    p0 = np.array([t_peak_guess, amp_guess, fwhm_guess])

    def resid(p):
        tp, amp, fw = p
        return (flux - flare_flux(t, tp, amp, fw, baseline=1.0)) / flux_err

    try:
        res = least_squares(resid, p0, bounds=(lo, hi), max_nfev=5000)
    except Exception as e:
        return FitResult("flare", dict(zip(names, p0)), success=False,
                         message=f"Flare fit failed: {e}",
                         n_params=len(p0), n_points=len(t),
                         chi2=float("inf"), bic=float("inf"))
    chi2 = float(np.sum(resid(res.x) ** 2))
    n = len(t)
    k = len(p0)
    return FitResult(
        name="flare",
        params={n_: float(v) for n_, v in zip(names, res.x)},
        param_err=_param_errors(res, names),
        chi2=chi2, chi2_red=chi2 / max(n - k, 1),
        bic=_bic(chi2, k, n), n_params=k, n_points=n,
        model_flux=flare_flux(t, *res.x, baseline=1.0),
        success=res.success, message=str(res.message),
    )


def fit_null(t, flux, flux_err) -> FitResult:
    w = 1.0 / (flux_err ** 2)
    baseline = float(np.sum(w * flux) / np.sum(w))
    model = np.full_like(t, baseline, dtype=float)
    chi2 = float(np.sum(((flux - model) / flux_err) ** 2))
    n = len(t)
    return FitResult(
        name="null",
        params={"baseline": baseline},
        param_err={"baseline": float(1.0 / np.sqrt(np.sum(w)))},
        chi2=chi2, chi2_red=chi2 / max(n - 1, 1),
        bic=_bic(chi2, 1, n), n_params=1, n_points=n,
        model_flux=model, success=True, message="closed-form weighted mean",
    )


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------
def symmetry_score(t, residuals, t0):
    left = t < t0
    right = t > t0
    if left.sum() < 3 or right.sum() < 3:
        return float("nan")
    left_t = t0 - t[left]
    right_t = t[right] - t0
    left_r = residuals[left]
    right_r = residuals[right]
    dmin = max(left_t.min(), right_t.min())
    dmax = min(left_t.max(), right_t.max())
    if not np.isfinite(dmin) or not np.isfinite(dmax) or dmax <= dmin:
        return float("nan")
    grid = np.linspace(dmin, dmax, 30)
    lo = np.interp(grid, np.sort(left_t), left_r[np.argsort(left_t)])
    ro = np.interp(grid, np.sort(right_t), right_r[np.argsort(right_t)])
    if np.std(lo) < 1e-12 or np.std(ro) < 1e-12:
        return float("nan")
    c = float(np.corrcoef(lo, ro)[0, 1])
    return c if math.isfinite(c) else float("nan")


# ---------------------------------------------------------------------------
# Observables + planet predictions (fiducial bulge lens)
# ---------------------------------------------------------------------------
_BTJD_OFFSET = 2_457_000.0
_M_L_FID_SUN = 0.30
_D_L_FID_KPC = 6.0
_D_S_FID_KPC = 8.0
_KAPPA_MAS_PER_MSUN = 8.144
_AU_KM = 1.495978707e8
_DAY_S = 86400.0
_CADENCE_FLOOR_D = 1.0 / 24.0
_Q_MIN_ABSOLUTE = 1e-6


def compute_observables(pspl_params, pspl_param_err) -> dict:
    t0 = float(pspl_params["t0"])
    tE = float(pspl_params["tE"])
    u0 = float(pspl_params["u0"])
    fs = float(pspl_params["f_s"])
    fb = float(pspl_params["f_b"])
    u0_safe = max(abs(u0), 1e-9)
    A_peak = (u0_safe ** 2 + 2.0) / (u0_safe * math.sqrt(u0_safe ** 2 + 4.0))
    baseline = fs + fb
    A_obs_peak = ((fs * A_peak + fb) / baseline) if baseline > 0 else A_peak
    delta_mag = -2.5 * math.log10(A_obs_peak) if A_obs_peak > 0 else float("nan")
    blend_g = (fs / baseline) if baseline > 0 else float("nan")

    if u0 < 1.0:
        einstein_crossing_d = 2.0 * tE * math.sqrt(1.0 - u0 ** 2)
    else:
        einstein_crossing_d = 0.0

    A_half = 0.5 * (A_peak + 1.0)

    def _A(u):
        return (u * u + 2.0) / (u * math.sqrt(u * u + 4.0))

    lo, hi = u0_safe, max(u0_safe * 2.0, 5.0)
    while _A(hi) > A_half and hi < 1e6:
        hi *= 2.0
    if _A(lo) >= A_half >= _A(hi):
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            if _A(mid) > A_half:
                lo = mid
            else:
                hi = mid
        u_half = 0.5 * (lo + hi)
        arg = u_half ** 2 - u0_safe ** 2
        fwhm_d = 2.0 * tE * math.sqrt(max(arg, 0.0))
    else:
        fwhm_d = float("nan")

    theta_e_fiducial_mas = 0.5
    mu_rel = (theta_e_fiducial_mas / tE) * 365.25 if tE > 0 else float("nan")

    return {
        "t0_btjd": t0,
        "t0_btjd_err": float(pspl_param_err.get("t0", float("nan"))),
        "t0_bjd": t0 + _BTJD_OFFSET,
        "einstein_timescale_d": tE,
        "einstein_timescale_err_d": float(pspl_param_err.get("tE", float("nan"))),
        "impact_parameter_u0": u0,
        "impact_parameter_err": float(pspl_param_err.get("u0", float("nan"))),
        "peak_magnification": A_peak,
        "peak_magnification_observed": A_obs_peak,
        "peak_brightening_mag": delta_mag,
        "einstein_crossing_duration_d": einstein_crossing_d,
        "magnification_fwhm_d": fwhm_d,
        "source_flux_fraction": blend_g,
        "blend_flux_fraction": (1.0 - blend_g) if math.isfinite(blend_g) else float("nan"),
        "f_s": fs,
        "f_b": fb,
        "mu_rel_mas_per_yr_fiducial": mu_rel,
    }


def compute_planet_predictions(pspl_params) -> dict:
    tE = float(pspl_params["tE"])
    u0 = float(pspl_params["u0"])
    pi_rel_mas = 1.0 / _D_L_FID_KPC - 1.0 / _D_S_FID_KPC
    theta_e_mas = math.sqrt(_KAPPA_MAS_PER_MSUN * _M_L_FID_SUN * pi_rel_mas)
    r_e_au = theta_e_mas * _D_L_FID_KPC
    v_rel = (r_e_au * _AU_KM) / (tE * _DAY_S) if tE > 0 else float("nan")
    closest_au = u0 * r_e_au
    if tE > 0:
        q_min = max((_CADENCE_FLOOR_D / (2.0 * tE)) ** 2, _Q_MIN_ABSOLUTE)
    else:
        q_min = float("nan")
    m_earth = q_min * _M_L_FID_SUN * 332_946.0 if math.isfinite(q_min) else float("nan")
    m_jup = m_earth / 317.83 if math.isfinite(m_earth) else float("nan")
    return {
        "assumption": (
            f"Fiducial bulge lens: M_L = {_M_L_FID_SUN} M_sun, "
            f"D_L = {_D_L_FID_KPC} kpc, D_S = {_D_S_FID_KPC} kpc."
        ),
        "fiducial_lens_mass_solar": _M_L_FID_SUN,
        "fiducial_lens_distance_kpc": _D_L_FID_KPC,
        "fiducial_source_distance_kpc": _D_S_FID_KPC,
        "theta_E_mas_fiducial": theta_e_mas,
        "einstein_radius_au_fiducial": r_e_au,
        "v_rel_km_s_fiducial": v_rel,
        "closest_approach_au_fiducial": closest_au,
        "planet_q_min_detectable": q_min,
        "planet_mass_floor_m_earth_fiducial": m_earth,
        "planet_mass_floor_m_jupiter_fiducial": m_jup,
    }


# ---------------------------------------------------------------------------
# Top-level analyzer
# ---------------------------------------------------------------------------
_STRONG_BIC = 10.0
_AMBIGUOUS_BIC = 6.0


def _verdict(bic_pspl, bic_flare, bic_null) -> Tuple[str, float]:
    bics = {"pspl": bic_pspl, "flare": bic_flare, "null": bic_null}
    order = sorted(bics.items(), key=lambda kv: kv[1])
    best, best_bic = order[0]
    margin = order[1][1] - best_bic
    dnp = bic_null - bic_pspl
    dfp = bic_flare - bic_pspl

    if best == "pspl":
        if dnp > _STRONG_BIC and abs(dfp) >= _AMBIGUOUS_BIC:
            verdict = "microlensing"
        elif abs(dfp) < _AMBIGUOUS_BIC:
            verdict = "ambiguous"
        else:
            verdict = "microlensing" if dnp > _STRONG_BIC else "ambiguous"
    elif best == "flare":
        verdict = "ambiguous" if abs(dfp) < _AMBIGUOUS_BIC else "flare"
    else:
        verdict = "null"
    confidence = float(1.0 - np.exp(-max(margin, 0.0) / _STRONG_BIC))
    return verdict, confidence


@dataclass
class MicrolensResult:
    verdict: str
    confidence: float
    window: dict
    t: np.ndarray
    flux_n: np.ndarray
    flux_err_n: np.ndarray
    pspl: FitResult
    flare: FitResult
    null: FitResult
    delta_bic: dict
    symmetry_score: float
    observables: dict = None
    planet_predictions: dict = None
    notes: List[str] = field(default_factory=list)


def analyze_event(
    time, flux, flux_err, t_start, t_end, t0_guess,
) -> MicrolensResult:
    time = np.asarray(time, dtype=float)
    flux = np.asarray(flux, dtype=float)
    flux_err = np.asarray(flux_err, dtype=float)

    m = (
        (time >= t_start) & (time <= t_end)
        & np.isfinite(time) & np.isfinite(flux) & np.isfinite(flux_err)
        & (flux_err > 0)
    )
    t = time[m]
    f = flux[m]
    fe = flux_err[m]
    if len(t) < 8:
        raise ValueError(f"Need at least 8 points in the window; got {len(t)}")

    q = np.quantile(f, 0.25)
    b_mask = f <= q
    if b_mask.sum() >= 3:
        w = 1.0 / (fe[b_mask] ** 2)
        baseline = float(np.sum(w * f[b_mask]) / np.sum(w))
    else:
        baseline = float(np.median(f))
    if baseline <= 0 or not np.isfinite(baseline):
        raise ValueError("Cannot normalise: non-positive baseline")

    f_n = f / baseline
    fe_n = fe / baseline
    window_width = float(t_end - t_start)

    pspl = fit_pspl(t, f_n, fe_n, t0_guess, window_width)
    flare = fit_flare(t, f_n, fe_n, t0_guess, window_width)
    null_ = fit_null(t, f_n, fe_n)

    verdict, confidence = _verdict(pspl.bic, flare.bic, null_.bic)

    if pspl.model_flux is not None:
        resid = f_n - pspl.model_flux
        sym = symmetry_score(t, resid, pspl.params["t0"])
    else:
        sym = float("nan")

    notes = [
        "Achromaticity not testable from single-band TESS data.",
    ]
    if not pspl.success:
        notes.append(f"PSPL fit did not fully converge: {pspl.message}")
    if not flare.success:
        notes.append(f"Flare fit did not fully converge: {flare.message}")

    obs = compute_observables(pspl.params, pspl.param_err) if pspl.success else None
    pp = compute_planet_predictions(pspl.params) if pspl.success else None

    return MicrolensResult(
        verdict=verdict,
        confidence=confidence,
        window={
            "t_start": float(t_start), "t_end": float(t_end),
            "baseline_flux": baseline, "n_points": int(len(t)),
        },
        t=t, flux_n=f_n, flux_err_n=fe_n,
        pspl=pspl, flare=flare, null=null_,
        delta_bic={
            "null_minus_pspl": null_.bic - pspl.bic,
            "flare_minus_pspl": flare.bic - pspl.bic,
            "null_minus_flare": null_.bic - flare.bic,
        },
        symmetry_score=sym,
        observables=obs,
        planet_predictions=pp,
        notes=notes,
    )
