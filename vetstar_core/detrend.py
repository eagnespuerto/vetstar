"""Sinusoidal + first-harmonic regression detrender used before BLS when the
user opts in to "High stellar variability" vetting.

Model: f(t) ≈ C + A1·sin(2πt/P) + B1·cos(2πt/P) + A2·sin(4πt/P) + B2·cos(4πt/P)

P is fixed (caller supplies it — Lomb-Scargle peak or user-supplied rotation
period). The five remaining coefficients are solved by linear least squares.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

import numpy as np


@dataclass
class SinusoidFit:
    period_days: float
    C: float
    A1: float
    B1: float
    A2: float
    B2: float
    amplitude_ppm: float            # sqrt(A1² + B1²) × 1e6
    harmonic_amplitude_ppm: float   # sqrt(A2² + B2²) × 1e6
    rms_before: float               # ppm
    rms_after: float                # ppm

    def as_dict(self) -> dict:
        return asdict(self)


def _design_matrix(t: np.ndarray, period_days: float) -> np.ndarray:
    omega = 2.0 * np.pi / period_days
    return np.column_stack([
        np.ones_like(t),
        np.sin(omega * t),
        np.cos(omega * t),
        np.sin(2.0 * omega * t),
        np.cos(2.0 * omega * t),
    ])


def fit_sinusoid(t: np.ndarray, f: np.ndarray, period_days: float) -> SinusoidFit:
    """Fit f(t) with a 5-coefficient sin+harmonic model at fixed period."""
    if period_days is None or period_days <= 0 or not np.isfinite(period_days):
        raise ValueError(f"period_days must be positive and finite, got {period_days!r}")
    X = _design_matrix(t, period_days)
    coeffs, *_ = np.linalg.lstsq(X, f, rcond=None)
    C, A1, B1, A2, B2 = (float(c) for c in coeffs)
    model = X @ coeffs
    amp = float(np.hypot(A1, B1))
    harm = float(np.hypot(A2, B2))
    rms_before = float(np.std(f - np.median(f))) * 1e6
    rms_after = float(np.std(f - model)) * 1e6
    return SinusoidFit(
        period_days=float(period_days),
        C=C, A1=A1, B1=B1, A2=A2, B2=B2,
        amplitude_ppm=amp * 1e6,
        harmonic_amplitude_ppm=harm * 1e6,
        rms_before=rms_before,
        rms_after=rms_after,
    )


def apply_detrend(t: np.ndarray, f: np.ndarray, fit: SinusoidFit) -> np.ndarray:
    """Subtract the fitted sinusoid, re-normalise to median = 1.0."""
    X = _design_matrix(t, fit.period_days)
    coeffs = np.array([fit.C, fit.A1, fit.B1, fit.A2, fit.B2])
    residual = f - X @ coeffs
    return residual + 1.0


def apply_variability_detrend(
    t: np.ndarray,
    f: np.ndarray,
    period_days: Optional[float],
    noise_floor_ppm: float,
    source: str,
) -> tuple[np.ndarray, dict]:
    """Run the sinusoidal detrend if amplitude clears the noise floor.

    Returns ``(f_out, meta)``. ``meta`` is the JSON-serialisable block
    described in the spec under ``result.detrend``.

    Parameters
    ----------
    period_days
        Period to fit at. None → returns ``reason="disabled"`` without
        touching ``f`` (caller decided not to detrend at all).
    source
        One of ``"ls_peak"`` or ``"user_period"`` — recorded in ``meta["reason"]``
        when the fit IS applied. Ignored otherwise.
    """
    if period_days is None or not np.isfinite(period_days) or period_days <= 0:
        return f, {
            "applied": False,
            "reason": "disabled",
            "period_days": None,
            "amplitude_ppm": None,
            "harmonic_amplitude_ppm": None,
            "rms_reduction_pct": None,
        }
    fit = fit_sinusoid(t, f, period_days)
    if fit.amplitude_ppm < noise_floor_ppm:
        return f, {
            "applied": False,
            "reason": "skipped_low_amplitude",
            "period_days": fit.period_days,
            "amplitude_ppm": fit.amplitude_ppm,
            "harmonic_amplitude_ppm": fit.harmonic_amplitude_ppm,
            "rms_reduction_pct": None,
        }
    out = apply_detrend(t, f, fit)
    rms_reduction = (
        100.0 * (fit.rms_before - fit.rms_after) / fit.rms_before
        if fit.rms_before > 0 else 0.0
    )
    return out, {
        "applied": True,
        "reason": source,
        "period_days": fit.period_days,
        "amplitude_ppm": fit.amplitude_ppm,
        "harmonic_amplitude_ppm": fit.harmonic_amplitude_ppm,
        "rms_reduction_pct": float(rms_reduction),
        "fit": fit.as_dict(),
    }
