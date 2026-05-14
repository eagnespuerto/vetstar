"""
MAST fetcher: given TIC + sector, locate and download a TESS light curve
FITS from the public MAST archive.

Tries multiple data providers in order:
  1. SPOC 2-min cadence (best — quality flags, centroids, CROWDSAP)
  2. SPOC 20-s cadence
  3. TESS-SPOC FFI      (10-min from full-frame images; near-complete coverage)
  4. QLP                (Quick Look Pipeline FFI light curves)

Returns the first provider that has a product. SPOC gives the richest
vetting (centroid + quality flags); FFI products lack centroid columns, so
the centroid test will report `available=False` for those.
"""
from __future__ import annotations

import os
import tempfile
from typing import Optional, List


DEFAULT_AUTHORS = [
    ("SPOC", 120),
    ("SPOC", 20),
    ("TESS-SPOC", 600),
    ("QLP", 1800),
]


def _query(Observations, tic_id, sector, author, exptime):
    criteria = dict(
        target_name=f"TIC {tic_id}",
        obs_collection="TESS",
        sequence_number=sector,
        provenance_name=author,
    )
    if exptime is not None:
        criteria["t_exptime"] = [exptime - 1, exptime + 1]
    return Observations.query_criteria(**criteria)


def fetch_spoc_lightcurve(
    tic_id: int,
    sector: int,
    out_dir: Optional[str] = None,
    authors: Optional[List[tuple]] = None,
) -> dict:
    try:
        from astroquery.mast import Observations
    except ImportError as e:
        raise RuntimeError(
            "astroquery is not installed. Add `astroquery>=0.4.7` to requirements."
        ) from e

    if authors is None:
        authors = DEFAULT_AUTHORS

    tried = []
    obs = None
    chosen_author = chosen_exptime = None

    for author, exptime in authors:
        tried.append(f"{author} ({exptime}s)")
        try:
            result = _query(Observations, tic_id, sector, author, exptime)
        except Exception as e:
            tried[-1] += f" [query error: {e}]"
            continue
        if len(result) > 0:
            obs = result
            chosen_author = author
            chosen_exptime = exptime
            break

    # Last-ditch: any provenance.
    if obs is None:
        try:
            result = Observations.query_criteria(
                target_name=f"TIC {tic_id}",
                obs_collection="TESS",
                sequence_number=sector,
            )
            if len(result) > 0:
                obs = result
                chosen_author = str(result[0].get("provenance_name", "unknown"))
                chosen_exptime = float(result[0].get("t_exptime", 0))
                tried.append(f"any provenance [matched {chosen_author}]")
        except Exception as e:
            tried.append(f"any provenance [error: {e}]")

    if obs is None or len(obs) == 0:
        # Tell the user which sectors ARE covered for this TIC.
        try:
            all_rows = Observations.query_criteria(
                target_name=f"TIC {tic_id}",
                obs_collection="TESS",
            )
            sectors_all = sorted({
                int(s) for s in all_rows["sequence_number"] if s is not None
            })
        except Exception:
            sectors_all = []
        msg = (
            f"No TESS light curves found at MAST for TIC {tic_id} in sector {sector}. "
            f"Tried: {', '.join(tried)}."
        )
        if sectors_all:
            msg += (
                f" This TIC IS observed in TESS sectors: "
                f"{', '.join(str(s) for s in sectors_all)}. "
                f"Try one of those instead."
            )
        else:
            msg += " No TESS observations of this TIC appear in MAST at all."
        raise RuntimeError(msg)

    # Filter products to lightcurve FITS.
    products = Observations.get_product_list(obs)

    def _is_lc(name):
        n = str(name).lower()
        return (
            n.endswith("_lc.fits")
            or n.endswith("-s_lc.fits")
            or n.endswith("_llc.fits")
            or ("lc" in n and n.endswith(".fits"))
        )

    lc_mask = [_is_lc(p) for p in products["productFilename"]]
    if not any(lc_mask):
        raise RuntimeError(
            f"Found {len(obs)} {chosen_author} observation(s) for "
            f"TIC {tic_id}/sector {sector} but no *_lc.fits attached."
        )
    products = products[lc_mask]

    if out_dir is None:
        out_dir = tempfile.mkdtemp(prefix="mast_")
    manifest = Observations.download_products(products, download_dir=out_dir, mrp_only=False)
    ok = [row for row in manifest if str(row.get("Status", "")).upper() == "COMPLETE"]
    if not ok:
        msgs = "; ".join(str(row.get("Message", "")) for row in manifest)
        raise RuntimeError(f"MAST download did not complete cleanly. {msgs}")

    local_path = str(ok[0]["Local Path"])
    fallback = chosen_author != "SPOC" or (chosen_exptime and chosen_exptime != 120)
    return {
        "path": local_path,
        "filename": os.path.basename(local_path),
        "obs_id": str(obs[0].get("obs_id", "")),
        "author": chosen_author,
        "exptime": float(chosen_exptime or 0),
        "matched": int(len(obs)),
        "n_products": int(len(products)),
        "fallback": bool(fallback),
        "tried": tried,
    }


def list_available_sectors(tic_id: int) -> list:
    """List sectors where any TESS product exists for a TIC.
    Returns [{"sector": N, "providers": ["SPOC","QLP",...]}]."""
    try:
        from astroquery.mast import Observations
    except ImportError:
        return []
    try:
        obs = Observations.query_criteria(
            target_name=f"TIC {tic_id}",
            obs_collection="TESS",
        )
    except Exception:
        return []
    by_sector: dict = {}
    for row in obs:
        s = row.get("sequence_number")
        if s is None:
            continue
        s = int(s)
        prov = str(row.get("provenance_name", "unknown"))
        by_sector.setdefault(s, set()).add(prov)
    return [
        {"sector": s, "providers": sorted(p)}
        for s, p in sorted(by_sector.items())
    ]
