"""
TIC v8 stellar-parameter lookup.

ExoFOP often returns a target with empty stellar fields, and FFI/QLP light
curve headers (the fallback products for recent sectors) usually lack
TEFF/RADIUS/LOGG.  In both cases we still need Teff, R*, M* (and distance)
to compute the POE observables, the physical interpretation, and the
density-derived stellar mass.

The TIC v8 catalogue carries these for essentially every TESS target, so we
query it directly via astroquery whenever those values are missing — not
only when ExoFOP is unreachable.

`fetch_tic_stellar` is defensive: it never raises, returns {} on any failure,
and caches per-process so repeated HCI/observables calls for the same TIC
hit the network only once.
"""
from __future__ import annotations

import logging
import math
from functools import lru_cache

log = logging.getLogger(__name__)


def _clean(v):
    """Convert masked/NaN/None to None, else float."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return f


@lru_cache(maxsize=512)
def fetch_tic_stellar(tic_id: int) -> dict:
    """
    Return a dict of stellar parameters from TIC v8:
        {teff, radius, mass, logg, distance, tmag, ra, dec}
    Missing values are omitted (not set to None) so callers can use
    ``star.setdefault`` / ``or`` merges cleanly. Returns {} on any failure.
    """
    try:
        from astroquery.mast import Catalogs
    except Exception as e:  # astroquery not installed / import error
        log.warning("astroquery unavailable for TIC lookup: %s", e)
        return {}

    try:
        tab = Catalogs.query_criteria(catalog="TIC", ID=int(tic_id))
    except Exception as e:
        log.warning("TIC v8 query failed for TIC %s: %s", tic_id, e)
        return {}

    if tab is None or len(tab) == 0:
        return {}

    row = tab[0]

    def g(col):
        try:
            return _clean(row[col])
        except Exception:
            return None

    out = {
        "teff": g("Teff"),
        "radius": g("rad"),
        "mass": g("mass"),
        "logg": g("logg"),
        "distance": g("d"),       # parsecs
        "tmag": g("Tmag"),
        "ra": g("ra"),
        "dec": g("dec"),
    }
    # Drop missing keys so `existing or fetched` and setdefault work as intended.
    return {k: v for k, v in out.items() if v is not None}


def backfill_star(star: dict, tic_id: int) -> tuple[dict, bool]:
    """
    Fill any missing teff/radius/mass (and logg/distance) on ``star`` from
    TIC v8. Only queries the catalogue when at least one of the core
    parameters is absent. Returns (star, used_tic_v8).
    """
    star = dict(star or {})
    core = ("radius", "teff", "mass")
    if all(star.get(k) is not None for k in core):
        return star, False

    tic = fetch_tic_stellar(tic_id)
    if not tic:
        return star, False

    used = False
    for k, v in tic.items():
        if star.get(k) is None and v is not None:
            star[k] = v
            used = True
    return star, used
