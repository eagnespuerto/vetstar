"""Persistent second tier for the caches in ``cache.py``: a Postgres table
on Supabase (``supabase/migrations``), reached over a direct connection.

The in-process dicts in ``cache.py`` stay as the first tier. This tier makes
a cached light curve or FFI cutout survive a restart (Render's free plan
sleeps after 15 minutes) and lets more than one instance share it.

Everything here fails soft. With no ``SUPABASE_DB_URL`` there is no store.
When the database is slow or down, calls return "not found" and the store
is skipped for a minute, so a vetting run never waits on it.
"""
from __future__ import annotations

import dataclasses
import io
import json
import logging
import os
import threading
import time
from typing import Optional

import numpy as np

from vetstar_core.pipeline import StarInfo

log = logging.getLogger("vetting")

TABLE = "public.vetstar_cache"
CONNECT_TIMEOUT_S = 8
BREAKER_SECONDS = 60.0
PRUNE_EVERY = 20                     # puts between prune calls
DEFAULT_MAX_MB = 300                 # of the free tier's 500 MB database


# ---------------------------------------------------------------------------
# Payload encoding
# ---------------------------------------------------------------------------

def encode_lc(entry: dict) -> bytes:
    """Cached light-curve entry -> compressed .npz bytes (no pickle)."""
    arrays = {
        "t": np.asarray(entry["t"], dtype=np.float64),
        "f": np.asarray(entry["f"], dtype=np.float64),
    }
    for name in ("mom_x", "mom_y"):
        if entry.get(name) is not None:
            arrays[name] = np.asarray(entry[name], dtype=np.float64)
    star = entry.get("star")
    if star is not None:
        arrays["star_json"] = np.array(json.dumps(dataclasses.asdict(star)))
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    return buf.getvalue()


def decode_lc(payload: bytes) -> dict:
    with np.load(io.BytesIO(payload), allow_pickle=False) as z:
        names = set(z.files)
        star = None
        if "star_json" in names:
            known = {f.name for f in dataclasses.fields(StarInfo)}
            data = json.loads(str(z["star_json"]))
            star = StarInfo(**{k: v for k, v in data.items() if k in known})
        return {
            "t": z["t"],
            "f": z["f"],
            "mom_x": z["mom_x"] if "mom_x" in names else None,
            "mom_y": z["mom_y"] if "mom_y" in names else None,
            "star": star,
        }


def encode_json(value) -> bytes:
    """FFI cutout dict (or None for "no cutout") -> JSON bytes."""
    return json.dumps(value, default=str).encode("utf-8")


def decode_json(payload: bytes):
    return json.loads(bytes(payload).decode("utf-8"))


# ---------------------------------------------------------------------------
# Postgres store
# ---------------------------------------------------------------------------

class PostgresStore:
    """``vetstar_cache`` rows as a key/value store of bytes.

    A connection is opened per call: calls are rare (first-tier misses and
    one write per analysis) and Supabase's pooler makes connecting cheap.
    ``prepare_threshold=None`` keeps psycopg compatible with the pooler's
    transaction mode.
    """

    def __init__(self, dsn: str, max_mb: int = DEFAULT_MAX_MB, connect=None):
        self._dsn = dsn
        self._max_bytes = int(max_mb) * 1024 * 1024
        self._connect_fn = connect
        self._lock = threading.Lock()
        self._down_until = 0.0
        self._puts = 0

    def _connect(self):
        if self._connect_fn is not None:
            return self._connect_fn()
        import psycopg
        return psycopg.connect(
            self._dsn, autocommit=True, prepare_threshold=None,
            connect_timeout=CONNECT_TIMEOUT_S,
        )

    def _run(self, what: str, fn, default=None):
        """Run ``fn(conn)``; on any error log it, trip the breaker and
        return ``default``."""
        if time.monotonic() < self._down_until:
            return default
        try:
            with self._connect() as conn:
                return fn(conn)
        except Exception as e:
            with self._lock:
                self._down_until = time.monotonic() + BREAKER_SECONDS
            log.warning("cache store %s failed (%s: %s); skipping the store for %.0fs",
                        what, type(e).__name__, e, BREAKER_SECONDS)
            return default

    @staticmethod
    def _one(conn, sql: str, params: tuple):
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None

    def get(self, kind: str, key: str) -> Optional[bytes]:
        payload = self._run("get", lambda c: self._one(
            c, f"select payload from {TABLE} "
               "where kind = %s and cache_key = %s and expires_at > now()",
            (kind, key)))
        return bytes(payload) if payload is not None else None

    def latest_for_tic(self, kind: str, tic_id: int) -> Optional[bytes]:
        payload = self._run("latest", lambda c: self._one(
            c, f"select payload from {TABLE} "
               "where kind = %s and tic_id = %s and expires_at > now() "
               "order by created_at desc limit 1",
            (kind, int(tic_id))))
        return bytes(payload) if payload is not None else None

    def exists(self, kind: str, key: str) -> bool:
        return bool(self._run("exists", lambda c: self._one(
            c, f"select 1 from {TABLE} "
               "where kind = %s and cache_key = %s and expires_at > now()",
            (kind, key))))

    def put(self, kind: str, key: str, payload: bytes, ttl_seconds: float,
            tic_id: Optional[int] = None, sector: Optional[int] = None) -> bool:
        def _do(conn):
            conn.execute(
                f"insert into {TABLE} (kind, cache_key, tic_id, sector, payload, expires_at) "
                "values (%s, %s, %s, %s, %s, now() + make_interval(secs => %s)) "
                "on conflict (kind, cache_key) do update set "
                "tic_id = excluded.tic_id, sector = excluded.sector, "
                "payload = excluded.payload, created_at = now(), "
                "expires_at = excluded.expires_at",
                (kind, key, tic_id, sector, payload, float(ttl_seconds)))
            with self._lock:
                self._puts += 1
                prune = self._puts % PRUNE_EVERY == 0
            if prune:
                conn.execute("select public.vetstar_cache_prune(%s)", (self._max_bytes,))
            return True
        return bool(self._run("put", _do, default=False))


def store_from_env() -> Optional[PostgresStore]:
    """The store named by ``SUPABASE_DB_URL``, or None when it is not set
    (local runs, tests, the Pi) or psycopg is not installed."""
    dsn = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not dsn:
        return None
    try:
        import psycopg  # noqa: F401
    except ImportError:
        log.warning("SUPABASE_DB_URL is set but psycopg is not installed; "
                    "caches stay in memory only.")
        return None
    max_mb = int(os.environ.get("VETSTAR_CACHE_MAX_MB", str(DEFAULT_MAX_MB)))
    log.info("cache store: Supabase Postgres (limit %d MB)", max_mb)
    return PostgresStore(dsn, max_mb=max_mb)
