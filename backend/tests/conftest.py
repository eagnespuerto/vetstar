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
