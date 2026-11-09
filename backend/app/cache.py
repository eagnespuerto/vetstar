"""Process-local caches shared by the routers: cleaned light curves (for
ExoMiner and the manual-dip tool) and TESScut FFI cutouts.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from vetstar_core.pipeline import clean_lightcurve

log = logging.getLogger("vetting")

# -------------------------------------------------
# Module-level LC cache for ExoMiner / manual-dip reuse
# Keyed by (tic_id, sector). Uploads with no TIC go under _ANON_LC_KEY.
# A request that names a TIC only ever gets that TIC's curve back.
# Process-local — fine for a single-worker deployment.
# -------------------------------------------------
_lc_cache: dict = {}
_LC_CACHE_MAX = 6
_ANON_LC_KEY = "__anon_upload__"

# FFI cutout cache so the on-screen panel and the PDF don't each hit TESScut.
# Keyed by (tic_id, sector). Process-local.
_ffi_cache: dict = {}
# Each entry is a base64 PNG (tens of KB). Bounded so a long-running
# instance can't creep towards the 512 MB limit one target at a time.
_FFI_CACHE_MAX = 32


def _put_ffi(key, cutout) -> None:
    """Store an FFI cutout, evicting the oldest entries past the limit."""
    _ffi_cache.pop(key, None)
    _ffi_cache[key] = cutout
    for old in list(_ffi_cache)[:-_FFI_CACHE_MAX]:
        _ffi_cache.pop(old, None)


def _lookup_lc(tic_id, sector) -> Optional[dict]:
    """Cached cleaned LC for exactly this target, or None.

    With a TIC we never fall back to another target's curve: on a shared
    server that would silently score someone else's light curve. With no
    sector, the most recent entry for the TIC is used. With no TIC (an
    upload whose header has none), the most recent TIC-less upload is used.
    """
    if not tic_id:
        return _lc_cache.get(_ANON_LC_KEY)
    if sector is not None:
        return _lc_cache.get((tic_id, sector))
    for key in reversed(list(_lc_cache)):
        if isinstance(key, tuple) and key[0] == tic_id:
            return _lc_cache[key]
    return None


def _get_ffi_cutout(tic_id, sector, ra, dec, size_px: int = 15, timeout_s: float = 45.0):
    """Return a TESScut FFI cutout dict for the target, fetching once and
    caching by (tic_id, sector). Fails soft (returns None). The TESScut
    fetch is bounded by a timeout and run in a worker thread so a slow or
    stuck MAST request can never hang the request and trip a gateway 502."""
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FutureTimeout

    from vetstar_core.ffi_cutout import make_ffi_cutout

    key = (tic_id, sector)
    if key in _ffi_cache:
        return _ffi_cache[key]

    cutout = None
    # Not a `with` block: its exit calls shutdown(wait=True), which would
    # block until the fetch finished and make the timeout useless.
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(
            make_ffi_cutout, ra=ra, dec=dec, sector=sector,
            tic_id=tic_id, size_px=size_px,
        )
        cutout = fut.result(timeout=timeout_s)
    except FutureTimeout:
        log.warning("FFI cutout timed out after %.0fs for TIC %s S%s", timeout_s, tic_id, sector)
        return None  # don't cache a timeout — a retry may succeed
    except Exception as e:
        log.warning("FFI cutout error for TIC %s S%s: %s", tic_id, sector, e)
        cutout = None
    finally:
        ex.shutdown(wait=False)

    _put_ffi(key, cutout)  # cache success or a clean "no cutout" (None)
    return cutout


def _cache_lc(parsed: dict) -> None:
    """Store cleaned LC arrays after a successful parse so ExoMiner can
    reuse them without re-downloading."""
    try:
        t_c, f_c, _ = clean_lightcurve(
            parsed["t"], parsed["flux"], parsed["flux_err"], parsed["quality"]
        )
        # Mask moments with the SAME finite/quality filter clean_lightcurve
        # applies, so centroid arrays line up 1:1 with t_c/f_c (required by
        # centroid_check, which needs len(mom_x) == len(t)).
        mom_x = parsed.get("mom_x")
        mom_y = parsed.get("mom_y")
        quality = parsed.get("quality")
        if mom_x is not None and quality is not None:
            m = (
                np.isfinite(parsed["t"])
                & np.isfinite(parsed["flux"])
                & (parsed["flux"] > 0)
                & (quality == 0)
            )
            mom_x = mom_x[m]
            mom_y = mom_y[m] if mom_y is not None else None
        star = parsed.get("star")
        tic_id = getattr(star, "tic_id", None)
        sector = getattr(star, "sector", None)
        entry = {
            "t": t_c,
            "f": f_c,
            "mom_x": mom_x,
            "mom_y": mom_y,
            "star": star,
        }
        key = (tic_id, sector) if tic_id else _ANON_LC_KEY
        _lc_cache.pop(key, None)
        _lc_cache[key] = entry
        # Bounded for the 512 MB budget: a 20-s cadence sector is several
        # MB of arrays. Dicts keep insertion order, so evict the oldest.
        for old in list(_lc_cache)[:-_LC_CACHE_MAX]:
            _lc_cache.pop(old, None)
    except Exception as exc:
        log.warning("LC cache update failed: %s", exc)
