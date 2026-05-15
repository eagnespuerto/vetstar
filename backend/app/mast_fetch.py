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
from __future__ import annotations

import logging
import os
import tempfile
import traceback
from typing import Optional, List, Tuple

log = logging.getLogger(__name__)


DEFAULT_AUTHORS = [
    ("SPOC", 120),
    ("SPOC", 20),
    ("TESS-SPOC", 600),
    ("QLP", 1800),
]

# Cone-search radius when finding observations by coordinates. The TESS
# pixel scale is 21"/px; a 30" radius safely catches any product associated
# with the same star without dragging in neighbours.
CONE_RADIUS_DEG = 30 / 3600.0   # 30 arcsec


def _row_get(row, key, default=None):
    """Safely read a field from either an astropy Table row or a dict.

    Astropy `Table` rows support `row[key]` but NOT `row.get(key, default)` —
    a missing key raises `KeyError`. They also return masked values for
    missing entries, which need extra handling. This helper papers over
    both behaviours so callers don't crash.
    """
    try:
        v = row[key]
    except (KeyError, IndexError, ValueError):
        return default
    # Astropy masked entries: treat as missing
    try:
        import numpy as _np
        if _np.ma.is_masked(v):
            return default
    except Exception:
        pass
    if v is None:
        return default
    return v


def _resolve_tic_to_coords(tic_id: int) -> Optional[Tuple[float, float]]:
    """Look up RA/Dec for a TIC ID using MAST's TIC catalog.
    Returns (ra, dec) in degrees, or None if not found."""
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
    except (KeyError, IndexError, ValueError):
        return None


def _find_observations(tic_id: int, sector: Optional[int],
                       author: Optional[str], exptime: Optional[int]):
    """Try multiple name-resolution strategies in order. Returns the first
    non-empty result, or an empty table-like object."""
    from astroquery.mast import Observations

    base_criteria = {"obs_collection": "TESS"}
    if sector is not None:
        base_criteria["sequence_number"] = sector
    if author is not None:
        base_criteria["provenance_name"] = author
    if exptime is not None:
        base_criteria["t_exptime"] = [exptime - 1, exptime + 1]

    # Strategy 1: literal target name match.
    for name_form in (f"TIC {tic_id}", f"TIC{tic_id}", str(tic_id)):
        try:
            r = Observations.query_criteria(target_name=name_form, **base_criteria)
            if len(r) > 0:
                return r, f"name='{name_form}'"
        except Exception:
            pass

    # Strategy 2: cone search by resolved coordinates.
    coords = _resolve_tic_to_coords(tic_id)
    if coords is not None:
        ra, dec = coords
        try:
            r = Observations.query_criteria(
                coordinates=f"{ra} {dec}",
                radius=CONE_RADIUS_DEG,
                **base_criteria,
            )
            if len(r) > 0:
                return r, f"cone search at ({ra:.4f}, {dec:.4f})"
        except Exception:
            pass

    # Strategy 3: object name (some products are filed under aliases that
    # MAST's name resolver can find). `query_object` takes radius in
    # arcseconds with a units suffix, but astroquery is permissive — pass
    # degrees directly.
    try:
        r = Observations.query_object(f"TIC {tic_id}", radius=CONE_RADIUS_DEG)
        if len(r) > 0:
            keep = [True] * len(r)
            for i, row in enumerate(r):
                if str(_row_get(row, "obs_collection", "")) != "TESS":
                    keep[i] = False
                    continue
                if sector is not None:
                    sn = _row_get(row, "sequence_number")
                    try:
                        if sn is None or int(sn) != sector:
                            keep[i] = False
                            continue
                    except (TypeError, ValueError):
                        keep[i] = False
                        continue
                if author is not None and str(_row_get(row, "provenance_name", "")) != author:
                    keep[i] = False
                    continue
                if exptime is not None:
                    et = _row_get(row, "t_exptime", 0)
                    try:
                        et = float(et) if et is not None else 0.0
                    except (TypeError, ValueError):
                        et = 0.0
                    if et and (et < exptime - 1 or et > exptime + 1):
                        keep[i] = False
                        continue
            r = r[keep]
            if len(r) > 0:
                return r, "query_object resolver"
    except Exception as e:
        log.debug("query_object strategy failed: %s", e)

    return None, None


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
    matched_via = None

    # Sweep authors in priority order, trying all name-resolution strategies
    # for each.
    for author, exptime in authors:
        result, via = _find_observations(tic_id, sector, author, exptime)
        tried.append(f"{author} ({exptime}s)" + (f" [no match]" if result is None or len(result) == 0 else f" [matched via {via}]"))
        if result is not None and len(result) > 0:
            obs = result
            chosen_author = author
            chosen_exptime = exptime
            matched_via = via
            break

    # Last-ditch: any provenance, in this sector.
    if obs is None:
        result, via = _find_observations(tic_id, sector, None, None)
        if result is not None and len(result) > 0:
            obs = result
            chosen_author = str(_row_get(result[0], "provenance_name", "unknown"))
            chosen_exptime = float(_row_get(result[0], "t_exptime", 0) or 0)
            matched_via = via
            tried.append(f"any provenance in sector {sector} [matched {chosen_author} via {via}]")

    if obs is None or len(obs) == 0:
        # Build a maximally informative error: what sectors DO have data?
        sectors_all = []
        try:
            all_rows, _ = _find_observations(tic_id, None, None, None)
            if all_rows is not None:
                sectors_all = sorted({
                    int(s) for s in all_rows["sequence_number"] if s is not None
                })
        except Exception:
            pass

        coords = _resolve_tic_to_coords(tic_id)
        msg_parts = [
            f"No TESS light curves found at MAST for TIC {tic_id} in sector {sector}.",
            f"Tried: {'; '.join(tried)}.",
        ]
        if coords:
            ra, dec = coords
            msg_parts.append(
                f"TIC {tic_id} resolves to RA={ra:.4f}, Dec={dec:.4f} in the TIC catalog "
                f"(so the TIC ID is valid)."
            )
        else:
            msg_parts.append(
                f"TIC {tic_id} could NOT be resolved in the TIC catalog. "
                f"Double-check the TIC ID — it may not exist."
            )
        if sectors_all:
            msg_parts.append(
                f"This TIC has light-curve products in TESS sectors: "
                f"{', '.join(str(s) for s in sectors_all)}. Try one of those."
            )
        elif coords:
            msg_parts.append(
                f"The TIC exists, but no light-curve products are archived for it "
                f"in any TESS sector. This usually means it was never on a 2-min target "
                f"list AND no FFI-based pipeline (TESS-SPOC, QLP, etc.) has produced a "
                f"light curve for it yet. You could still download the FFI cutout via "
                f"`lightkurve` and detrend manually."
            )
        raise RuntimeError(" ".join(msg_parts))

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
            f"TIC {tic_id}/sector {sector} (matched via {matched_via}) "
            f"but no *_lc.fits attached."
        )
    products = products[lc_mask]

    if out_dir is None:
        out_dir = tempfile.mkdtemp(prefix="mast_")
    manifest = Observations.download_products(products, download_dir=out_dir, mrp_only=False)
    ok = [row for row in manifest if str(_row_get(row, "Status", "")).upper() == "COMPLETE"]
    if not ok:
        msgs = "; ".join(str(_row_get(row, "Message", "")) for row in manifest)
        raise RuntimeError(f"MAST download did not complete cleanly. {msgs}")

    local_path = str(ok[0]["Local Path"])
    fallback = chosen_author != "SPOC" or (chosen_exptime and chosen_exptime != 120)
    return {
        "path": local_path,
        "filename": os.path.basename(local_path),
        "obs_id": str(_row_get(obs[0], "obs_id", "")),
        "author": chosen_author,
        "exptime": float(chosen_exptime or 0),
        "matched": int(len(obs)),
        "matched_via": matched_via,
        "n_products": int(len(products)),
        "fallback": bool(fallback),
        "tried": tried,
    }


def list_available_sectors(tic_id: int) -> list:
    """List sectors where any TESS product exists for a TIC.
    Returns [{"sector": N, "providers": ["SPOC","QLP",...]}]."""
    try:
        from astroquery.mast import Observations  # noqa: F401
    except ImportError:
        return []
    rows, _ = _find_observations(tic_id, None, None, None)
    if rows is None or len(rows) == 0:
        return []
    by_sector: dict = {}
    for row in rows:
        s = _row_get(row, "sequence_number")
        if s is None:
            continue
        try:
            s = int(s)
        except (TypeError, ValueError):
            continue
        prov = str(_row_get(row, "provenance_name", "unknown"))
        by_sector.setdefault(s, set()).add(prov)
    return [
        {"sector": s, "providers": sorted(p)}
        for s, p in sorted(by_sector.items())
    ]
