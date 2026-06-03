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


def test_validate_secondary_sigma_rejects_out_of_range():
    """The API-level validator raises HTTP 422 outside [0.5, 20.0].

    Using the validator directly rather than going through TestClient because
    every endpoint that exercises _run_pipeline first requires either a real
    FITS upload or a successful MAST fetch — both of which would mask the
    422 with an unrelated 500/502. Testing the validator directly verifies
    the exact behaviour every endpoint inherits.
    """
    from fastapi import HTTPException
    import pytest
    from backend.app.main import _validate_secondary_sigma

    assert _validate_secondary_sigma(3.0) == 3.0
    assert _validate_secondary_sigma(0.5) == 0.5
    assert _validate_secondary_sigma(20.0) == 20.0
    with pytest.raises(HTTPException) as exc:
        _validate_secondary_sigma(25.0)
    assert exc.value.status_code == 422
    assert "secondary_sigma" in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        _validate_secondary_sigma(0.1)
    assert exc.value.status_code == 422
