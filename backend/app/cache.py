"""Caches shared by the routers: cleaned light curves (for ExoMiner and the
manual-dip tool) and TESScut FFI cutouts.

Two tiers. The first is a pair of small in-process dicts, guarded by one
lock. The second is optional: a Supabase Postgres table (``cache_store``),
used when ``SUPABASE_DB_URL`` is set, so entries survive a restart and are
shared between instances.

Who can read a cached light curve:

* A curve with a TIC is public MAST data. It is keyed by (TIC, sector) and
  only ever returned for that TIC.
* An upload with no TIC belongs to the request that sent it. ``_cache_lc``
  returns a random token for it, the response carries the token, and only a
  request that sends the token back gets the curve. Such uploads stay in
  memory unless ``VETSTAR_PERSIST_UPLOADS=1``.
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import numpy as np

from vetstar_core.pipeline import clean_lightcurve

from . import cache_store

log = logging.getLogger("vetting")

_LOCK = threading.RLock()

# -------------------------------------------------
# First tier. Dicts keep insertion order, so the oldest entry is evicted.
# LC keys: (tic_id, sector), or (_UPLOAD, token) for an upload with no TIC.
# FFI keys: (tic_id, sector).
# -------------------------------------------------
_lc_cache: dict = {}
# Bounded for the 512 MB budget: a 20-s cadence sector is several MB.
_LC_CACHE_MAX = 6
_UPLOAD = "__upload__"

_ffi_cache: dict = {}
# Each entry is a base64 PNG (tens of KB).
_FFI_CACHE_MAX = 32

# -------------------------------------------------
# Second tier
# -------------------------------------------------
LC_TTL_S = 7 * 86400          # MAST light curves
UPLOAD_TTL_S = 86400          # TIC-less uploads, when persisted at all
FFI_TTL_S = 30 * 86400
FFI_MISS_TTL_S = 86400        # "TESScut has no cutout" — retry after a day

_STORE_UNSET = object()
_STORE = _STORE_UNSET
_WRITER: Optional[ThreadPoolExecutor] = None


def _store():
    """The persistent store, or None. Created on first use."""
    global _STORE
    if _STORE is _STORE_UNSET:
        with _LOCK:
            if _STORE is _STORE_UNSET:
                _STORE = cache_store.store_from_env()
    return _STORE


def _submit(fn, *args) -> None:
    """Run a store write off the request path (one writer thread)."""
    global _WRITER
    with _LOCK:
        if _WRITER is None:
            _WRITER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cache-store")
        writer = _WRITER
    writer.submit(fn, *args)


def _persist_uploads() -> bool:
    return os.environ.get("VETSTAR_PERSIST_UPLOADS", "").strip().lower() in ("1", "true", "yes")


def _remember(cache: dict, key, value, limit: int) -> None:
    with _LOCK:
        cache.pop(key, None)
        cache[key] = value
        for old in list(cache)[:-limit]:
            cache.pop(old, None)


def _lc_store_key(tic_id, sector, token) -> Optional[str]:
    if tic_id:
        return f"tic:{int(tic_id)}:{'' if sector is None else int(sector)}"
    return f"upload:{token}" if token else None


def _ffi_store_key(tic_id, sector) -> Optional[str]:
    if not tic_id:
        return None
    return f"tic:{int(tic_id)}:{'' if sector is None else int(sector)}"


# -------------------------------------------------
# Light curves
# -------------------------------------------------

def _lookup_lc(tic_id, sector=None, token: Optional[str] = None) -> Optional[dict]:
    """Cached cleaned LC for exactly this target, or None.

    With a TIC we never fall back to another target's curve. With no
    sector, the most recent entry for the TIC is used. With no TIC, the
    curve is found only by the token ``_cache_lc`` gave for that upload.
    """
    store = _store()
    if not tic_id:
        if not token:
            return None
        with _LOCK:
            hit = _lc_cache.get((_UPLOAD, token))
        if hit is None and store is not None and _persist_uploads():
            hit = _load_lc(store.get("lc", _lc_store_key(None, None, token)),
                           (_UPLOAD, token))
        return hit

    if sector is not None:
        with _LOCK:
            hit = _lc_cache.get((tic_id, sector))
        if hit is None and store is not None:
            hit = _load_lc(store.get("lc", _lc_store_key(tic_id, sector, None)),
                           (tic_id, sector))
        return hit

    with _LOCK:
        for key in reversed(list(_lc_cache)):
            if key[0] == tic_id:
                return _lc_cache[key]
    if store is not None:
        hit = _load_lc(store.latest_for_tic("lc", tic_id), None)
        if hit is not None:
            star_sector = getattr(hit.get("star"), "sector", None)
            _remember(_lc_cache, (tic_id, star_sector), hit, _LC_CACHE_MAX)
        return hit
    return None


def _load_lc(payload: Optional[bytes], memory_key) -> Optional[dict]:
    """Decode a stored light curve and keep it in the first tier."""
    if payload is None:
        return None
    try:
        entry = cache_store.decode_lc(payload)
    except Exception as exc:
        log.warning("cached light curve could not be read: %s", exc)
        return None
    if memory_key is not None:
        _remember(_lc_cache, memory_key, entry, _LC_CACHE_MAX)
    return entry


def _cache_lc(parsed: dict) -> Optional[str]:
    """Store cleaned LC arrays after a successful parse so ExoMiner and the
    manual-dip tool can reuse them without re-downloading.

    Returns the access token for an upload with no TIC (send it back as
    ``lc_token``), else None.
    """
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
        entry = {"t": t_c, "f": f_c, "mom_x": mom_x, "mom_y": mom_y, "star": star}

        token = None if tic_id else uuid.uuid4().hex
        key = (tic_id, sector) if tic_id else (_UPLOAD, token)
        _remember(_lc_cache, key, entry, _LC_CACHE_MAX)

        store = _store()
        if store is not None and (tic_id or _persist_uploads()):
            _submit(_write_lc, store, _lc_store_key(tic_id, sector, token), entry,
                    tic_id, sector)
        return token
    except Exception as exc:
        log.warning("LC cache update failed: %s", exc)
        return None


def _write_lc(store, key: str, entry: dict, tic_id, sector) -> None:
    try:
        store.put("lc", key, cache_store.encode_lc(entry),
                  LC_TTL_S if tic_id else UPLOAD_TTL_S,
                  tic_id=tic_id or None, sector=sector)
    except Exception as exc:
        log.warning("LC cache store write failed: %s", exc)


# -------------------------------------------------
# FFI cutouts
# -------------------------------------------------

def _peek_ffi(tic_id, sector) -> tuple:
    """(found, cutout). ``cutout`` may be None: a cached "no cutout"."""
    key = (tic_id, sector)
    with _LOCK:
        if key in _ffi_cache:
            return True, _ffi_cache[key]
    store = _store()
    skey = _ffi_store_key(tic_id, sector)
    if store is None or skey is None:
        return False, None
    payload = store.get("ffi", skey)
    if payload is None:
        return False, None
    try:
        cutout = cache_store.decode_json(payload)
    except Exception as exc:
        log.warning("cached FFI cutout could not be read: %s", exc)
        return False, None
    _remember(_ffi_cache, key, cutout, _FFI_CACHE_MAX)
    return True, cutout


def _has_ffi(tic_id, sector) -> bool:
    return _peek_ffi(tic_id, sector)[0]


def _put_ffi(key, cutout) -> None:
    """Store an FFI cutout (or None for "no cutout") in both tiers."""
    _remember(_ffi_cache, key, cutout, _FFI_CACHE_MAX)
    store = _store()
    skey = _ffi_store_key(*key)
    if store is not None and skey is not None:
        _submit(_write_ffi, store, skey, cutout, key)


def _write_ffi(store, skey: str, cutout, key) -> None:
    try:
        store.put("ffi", skey, cache_store.encode_json(cutout),
                  FFI_TTL_S if cutout else FFI_MISS_TTL_S,
                  tic_id=key[0], sector=key[1])
    except Exception as exc:
        log.warning("FFI cache store write failed: %s", exc)


def _get_ffi_cutout(tic_id, sector, ra, dec, size_px: int = 15, timeout_s: float = 45.0):
    """Return a TESScut FFI cutout dict for the target, fetching once and
    caching by (tic_id, sector). Fails soft (returns None). The TESScut
    fetch is bounded by a timeout and run in a worker thread so a slow or
    stuck MAST request can never hang the request and trip a gateway 502."""
    from concurrent.futures import TimeoutError as FutureTimeout

    from vetstar_core.ffi_cutout import make_ffi_cutout

    found, cached = _peek_ffi(tic_id, sector)
    if found:
        return cached

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

    _put_ffi((tic_id, sector), cutout)  # cache success or a clean "no cutout" (None)
    return cutout
