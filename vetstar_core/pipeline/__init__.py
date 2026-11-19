"""Vetting pipeline: BLS, Lomb-Scargle, centroid check, odd/even, secondary
eclipse search, shape analysis, verdict.

All functions are pure-ish: they take arrays + metadata and return dicts of
results. Plotting helpers return base64-encoded PNGs so the API can ship
them straight to the frontend.

Split by job (import from this package; the submodules are internal):
    models       data containers and match tolerances
    catalogs     external catalog cross-match and known-object overrides
    detection    cleaning, Lomb-Scargle, BLS and event detection
    verdict      vetting checks and the final verdict
    plots        matplotlib figures as base64 PNGs
    vetting      run_full_vetting, the single-sector driver
    multisector  run_multisector_analysis
"""
from __future__ import annotations

from .models import (  # noqa: F401
    COMPANION_EB_HARD_RJUP,
    COMPANION_PLANET_CAP_RJUP,
    DURATION_MATCH_TOL_H,
    PERIOD_MATCH_TOL_FRAC,
    StarInfo,
    VettingResult,
    durations_consistent,
    periods_consistent,
)
from .catalogs import (  # noqa: F401
    CROSSMATCH_RADIUS_ARCSEC,
    CROSSMATCH_TIMEOUT_S,
    apply_known_object_override,
    crossmatch_known_object,
)
from .detection import (  # noqa: F401
    BLS_KNOWN_PERIOD_TOL_FRAC,
    clean_lightcurve,
    detect_events,
    bls_variability_check,
    run_bls,
    run_bls_constrained,
    run_lomb_scargle,
)
from .verdict import (  # noqa: F401
    centroid_check,
    count_events_by_parity,
    make_verdict,
    measure_shape,
    odd_even_check,
    physics_interpretation,
    secondary_eclipse_search,
)
from .plots import (  # noqa: F401
    make_plots,
)
from .vetting import (  # noqa: F401
    run_full_vetting,
)
from .multisector import (  # noqa: F401
    EVENTS_PER_SECTOR,
    MAX_OBJECTS,
    MAX_OBJECTS_HARD_CAP,
    MAX_SECTORS,
    run_multisector_analysis,
)

__all__ = [
    "COMPANION_EB_HARD_RJUP",
    "COMPANION_PLANET_CAP_RJUP",
    "DURATION_MATCH_TOL_H",
    "PERIOD_MATCH_TOL_FRAC",
    "StarInfo",
    "VettingResult",
    "durations_consistent",
    "periods_consistent",
    "CROSSMATCH_RADIUS_ARCSEC",
    "CROSSMATCH_TIMEOUT_S",
    "apply_known_object_override",
    "crossmatch_known_object",
    "BLS_KNOWN_PERIOD_TOL_FRAC",
    "clean_lightcurve",
    "detect_events",
    "bls_variability_check",
    "run_bls",
    "run_bls_constrained",
    "run_lomb_scargle",
    "centroid_check",
    "count_events_by_parity",
    "make_verdict",
    "measure_shape",
    "odd_even_check",
    "physics_interpretation",
    "secondary_eclipse_search",
    "make_plots",
    "run_full_vetting",
    "EVENTS_PER_SECTOR",
    "MAX_OBJECTS",
    "MAX_OBJECTS_HARD_CAP",
    "MAX_SECTORS",
    "run_multisector_analysis",
]
