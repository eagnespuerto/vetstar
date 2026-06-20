"""Progress reporting for long-running analyses.

The vetting pipeline takes 10–60s — long enough that an indeterminate
spinner frustrates users. This module provides:

* ``ProgressReporter`` — a callable threaded through the pipeline. Each
  stage calls ``reporter(stage_name, percent, message="")``. Percent is
  monotone, computed from per-stage weights so partial-stage updates
  don't move the bar backwards.

* ``JobRegistry`` — in-memory map ``{job_id: JobState}`` backing the
  ``/api/jobs/{id}/stream`` SSE endpoint. Process-local, fine for the
  single-machine Render/Fly deployment.

Stage weights below sum to 100 and reflect typical wall-clock fractions
observed on a warm MAST cache. The biggest single chunk is the MAST
fetch on a cold target.
"""
from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional


# Stage weights — sum to 100. Adjust if telemetry shows drift.
STAGE_WEIGHTS: dict[str, int] = {
    "mast_fetch":   35,
    "dvt_fetch":     3,
    "parse":         2,
    "clean":         3,
    "lomb_scargle":  7,
    "detrend":       5,
    "bls":          25,
    "events":        4,
    "shape":         3,
    "centroid":      3,
    "odd_even":      3,
    "secondary":     2,
    "physics":       1,
    "verdict":       1,
    "crossmatch":    2,
    "plots":         6,
}

# Human-readable labels shown in the UI progress bar.
STAGE_LABELS: dict[str, str] = {
    "mast_fetch":   "Fetching light curve from MAST",
    "dvt_fetch":    "Fetching SPOC DV summary",
    "parse":        "Parsing FITS",
    "clean":        "Cleaning cadences",
    "lomb_scargle": "Lomb-Scargle periodogram",
    "detrend":      "Detrending stellar variability",
    "bls":          "Running Box-Least-Squares",
    "events":       "Detecting events",
    "shape":        "Measuring transit shape",
    "centroid":     "Centroid check",
    "odd_even":     "Odd-even test",
    "secondary":    "Secondary-eclipse search",
    "physics":      "Physics interpretation",
    "verdict":      "Compiling verdict",
    "crossmatch":   "External catalog cross-match",
    "plots":        "Rendering plots",
}


def _cumulative_percent(stage: str, fraction: float) -> float:
    """Total percent after ``fraction`` (0..1) of ``stage`` is complete."""
    pct = 0.0
    for name, weight in STAGE_WEIGHTS.items():
        if name == stage:
            pct += weight * max(0.0, min(1.0, fraction))
            return pct
        pct += weight
    # Unknown stage — return the running total as a safe upper bound.
    return pct


class ProgressReporter:
    """Callable that turns stage events into monotone percent updates.

    Use as ``reporter("bls", 0.0)`` at stage start and
    ``reporter("bls", 1.0)`` at stage end. Anything in between (e.g.
    ``reporter("bls", 0.5, "halfway")``) is allowed. The reporter
    guarantees percent never decreases — late callbacks from a stage
    that already advanced are clamped to the current maximum.
    """

    def __init__(self, sink: Callable[[str, float, str], None]):
        self._sink = sink
        self._max_pct = 0.0
        self._lock = threading.Lock()

    def __call__(self, stage: str, fraction: float = 1.0, message: str = "") -> None:
        pct = _cumulative_percent(stage, fraction)
        with self._lock:
            if pct < self._max_pct:
                pct = self._max_pct
            else:
                self._max_pct = pct
        label = STAGE_LABELS.get(stage, stage)
        try:
            self._sink(stage, pct, message or label)
        except Exception:
            # A broken sink (e.g. client disconnected) must never crash the pipeline.
            pass


def make_noop_reporter() -> ProgressReporter:
    """Reporter that discards all events — used when no client is listening."""
    return ProgressReporter(lambda *_a, **_k: None)


# ---------------------------------------------------------------------------
# Job registry — backs the SSE endpoint.
# ---------------------------------------------------------------------------


@dataclass
class JobState:
    job_id: str
    queue: "queue.Queue[dict]" = field(default_factory=queue.Queue)
    done: bool = False
    result: Optional[dict] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)


class JobRegistry:
    """Thread-safe ``{job_id: JobState}`` map with periodic GC of finished jobs.

    Finished jobs are kept ``ttl_seconds`` (default 5 min) so a slow
    client can still pick up the final result/error after the worker
    thread exits.
    """

    def __init__(self, ttl_seconds: float = 300.0):
        self._jobs: dict[str, JobState] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    def create(self) -> JobState:
        self._gc()
        st = JobState(job_id=uuid.uuid4().hex)
        with self._lock:
            self._jobs[st.job_id] = st
        return st

    def get(self, job_id: str) -> Optional[JobState]:
        with self._lock:
            return self._jobs.get(job_id)

    def reporter_for(self, st: JobState) -> ProgressReporter:
        def sink(stage: str, pct: float, message: str) -> None:
            st.queue.put({"type": "progress", "stage": stage, "percent": pct, "message": message})
        return ProgressReporter(sink)

    def finish_ok(self, st: JobState, result: dict) -> None:
        st.result = result
        st.done = True
        st.queue.put({"type": "done", "percent": 100.0, "result": result})

    def finish_err(self, st: JobState, message: str) -> None:
        st.error = message
        st.done = True
        st.queue.put({"type": "error", "message": message})

    def _gc(self) -> None:
        now = time.time()
        with self._lock:
            stale = [jid for jid, st in self._jobs.items()
                     if st.done and (now - st.created_at) > self._ttl]
            for jid in stale:
                self._jobs.pop(jid, None)


# Module-level singleton — main.py imports this directly.
JOBS = JobRegistry()
