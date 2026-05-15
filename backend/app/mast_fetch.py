"""
Robust MAST fetcher for TESS light curves.
"""

from __future__ import annotations

import logging
import time
import random
from typing import Optional

log = logging.getLogger(__name__)


# -------------------------------------------------
# Retry helper
# -------------------------------------------------

def retry(func, retries=3, name="operation"):
    for attempt in range(1, retries + 1):
        try:
            return func()
        except Exception as e:
            if attempt == retries:
                raise RuntimeError(f"{name} failed after {retries} attempts: {e}") from e

            delay = 1.5 * (2 ** (attempt - 1)) + random.random()
            log.warning(f"{name} failed (attempt {attempt}/{retries}): {e}")
            time.sleep(delay)


# -------------------------------------------------
# Safe helpers
# -------------------------------------------------

def safe_get(row, key, default=None):
    try:
        return row[key]
    except Exception:
        return default


# -------------------------------------------------
# Observation search (robust)
# -------------------------------------------------

def find_observations(tic_id: int):
    """Try multiple strategies to find observations."""

    from astroquery.mast import Observations

    # 1. Try TIC name
    def q1():
        return Observations.query_criteria(
            objectname=f"TIC {tic_id}",
            obs_collection="TESS"
        )

    obs = retry(q1, name="query by TIC name")

    # Fallback if empty
    if len(obs) == 0:
        log.warning("No results via TIC name, trying generic query")

        def q2():
            return Observations.query_criteria(obs_collection="TESS")

        obs = retry(q2, name="fallback query")

    return obs


# -------------------------------------------------
# Main fetch
# -------------------------------------------------

def fetch_spoc_lightcurve(tic_id: int, sector: int) -> dict:
    from astroquery.mast import Observations

    log.info(f"Fetching TIC {tic_id}, sector {sector}")

    obs = find_observations(tic_id)

    if len(obs) == 0:
        raise RuntimeError(f"No observations found for TIC {tic_id}")

    # Filter by sector safely
    filtered = []
    for o in obs:
        seq = safe_get(o, "sequence_number")
        try:
            if seq is not None and int(seq) == int(sector):
                filtered.append(o)
        except Exception:
            continue

    if len(filtered) == 0:
        raise RuntimeError(f"No observations found for sector {sector}")

    # Get products
    def get_products():
        return Observations.get_product_list(filtered)

    products = retry(get_products, name="get products")

    if len(products) == 0:
        raise RuntimeError("No products returned")

    # Filter light curves
    products = Observations.filter_products(
        products,
        productSubGroupDescription="LC",
        extension="fits"
    )

    if len(products) == 0:
        raise RuntimeError("No SPOC light curves found")

    # Download
    def download():
        return Observations.download_products(products[:1])

    manifest = retry(download, name="download")

    if "Local Path" not in manifest.colnames or len(manifest) == 0:
        raise RuntimeError("Download failed (no file returned)")

    file_path = manifest["Local Path"][0]

    log.info(f"Downloaded: {file_path}")

    return {
        "filename": file_path,
        "obs_id": str(safe_get(products[0], "obsID")),
        "matched": len(products),
        "author": safe_get(products[0], "provenance_name"),
        "sector": sector,
    }


# -------------------------------------------------
# Sector listing
# -------------------------------------------------

def list_available_sectors(tic_id: int):
    from astroquery.mast import Observations

    obs = find_observations(tic_id)

    if len(obs) == 0:
        return []

    sectors = set()

    for row in obs:
        seq = safe_get(row, "sequence_number")
        try:
            if seq is not None:
                sectors.add(int(seq))
        except Exception:
            continue

    return [{"sector": s} for s in sorted(sectors)]
