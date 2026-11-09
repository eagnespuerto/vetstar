"""Data containers and match tolerances shared across the pipeline.
"""
from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from typing import Optional

import numpy as np


# ----------------------------------------------------------------------
# Tolerances
# ----------------------------------------------------------------------
# How far two transit/eclipse durations may differ (in HOURS) and still be
# treated as "the same" event — across cycles within a sector, or across
# sectors in a multi-sector run. Mature vetting pipelines allow a small
# slop here so that noise-driven width differences are NOT mistaken for the
# unequal primary/secondary durations that betray an eclipsing binary.
# Tune this if real signals are being over-flagged as EBs.
DURATION_MATCH_TOL_H = 0.05  # hours

# Fractional tolerance for calling two periods "the same" across sectors.
PERIOD_MATCH_TOL_FRAC = 0.02  # 2%

# Companion-size thresholds used by the verdict. A radius just above the
# planetary cap is BORDERLINE — mature pipelines do not call an eclipsing
# binary on size alone; they need an eclipse signature (secondary / odd-even)
# or an RV mass. Only a radius well above the cap is treated as unambiguously
# stellar/brown-dwarf sized.
COMPANION_PLANET_CAP_RJUP = 2.2   # largest plausible (inflated) giant planet
COMPANION_EB_HARD_RJUP = 4.0      # above this: unambiguously not a planet


def durations_consistent(durations_h, tol_h: float = DURATION_MATCH_TOL_H) -> bool:
    """True if all finite durations (hours) lie within ``tol_h`` of their median.

    A single value (or none) is trivially consistent. This is the absolute
    test used when the durations being compared are already robust medians
    (e.g. odd-vs-even transit durations, or one representative event per
    sector) rather than raw, noise-broadened single-event widths.
    """
    arr = np.asarray(
        [d for d in durations_h if d is not None and np.isfinite(d)], dtype=float
    )
    if arr.size < 2:
        return True
    return bool(np.all(np.abs(arr - np.median(arr)) <= tol_h))


def periods_consistent(periods_d, tol_frac: float = PERIOD_MATCH_TOL_FRAC) -> bool:
    """True if all finite periods agree to within ``tol_frac`` of their median."""
    arr = np.asarray(
        [p for p in periods_d if p is not None and np.isfinite(p) and p > 0],
        dtype=float,
    )
    if arr.size < 2:
        return True
    med = np.median(arr)
    return bool(med > 0 and np.all(np.abs(arr - med) / med <= tol_frac))


# ----------------------------------------------------------------------
# Data containers
# ----------------------------------------------------------------------
@dataclass
class StarInfo:
    tic_id: Optional[int] = None
    tmag: Optional[float] = None
    teff: Optional[float] = None
    radius: Optional[float] = None        # R_sun
    logg: Optional[float] = None
    mass: Optional[float] = None          # M_sun (often derived)
    ra: Optional[float] = None
    dec: Optional[float] = None
    sector: Optional[int] = None
    camera: Optional[int] = None
    ccd: Optional[int] = None
    crowdsap: Optional[float] = None
    source: str = "unknown"               # "fits" or "exofop_json"


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
