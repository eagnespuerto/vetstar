"""
Robust MAST fetcher for TESS light curves.
"""

from __future__ import annotations

import logging
import time
import random
from typing import Optional

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# -------------------------------------------------
# Retry helper
# -------------------------------------------------

def retry(func, retries=3, name="operation"):
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
# Helpers
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
    from astroquery.mast import Observations, Catalogs

    # ---- Try TIC name ----
    def q1():
        return Observations.query_criteria(
            objectname=f"TIC {tic_id}",
            obs_collection="TESS"
        )

    obs = retry(q1, name="query by TIC name")

    if len(obs) > 0:
        log.info(f"Found {len(obs)} observations via TIC name")
        return obs

    # ---- Fallback: TIC → RA/Dec ----
    log.warning("TIC name lookup failed, trying coordinates")

    def q2():
        return Catalogs.query_criteria(catalog="TIC", ID=int(tic_id))

    cat = retry(q2, name="TIC catalog lookup")

    if len(cat) == 0:
        raise RuntimeError(f"TIC {tic_id} not found in catalog")

    ra = float(cat["ra"][0])
    dec = float(cat["dec"][0])

    def q3():
        return Observations.query_region(f"{ra} {dec}", radius=0.01)

    obs = retry(q3, name="cone search")

    log.info(f"Found {len(obs)} observations via cone search")

    return obs


# -------------------------------------------------
# Fetch light curve
# -------------------------------------------------
def fetch_spoc_lightcurve(tic_id: int, sector: int) -> dict:
    from astroquery.mast import Observations

    log.info(f"Fetching TIC {tic_id}, sector {sector}")

    obs = find_observations(tic_id)

    if len(obs) == 0:
        raise RuntimeError("No observations found")

    # FIXED FILTERING (keeps Astropy table)
    try:
        mask = [
            int(safe_get(o, "sequence_number", -1)) == int(sector)
            for o in obs
        ]
        filtered = obs[mask]
    except Exception as e:
        raise RuntimeError(f"Sector filtering failed: {e}")

    log.info(f"{len(filtered)} observations match sector {sector}")

    if len(filtered) == 0:
        raise RuntimeError(f"No observations found for sector {sector}")

    # Retry-safe product fetch
    def get_products():
        return Observations.get_product_list(filtered)

    products = retry(get_products, name="get product list")

    if len(products) == 0:
        raise RuntimeError("No products returned")

    # Prefer light curves
    lc_products = Observations.filter_products(
        products,
        productSubGroupDescription="LC",
        extension="fits"
    )

    if len(lc_products) == 0:
        log.warning("No LC products found — falling back to any FITS")
        lc_products = Observations.filter_products(products, extension="fits")

    if len(lc_products) == 0:
        raise RuntimeError("No FITS products available")

    # Download safely
    def download():
        return Observations.download_products(lc_products[:1])

    manifest = retry(download, name="download")

    if len(manifest) == 0 or "Local Path" not in manifest.colnames:
        raise RuntimeError("Download failed — empty manifest")

    file_path = manifest["Local Path"][0]

    log.info(f"Downloaded file: {file_path}")

    return {
        "filename": file_path,
        "obs_id": str(safe_get(lc_products[0], "obsID")),
        "matched": len(lc_products),
        "sector": sector,
    }
# -------------------------------------------------
# List sectors
# -------------------------------------------------

def list_available_sectors(tic_id: int):
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
