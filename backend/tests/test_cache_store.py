"""Two-tier cache: in-process dicts in front of an optional Supabase
Postgres store. A TIC-less upload is reachable only with its token."""
from __future__ import annotations

import numpy as np
import pytest

from backend.app import cache, cache_store
from vetstar_core.pipeline import StarInfo


class FakeStore:
    """In-memory stand-in for PostgresStore (same interface)."""

    def __init__(self):
        self.rows: dict = {}      # (kind, key) -> (payload, tic_id, order)
        self.n = 0

    def get(self, kind, key):
        row = self.rows.get((kind, key))
        return row[0] if row else None

    def latest_for_tic(self, kind, tic_id):
        rows = [r for (k, _), r in self.rows.items() if k == kind and r[1] == tic_id]
        return max(rows, key=lambda r: r[2])[0] if rows else None

    def exists(self, kind, key):
        return (kind, key) in self.rows

    def put(self, kind, key, payload, ttl_seconds, tic_id=None, sector=None):
        self.n += 1
        self.rows[(kind, key)] = (payload, tic_id, self.n)
        return True


@pytest.fixture
def tiers(monkeypatch):
    """Fresh first tier, a fake second tier, and synchronous writes."""
    store = FakeStore()
    monkeypatch.setattr(cache, "_lc_cache", {})
    monkeypatch.setattr(cache, "_ffi_cache", {})
    monkeypatch.setattr(cache, "_STORE", store)
    monkeypatch.setattr(cache, "_submit", lambda fn, *a: fn(*a))
    monkeypatch.delenv("VETSTAR_PERSIST_UPLOADS", raising=False)
    return store


def _parsed(tic=None, sector=None, n=50):
    t = np.linspace(0.0, 10.0, n)
    return {
        "t": t, "flux": np.ones(n), "flux_err": np.full(n, 1e-3),
        "quality": np.zeros(n, dtype=int),
        "mom_x": np.linspace(0, 1, n), "mom_y": np.linspace(1, 0, n),
        "star": StarInfo(tic_id=tic, sector=sector, teff=5000.0, crowdsap=0.9),
    }


def _restart(monkeypatch):
    """Drop the first tier, as a restart or a second instance would."""
    monkeypatch.setattr(cache, "_lc_cache", {})
    monkeypatch.setattr(cache, "_ffi_cache", {})


def test_lc_payload_round_trip():
    entry = {"t": np.arange(5.0), "f": np.ones(5), "mom_x": np.zeros(5),
             "mom_y": None, "star": StarInfo(tic_id=7, sector=3, teff=4321.0)}
    out = cache_store.decode_lc(cache_store.encode_lc(entry))
    np.testing.assert_array_equal(out["t"], entry["t"])
    np.testing.assert_array_equal(out["mom_x"], entry["mom_x"])
    assert out["mom_y"] is None
    assert out["star"] == entry["star"]


def test_tic_curve_survives_a_restart(tiers, monkeypatch):
    assert cache._cache_lc(_parsed(tic=111, sector=5)) is None   # no token for a TIC
    _restart(monkeypatch)
    hit = cache._lookup_lc(111, 5)
    assert hit is not None and hit["star"].tic_id == 111
    assert len(hit["t"]) == len(hit["mom_x"]) == 50
    # It is back in the first tier: a second lookup needs no store.
    monkeypatch.setattr(cache, "_STORE", None)
    assert cache._lookup_lc(111, 5) is not None


def test_store_never_returns_another_targets_curve(tiers, monkeypatch):
    cache._cache_lc(_parsed(tic=111, sector=5))
    _restart(monkeypatch)
    assert cache._lookup_lc(222, 5) is None
    assert cache._lookup_lc(111, 6) is None


def test_latest_sector_for_a_tic_comes_from_the_store(tiers, monkeypatch):
    cache._cache_lc(_parsed(tic=111, sector=5, n=40))
    cache._cache_lc(_parsed(tic=222, sector=9))
    cache._cache_lc(_parsed(tic=111, sector=7, n=60))
    _restart(monkeypatch)
    hit = cache._lookup_lc(111, None)
    assert hit["star"].sector == 7 and len(hit["t"]) == 60


def test_ticless_upload_needs_its_own_token(tiers):
    a = cache._cache_lc(_parsed(n=40))
    b = cache._cache_lc(_parsed(n=60))
    assert a and b and a != b
    assert len(cache._lookup_lc(None, None, a)["t"]) == 40
    assert len(cache._lookup_lc(None, None, b)["t"]) == 60
    # No token, or a wrong one, reaches nobody's upload.
    assert cache._lookup_lc(None, None) is None
    assert cache._lookup_lc(None, None, "not-a-token") is None
    assert cache._lookup_lc(333, None, a) is None


def test_uploads_stay_out_of_the_store_by_default(tiers, monkeypatch):
    token = cache._cache_lc(_parsed())
    assert tiers.rows == {}
    _restart(monkeypatch)
    assert cache._lookup_lc(None, None, token) is None


def test_uploads_are_stored_when_enabled(tiers, monkeypatch):
    monkeypatch.setenv("VETSTAR_PERSIST_UPLOADS", "1")
    token = cache._cache_lc(_parsed())
    assert ("lc", f"upload:{token}") in tiers.rows
    _restart(monkeypatch)
    assert cache._lookup_lc(None, None, token) is not None


def test_ffi_cutout_and_miss_survive_a_restart(tiers, monkeypatch):
    cache._put_ffi((42, 7), {"image": "abc", "n_frames": 3})
    cache._put_ffi((42, 8), None)                    # "TESScut has nothing"
    _restart(monkeypatch)
    assert cache._peek_ffi(42, 7) == (True, {"image": "abc", "n_frames": 3})
    assert cache._peek_ffi(42, 8) == (True, None)
    assert cache._peek_ffi(42, 9) == (False, None)
    assert cache._has_ffi(42, 7) and not cache._has_ffi(43, 7)


def test_get_ffi_cutout_uses_the_store_before_tesscut(tiers, monkeypatch):
    cache._put_ffi((42, 7), {"image": "abc"})
    _restart(monkeypatch)
    from vetstar_core import ffi_cutout
    monkeypatch.setattr(ffi_cutout, "make_ffi_cutout",
                        lambda **k: pytest.fail("TESScut should not be called"))
    assert cache._get_ffi_cutout(42, 7, 10.0, -20.0) == {"image": "abc"}


def test_no_store_means_memory_only(monkeypatch):
    monkeypatch.setattr(cache, "_lc_cache", {})
    monkeypatch.setattr(cache, "_STORE", None)
    cache._cache_lc(_parsed(tic=5, sector=1))
    assert cache._lookup_lc(5, 1) is not None
    assert cache._lookup_lc(5, 2) is None


# ---- PostgresStore failure handling ----------------------------------------

def test_store_failure_is_soft_and_trips_the_breaker():
    calls = []

    def connect():
        calls.append(1)
        raise OSError("database unreachable")

    store = cache_store.PostgresStore("postgresql://unused", connect=connect)
    assert store.get("lc", "tic:1:1") is None
    assert store.put("lc", "tic:1:1", b"x", 60) is False
    assert store.exists("lc", "tic:1:1") is False
    assert store.latest_for_tic("lc", 1) is None
    assert len(calls) == 1          # later calls skip the store for a while


def test_broken_store_does_not_break_a_lookup(monkeypatch):
    store = cache_store.PostgresStore(
        "postgresql://unused", connect=lambda: (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setattr(cache, "_lc_cache", {})
    monkeypatch.setattr(cache, "_ffi_cache", {})
    monkeypatch.setattr(cache, "_STORE", store)
    monkeypatch.setattr(cache, "_submit", lambda fn, *a: fn(*a))
    cache._cache_lc(_parsed(tic=9, sector=2))
    assert cache._lookup_lc(9, 2) is not None        # first tier still works
    assert cache._lookup_lc(9, 3) is None
    assert cache._peek_ffi(9, 2) == (False, None)


def test_store_from_env_is_none_without_a_url(monkeypatch):
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    assert cache_store.store_from_env() is None


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    """Records SQL; answers selects with a canned row."""

    def __init__(self, log, row=None):
        self.log, self.row = log, row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.log.append((" ".join(sql.split()), params))
        return _Cursor(self.row)


def test_postgres_store_sql_shape():
    log: list = []
    store = cache_store.PostgresStore(
        "postgresql://unused", connect=lambda: _Conn(log, row=(memoryview(b"abc"),)))
    assert store.get("lc", "tic:1:2") == b"abc"
    assert store.latest_for_tic("lc", 1) == b"abc"
    assert store.put("ffi", "tic:1:2", b"{}", 3600, tic_id=1, sector=2) is True
    get_sql, latest_sql, put_sql = (entry[0] for entry in log)
    assert "expires_at > now()" in get_sql and "expires_at > now()" in latest_sql
    assert "order by created_at desc limit 1" in latest_sql
    assert "on conflict (kind, cache_key) do update" in put_sql
    assert log[2][1][:4] == ("ffi", "tic:1:2", 1, 2)


def test_postgres_store_prunes_every_few_puts(monkeypatch):
    log: list = []
    monkeypatch.setattr(cache_store, "PRUNE_EVERY", 3)
    store = cache_store.PostgresStore(
        "postgresql://unused", max_mb=10, connect=lambda: _Conn(log))
    for i in range(6):
        store.put("lc", f"k{i}", b"x", 60)
    prunes = [e for e in log if "vetstar_cache_prune" in e[0]]
    assert len(prunes) == 2 and prunes[0][1] == (10 * 1024 * 1024,)
