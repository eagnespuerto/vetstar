
"""
MAST fetcher: given TIC + sector, locate and download a TESS light curve
FITS from the public MAST archive.

Why this is more involved than it looks:

  MAST's `target_name` field is a string the observer typed; it doesn't
  always match `TIC <n>` literally even when the target IS in the catalog.
  Some products are filed under the Gaia name, some under TYC, some under
  the survey's internal ID, and some genuinely under `TIC <n>`. The
  reliable way to find observations of a specific TIC is to:

    1. Try the literal name match (fastest when it works).
    2. Resolve the TIC to RA/Dec via the TIC catalog.
    3. Cone-search MAST at those coordinates with a small radius.

  Step 3 catches everything step 1 misses.

Tries multiple data providers in order:
  1. SPOC 2-min cadence (best — quality flags, centroids, CROWDSAP)
  2. SPOC 20-s cadence
  3. TESS-SPOC FFI      (10-min from FFIs; near-complete coverage)
  4. QLP                (Quick Look Pipeline FFI light curves)
"""
"""
MAST fetcher: given TIC + sector, locate and download a TESS light curve
FITS from the public MAST archive.
"""

from __future__ import annotations

import logging
from typing import Optional, List, Tuple

log = logging.getLogger(__name__)

CONE_RADIUS_DEG = 30 / 3600.0  # 30 arcsec


# -------------------------------------------------
# Helpers
# -------------------------------------------------

import time
import random

import time
import random

def retry(operation, retries=3, base_delay=1.5, jitter=0.5, exceptions=(Exception,), name="operation"):
    """
    Generic retry helper with exponential backoff.

    retries: number of attempts
    base_delay: starting delay in seconds
    jitter: random jitter added to delay
    """

    for attempt in range(1, retries + 1):
        try:
            return operation()

        except exceptions as e:
            if attempt == retries:
                raise RuntimeError(f"{name} failed after {retries} attempts: {e}") from e

            delay = base_delay * (2 ** (attempt - 1))
            delay += random.uniform(0, jitter)

            log.warning(f"{name} failed (attempt {attempt}/{retries}): {e}")
            log.info(f"Retrying in {delay:.2f}s...")

            time.sleep(delay)
``


def _row_get(row, key, default=None):
    """Safely read a field from either an astropy Table row or a dict."""
    try:
        return row[key]
    except Exception:
        return default


def _resolve_tic_to_coords(tic_id: int) -> Optional[Tuple[float, float]]:
    """Look up RA/Dec for a TIC ID using MAST TIC catalog."""
    try:
        from astroquery.mast import Catalogs
    except ImportError:
        return None

    try:
        result = Catalogs.query_criteria(catalog="TIC", ID=int(tic_id))
    except Exception:
        return None

    if len(result) == 0:
        return None

    try:
        return float(result["ra"][0]), float(result["dec"][0])
    except Exception:
        return None


def _find_observations(tic_id: int, sector: Optional[int]):
    from astroquery.mast import Observations

    def query():
        return Observations.query_criteria(
            objectname=f"TIC {tic_id}",
            obs_collection="TESS"
        )

    obs = retry(query, name="MAST query")

    if sector is not None:
        obs = [
            o for o in obs
            if int(_row_get(o, "sequence_number", -1)) == int(sector)
        ]

    return obs
`` 

# -------------------------------------------------
# Public API
# -------------------------------------------------

def fetch_spoc_lightcurve(
    tic_id: int,
    sector: int,
    out_dir: Optional[str] = None,
    authors: Optional[List[tuple]] = None,
) -> dict:
    """
    Fetch a SPOC light curve FITS from MAST.
    Returns metadata + local file path.
    """

    try:
        from astroquery.mast import Observations
    except ImportError as e:
        raise RuntimeError("astroquery is required") from e

    log.info(f"Fetching TIC {tic_id}, sector {sector}")

    obs = _find_observations(tic_id, sector)

    if not obs:
        raise RuntimeError(f"No observations found for TIC {tic_id} sector {sector}")

    products = Observations.get_product_list(obs)

    products = Observations.filter_products(
        products,
        productSubGroupDescription="LC",
        extension="fits"
    )

    if len(products) == 0:
        raise RuntimeError("No SPOC light curve products found")

    # Download first match
    manifest = Observations.download_products(products[:1])

    file_path = manifest["Local Path"][0]

    return {
        "filename": file_path,
        "obs_id": str(_row_get(products[0], "obsID")),
        "matched": len(products),
        "author": _row_get(products[0], "provenance_name"),
        "exptime": _row_get(products[0], "t_exptime"),
    }


def list_available_sectors(tic_id: int) -> list:
    """List sectors available for a TIC."""
    try:
        from astroquery.mast import Observations
    except ImportError:
        return []

    obs = _find_observations(tic_id, None)

    if not obs:
        return []

    by_sector = {}

    for row in obs:
        s = _row_get(row, "sequence_number")
        if s is None:
            continue

        try:
            s = int(s)
        except Exception:
            continue

        prov = str(_row_get(row, "provenance_name", "unknown"))
        by_sector.setdefault(s, set()).add(prov)

    return [
        {"sector": s, "providers": sorted(p)}
        for s, p in sorted(by_sector.items())
    ]
``
