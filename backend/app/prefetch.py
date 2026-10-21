"""Up-front network prefetch for a single vetting job, plus a small store
for the finished PDF.

A vetting run waits on several slow remote services: TESScut (FFI cutout,
the slowest, often 20-45 s), the SPOC DVT file (a multi-MB MAST download),
the Gaia/SIMBAD/NEA cross-match (up to 30 s) and ExoFOP. None of them
depends on the BLS result, so each job starts them as soon as it knows the
target's TIC/sector/coordinates, heaviest first, and they overlap with the
light-curve download and the CPU-bound pipeline.

Memory budget: the app is sized for a 512 MB instance (Render free tier).
``Prefetcher`` uses a small pool (3 workers), ``VET_SLOTS`` runs one
vetting job at a time by default, and ``ReportStore`` keeps only a few
finished PDFs, each a few MB.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any, Callable, Optional

log = logging.getLogger("vetting")

# Concurrent vetting jobs. Each holds a light curve, the pipeline arrays,
# base64 plots and a PDF in memory; one at a time keeps a 512 MB instance
# well clear of the OOM killer. Override with VETSTAR_MAX_JOBS on bigger boxes.
VET_SLOTS = threading.BoundedSemaphore(max(1, int(os.environ.get("VETSTAR_MAX_JOBS", "1"))))

# pyplot's figure registry isn't thread-safe, and prefetch threads (DVT
# phase fold, FFI cutout) render while the job thread draws its own plots.
# Every matplotlib render that can overlap another takes this lock.
PLOT_LOCK = threading.RLock()


class Prefetcher:
    """Run named fetches in a small background pool and collect them later.

    Tasks start in the order they are submitted, so submit the heaviest
    first. ``get`` never raises: a failed or timed-out fetch returns the
    caller's default, matching the fail-soft behaviour of every fetch.
    """

    def __init__(self, max_workers: int = 3):
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="prefetch")
        self._futs: dict[str, Future] = {}

    def submit(self, name: str, fn: Callable[..., Any], *args, **kwargs) -> None:
        if name not in self._futs:
            self._futs[name] = self._pool.submit(fn, *args, **kwargs)

    def has(self, name: str) -> bool:
        return name in self._futs

    def get(self, name: str, timeout: Optional[float] = None, default: Any = None) -> Any:
        fut = self._futs.get(name)
        if fut is None:
            return default
        try:
            return fut.result(timeout=timeout)
        except FutureTimeout:
            log.warning("prefetch %s timed out after %ss", name, timeout)
        except Exception as e:
            log.warning("prefetch %s failed: %s", name, e)
        return default

    def close(self) -> None:
        # Don't wait: a stuck remote call must not hold the job open. Its
        # thread finishes (or times out) on its own.
        self._pool.shutdown(wait=False, cancel_futures=True)


class ReportStore:
    """Bounded, TTL'd ``{report_id: (pdf_bytes, filename)}`` map."""

    def __init__(self, max_items: int = 4, ttl_seconds: float = 1800.0):
        self._items: "OrderedDict[str, tuple[float, bytes, str]]" = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_items
        self._ttl = ttl_seconds

    def put(self, pdf: bytes, filename: str) -> str:
        rid = uuid.uuid4().hex
        with self._lock:
            self._gc()
            self._items[rid] = (time.time(), pdf, filename)
            while len(self._items) > self._max:
                self._items.popitem(last=False)
        return rid

    def get(self, rid: str) -> Optional[tuple[bytes, str]]:
        with self._lock:
            self._gc()
            item = self._items.get(rid)
        return (item[1], item[2]) if item else None

    def _gc(self) -> None:
        now = time.time()
        for rid in [r for r, (ts, _, _) in self._items.items() if now - ts > self._ttl]:
            self._items.pop(rid, None)


REPORTS = ReportStore()
