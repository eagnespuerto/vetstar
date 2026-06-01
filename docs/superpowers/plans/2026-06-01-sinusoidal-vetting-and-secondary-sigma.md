# Sinusoidal-Regression Vetting + Secondary-Eclipse σ Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional sinusoid + first-harmonic detrend before BLS for variable stars, plus a user-tunable secondary-eclipse σ threshold; both apply to single- and multi-sector vetting and surface in the JSON, plots, PDF, and frontend (with a Share button on the new plot).

**Architecture:** New `backend/app/detrend.py` performs a linear least-squares fit of `[1, sin(2πt/P), cos(2πt/P), sin(4πt/P), cos(4πt/P)]` to per-sector light curves; `pipeline.py` calls it after Lomb-Scargle and before BLS when `high_variability=True`. A new `secondary_sigma` parameter threads from request schemas through `secondary_eclipse_search` (now parameterised). A new "Stellar variability detrend" plot is rendered as base64 PNG and shown inline with the existing `ShareToImgbbButton`.

**Tech Stack:** Python 3 + numpy + astropy + matplotlib + FastAPI/Pydantic (backend); React + TypeScript + Tailwind (frontend); pytest for tests.

**Spec:** `docs/superpowers/specs/2026-06-01-sinusoidal-vetting-and-secondary-sigma-design.md`

---

## Task 1: Bootstrap backend test scaffold

**Files:**
- Create: `backend/tests/__init__.py` (empty)
- Create: `backend/tests/conftest.py`
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Add pytest to requirements**

Edit `backend/requirements.txt` — append at the end:

```
pytest>=8.0
```

- [ ] **Step 2: Create empty test package marker**

Write `backend/tests/__init__.py` with no content.

- [ ] **Step 3: Create conftest with the synthetic-LC helper used by later tasks**

Write `backend/tests/conftest.py`:

```python
"""Shared fixtures for backend tests."""
from __future__ import annotations

import numpy as np
import pytest


@pytest.fixture
def rng():
    return np.random.default_rng(seed=42)


def make_sinusoid(t: np.ndarray, period: float, amp: float, phase: float = 0.0,
                  harmonic_amp: float = 0.0) -> np.ndarray:
    """Return 1 + amp·sin(2πt/P + φ) + harmonic_amp·sin(4πt/P)."""
    s = amp * np.sin(2 * np.pi * t / period + phase)
    if harmonic_amp:
        s = s + harmonic_amp * np.sin(4 * np.pi * t / period)
    return 1.0 + s


def inject_box_transit(t: np.ndarray, f: np.ndarray, period: float,
                       t0: float, depth: float, duration: float) -> np.ndarray:
    """Return f with a box-shaped transit of `depth` injected at each cycle."""
    phase = ((t - t0 + 0.5 * period) % period) - 0.5 * period
    in_tx = np.abs(phase) < (0.5 * duration)
    out = f.copy()
    out[in_tx] -= depth
    return out
```

- [ ] **Step 4: Install pytest and verify it runs**

Run: `pip install -r backend/requirements.txt && pytest backend/tests -q`
Expected: `no tests ran in 0.0Xs` (no tests yet, but pytest discovered the directory).

- [ ] **Step 5: Commit**

```bash
git add backend/requirements.txt backend/tests/__init__.py backend/tests/conftest.py
git commit -m "test: scaffold backend pytest layout with synthetic-LC helpers"
```

---

## Task 2: Implement `fit_sinusoid` + `apply_detrend` (TDD)

**Files:**
- Create: `backend/tests/test_detrend.py`
- Create: `backend/app/detrend.py`

- [ ] **Step 1: Write the failing test for amplitude/period recovery**

Write `backend/tests/test_detrend.py`:

```python
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

    # Amplitude in ppm: sqrt(A1² + B1²) × 1e6
    expected_amp_ppm = amp * 1e6
    assert fit.amplitude_ppm == pytest.approx(expected_amp_ppm, rel=0.01)
    assert fit.period_days == pytest.approx(period)
    assert fit.rms_after < fit.rms_before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_detrend.py::test_fit_recovers_amplitude_and_period_within_one_percent -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.app.detrend'`.

- [ ] **Step 3: Write minimal implementation**

Write `backend/app/detrend.py`:

```python
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
    # Re-add 1.0 so the residual remains a normalised flux around unity.
    return residual + 1.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_detrend.py::test_fit_recovers_amplitude_and_period_within_one_percent -v`
Expected: PASS.

- [ ] **Step 5: Add the BLS-uplift test**

Append to `backend/tests/test_detrend.py`:

```python
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

    # Raw scatter is dominated by rotation; residual scatter should drop a lot.
    assert np.std(resid) < 0.5 * np.std(f)
    # Transit depth should now stand >3x above residual scatter.
    assert transit_depth > 3.0 * np.std(resid)
```

- [ ] **Step 6: Run the second test to verify it passes**

Run: `pytest backend/tests/test_detrend.py -v`
Expected: both tests PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/detrend.py backend/tests/test_detrend.py
git commit -m "feat(detrend): add sinusoid + first-harmonic regression detrender"
```

---

## Task 3: Pipeline integration helper — `apply_variability_detrend`

**Files:**
- Modify: `backend/app/detrend.py`
- Modify: `backend/tests/test_detrend.py`

This task wraps `fit_sinusoid` + `apply_detrend` in a single helper that also enforces the low-amplitude skip rule and returns the JSON-ready metadata block.

- [ ] **Step 1: Write the failing test for the skip-when-flat case**

Append to `backend/tests/test_detrend.py`:

```python
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
    assert meta["amplitude_ppm"] > 5000.0  # 1% ≈ 10 000 ppm
    assert meta["rms_reduction_pct"] > 50.0
    assert np.std(out_f) < np.std(f)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_detrend.py -v`
Expected: the two new tests FAIL with `ImportError: cannot import name 'apply_variability_detrend'`.

- [ ] **Step 3: Add the helper to `detrend.py`**

Append to `backend/app/detrend.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_detrend.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/detrend.py backend/tests/test_detrend.py
git commit -m "feat(detrend): add apply_variability_detrend with low-amp skip"
```

---

## Task 4: Parameterise `secondary_eclipse_search` with `secondary_sigma` (TDD)

**Files:**
- Create: `backend/tests/test_secondary_sigma.py`
- Modify: `backend/app/pipeline.py:617-638` (the `secondary_eclipse_search` function)

- [ ] **Step 1: Write the failing test**

Write `backend/tests/test_secondary_sigma.py`:

```python
"""Tests for the user-tunable secondary-eclipse sigma threshold."""
from __future__ import annotations

import numpy as np

from backend.app.pipeline import secondary_eclipse_search


def _make_lc_with_secondary(sigma_target: float):
    """Build a synthetic LC where the secondary dip sits at exactly `sigma_target`σ."""
    rng = np.random.default_rng(123)
    t = np.linspace(0.0, 27.0, 20000)
    period = 3.0
    t0 = 0.5
    duration = 0.10
    noise = rng.normal(0.0, 0.001, size=t.size)
    f = 1.0 + noise
    phase = ((t - t0) / period) % 1.0
    in_sec = np.abs(phase - 0.5) < (duration / period / 1.5)
    # depth so that depth/(σ/√n_in) ≈ sigma_target
    sigma_local = np.std(f) / np.sqrt(in_sec.sum())
    depth = sigma_target * sigma_local
    f[in_sec] -= depth
    return t, f, period, t0, duration


def test_detected_at_default_three_sigma():
    t, f, p, t0, d = _make_lc_with_secondary(sigma_target=4.0)
    out = secondary_eclipse_search(t, f, p, t0, d, secondary_sigma=3.0)
    assert out["available"] is True
    assert out["detected"] is True


def test_not_detected_when_threshold_raised_above_signal():
    t, f, p, t0, d = _make_lc_with_secondary(sigma_target=4.0)
    out = secondary_eclipse_search(t, f, p, t0, d, secondary_sigma=6.0)
    assert out["available"] is True
    assert out["detected"] is False


def test_detected_when_threshold_lowered_to_one_sigma():
    t, f, p, t0, d = _make_lc_with_secondary(sigma_target=2.0)
    out = secondary_eclipse_search(t, f, p, t0, d, secondary_sigma=1.0)
    assert out["available"] is True
    assert out["detected"] is True


def test_default_argument_is_three():
    """Calling without secondary_sigma must behave like the old hard-coded 3."""
    t, f, p, t0, d = _make_lc_with_secondary(sigma_target=4.0)
    out = secondary_eclipse_search(t, f, p, t0, d)
    assert out["detected"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest backend/tests/test_secondary_sigma.py -v`
Expected: FAIL — `secondary_eclipse_search() got an unexpected keyword argument 'secondary_sigma'`.

- [ ] **Step 3: Edit `secondary_eclipse_search` to take the threshold**

In `backend/app/pipeline.py`, replace the function at lines 617-638 with:

```python
def secondary_eclipse_search(t, f, period, t0, duration, secondary_sigma: float = 3.0) -> dict:
    """Look at phase 0.5 for a secondary dip.

    ``secondary_sigma`` is the user-tunable detection threshold (default 3σ).
    """
    if period is None or period <= 0:
        return {"available": False, "reason": "no period"}
    phase = ((t - t0) / period) % 1.0
    in_sec = np.abs(phase - 0.5) < (duration / period / 1.5)
    if in_sec.sum() < 5:
        return {"available": False, "reason": "no phase-0.5 coverage"}
    f_sec = np.median(f[in_sec])
    oot = (phase > 0.2) & (phase < 0.4) | (phase > 0.6) & (phase < 0.8)
    if oot.sum() < 10:
        return {"available": False, "reason": "no oot baseline"}
    baseline = np.median(f[oot])
    depth = baseline - f_sec
    noise = np.std(f[oot]) / np.sqrt(in_sec.sum())
    sigma = depth / noise if noise > 0 else 0
    return {
        "available": True,
        "depth": float(depth),
        "sigma": float(sigma),
        "detected": bool(sigma > secondary_sigma),
        "threshold_sigma": float(secondary_sigma),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/test_secondary_sigma.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/pipeline.py backend/tests/test_secondary_sigma.py
git commit -m "feat(pipeline): make secondary-eclipse sigma threshold tunable"
```

---

## Task 5: Wire detrend + secondary_sigma into `run_full_vetting`

**Files:**
- Modify: `backend/app/pipeline.py` — `run_full_vetting` signature + body
- Modify: `backend/app/pipeline.py` — `VettingResult` dataclass

- [ ] **Step 1: Add `detrend` field to `VettingResult`**

In `backend/app/pipeline.py`, find the `VettingResult` dataclass (around line 291-310) and add a `detrend` field. Replace the dataclass with:

```python
@dataclass
class VettingResult:
    star: StarInfo
    summary: dict = field(default_factory=dict)
    bls: dict = field(default_factory=dict)
    lomb_scargle: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    centroid: dict = field(default_factory=dict)
    odd_even: dict = field(default_factory=dict)
    secondary: dict = field(default_factory=dict)
    shape: dict = field(default_factory=dict)
    physics: dict = field(default_factory=dict)
    verdict: dict = field(default_factory=dict)
    known_object: dict = field(default_factory=dict)
    detrend: dict = field(default_factory=dict)        # NEW
    sensitivity: dict = field(default_factory=dict)    # NEW (echo applied thresholds)
    plots: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["star"] = asdict(self.star)
        return d
```

- [ ] **Step 2: Change the `run_full_vetting` signature and body**

In `backend/app/pipeline.py`, find `def run_full_vetting(` (around line 1017). Replace the entire function body up through the `return VettingResult(...)` with:

```python
def run_full_vetting(
    t: np.ndarray,
    flux: np.ndarray,
    flux_err: np.ndarray,
    quality: Optional[np.ndarray],
    mom_x: Optional[np.ndarray],
    mom_y: Optional[np.ndarray],
    star: StarInfo,
    detect_threshold: float = 0.997,
    detect_min_snr: float = 4.0,
    high_variability: bool = False,
    rotation_period_days: Optional[float] = None,
    secondary_sigma: float = 3.0,
) -> VettingResult:
    from .detrend import apply_variability_detrend

    # Clean
    t_c, f_c, fe_c = clean_lightcurve(t, flux, flux_err, quality)
    if mom_x is not None and quality is not None:
        m = np.isfinite(t) & np.isfinite(flux) & (flux > 0) & (quality == 0)
        mom_x = mom_x[m]
        mom_y = mom_y[m]

    # Stats
    span = float(t_c.max() - t_c.min())

    # Lomb-Scargle (cap at half the baseline)
    ls = run_lomb_scargle(t_c, f_c, fe_c, p_min=0.1, p_max=min(20.0, span / 2))

    # --- Optional sinusoidal detrend before BLS ----------------------------
    detrend_meta: dict
    if high_variability:
        period_for_fit = rotation_period_days or ls.get("top_period")
        source = "user_period" if rotation_period_days else "ls_peak"
        # Per-cadence noise floor in ppm — anything below this is just scatter.
        noise_floor_ppm = float(1.4826 * np.nanmedian(np.abs(f_c - 1.0))) * 1e6
        f_c, detrend_meta = apply_variability_detrend(
            t_c, f_c, period_days=period_for_fit,
            noise_floor_ppm=noise_floor_ppm, source=source,
        )
    else:
        detrend_meta = {
            "applied": False, "reason": "disabled",
            "period_days": None, "amplitude_ppm": None,
            "harmonic_amplitude_ppm": None, "rms_reduction_pct": None,
        }

    # BLS (runs on detrended residual when high_variability was enabled)
    bls = run_bls(t_c, f_c, fe_c, p_min=0.5, p_max=span * 0.7)

    # Direct event detection (user-tunable sensitivity).
    events = detect_events(
        t_c, f_c,
        threshold=detect_threshold,
        min_pts=10,
        min_snr=detect_min_snr,
    )

    # If exactly one in-sector event, anchor centroid/shape on it.
    primary_event = events[0] if len(events) == 1 else None
    if len(events) > 1:
        primary_event = max(events, key=lambda e: e["depth"])

    # Centroid
    centroid = {"available": False}
    if primary_event and mom_x is not None and mom_y is not None and len(mom_x) == len(t_c):
        centroid = centroid_check(
            t_c, mom_x, mom_y, primary_event["t_start"], primary_event["t_end"]
        )

    # Shape
    shape = {"available": False}
    if primary_event:
        shape = measure_shape(t_c, f_c, primary_event["t_start"], primary_event["t_end"])

    # Odd/even (unchanged) and secondary (now threshold-tunable)
    odd_even = odd_even_check(t_c, f_c, bls["period"], bls["t0"], bls["duration"])
    secondary = secondary_eclipse_search(
        t_c, f_c, bls["period"], bls["t0"], bls["duration"],
        secondary_sigma=secondary_sigma,
    )

    # Physics
    depth_for_physics = primary_event["depth"] if primary_event else bls.get("depth")
    t14_for_physics = shape.get("t14_d") if shape.get("available") else bls.get("duration")
    physics = physics_interpretation(star, depth_for_physics, t14_for_physics)

    # Verdict
    verdict = make_verdict(
        n_events=len(events),
        physics=physics,
        centroid=centroid,
        odd_even=odd_even,
        secondary=secondary,
        bls_sde=bls["sde"],
    )

    # External catalog cross-match (unchanged).
    known = crossmatch_known_object(star.ra, star.dec)
    if known.get("matched"):
        verdict["original_headline"] = verdict.get("headline")
        verdict["original_category"] = verdict.get("category")
        verdict["headline"] = known["headline"]
        verdict["category"] = "known_object"
        verdict["confidence"] = 0.99
        match_reason = (
            f"Catalog override: {known['headline']}"
            + (f" — {known['name']}" if known.get("name") else "")
            + (f" ({known['description']})" if known.get("description") else "")
            + (
                f" at {known['distance_arcsec']:.2f}\" from target"
                if known.get("distance_arcsec") is not None else ""
            )
            + "."
        )
        verdict.setdefault("reasons", []).insert(0, match_reason)
        verdict.setdefault("flags", []).append("known_object_override")

    # Plots — pass the pre-detrend curve so the new variability panel can show
    # raw vs fit vs residual side-by-side (see Task 7).
    plots = make_plots(
        t_c, f_c, fe_c, mom_x, mom_y, events, primary_event,
        bls.get("_periodogram"), ls, detrend_meta=detrend_meta,
    )

    summary = {
        "n_points": int(len(t_c)),
        "time_span_d": span,
        "median_cadence_min": float(np.median(np.diff(t_c)) * 1440),
        "n_events_detected": len(events),
        "scatter_mad": float(1.4826 * np.nanmedian(np.abs(f_c - 1))),
    }

    bls.pop("_periodogram", None)

    return VettingResult(
        star=star,
        summary=summary,
        bls=bls,
        lomb_scargle=ls,
        events=events,
        centroid=centroid,
        odd_even=odd_even,
        secondary=secondary,
        shape=shape,
        physics=physics,
        verdict=verdict,
        known_object=known,
        detrend=detrend_meta,
        sensitivity={
            "threshold": float(detect_threshold),
            "min_snr": float(detect_min_snr),
            "secondary_sigma": float(secondary_sigma),
        },
        plots=plots,
    )
```

- [ ] **Step 3: Write integration test that defaults are byte-stable**

Append to `backend/tests/test_detrend.py`:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `pytest backend/tests -v`
Expected: all tests PASS. If `make_plots` errors on the new `detrend_meta` kwarg, **skip ahead to Task 7 first**, then return here. (The plan ordering puts the plot change next.)

- [ ] **Step 5: Commit**

```bash
git add backend/app/pipeline.py backend/tests/test_detrend.py
git commit -m "feat(pipeline): thread high_variability + secondary_sigma into run_full_vetting"
```

---

## Task 6: Wire new params into `run_multisector_analysis`

**Files:**
- Modify: `backend/app/pipeline.py` — `run_multisector_analysis` signature

The multi-sector function itself doesn't perform per-sector fitting — that already happens upstream when each sector's `run_full_vetting` is called. But the function's signature carries `detect_threshold` / `detect_min_snr`; we extend it for symmetry so the verdict can quote them.

- [ ] **Step 1: Update signature**

Find `def run_multisector_analysis(` (around line 1188). Update its signature and the verdict block:

```python
def run_multisector_analysis(
    sector_results: list,
    period_d: float | None = None,
    t0: float | None = None,
    detect_threshold: float = 0.997,
    detect_min_snr: float = 4.0,
    high_variability: bool = False,
    secondary_sigma: float = 3.0,
    duration_tol_h: float = DURATION_MATCH_TOL_H,
) -> dict:
```

- [ ] **Step 2: Find the analysis dict assembly in the same function and add the new fields**

Locate the spot where `analysis["sector_verdicts"]` (or equivalent) is built (in `main.py`, around line 933). In `pipeline.py`, locate where `run_multisector_analysis` returns its final dict (after the objects loop — search for `return {` or the function's last lines). Add:

```python
# Echo applied user knobs so the frontend / report can quote them.
analysis_settings = {
    "detect_threshold": float(detect_threshold),
    "detect_min_snr": float(detect_min_snr),
    "high_variability": bool(high_variability),
    "secondary_sigma": float(secondary_sigma),
}
```

…and include `"settings": analysis_settings` in the final return dict.

- [ ] **Step 3: Update `_run_pipeline` and `_run_mast_multisector` callers in `main.py`**

Edit `backend/app/main.py`. Replace `_run_pipeline` (lines 139-151) with:

```python
def _run_pipeline(parsed: dict, detect_threshold: float, detect_min_snr: float,
                  high_variability: bool = False,
                  rotation_period_days: Optional[float] = None,
                  secondary_sigma: float = 3.0):
    th, snr = _clamp_params(detect_threshold, detect_min_snr)
    sec_sig = max(1.0, min(7.0, float(secondary_sigma)))
    return run_full_vetting(
        t=parsed["t"],
        flux=parsed["flux"],
        flux_err=parsed["flux_err"],
        quality=parsed["quality"],
        mom_x=parsed["mom_x"],
        mom_y=parsed["mom_y"],
        star=parsed["star"],
        detect_threshold=th,
        detect_min_snr=snr,
        high_variability=high_variability,
        rotation_period_days=rotation_period_days,
        secondary_sigma=sec_sig,
    )
```

Then in `_run_mast_multisector` (line 869) replace the `run_multisector_analysis(...)` call (line 922) with:

```python
analysis = run_multisector_analysis(
    sector_results,
    detect_threshold=query.detect_threshold,
    detect_min_snr=query.detect_min_snr,
    high_variability=query.high_variability,
    secondary_sigma=query.secondary_sigma,
)
```

And the `_run_pipeline(parsed, query.detect_threshold, query.detect_min_snr)` call at line 900 with:

```python
result = _run_pipeline(
    parsed,
    query.detect_threshold, query.detect_min_snr,
    high_variability=query.high_variability,
    rotation_period_days=query.rotation_period_days,
    secondary_sigma=query.secondary_sigma,
)
```

- [ ] **Step 4: Run all backend tests**

Run: `pytest backend/tests -v`
Expected: PASS. (Frontend still untouched — request schemas come in Task 8.)

- [ ] **Step 5: Commit**

```bash
git add backend/app/pipeline.py backend/app/main.py
git commit -m "feat(pipeline): thread new params into multi-sector runner"
```

---

## Task 7: Add "Stellar variability detrend" plot panel

**Files:**
- Modify: `backend/app/pipeline.py` — `make_plots` signature + new panel

The detrend plot needs the **pre-detrend** flux to overlay the fit and the **post-detrend** residual to show what BLS will see. We capture both upstream and pass them in.

- [ ] **Step 1: In `run_full_vetting`, capture pre-detrend flux**

Edit the section that currently does:

```python
if high_variability:
    period_for_fit = rotation_period_days or ls.get("top_period")
    source = "user_period" if rotation_period_days else "ls_peak"
    noise_floor_ppm = float(1.4826 * np.nanmedian(np.abs(f_c - 1.0))) * 1e6
    f_c, detrend_meta = apply_variability_detrend(
        t_c, f_c, period_days=period_for_fit,
        noise_floor_ppm=noise_floor_ppm, source=source,
    )
```

Replace with:

```python
f_raw_for_plot = f_c.copy() if high_variability else None
if high_variability:
    period_for_fit = rotation_period_days or ls.get("top_period")
    source = "user_period" if rotation_period_days else "ls_peak"
    noise_floor_ppm = float(1.4826 * np.nanmedian(np.abs(f_c - 1.0))) * 1e6
    f_c, detrend_meta = apply_variability_detrend(
        t_c, f_c, period_days=period_for_fit,
        noise_floor_ppm=noise_floor_ppm, source=source,
    )
```

Then update the `make_plots(...)` call:

```python
plots = make_plots(
    t_c, f_c, fe_c, mom_x, mom_y, events, primary_event,
    bls.get("_periodogram"), ls,
    detrend_meta=detrend_meta,
    f_raw=f_raw_for_plot,
)
```

- [ ] **Step 2: Update `make_plots` signature and add the new panel**

In `backend/app/pipeline.py`, change the signature of `make_plots` (line 879):

```python
def make_plots(
    t, f, fe, mom_x, mom_y, events, primary_event,
    bls_periodogram, ls_periodogram,
    detrend_meta: Optional[dict] = None,
    f_raw: Optional[np.ndarray] = None,
) -> dict:
```

Right BEFORE the existing line `# 1. Full LC with all events shaded.`, insert the new panel:

```python
    # 0. Stellar variability detrend (only when actually applied).
    if (
        detrend_meta and detrend_meta.get("applied")
        and f_raw is not None and "fit" in detrend_meta
    ):
        from .detrend import _design_matrix  # internal but stable for plot use
        fit = detrend_meta["fit"]
        X = _design_matrix(t, fit["period_days"])
        coeffs = np.array([fit["C"], fit["A1"], fit["B1"], fit["A2"], fit["B2"]])
        model = X @ coeffs
        residual = f_raw - model + 1.0

        fig, axes = plt.subplots(3, 1, figsize=(10, 6.5), sharex=True)
        axes[0].plot(t, f_raw, "k.", ms=1, alpha=0.4)
        axes[0].plot(t, model, "C1-", lw=1.0, alpha=0.9, label="sin + 1st harmonic")
        axes[0].set_ylabel("Raw flux")
        axes[0].set_title(
            f"Stellar variability detrend — P = {fit['period_days']:.3f} d, "
            f"amplitude = {detrend_meta['amplitude_ppm']:.0f} ppm, "
            f"RMS reduced {detrend_meta['rms_reduction_pct']:.1f}%"
        )
        axes[0].legend(fontsize=8, loc="upper right")

        axes[1].plot(t, model, "C1-", lw=0.8)
        axes[1].set_ylabel("Fitted model")

        axes[2].plot(t, residual, "k.", ms=1, alpha=0.4)
        axes[2].axhline(1.0, color="gray", ls=":", alpha=0.5)
        axes[2].set_ylabel("Residual (BLS in)")
        axes[2].set_xlabel("Time (BTJD or similar)")

        for ax in axes:
            ax.ticklabel_format(axis="y", useOffset=False, style="plain")
            ax.yaxis.set_major_formatter(plt.ScalarFormatter(useOffset=False))

        fig.tight_layout()
        plots["detrend"] = _fig_to_b64(fig)
```

- [ ] **Step 3: Re-run all tests**

Run: `pytest backend/tests -v`
Expected: all PASS, including `test_run_full_vetting_with_variability_toggle_runs_fit`.

- [ ] **Step 4: Add a plot-presence test**

Append to `backend/tests/test_detrend.py`:

```python
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
```

- [ ] **Step 5: Run and commit**

Run: `pytest backend/tests -v` → all PASS.

```bash
git add backend/app/pipeline.py backend/tests/test_detrend.py
git commit -m "feat(plots): add stellar-variability detrend panel (raw / fit / residual)"
```

---

## Task 8: Extend FastAPI request schemas

**Files:**
- Modify: `backend/app/main.py` — `MastQuery`, `MultisectorQuery`, `analyze`, `report`

- [ ] **Step 1: Add fields to `MastQuery`**

Replace the `MastQuery` class (around line 282-286):

```python
class MastQuery(BaseModel):
    tic_id: int
    sector: int
    detect_threshold: float = 0.997
    detect_min_snr: float = 4.0
    high_variability: bool = False
    rotation_period_days: Optional[float] = None
    secondary_sigma: float = 3.0
```

- [ ] **Step 2: Add fields to `MultisectorQuery`**

Replace the `MultisectorQuery` class (around line 421-425):

```python
class MultisectorQuery(BaseModel):
    tic_id: int
    sectors: Optional[list] = None
    detect_threshold: float = 0.997
    detect_min_snr: float = 4.0
    high_variability: bool = False
    rotation_period_days: Optional[float] = None
    secondary_sigma: float = 3.0
```

- [ ] **Step 3: Extend `analyze` and `report` upload endpoints**

Replace the `analyze` signature (around line 189-194):

```python
@app.post("/api/analyze")
async def analyze(
    file: UploadFile = File(...),
    detect_threshold: float = 0.997,
    detect_min_snr: float = 4.0,
    high_variability: bool = False,
    rotation_period_days: Optional[float] = None,
    secondary_sigma: float = 3.0,
):
```

Inside the body, change the `_run_pipeline(...)` call (around line 208) to:

```python
result = _run_pipeline(
    parsed, detect_threshold, detect_min_snr,
    high_variability=high_variability,
    rotation_period_days=rotation_period_days,
    secondary_sigma=secondary_sigma,
)
```

Do the same for the `report` endpoint (signature around line 224 + the `_run_pipeline` call around line 239).

- [ ] **Step 4: Extend `mast_analyze` and `mast_report`**

Those already take `MastQuery` and call `_mast_fetch_and_analyze(query)`. Edit `_mast_fetch_and_analyze` (around line 298): change the `_run_pipeline` call (line 327) to:

```python
result = _run_pipeline(
    parsed, query.detect_threshold, query.detect_min_snr,
    high_variability=query.high_variability,
    rotation_period_days=query.rotation_period_days,
    secondary_sigma=query.secondary_sigma,
)
```

- [ ] **Step 5: Validate range on `secondary_sigma`**

Add validation by replacing the `_clamp_params` helper (line 133):

```python
def _clamp_params(detect_threshold: float, detect_min_snr: float):
    th = max(0.95, min(0.999, float(detect_threshold)))
    snr = max(1.0, min(20.0, float(detect_min_snr)))
    return th, snr


def _validate_secondary_sigma(secondary_sigma: float) -> float:
    if not (1.0 <= float(secondary_sigma) <= 7.0):
        raise HTTPException(
            status_code=422,
            detail=f"secondary_sigma must be in [1.0, 7.0], got {secondary_sigma}.",
        )
    return float(secondary_sigma)
```

Then in `_run_pipeline` replace the inline clamp `sec_sig = max(1.0, min(7.0, float(secondary_sigma)))` with:

```python
sec_sig = _validate_secondary_sigma(secondary_sigma)
```

- [ ] **Step 6: Add API-level test**

Append to `backend/tests/test_secondary_sigma.py`:

```python
def test_api_rejects_out_of_range_secondary_sigma():
    from fastapi.testclient import TestClient
    from backend.app.main import app

    client = TestClient(app)
    # 8.0 is outside [1, 7]; FastAPI will dispatch to the endpoint, then
    # _validate_secondary_sigma should raise 422.
    r = client.post("/api/mast/analyze", json={
        "tic_id": 1, "sector": 1, "secondary_sigma": 8.0,
    })
    assert r.status_code == 422
    assert "secondary_sigma" in r.json()["detail"]
```

(`fastapi.testclient` is bundled with the existing `fastapi` install — no new dependency.)

- [ ] **Step 7: Run all tests**

Run: `pytest backend/tests -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/main.py backend/tests/test_secondary_sigma.py
git commit -m "feat(api): expose high_variability + secondary_sigma in request schemas"
```

---

## Task 9: Add detrend section to PDF report

**Files:**
- Modify: `backend/app/report.py`

- [ ] **Step 1: Inject the new section in `build_pdf`**

In `backend/app/report.py`, find the block that begins `# ---------------- Detrended light curve ----------------` (around line 322). Insert the following BEFORE that block (so the detrend story comes first when present):

```python
    # ---------------- Stellar-variability detrend (optional) ----------------
    detrend = getattr(result, "detrend", None) or {}
    if detrend.get("applied") and "detrend" in result.plots:
        amp = detrend.get("amplitude_ppm")
        period = detrend.get("period_days")
        rms_red = detrend.get("rms_reduction_pct")
        caption_bits = []
        if period is not None:
            caption_bits.append(f"P = {period:.4f} d")
        if amp is not None:
            caption_bits.append(f"amplitude {amp:.0f} ppm")
        if rms_red is not None:
            caption_bits.append(f"RMS reduced {rms_red:.1f}%")
        caption = (
            "Sinusoid + first-harmonic regression applied before BLS to "
            "suppress stellar variability. Fitted: " + ", ".join(caption_bits) + "."
        )
        story += _section(
            "Stellar variability detrend",
            _b64_image(result.plots["detrend"]),
            Paragraph(caption, styles["caption"]),
            styles=styles,
        )
        story.append(Spacer(1, 0.1 * inch))
    elif detrend.get("reason") == "skipped_low_amplitude":
        story += _section(
            "Stellar variability detrend",
            Paragraph(
                "Detrending was requested but the fitted sinusoid amplitude "
                "was below the per-cadence noise floor — no detrend applied.",
                styles["body"],
            ),
            styles=styles,
        )
        story.append(Spacer(1, 0.1 * inch))
```

- [ ] **Step 2: Same treatment in `build_multisector_pdf`**

Find `def build_multisector_pdf` (around line 704). Where it iterates per-sector verdicts/plots, add a small mention beneath the sector header when that sector's `detrend.applied` is true. Concrete patch: locate the loop that emits per-sector content; for each sector result `r`, after the sector heading, insert:

```python
        sec_detrend = getattr(r, "detrend", None) or {}
        if sec_detrend.get("applied"):
            story.append(Paragraph(
                f"Detrend: P = {sec_detrend.get('period_days'):.4f} d, "
                f"amp {sec_detrend.get('amplitude_ppm'):.0f} ppm, "
                f"RMS −{sec_detrend.get('rms_reduction_pct'):.1f}%.",
                styles["caption"],
            ))
            if "detrend" in r.plots:
                story.append(_b64_image(r.plots["detrend"], max_height=2.5 * inch))
```

(Use the same per-sector iteration variable name already present in `build_multisector_pdf` — search the function for the existing loop over sector results.)

- [ ] **Step 3: Render a sample PDF end-to-end (smoke check)**

Run a quick ad-hoc test that the PDF builder doesn't crash on the new field:

```bash
python - <<'PY'
import numpy as np
from backend.app.pipeline import run_full_vetting, StarInfo
from backend.app.report import build_pdf

t = np.linspace(0.0, 27.0, 8000)
f = 1.0 + 0.01 * np.sin(2 * np.pi * t / 4.0)
fe = np.full_like(t, 1e-3)
res = run_full_vetting(
    t, f, fe, quality=None, mom_x=None, mom_y=None, star=StarInfo(),
    high_variability=True, rotation_period_days=4.0,
)
pdf = build_pdf(res)
assert pdf[:4] == b"%PDF", f"Bad header: {pdf[:8]}"
print(f"OK: {len(pdf)} bytes")
PY
```

Expected: `OK: <N> bytes`.

- [ ] **Step 4: Commit**

```bash
git add backend/app/report.py
git commit -m "feat(report): add stellar-variability detrend section to PDFs"
```

---

## Task 10: Frontend — extend `DetectParams` and serialize new fields

**Files:**
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: Update the `DetectParams` interface and default**

Replace the existing interface + `DEFAULT_PARAMS` + `qs` (around lines 8-17) with:

```typescript
export interface DetectParams {
  threshold: number;             // 0.95..0.999
  minSnr: number;                // 1..20
  highVariability: boolean;      // toggle for sinusoid+harmonic detrend
  rotationPeriod: number | null; // optional manual rotation period (days)
  secondarySigma: number;        // 1..7, default 3
}

const DEFAULT_PARAMS: DetectParams = {
  threshold: 0.997,
  minSnr: 4.0,
  highVariability: false,
  rotationPeriod: null,
  secondarySigma: 3.0,
};

function qs(params: DetectParams = DEFAULT_PARAMS): string {
  const base = `?detect_threshold=${params.threshold}&detect_min_snr=${params.minSnr}`;
  const tail =
    `&high_variability=${params.highVariability}` +
    (params.rotationPeriod !== null ? `&rotation_period_days=${params.rotationPeriod}` : "") +
    `&secondary_sigma=${params.secondarySigma}`;
  return base + tail;
}
```

- [ ] **Step 2: Update every JSON body**

Search `api.ts` for `detect_threshold:` (used in `mastAnalyze`, `mastReport`, `fetchMultisector`, `multisectorReport`). For each, add the three new keys to the JSON body. Example for `mastAnalyze`:

```typescript
export async function mastAnalyze(ticId: number, sector: number, params: DetectParams = DEFAULT_PARAMS) {
  const r = await fetch(`${API_BASE}/api/mast/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      tic_id: ticId, sector,
      detect_threshold: params.threshold,
      detect_min_snr: params.minSnr,
      high_variability: params.highVariability,
      rotation_period_days: params.rotationPeriod,
      secondary_sigma: params.secondarySigma,
    }),
  });
  if (!r.ok) throw new Error(`MAST analyze failed (${r.status}): ${await r.text()}`);
  return r.json();
}
```

Apply the same three-key addition to `mastReport`, `fetchMultisector`, and `multisectorReport`.

- [ ] **Step 3: Type-check the frontend**

Run: `cd frontend && npx tsc --noEmit -p .`
Expected: TypeScript errors complaining that callers in `App.tsx` are constructing `DetectParams` without the new fields. That is expected — we'll fix the callers in Tasks 12–13.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat(api): expose high_variability + secondary_sigma in DetectParams"
```

---

## Task 11: Frontend types — add `DetrendBlock` to `VettingResult`

**Files:**
- Modify: `frontend/src/types.ts`

- [ ] **Step 1: Add interface and field**

In `frontend/src/types.ts`, BEFORE the `VettingResult` interface (after line 70), add:

```typescript
export interface DetrendBlock {
  applied: boolean;
  reason: "user_period" | "ls_peak" | "skipped_low_amplitude" | "disabled";
  period_days: number | null;
  amplitude_ppm: number | null;
  harmonic_amplitude_ppm: number | null;
  rms_reduction_pct: number | null;
}

export interface SensitivityEcho {
  threshold: number;
  min_snr: number;
  secondary_sigma: number;
}
```

Then in `VettingResult` (around lines 72-90), add the two new optional fields just above `plots`:

```typescript
  detrend?: DetrendBlock;
  sensitivity?: SensitivityEcho;
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/types.ts
git commit -m "feat(types): add DetrendBlock and SensitivityEcho on VettingResult"
```

---

## Task 12: Frontend — "High stellar variability" toggle next to scope buttons

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Update the initial `params` state**

In `frontend/src/App.tsx` find line 93:

```typescript
const [params, setParams] = useState<DetectParams>({ threshold: 0.997, minSnr: 4.0 });
```

Replace with:

```typescript
const [params, setParams] = useState<DetectParams>({
  threshold: 0.997,
  minSnr: 4.0,
  highVariability: false,
  rotationPeriod: null,
  secondarySigma: 3.0,
});
```

- [ ] **Step 2: Add the toggle UI**

Find the scope-button block — search for `Analysis scope` (around line 357). Immediately after the closing `</div>` of the "scope" block (just before the `<div className="grid sm:grid-cols-3 gap-3 items-end">` for TIC/Sector inputs, around line 384), insert:

```tsx
            {/* High stellar variability — fit + subtract a sinusoid before BLS. */}
            <div className="mb-4 p-3 bg-slate-50 rounded border border-slate-200">
              <label className="flex items-center gap-2 text-sm font-medium text-slate-700">
                <input
                  type="checkbox"
                  checked={params.highVariability}
                  onChange={(e) =>
                    setParams({ ...params, highVariability: e.target.checked })
                  }
                />
                High stellar variability (detrend before BLS)
              </label>
              {params.highVariability && (
                <div className="mt-2 flex items-center gap-2 text-xs text-slate-600">
                  <label>Expected rotation period (days, optional):</label>
                  <input
                    type="number"
                    step="0.001"
                    min="0"
                    value={params.rotationPeriod ?? ""}
                    placeholder="auto (Lomb-Scargle peak)"
                    onChange={(e) => {
                      const v = e.target.value.trim();
                      setParams({
                        ...params,
                        rotationPeriod: v === "" ? null : parseFloat(v),
                      });
                    }}
                    className="border rounded px-2 py-0.5 font-mono w-40 text-xs"
                  />
                </div>
              )}
              <p className="text-[11px] text-slate-500 mt-1">
                Fits a sine + first harmonic and subtracts it before BLS. Helps
                detect shallow dips on spotted rotators and wave-like variables.
              </p>
            </div>
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && npx tsc --noEmit -p .`
Expected: no errors for the toggle. (Errors for the slider still — fixed in Task 13.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(ui): add 'High stellar variability' toggle next to scope buttons"
```

---

## Task 13: Frontend — Secondary-eclipse σ slider in `SensitivityPanel`

**Files:**
- Modify: `frontend/src/App.tsx` — `SensitivityPanel` component

- [ ] **Step 1: Update the `isDefault` test**

Find (around line 538):

```typescript
const isDefault = params.threshold === 0.997 && params.minSnr === 4.0;
```

Replace with:

```typescript
const isDefault =
  params.threshold === 0.997 &&
  params.minSnr === 4.0 &&
  params.secondarySigma === 3.0;
```

- [ ] **Step 2: Add the slider control**

Inside the `SensitivityPanel` collapsible body, after the existing min-SNR block (just before the closing `</div>` containing the "Tip:" paragraph, around line 647), insert:

```tsx
          <div>
            <label className="flex justify-between text-xs font-medium text-slate-700 mb-1">
              <span>
                Secondary eclipse σ:{" "}
                <span className="font-mono">{params.secondarySigma.toFixed(1)}σ</span>
                <span className="text-slate-400 ml-1">
                  (depth at phase 0.5 must exceed this × local scatter)
                </span>
              </span>
              <button
                onClick={() => setParams({ ...params, secondarySigma: 3.0 })}
                className="text-blue-600 hover:underline"
              >
                reset
              </button>
            </label>
            <input
              type="range"
              min={1.0}
              max={7.0}
              step={0.1}
              value={params.secondarySigma}
              onChange={(e) =>
                setParams({ ...params, secondarySigma: parseFloat(e.target.value) })
              }
              className="w-full"
            />
            <div className="flex justify-between text-[10px] text-slate-400 mt-0.5">
              <span>1σ (very loose, more EB flags)</span>
              <span>3σ default</span>
              <span>7σ (very strict)</span>
            </div>
          </div>
```

- [ ] **Step 3: Type-check the whole frontend**

Run: `cd frontend && npx tsc --noEmit -p .`
Expected: zero errors.

- [ ] **Step 4: Build the bundle**

Run: `cd frontend && npm run build`
Expected: a fresh `dist/` with no warnings about unused symbols.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(ui): add secondary-eclipse sigma slider to SensitivityPanel"
```

---

## Task 14: Render detrend plot in the results view with Share button

**Files:**
- Modify: `frontend/src/App.tsx` — `ResultsView` (or wherever existing plots render)

- [ ] **Step 1: Locate the existing plot rendering**

Find the section of `App.tsx` that renders `result.plots.lightcurve`, `result.plots.bls`, etc. Search for `result.plots["lightcurve"]` or `result.plots.lightcurve`. (It is inside `ResultsView` — search `function ResultsView(`.) Note the visual style and the existing `<ShareToImgbbButton>` usage from Tasks already reviewed in `ExoMinerPanel.tsx`.

- [ ] **Step 2: Insert the new detrend figure block**

Immediately BEFORE the existing rendering of `result.plots.lightcurve`, add:

```tsx
{result.detrend?.applied && result.plots.detrend && (
  <figure className="space-y-1 mb-4">
    <div className="flex items-center justify-between">
      <figcaption className="text-xs text-slate-500 font-medium">
        Stellar variability detrend — P ={" "}
        {result.detrend.period_days?.toFixed(4)} d, amplitude{" "}
        {result.detrend.amplitude_ppm?.toFixed(0)} ppm, RMS reduced{" "}
        {result.detrend.rms_reduction_pct?.toFixed(1)}%
      </figcaption>
      <ShareToImgbbButton
        base64={result.plots.detrend}
        title={`detrend_TIC${result.star.tic_id ?? ""}_S${result.star.sector ?? ""}`}
        label="Stellar variability detrend"
      />
    </div>
    <img
      src={`data:image/png;base64,${result.plots.detrend}`}
      alt="Stellar variability detrend"
      className="w-full rounded border border-slate-200"
    />
  </figure>
)}
{result.detrend?.reason === "skipped_low_amplitude" && (
  <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded p-2 mb-3">
    Detrending was requested but the fitted sinusoid amplitude was below the
    per-cadence noise floor — no detrend applied.
  </p>
)}
```

The `ShareToImgbbButton` import already exists at the top of `App.tsx` (line 21).

- [ ] **Step 3: Manual UI verification**

Run `cd frontend && npm run dev` in one terminal and the backend (`uvicorn backend.app.main:app --reload`) in another. In the browser:

1. Switch to MAST mode, enter a known variable TIC (e.g. `38846515` — a spotted star), pick a sector.
2. Toggle "High stellar variability". Leave rotation period blank.
3. Click "Fetch & vet".
4. Verify the "Stellar variability detrend" panel appears above the light-curve plot, the Share button is visible, and clicking it uploads to ImgBB and shows copy-URL/MD/BBC chips.
5. Toggle off → re-run → the detrend panel should disappear.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(ui): render detrend plot with Share button in results"
```

---

## Task 15: Add glossary entries

**Files:**
- Modify: `frontend/src/glossary.ts`

- [ ] **Step 1: Append the four new glossary keys**

Open `frontend/src/glossary.ts`. Find the closing `}` of the glossary object literal (whatever shape it has — current code shows `"confirmed_multisector":` so it's a flat key→string map). Just BEFORE that closing brace, append:

```typescript
  "high_stellar_variability":
    "When ticked, the pipeline fits a sine wave plus first harmonic to the light curve at the user-supplied rotation period (or the Lomb-Scargle peak if blank) and subtracts it before running BLS. Useful for spotted rotators and other wave-like variables where the rotation signal would otherwise mask shallow planetary dips.",
  "rotation_period_days":
    "Optional period (in days) of the dominant stellar rotation signal to subtract before BLS. Leave blank to let the pipeline use its Lomb-Scargle top peak instead.",
  "secondary_sigma":
    "Detection threshold for the secondary-eclipse search at orbital phase 0.5. A candidate is flagged when the dip at phase 0.5 exceeds this many σ above the out-of-transit scatter. Lower values flag more eclipsing-binary candidates (more false positives); higher values are stricter.",
  "detrend_amplitude":
    "Peak amplitude of the fitted sinusoid (square-root of A1² + B1²), expressed in parts per million of the median flux. Quantifies how much rotational variability the detrender removed.",
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npx tsc --noEmit -p .`
Expected: zero errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/glossary.ts
git commit -m "docs(glossary): add entries for new variability + secondary-sigma controls"
```

---

## Task 16: Backwards-compat smoke test

**Files:**
- Modify: `backend/tests/test_detrend.py`

- [ ] **Step 1: Add a regression test that defaults preserve old behaviour**

Append to `backend/tests/test_detrend.py`:

```python
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
    # Same period & SDE → same BLS pass.
    assert a.bls["period"] == b.bls["period"]
    assert a.bls["sde"] == b.bls["sde"]
    # Same secondary detection state.
    assert a.secondary.get("detected") == b.secondary.get("detected")
    assert a.verdict["category"] == b.verdict["category"]
    # New blocks present but inert.
    assert a.detrend["applied"] is False
    assert a.sensitivity["secondary_sigma"] == 3.0
```

- [ ] **Step 2: Run the full backend test suite**

Run: `pytest backend/tests -v`
Expected: every test PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_detrend.py
git commit -m "test: regression-lock default behaviour against pre-change pipeline"
```

---

## Self-review

**Spec coverage:**
- UI placement (variability toggle next to scope; sigma in sensitivity panel) — Tasks 12, 13. ✓
- Detrend module `fit_sinusoid` + `apply_detrend` + low-amp skip — Tasks 2, 3. ✓
- Pipeline wiring single-sector — Task 5. ✓
- Pipeline wiring multi-sector — Task 6 (per-sector fit happens through `_run_pipeline`; multi-sector wrapper just propagates the params). ✓
- `secondary_eclipse_search` parameterisation + `pipeline.py:637` change — Task 4. ✓
- Request schemas + server-side `[1.0, 7.0]` clamp + 422 — Task 8. ✓
- Result schema `detrend` + `sensitivity` echo — Task 5 (Step 1 dataclass; Step 2 populates both). ✓
- New PDF section single + multi — Task 9. ✓
- Site display + ShareToImgbbButton — Task 14. ✓
- Glossary — Task 15. ✓
- Tests (`test_detrend.py`, `test_secondary_sigma.py`, regression) — Tasks 2, 3, 4, 5, 7, 8, 16. ✓

**Placeholder scan:** no "TBD" / "TODO" / "similar to" left. All code blocks contain full code.

**Type consistency:** `DetectParams` fields used in App.tsx (`highVariability`, `rotationPeriod`, `secondarySigma`) match the names defined in `api.ts` (Task 10). Backend kwarg names (`high_variability`, `rotation_period_days`, `secondary_sigma`) match Pydantic field names and `_run_pipeline` parameter names. The `detrend` dict keys returned by `apply_variability_detrend` (Task 3) match what `report.py` and `App.tsx` consume (`applied`, `reason`, `period_days`, `amplitude_ppm`, `harmonic_amplitude_ppm`, `rms_reduction_pct`). The internal `fit` key is read by `make_plots` in Task 7 (added when `applied=True`).
