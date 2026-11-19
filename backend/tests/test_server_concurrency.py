"""Server-level guards: handlers must not block the event loop, and the
module-level caches must stay bounded and per target."""
from __future__ import annotations

import inspect

import numpy as np
import pytest
from fastapi.routing import APIRoute

from backend.app import cache, main
from backend.app.routers import ROUTERS

# Handlers that genuinely await (and so belong on the event loop).
ASYNC_ALLOWED = {"job_stream"}


def test_only_awaiting_handlers_are_async():
    """A blocking `async def` handler freezes every other request, SSE
    progress streams included. Plain `def` runs on the worker threadpool."""
    offenders = [
        r.endpoint.__name__
        for r in [*main.app.routes, *(rt for router in ROUTERS for rt in router.routes)]
        if isinstance(r, APIRoute)
        and inspect.iscoroutinefunction(r.endpoint)
        and r.endpoint.__name__ not in ASYNC_ALLOWED
    ]
    assert offenders == []


@pytest.fixture
def lc_cache(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(cache, "_lc_cache", store)
    return store


def _entry(tag):
    return {"t": np.array([0.0]), "f": np.array([1.0]), "tag": tag}


def test_lookup_never_returns_another_targets_curve(lc_cache):
    lc_cache[(111, 5)] = _entry("a")
    assert cache._lookup_lc(111, 5)["tag"] == "a"
    # A different TIC, or the same TIC in an uncached sector, gets nothing.
    assert cache._lookup_lc(222, 5) is None
    assert cache._lookup_lc(111, 6) is None


def test_lookup_without_sector_uses_latest_for_that_tic(lc_cache):
    lc_cache[(111, 5)] = _entry("old")
    lc_cache[(222, 9)] = _entry("other")
    lc_cache[(111, 7)] = _entry("new")
    assert cache._lookup_lc(111, None)["tag"] == "new"


def test_ticless_upload_only_matches_its_token(lc_cache):
    lc_cache[(111, 5)] = _entry("tic")
    lc_cache[(cache._UPLOAD, "tok")] = _entry("anon")
    assert cache._lookup_lc(None, None, "tok")["tag"] == "anon"
    # Without the token nobody gets it, TIC or not.
    assert cache._lookup_lc(None, None) is None
    assert cache._lookup_lc(333, None) is None


def test_ffi_cache_is_bounded(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(cache, "_ffi_cache", store)
    monkeypatch.setattr(cache, "_FFI_CACHE_MAX", 3)
    for i in range(5):
        cache._put_ffi((i, 1), {"image": str(i)})
    assert list(store) == [(2, 1), (3, 1), (4, 1)]
    # Re-putting an old key moves it to the newest slot.
    cache._put_ffi((2, 1), None)
    assert list(store) == [(3, 1), (4, 1), (2, 1)]
