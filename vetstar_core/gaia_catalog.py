"""
Gaia DR3 stellar-parameter backfill.

ExoFOP + TIC v8 cover the bright/well-studied targets, but many faint TICs
(e.g. TIC 330014070 / TOI-3200) ship with nothing but Tmag, leaving the
observables panel almost empty. Gaia DR3 routinely carries Teff/logg from
GSP-Phot together with a Bayesian distance (distance_gspphot) and a FLAME
radius for solar-type stars — enough to fill in luminosity, HZ in mas,
astrometric Δθ, and the max projected separation.

Lookup is by sky position so we do not have to thread a Gaia source_id
through the rest of the pipeline. Defensive throughout: never raises,
returns {} on any failure, cached per process.
"""
from __future__ import annotations

import logging
import math
from functools import lru_cache

log = logging.getLogger(__name__)


def _clean(v):
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
def fetch_gaia_stellar(ra_deg: float, dec_deg: float, radius_arcsec: float = 3.0) -> dict:
    """
    Return Gaia DR3 stellar parameters for the brightest source within
    ``radius_arcsec`` of the target coordinates. Keys are the same canonical
    names used by the rest of the backfill chain (teff, radius, mass, logg,
    distance) so callers can merge results uniformly.

    Returns {} on any failure (astroquery missing, network down, no match).
    """
    if ra_deg is None or dec_deg is None:
        return {}
    try:
        from astroquery.vizier import Vizier
        from astropy.coordinates import SkyCoord
        import astropy.units as u
    except Exception as e:
        log.warning("astroquery/astropy unavailable for Gaia lookup: %s", e)
        return {}

    try:
        v = Vizier(
            columns=["Source", "Gmag", "Teff", "logg", "Rad", "Dist", "Plx"],
            row_limit=5,
        )
        coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
        tabs = v.query_region(
            coord, radius=radius_arcsec * u.arcsec, catalog="I/355/gaiadr3"
        )
    except Exception as e:
        log.warning("Gaia DR3 query failed at (%.4f, %.4f): %s", ra_deg, dec_deg, e)
        return {}

    if not tabs or len(tabs[0]) == 0:
        return {}

    tab = tabs[0]
    # Pick the brightest source in the cone — Gaia G is reliable.
    if "Gmag" in tab.colnames:
        try:
            tab.sort("Gmag")
        except Exception:
            pass
    row = tab[0]

    def g(col):
        try:
            return _clean(row[col]) if col in tab.colnames else None
        except Exception:
            return None

    dist = g("Dist")
    # Bailer-Jones-style distance from parallax as a last resort (parallax in mas
    # → distance in pc = 1000 / plx). Only trust positive parallaxes.
    if dist is None:
        plx = g("Plx")
        if plx and plx > 0:
            dist = 1000.0 / plx

    out = {
        "teff":     g("Teff"),
        "radius":   g("Rad"),
        "logg":     g("logg"),
        "distance": dist,
    }
    return {k: v for k, v in out.items() if v is not None}


def backfill_star_gaia(star: dict, ra_deg: float, dec_deg: float) -> tuple[dict, bool]:
    """
    Fill any missing teff/radius/logg/distance on ``star`` from Gaia DR3
    using sky-position cross-match. Returns (star, used_gaia).
    """
    star = dict(star or {})
    wanted = ("radius", "teff", "mass", "logg", "distance")
    if all(star.get(k) is not None for k in wanted):
        return star, False

    gaia = fetch_gaia_stellar(ra_deg, dec_deg)
    if not gaia:
        return star, False

    used = False
    for k, v in gaia.items():
        if star.get(k) is None and v is not None:
            star[k] = v
            used = True
    return star, used
