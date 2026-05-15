"""
Robust MAST fetcher for TESS light curves.

Strategy:
  1. Search by ``objectname="TIC <n>"`` (fast, works for most targets).
  2. Fall back to TIC catalog → RA/Dec → cone search (catches aliases).
  3. Filter results by sector, then prefer SPOC 2-min → SPOC 20-s →
     TESS-SPOC → QLP, falling back through that order.
  4. All MAST calls wrapped in retry-with-exponential-backoff.

Returns enough metadata (author, exptime, fallback flag) for the frontend
to show "data source" banners.
"""

from __future__ import annotations

import logging
import random
import time
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# Author/cadence preference order. The fetcher walks this list and uses the
# first match. SPOC 2-min comes first because it has CROWDSAP + centroid
# columns which power the blend test; FFI products lack centroids.
DEFAULT_AUTHORS: List[Tuple[str, int]] = [
    ("SPOC", 120),
    ("SPOC", 20),
    ("TESS-SPOC", 600),
    ("QLP", 1800),
]

# 30" cone covers the TESS pixel (21"/px) plus a small buffer.
CONE_RADIUS_DEG = 30 / 3600.0


# -------------------------------------------------
# Retry helper
# -------------------------------------------------

def retry(func, retries=3, name="operation"):
    """Run ``func`` with exponential backoff. Raises RuntimeError after
    ``retries`` failed attempts, bubbling the final exception text."""
    for attempt in range(1, retries + 1):
        try:
            return func()
        except Exception as e:
            if attempt == retries:
                raise RuntimeError(f"{name} failed after {retries} attempts: {e}")
            delay = 1.5 * (2 ** (attempt - 1)) + random.random()
            log.warning(f"{name} failed (attempt {attempt}/{retries}): {e}")
            time.sleep(delay)


# -------------------------------------------------
# Row access helper — astropy Table rows don't support .get()
# -------------------------------------------------

def safe_get(row, key, default=None):
    """Read a field from either an astropy Table row, a dict, or any
    mapping-ish object. Handles masked values and missing keys."""
    try:
        v = row[key]
    except Exception:
        return default
    try:
        import numpy as _np
        if _np.ma.is_masked(v):
            return default
    except Exception:
        pass
    if v is None:
        return default
    return v


# -------------------------------------------------
# Observation search (multi-strategy)
# -------------------------------------------------

def find_observations(tic_id: int, sector: Optional[int] = None):
    """Return all TESS observations for ``tic_id``, optionally pre-filtered
    by sector. Tries ``objectname`` first, then cone search via TIC catalog
    coordinates."""
    from astroquery.mast import Catalogs, Observations

    criteria = {"obs_collection": "TESS"}
    if sector is not None:
        criteria["sequence_number"] = sector

    # Strategy 1: query by object name.
    def q1():
        return Observations.query_criteria(
            objectname=f"TIC {tic_id}",
            **criteria,
        )

    try:
        obs = retry(q1, name=f"query by TIC name (sector={sector})")
        if len(obs) > 0:
            log.info(f"Found {len(obs)} observations via TIC name")
            return obs
    except RuntimeError as e:
        log.warning(f"TIC name lookup failed: {e}")
        obs = None

    # Strategy 2: cone search via TIC catalog coordinates.
    log.info("Falling back to coordinate cone search")

    def q_cat():
        return Catalogs.query_criteria(catalog="TIC", ID=int(tic_id))

    try:
        cat = retry(q_cat, name="TIC catalog lookup")
    except RuntimeError as e:
        raise RuntimeError(
            f"TIC {tic_id} could not be resolved at MAST: {e}. "
            f"Check the TIC ID is correct."
        )
    if len(cat) == 0:
        raise RuntimeError(
            f"TIC {tic_id} not found in MAST's TIC catalog. "
            f"Double-check the TIC ID — it may not exist."
        )

    ra = float(cat["ra"][0])
    dec = float(cat["dec"][0])

    def q_cone():
        # Use query_criteria with the sector filter applied. Provide both
        # the coordinates and the radius so we get a proper cone search.
        return Observations.query_criteria(
            coordinates=f"{ra} {dec}",
            radius=CONE_RADIUS_DEG,
            **criteria,
        )

    obs = retry(q_cone, name="cone search")
    log.info(f"Found {len(obs)} observations via cone search at ({ra:.4f}, {dec:.4f})")
    return obs


# -------------------------------------------------
# Fetch light curve (with author preference + fallback)
# -------------------------------------------------

def fetch_spoc_lightcurve(
    tic_id: int,
    sector: int,
    authors: Optional[List[Tuple[str, int]]] = None,
    **_unused_kwargs,
) -> dict:
    """Fetch a TESS light curve for (TIC, sector), trying author/cadence
    combinations in preference order. Returns a dict with at least:

        path, filename, author, exptime, fallback, matched, obs_id
    """
    from astroquery.mast import Observations

    if authors is None:
        authors = DEFAULT_AUTHORS

    log.info(f"Fetching TIC {tic_id}, sector {sector}")

    # 1. Get all sector-matching observations (sector filter applied
    #    server-side for efficiency).
    obs = find_observations(tic_id, sector=sector)
    if len(obs) == 0:
        # Be helpful: list which sectors DO have data.
        try:
            all_obs = find_observations(tic_id, sector=None)
            sectors_all = sorted({
                int(s) for s in (safe_get(r, "sequence_number") for r in all_obs)
                if s is not None
            })
        except Exception:
            sectors_all = []
        msg = f"No TESS observations found for TIC {tic_id} in sector {sector}."
        if sectors_all:
            msg += (
                f" This TIC has products in sectors: "
                f"{', '.join(str(s) for s in sectors_all)}. Try one of those."
            )
        else:
            msg += " This TIC has no TESS observations archived at MAST."
        raise RuntimeError(msg)

    log.info(f"{len(obs)} observations match sector {sector}")

    # 2. Walk author preferences. For each, filter the observations by
    #    provenance + exptime; first non-empty wins.
    chosen = None
    chosen_author = None
    chosen_exptime = None
    tried = []
    for author, exptime in authors:
        try:
            mask = []
            for o in obs:
                prov = str(safe_get(o, "provenance_name", ""))
                et = safe_get(o, "t_exptime", 0)
                try:
                    et = float(et) if et is not None else 0.0
                except (TypeError, ValueError):
                    et = 0.0
                mask.append(prov == author and abs(et - exptime) < 1.5)
            filtered = obs[mask]
        except Exception as e:
            tried.append(f"{author}/{exptime}s [filter error: {e}]")
            continue
        if len(filtered) > 0:
            chosen = filtered
            chosen_author = author
            chosen_exptime = exptime
            tried.append(f"{author}/{exptime}s [matched {len(filtered)}]")
            break
        tried.append(f"{author}/{exptime}s [0]")

    # 3. Last-ditch: take any provenance.
    if chosen is None or len(chosen) == 0:
        chosen = obs
        chosen_author = str(safe_get(obs[0], "provenance_name", "unknown"))
        chosen_exptime = float(safe_get(obs[0], "t_exptime", 0) or 0)
        tried.append(f"any provenance [matched {chosen_author}/{chosen_exptime}s]")

    # 4. Get products and filter to light-curve FITS.
    def get_products():
        return Observations.get_product_list(chosen)

    products = retry(get_products, name="get product list")
    if len(products) == 0:
        raise RuntimeError(
            f"No products returned for {chosen_author}/{chosen_exptime}s. Tried: {tried}"
        )

    lc_products = Observations.filter_products(
        products,
        productSubGroupDescription="LC",
        extension="fits",
    )
    if len(lc_products) == 0:
        log.warning("No LC products with SubGroup=LC; falling back to any *.fits")
        lc_products = Observations.filter_products(products, extension="fits")
    if len(lc_products) == 0:
        raise RuntimeError(f"No FITS light-curve products available. Tried: {tried}")

    # 5. Download.
    def download():
        return Observations.download_products(lc_products[:1])

    manifest = retry(download, name="download")
    if len(manifest) == 0 or "Local Path" not in manifest.colnames:
        raise RuntimeError("Download returned empty manifest")

    file_path = str(manifest["Local Path"][0])
    log.info(f"Downloaded: {file_path}")

    fallback = chosen_author != "SPOC" or (chosen_exptime and chosen_exptime != 120)
    return {
        "path": file_path,
        "filename": file_path,
        "obs_id": str(safe_get(lc_products[0], "obsID", "")),
        "author": chosen_author,
        "exptime": float(chosen_exptime or 0),
        "matched": int(len(chosen)),
        "fallback": bool(fallback),
        "tried": tried,
        "sector": sector,
    }


# -------------------------------------------------
# List sectors (returns providers so the UI can color SPOC-only sectors)
# -------------------------------------------------

def list_available_sectors(tic_id: int) -> list:
    """Return ``[{"sector": N, "providers": ["SPOC", "QLP", ...]}, ...]``."""
    try:
        obs = find_observations(tic_id, sector=None)
    except RuntimeError:
        return []
    if len(obs) == 0:
        return []
    by_sector: dict = {}
    for row in obs:
        s = safe_get(row, "sequence_number")
        if s is None:
            continue
        try:
            s = int(s)
        except (TypeError, ValueError):
            continue
        prov = str(safe_get(row, "provenance_name", "unknown"))
        by_sector.setdefault(s, set()).add(prov)
    return [
        {"sector": s, "providers": sorted(p)}
        for s, p in sorted(by_sector.items())
    ]

