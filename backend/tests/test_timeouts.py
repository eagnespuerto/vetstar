"""The shared run-with-timeout helper and its pool."""
import threading
import time

import pytest

from vetstar_core import timeouts
from vetstar_core.timeouts import (
    collect_within,
    result_within,
    run_with_timeout,
    shared_pool,
    submit,
)


def test_returns_the_result_and_passes_arguments():
    assert run_with_timeout(lambda a, b=0: a + b, 2, b=3, timeout_s=5) == 5


def test_late_call_raises_builtin_timeout_error():
    release = threading.Event()
    try:
        t0 = time.monotonic()
        with pytest.raises(TimeoutError):
            run_with_timeout(release.wait, 10, timeout_s=0.1)
        assert time.monotonic() - t0 < 2.0  # did not wait for the call
    finally:
        release.set()


def test_exception_from_the_call_propagates():
    def boom():
        raise ValueError("bad input")

    with pytest.raises(ValueError, match="bad input"):
        run_with_timeout(boom, timeout_s=5)


def test_timeout_raised_by_the_call_itself_is_not_swallowed():
    def socket_like_timeout():
        raise TimeoutError("read timed out")

    with pytest.raises(TimeoutError, match="read timed out"):
        run_with_timeout(socket_like_timeout, timeout_s=5)


def test_every_call_uses_the_one_shared_pool():
    names = {run_with_timeout(lambda: threading.current_thread().name, timeout_s=5)
             for _ in range(4)}
    assert all(n.startswith("vetstar-io") for n in names)
    assert shared_pool() is shared_pool()
    assert threading.active_count() <= timeouts._MAX_WORKERS + 50


def test_result_within_can_leave_a_late_future_queued():
    release = threading.Event()
    fut = submit(lambda: release.wait(10) and "done")
    try:
        with pytest.raises(TimeoutError):
            result_within(fut, 0.05, cancel=False)
        assert not fut.cancelled()
    finally:
        release.set()
    assert result_within(fut, 5) == "done"


def test_collect_within_reports_late_and_failed_futures_softly():
    release = threading.Event()

    def boom():
        raise RuntimeError("service down")

    try:
        out = collect_within(
            {"ok": submit(lambda: 1), "bad": submit(boom),
             "slow": submit(release.wait, 10)},
            timeout_s=0.2,
        )
    finally:
        release.set()
    assert out["ok"] == 1
    assert out["bad"] == {"error": "service down"}
    assert "timed out" in out["slow"]["error"]
