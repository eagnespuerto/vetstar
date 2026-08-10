"""Module A — microlensing model-comparison classifier.

Given a user-flagged positive excursion window in a TESS light curve, fit
three competing models and return a verdict:
  1. PSPL (Paczynski single-lens) — closed-form point-source point-lens.
  2. Flare (Davenport 2014 empirical template) — the key astrophysical impostor.
  3. Null (flat baseline) — reference model.

Selection is done via BIC (delta_bic > 10 = strong, |delta| < 6 = ambiguous).

MulensModel is listed as an optional dependency; the PSPL closed form here
matches its point-source point-lens output. If a future build wants finite-source
or parallax, swap the closed form for a MulensModel call.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import least_squares


def _json_safe(x: Any) -> Any:
    """Recursively replace NaN/±Inf with None so the response encodes as JSON.

    Stdlib json.dumps refuses non-finite floats; FastAPI uses it by default.
    We keep the semantic ("this quantity is undefined / this fit didn't
    converge") without breaking the wire format.
    """
    if isinstance(x, dict):
        return {k: _json_safe(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_json_safe(v) for v in x]
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, (np.floating,)):
        v = float(x)
        return v if math.isfinite(v) else None
    return x


# ---------------------------------------------------------------------------
# Model functions
# ---------------------------------------------------------------------------

def pspl_magnification(t: np.ndarray, t0: float, tE: float, u0: float) -> np.ndarray:
    """Paczynski single-lens magnification A(u).

    u(t)  = sqrt(u0^2 + ((t - t0) / tE)^2)
    A(u)  = (u^2 + 2) / (u * sqrt(u^2 + 4))
    """
    tau = (t - t0) / tE
    u = np.sqrt(u0 * u0 + tau * tau)
    # Guard against u == 0 (u0 == 0 exactly and t == t0). Physically A -> +inf
    # but our fits keep u0 > 0, so this branch never triggers for real fits.
    u_safe = np.where(u > 1e-12, u, 1e-12)
    return (u_safe * u_safe + 2.0) / (u_safe * np.sqrt(u_safe * u_safe + 4.0))


def pspl_flux(t: np.ndarray, t0: float, tE: float, u0: float,
              f_s: float, f_b: float) -> np.ndarray:
    """Blended PSPL flux model. f_s = source flux fraction, f_b = blend."""
    return f_s * pspl_magnification(t, t0, tE, u0) + f_b


# Davenport (2014) empirical flare template coefficients, ApJ 797, 122 Table 1.
_FLARE_RISE = (1.941, -0.175, -2.246, -1.125)         # for t' in [-1, 0]
_FLARE_DECAY = ((0.6890, -1.600), (0.3030, -0.2783))  # (amp, tau) pairs, t' > 0


def flare_template(t_norm: np.ndarray) -> np.ndarray:
    """Davenport 2014 unit-amplitude flare template on normalized time.

    t_norm = (t - t_peak) / fwhm. Peak value is 1.0 at t_norm = 0.
    Returns 0 before the rise (t_norm < -1) and the double-exponential decay
    for t_norm > 0.
    """
    out = np.zeros_like(t_norm, dtype=float)
    rise_mask = (t_norm >= -1.0) & (t_norm <= 0.0)
    decay_mask = t_norm > 0.0

    tr = t_norm[rise_mask]
    a1, a2, a3, a4 = _FLARE_RISE
    out[rise_mask] = 1.0 + a1 * tr + a2 * tr**2 + a3 * tr**3 + a4 * tr**4

    td = t_norm[decay_mask]
    (b1, k1), (b2, k2) = _FLARE_DECAY
    out[decay_mask] = b1 * np.exp(k1 * td) + b2 * np.exp(k2 * td)
    return out


def flare_flux(t: np.ndarray, t_peak: float, amplitude: float, fwhm: float,
               baseline: float = 1.0) -> np.ndarray:
    """Full flare model: baseline + amplitude * template((t - t_peak) / fwhm)."""
    fwhm_safe = max(abs(fwhm), 1e-9)
    return baseline + amplitude * flare_template((t - t_peak) / fwhm_safe)


# ---------------------------------------------------------------------------
# Fits
# ---------------------------------------------------------------------------

@dataclass
class FitResult:
    params: Dict[str, float]
    param_err: Dict[str, float] = field(default_factory=dict)
    chi2: float = 0.0
    chi2_red: float = 0.0
    bic: float = 0.0
    n_params: int = 0
    n_points: int = 0
    model_flux: Optional[np.ndarray] = None
    success: bool = True
    message: str = ""


def _bic(chi2: float, k: int, n: int) -> float:
    if n <= 0:
        return float("inf")
    return chi2 + k * np.log(n)


def _param_errors(res, param_names: List[str]) -> Dict[str, float]:
    """Approximate 1-sigma errors from the least_squares Jacobian.

    Follows scipy.optimize.curve_fit's covariance recipe: pcov = (J^T J)^-1
    scaled by residual variance (chi2 / (N - k)).
    """
    try:
        J = res.jac
        n, k = J.shape
        if n <= k:
            return {name: float("nan") for name in param_names}
        residual_var = float(2.0 * res.cost / max(n - k, 1))  # 2*cost = sum sq residuals
        # (JT J)^-1 * sigma^2
        JTJ = J.T @ J
        cov = np.linalg.pinv(JTJ) * residual_var
        errs = np.sqrt(np.clip(np.diag(cov), 0.0, np.inf))
        return {name: float(errs[i]) for i, name in enumerate(param_names)}
    except Exception:
        return {name: float("nan") for name in param_names}


def fit_pspl(t: np.ndarray, flux: np.ndarray, flux_err: np.ndarray,
             t0_guess: float, window_width: float) -> FitResult:
    """Fit 5-param blended PSPL."""
    p0 = np.array([t0_guess, max(window_width / 4.0, 1e-3), 0.3, 0.8, 0.2])
    param_names = ["t0", "tE", "u0", "f_s", "f_b"]
    # Bounds: t0 near guess, tE > 0, u0 > 0, f_s and f_b >= 0.
    lo = [t0_guess - window_width, 1e-4, 1e-3, 0.0, 0.0]
    hi = [t0_guess + window_width, window_width * 5.0, 5.0, 5.0, 5.0]

    def resid(p):
        t0, tE, u0, fs, fb = p
        model = pspl_flux(t, t0, tE, u0, fs, fb)
        return (flux - model) / flux_err

    try:
        res = least_squares(resid, p0, bounds=(lo, hi), max_nfev=5000)
    except Exception as e:
        return FitResult(params=dict(zip(param_names, p0)), success=False,
                         message=f"PSPL fit failed: {e}",
                         n_params=len(p0), n_points=len(t),
                         chi2=float("inf"), bic=float("inf"))

    chi2 = float(np.sum(resid(res.x) ** 2))
    n = len(t)
    k = len(p0)
    params = {name: float(v) for name, v in zip(param_names, res.x)}
    return FitResult(
        params=params,
        param_err=_param_errors(res, param_names),
        chi2=chi2,
        chi2_red=chi2 / max(n - k, 1),
        bic=_bic(chi2, k, n),
        n_params=k,
        n_points=n,
        model_flux=pspl_flux(t, *res.x),
        success=res.success,
        message=str(res.message),
    )


def fit_flare(t: np.ndarray, flux: np.ndarray, flux_err: np.ndarray,
              t_peak_guess: float, window_width: float) -> FitResult:
    """Fit 3-param Davenport-2014 flare (fixed baseline = 1.0 by normalization)."""
    # Amplitude guess: peak minus baseline of the windowed data. Strong
    # lensing peaks can push this over the flare upper bound (5x baseline);
    # clip so least_squares' bounds-check doesn't reject the initial point.
    amp_guess = max(float(np.max(flux) - 1.0), 1e-4)
    fwhm_guess = max(window_width / 6.0, 1e-3)
    param_names = ["t_peak", "amplitude", "fwhm"]
    lo = [t_peak_guess - window_width, 1e-5, 1e-4]
    hi = [t_peak_guess + window_width, 5.0, window_width * 2.0]
    amp_guess = min(max(amp_guess, lo[1] * 1.01), hi[1] * 0.99)
    p0 = np.array([t_peak_guess, amp_guess, fwhm_guess])

    def resid(p):
        tp, amp, fw = p
        model = flare_flux(t, tp, amp, fw, baseline=1.0)
        return (flux - model) / flux_err

    try:
        res = least_squares(resid, p0, bounds=(lo, hi), max_nfev=5000)
    except Exception as e:
        return FitResult(params=dict(zip(param_names, p0)), success=False,
                         message=f"Flare fit failed: {e}",
                         n_params=len(p0), n_points=len(t),
                         chi2=float("inf"), bic=float("inf"))

    chi2 = float(np.sum(resid(res.x) ** 2))
    n = len(t)
    k = len(p0)
    params = {name: float(v) for name, v in zip(param_names, res.x)}
    return FitResult(
        params=params,
        param_err=_param_errors(res, param_names),
        chi2=chi2,
        chi2_red=chi2 / max(n - k, 1),
        bic=_bic(chi2, k, n),
        n_params=k,
        n_points=n,
        model_flux=flare_flux(t, *res.x, baseline=1.0),
        success=res.success,
        message=str(res.message),
    )


def fit_null(t: np.ndarray, flux: np.ndarray, flux_err: np.ndarray) -> FitResult:
    """Fit constant baseline (1 free param)."""
    w = 1.0 / (flux_err ** 2)
    baseline = float(np.sum(w * flux) / np.sum(w))
    model = np.full_like(t, baseline, dtype=float)
    chi2 = float(np.sum(((flux - model) / flux_err) ** 2))
    n = len(t)
    return FitResult(
        params={"baseline": baseline},
        param_err={"baseline": float(1.0 / np.sqrt(np.sum(w)))},
        chi2=chi2,
        chi2_red=chi2 / max(n - 1, 1),
        bic=_bic(chi2, 1, n),
        n_params=1,
        n_points=n,
        model_flux=model,
        success=True,
        message="closed-form weighted mean",
    )


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def symmetry_score(t: np.ndarray, residuals: np.ndarray, t0: float) -> float:
    """Correlation between the residual left wing and the mirrored right wing.

    +1 = perfectly symmetric about t0 (PSPL-like)
    ~0 = uncorrelated (flare-like asymmetry or noise dominated)
    <0 = anti-symmetric

    NaN if there are fewer than 3 points on either side.
    """
    left_mask = t < t0
    right_mask = t > t0
    if left_mask.sum() < 3 or right_mask.sum() < 3:
        return float("nan")

    left_t = t0 - t[left_mask]        # distance from t0, positive
    left_r = residuals[left_mask]
    right_t = t[right_mask] - t0      # distance from t0, positive
    right_r = residuals[right_mask]

    # Common grid = shared range of distances.
    dmin = max(left_t.min(), right_t.min())
    dmax = min(left_t.max(), right_t.max())
    if not np.isfinite(dmin) or not np.isfinite(dmax) or dmax <= dmin:
        return float("nan")

    grid = np.linspace(dmin, dmax, 30)
    left_interp = np.interp(grid, np.sort(left_t), left_r[np.argsort(left_t)])
    right_interp = np.interp(grid, np.sort(right_t), right_r[np.argsort(right_t)])

    if np.std(left_interp) < 1e-12 or np.std(right_interp) < 1e-12:
        return float("nan")

    corr = float(np.corrcoef(left_interp, right_interp)[0, 1])
    if not np.isfinite(corr):
        return float("nan")
    return corr


# ---------------------------------------------------------------------------
# Observable parameters — derived from the PSPL fit alone (no external
# priors on lens mass / distance / parallax). Report the quantities a
# reviewer would need to write up the event.
# ---------------------------------------------------------------------------

# BTJD → BJD constant (TESS mission convention).
_BTJD_OFFSET = 2_457_000.0

# Magnification at u = 1 Einstein radius (Paczynski): 3/sqrt(5) ≈ 1.3416.
# Used as the "significant lensing" threshold for the Einstein-crossing duration.
_A_AT_U_EQ_1 = 3.0 / math.sqrt(5.0)


def compute_observables(pspl_params: Dict[str, float],
                        pspl_param_err: Dict[str, float]) -> dict:
    """Physical / observable quantities derived from the fitted PSPL params.

    Nothing here uses external priors — everything is derivable from
    (t0, tE, u0, f_s, f_b) alone. Downstream tools (ExoFOP-style report,
    on-screen panel) render these directly.
    """
    t0 = float(pspl_params["t0"])
    tE = float(pspl_params["tE"])
    u0 = float(pspl_params["u0"])
    fs = float(pspl_params["f_s"])
    fb = float(pspl_params["f_b"])

    u0_safe = max(abs(u0), 1e-9)
    # Intrinsic (unblended) peak magnification at closest approach u = u0.
    A_peak = (u0_safe ** 2 + 2.0) / (u0_safe * math.sqrt(u0_safe ** 2 + 4.0))
    # Peak brightening seen in the blended aperture (source + blend).
    baseline = fs + fb
    A_obs_peak = ((fs * A_peak + fb) / baseline) if baseline > 0 else A_peak
    delta_mag = -2.5 * math.log10(A_obs_peak) if A_obs_peak > 0 else float("nan")

    # Source flux fraction — what part of the aperture flux is the lensed source.
    blend_g = (fs / baseline) if baseline > 0 else float("nan")

    # Einstein-crossing duration: the total time the source spent inside the
    # Einstein ring (u < 1, A > 1.34). For u0 < 1 it's real; otherwise the
    # event is "far" and the source never enters the ring.
    if u0 < 1.0:
        einstein_crossing_d = 2.0 * tE * math.sqrt(1.0 - u0 ** 2)
    else:
        einstein_crossing_d = 0.0

    # FWHM of the magnification profile. Solve A(u_half) = (A_peak + 1) / 2
    # for u_half >= u0, then t at u = u_half is t_half = tE * sqrt(u_half² - u0²).
    A_half = 0.5 * (A_peak + 1.0)
    # Invert A(u) via a monotone bisection on u >= u0 (A is decreasing in u
    # for u >= u0). Bounds: u_hi = a value comfortably past where A drops
    # below A_half. For A_half >= 1, u_hi = max(u0, 5.0) is safe.
    def _A_of_u(u: float) -> float:
        return (u * u + 2.0) / (u * math.sqrt(u * u + 4.0))
    lo, hi = u0_safe, max(u0_safe * 2.0, 5.0)
    while _A_of_u(hi) > A_half and hi < 1e6:
        hi *= 2.0
    if _A_of_u(lo) >= A_half >= _A_of_u(hi):
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            if _A_of_u(mid) > A_half:
                lo = mid
            else:
                hi = mid
        u_half = 0.5 * (lo + hi)
        # From u_half to time: sqrt(u0² + (dt/tE)²) = u_half.
        arg = u_half ** 2 - u0_safe ** 2
        fwhm_d = 2.0 * tE * math.sqrt(max(arg, 0.0))
    else:
        fwhm_d = float("nan")

    # Angular-quantity note: without an external lens-mass prior, θ_E and
    # the lens mass M_L stay degenerate. We surface a formal proper-motion
    # estimate under the fiducial assumption θ_E ≈ 0.5 mas (typical bulge
    # value from Gould 2000) — flagged clearly in the notes.
    theta_e_fiducial_mas = 0.5
    mu_rel_mas_per_yr = (theta_e_fiducial_mas / tE) * 365.25 if tE > 0 else float("nan")

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
        "mu_rel_mas_per_yr_fiducial": mu_rel_mas_per_yr,
        "mu_rel_note": ("Proper motion assumes fiducial θ_E = 0.5 mas (typical "
                        "bulge value); real θ_E is degenerate with lens mass "
                        "without additional constraints. Gaia baseline "
                        "photometry (Harris et al. 2026, ApJL 1005 L33) is "
                        "the standard way to break this degeneracy for TESS."),
    }


# ---------------------------------------------------------------------------
# Planet-detection predictions — geometry-anchored physical scales and the
# planetary anomaly sensitivity floor implied by the fit's tE. A single-lens
# fit CAN'T actually detect a planet (that needs a binary-lens fit with a
# caustic-crossing anomaly), so everything here is either a fiducial-lens
# prediction or a detection-floor sensitivity — flagged accordingly.
# ---------------------------------------------------------------------------

# Standard bulge-lens fiducials for solar-system-scale mass/distance priors.
_M_L_FID_SUN = 0.30            # M_L / M_sun — typical bulge lens (Sumi+ 2011)
_D_L_FID_KPC = 6.0             # kpc — bulge midplane
_D_S_FID_KPC = 8.0             # kpc — Galactic centre distance
# Einstein-radius normalization constant κ ≈ 8.144 mas / M_sun.
_KAPPA_MAS_PER_MSUN = 8.144
_AU_KM = 1.495978707e8
_DAY_S = 86400.0
# Assume ~1-hour effective cadence at bulge magnifications (2-min SPOC binned
# down + FFI 10-min cadence typical). Anomaly ≥ 2·cadence to be recognisable.
_CADENCE_FLOOR_D = 1.0 / 24.0
# Below this q the anomaly duration collapses under any TESS cadence — floor
# it to avoid absurdly small numbers.
_Q_MIN_ABSOLUTE = 1e-6


def compute_planet_predictions(pspl_params: Dict[str, float]) -> dict:
    """Fiducial-lens geometry + planet-detection sensitivity floor.

    Physical scales assume the standard bulge-lens fiducial
    (M_L = 0.3 M☉, D_L = 6 kpc, D_S = 8 kpc). These are order-of-magnitude
    unless the event has an independent mass measurement (parallax + finite
    source), which single-band TESS photometry cannot supply.

    Sensitivity floor uses the rule-of-thumb planetary caustic anomaly
    duration Δt ≈ 2·tE·√q. Requiring Δt ≥ 2·cadence gives
    q_min ≈ (cadence / tE)², below which a planetary perturbation would be
    unresolvable on the current light curve.
    """
    tE = float(pspl_params["tE"])
    u0 = float(pspl_params["u0"])

    # Fiducial angular Einstein radius:
    #   π_rel = 1 AU · (1/D_L - 1/D_S)  in mas (with D in kpc)
    #   θ_E   = sqrt(κ · M_L · π_rel)   in mas
    pi_rel_mas = 1.0 / _D_L_FID_KPC - 1.0 / _D_S_FID_KPC
    theta_e_mas_fid = math.sqrt(_KAPPA_MAS_PER_MSUN * _M_L_FID_SUN * pi_rel_mas)

    # Physical Einstein radius at the lens: r_E = θ_E · D_L
    # With θ_E in mas and D_L in kpc, r_E comes out in AU directly.
    r_e_au_fid = theta_e_mas_fid * _D_L_FID_KPC

    # Transverse relative velocity: v_rel = r_E / tE
    v_rel_km_s_fid = (r_e_au_fid * _AU_KM) / (tE * _DAY_S) if tE > 0 else float("nan")

    # Impact-parameter physical closest approach: b = u0 · r_E (AU)
    closest_approach_au_fid = u0 * r_e_au_fid

    # Planetary anomaly sensitivity floor.
    # anomaly duration ≈ 2 · tE · sqrt(q)  →  q_min ≈ (cadence / (2·tE))²·? use ≈ (cadence/tE)² for order.
    if tE > 0:
        q_min = max((_CADENCE_FLOOR_D / (2.0 * tE)) ** 2, _Q_MIN_ABSOLUTE)
    else:
        q_min = float("nan")

    m_planet_earth_floor = q_min * _M_L_FID_SUN * 332_946.0 if math.isfinite(q_min) else float("nan")
    m_planet_jupiter_floor = m_planet_earth_floor / 317.83 if math.isfinite(m_planet_earth_floor) else float("nan")

    return {
        "assumption": (
            f"Fiducial bulge lens: M_L = {_M_L_FID_SUN} M_sun, "
            f"D_L = {_D_L_FID_KPC} kpc, D_S = {_D_S_FID_KPC} kpc. "
            "All physical scales scale as √(M_L·π_rel), so a factor-of-2 "
            "shift in either input shifts them by <30%."
        ),
        "fiducial_lens_mass_solar": _M_L_FID_SUN,
        "fiducial_lens_distance_kpc": _D_L_FID_KPC,
        "fiducial_source_distance_kpc": _D_S_FID_KPC,
        "theta_E_mas_fiducial": theta_e_mas_fid,
        "einstein_radius_au_fiducial": r_e_au_fid,
        "v_rel_km_s_fiducial": v_rel_km_s_fid,
        "closest_approach_au_fiducial": closest_approach_au_fid,
        "planet_sensitivity_note": (
            "Single-lens fit cannot detect a planet — that needs a "
            "binary-lens fit with a resolved caustic-crossing anomaly. "
            "The floor below is the minimum planet-to-lens mass ratio whose "
            f"caustic anomaly would be resolvable on this light curve (anomaly "
            f"≥ 2·{_CADENCE_FLOOR_D * 24:.1f} h). Harris et al. (2026, ApJL "
            "1005, L33) show that robust TESS microlensing detections require "
            "a joint TESS + Gaia fit (their pyLIMA modelling of Gaia23bra used "
            "TESS difference-image photometry + Gaia G-band photometry to pin "
            "the binary-lens geometry) — TESS-only fits are limited by the "
            "short single-sector baseline and 21″ pixel scale."
        ),
        "planet_q_min_detectable": q_min,
        "planet_mass_floor_m_earth_fiducial": m_planet_earth_floor,
        "planet_mass_floor_m_jupiter_fiducial": m_planet_jupiter_floor,
    }


# ---------------------------------------------------------------------------
# Top-level analyzer
# ---------------------------------------------------------------------------

# Verdict thresholds (see spec Module A).
_STRONG_BIC = 10.0
_AMBIGUOUS_BIC = 6.0


def _verdict_and_confidence(bic_pspl: float, bic_flare: float, bic_null: float
                            ) -> Tuple[str, float]:
    """Apply the spec's decision rules.

    Confidence maps the margin between the winning model and the runner-up
    through 1 - exp(-margin / _STRONG_BIC), i.e. saturates near 1 for
    delta_bic >> 10 and stays near 0 for close calls.
    """
    bics = {"pspl": bic_pspl, "flare": bic_flare, "null": bic_null}
    order = sorted(bics.items(), key=lambda kv: kv[1])
    best, best_bic = order[0]
    second_bic = order[1][1]
    margin = second_bic - best_bic

    delta_null_pspl = bic_null - bic_pspl
    delta_flare_pspl = bic_flare - bic_pspl

    if best == "pspl":
        if delta_null_pspl > _STRONG_BIC and abs(delta_flare_pspl) >= _AMBIGUOUS_BIC:
            verdict = "microlensing"
        elif abs(delta_flare_pspl) < _AMBIGUOUS_BIC:
            verdict = "ambiguous"
        else:
            verdict = "microlensing" if delta_null_pspl > _STRONG_BIC else "ambiguous"
    elif best == "flare":
        if abs(delta_flare_pspl) < _AMBIGUOUS_BIC:
            verdict = "ambiguous"
        elif margin > _STRONG_BIC:
            verdict = "flare"
        else:
            verdict = "flare"
    else:  # null best
        verdict = "null"

    confidence = float(1.0 - np.exp(-max(margin, 0.0) / _STRONG_BIC))
    return verdict, confidence


def analyze_event(time: np.ndarray, flux: np.ndarray, flux_err: np.ndarray,
                  t_start: float, t_end: float, t0_guess: float) -> dict:
    """Fit PSPL / flare / null on the [t_start, t_end] window and return the
    full response dict per the spec.
    """
    time = np.asarray(time, dtype=float)
    flux = np.asarray(flux, dtype=float)
    flux_err = np.asarray(flux_err, dtype=float)
    if time.shape != flux.shape or time.shape != flux_err.shape:
        raise ValueError("time, flux, flux_err must have the same shape")

    # Window selection + finite/positive filtering.
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

    # Normalize flux to baseline = 1.0. Baseline = weighted mean of the
    # OUT-of-excursion points (defined as the lower quartile of the window flux).
    q = np.quantile(f, 0.25)
    baseline_mask = f <= q
    if baseline_mask.sum() >= 3:
        w = 1.0 / (fe[baseline_mask] ** 2)
        baseline = float(np.sum(w * f[baseline_mask]) / np.sum(w))
    else:
        baseline = float(np.median(f))
    if baseline <= 0 or not np.isfinite(baseline):
        raise ValueError("Cannot normalize: non-positive/non-finite baseline")

    f_n = f / baseline
    fe_n = fe / baseline

    window_width = float(t_end - t_start)

    pspl = fit_pspl(t, f_n, fe_n, t0_guess=t0_guess, window_width=window_width)
    flare = fit_flare(t, f_n, fe_n, t_peak_guess=t0_guess, window_width=window_width)
    null = fit_null(t, f_n, fe_n)

    verdict, confidence = _verdict_and_confidence(pspl.bic, flare.bic, null.bic)

    # Symmetry: fold residuals of the winning shape model (PSPL) about its fitted t0.
    if pspl.model_flux is not None:
        pspl_residuals = f_n - pspl.model_flux
        sym = symmetry_score(t, pspl_residuals, pspl.params["t0"])
    else:
        sym = float("nan")

    notes: List[str] = [
        "Achromaticity not testable from single-band TESS data — supply a "
        "second-band array via a future request field to enable this check.",
    ]
    if not pspl.success:
        notes.append(f"PSPL fit did not fully converge: {pspl.message}")
    if not flare.success:
        notes.append(f"Flare fit did not fully converge: {flare.message}")

    def _pack(fit: FitResult) -> dict:
        d = {
            "params": fit.params,
            "param_err": fit.param_err,
            "chi2": fit.chi2,
            "chi2_red": fit.chi2_red,
            "bic": fit.bic,
            "n_params": fit.n_params,
            "n_points": fit.n_points,
            "success": fit.success,
            "model_flux": fit.model_flux.tolist() if fit.model_flux is not None else None,
        }
        return d

    observables = compute_observables(pspl.params, pspl.param_err) if pspl.success else None
    planet_predictions = compute_planet_predictions(pspl.params) if pspl.success else None

    return _json_safe({
        "verdict": verdict,
        "confidence": confidence,
        "window": {"t_start": float(t_start), "t_end": float(t_end),
                   "baseline_flux": baseline, "n_points": int(len(t))},
        "time_windowed": t.tolist(),
        "flux_normalized": f_n.tolist(),
        "flux_err_normalized": fe_n.tolist(),
        "models": {"pspl": _pack(pspl), "flare": _pack(flare), "null": _pack(null)},
        "delta_bic": {
            "null_minus_pspl": null.bic - pspl.bic,
            "flare_minus_pspl": flare.bic - pspl.bic,
            "null_minus_flare": null.bic - flare.bic,
        },
        "symmetry_score": sym,
        "observables": observables,
        "planet_predictions": planet_predictions,
        "notes": notes,
    })
