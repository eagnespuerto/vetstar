"""One place for "run this, but give up after N seconds".

Remote services (TESScut, Gaia, SIMBAD, the Exoplanet Archive) sometimes
hang, and most of their client libraries ignore timeouts. A thread cannot
be killed, so the pattern everywhere is the same: run the call in a worker
thread, wait a bounded time, and walk away if it is late. The late call is
left to finish on its own and its result is dropped.

All of that runs on one shared pool, so a slow service ties up a bounded
number of threads instead of one new executor per call.

    run_with_timeout(fn, *args, timeout_s=45)   # raises TimeoutError
    fut = submit(fn, *args)                     # a Future on the shared pool
    result_within(fut, 45)                      # raises TimeoutError
    collect_within({"a": fut_a, "b": fut_b}, 30)  # never raises

``TimeoutError`` here is always the builtin. On Python < 3.11
``concurrent.futures.TimeoutError`` is a different class, so the helpers
convert it and callers can catch one name on the site and on the Pi.
"""
from __future__ import annotations

import concurrent.futures as cf
import os
import threading
from typing import Any, Callable, Dict, Hashable, Optional

# Enough for one job's catalog cross-match (three queries at once) plus an
# FFI cutout, with room left when an abandoned call is still holding a thread.
_MAX_WORKERS = max(2, int(os.environ.get("VETSTAR_IO_WORKERS", "8")))

_pool: Optional[cf.ThreadPoolExecutor] = None
_pool_lock = threading.Lock()


def shared_pool() -> cf.ThreadPoolExecutor:
    """The process-wide worker pool for bounded calls (created on first use)."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = cf.ThreadPoolExecutor(
                    max_workers=_MAX_WORKERS, thread_name_prefix="vetstar-io"
                )
    return _pool


def submit(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> cf.Future:
    """Start ``fn(*args, **kwargs)`` on the shared pool and return its Future."""
    return shared_pool().submit(fn, *args, **kwargs)


def result_within(
    fut: cf.Future, timeout_s: Optional[float], cancel: bool = True
) -> Any:
    """Return the future's result, or raise the builtin ``TimeoutError``.

    A late future is cancelled if it has not started yet (pass
    ``cancel=False`` to leave it queued for a later wait). If it is already
    running it is abandoned: it finishes in the background and its result is
    dropped. An exception raised by the call itself is re-raised unchanged.
    """
    try:
        return fut.result(timeout=timeout_s)
    except cf.TimeoutError:
        # On 3.11+ this is the builtin TimeoutError, which a call may also
        # raise by itself (e.g. a socket timeout). Only treat it as "late"
        # when the future really is unfinished.
        if fut.done():
            raise
        if cancel:
            fut.cancel()
        raise TimeoutError(f"timed out after {timeout_s:g}s") from None


def run_with_timeout(
    fn: Callable[..., Any], *args: Any, timeout_s: float, **kwargs: Any
) -> Any:
    """Run ``fn(*args, **kwargs)`` on the shared pool, waiting ``timeout_s``.

    Raises the builtin ``TimeoutError`` when the call is late (see
    :func:`result_within`). Any exception from ``fn`` propagates.

    Do not call this from inside a task that is itself running on the shared
    pool with every worker busy: the inner call would queue behind its own
    parent. It then times out cleanly, but it does no work.
    """
    return result_within(submit(fn, *args, **kwargs), timeout_s)


def collect_within(
    futs: Dict[Hashable, cf.Future], timeout_s: float
) -> Dict[Hashable, Any]:
    """Wait up to ``timeout_s`` in total for several futures. Never raises.

    Returns ``{key: result}``. A late or failed future maps to
    ``{"error": "..."}`` instead, which is the fail-soft shape the catalog
    cross-match already uses.
    """
    cf.wait(list(futs.values()), timeout=timeout_s)
    out: Dict[Hashable, Any] = {}
    for key, fut in futs.items():
        if not fut.done():
            fut.cancel()
            out[key] = {"error": f"timed out after {timeout_s:.0f}s"}
            continue
        try:
            out[key] = fut.result()
        except Exception as exc:
            out[key] = {"error": str(exc)}
    return out
