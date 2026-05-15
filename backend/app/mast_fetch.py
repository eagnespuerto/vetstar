"""
MAST fetcher for TESS light curves.
"""

from __future__ import annotations

import logging
import time
import random
from typing import Optional, List

log = logging.getLogger(__name__)


# ------------------------------
# Retry helper
# ------------------------------

def retry(func, retries=3, name="operation"):
    for attempt in range(1, retries + 1):
        try:
            return func()
        except Exception as e:
            if attempt == retries:
                raise
            delay = 1.5 * (2 ** (attempt - 1)) + random.random()
            log.warning(f"{name} failed (attempt {attempt}): {e}")
            time.sleep(delay)


# ------------------------------
# Core logic
# ------------------------------

def fetch_spoc_lightcurve(tic_id: int, sector: int) -> dict:
    from astroquery.mast import Observations

    def query():
        return Observations.query_criteria(
            objectname=f"TIC {tic_id}",
            obs_collection="TESS"
        )

    obs = retry(query, name="MAST query")

    obs = [o for o in obs if int(o["sequence_number"]) == sector]

    if not obs:
        raise RuntimeError("No observations found")

    def get_products():
        return Observations.get_product_list(obs)

    products = retry(get_products, name="product list")

    products = Observations.filter_products(
        products,
        productSubGroupDescription="LC",
        extension="fits"
    )

    if len(products) == 0:
        raise RuntimeError("No light curves found")

    def download():
        return Observations.download_products(products[:1])

    manifest = retry(download, name="download")

    file_path = manifest["Local Path"][0]

    return {
        "filename": file_path,
        "obs_id": str(products[0]["obsID"]),
        "matched": len(products),
    }


def list_available_sectors(tic_id: int) -> list:
    from astroquery.mast import Observations

    obs = Observations.query_criteria(
        objectname=f"TIC {tic_id}",
        obs_collection="TESS"
    )

    sectors = set()

    for row in obs:
        try:
            sectors.add(int(row["sequence_number"]))
        except Exception:
            continue

    return [{"sector": s} for s in sorted(sectors)]

