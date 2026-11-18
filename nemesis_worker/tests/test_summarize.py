"""Tests for the worker's summary and LEO-Vetter labelling (no NEMESIS needed)."""
import numpy as np
import pandas as pd
import pytest

from worker.leo_tests import LEO_TESTS, label_leo_results, leo_disposition
from worker.summarize import MAX_LC_POINTS, summarize


def _lc(n=5000, period=2.0, t0=1.0, depth=0.01):
    t = np.linspace(0, 27, n)
    f = np.ones(n)
    phase = ((t - t0) / period + 0.5) % 1 - 0.5
    f[np.abs(phase) < 0.01] -= depth
    return pd.DataFrame({"Time": t, "SAP Flux": f, "PLD Flux": f,
                         "Detrended Flux": f, "Fitted Trend": np.ones(n)})


def _fake_check(metrics, verbose=False, collect_output_strings=True):
    n = len(LEO_TESTS) if "offset_qual" in metrics else len(LEO_TESTS) - 1
    flags = np.zeros(n, dtype=bool)
    if metrics.get("force_fp"):
        flags[13] = True  # odd_even
    return bool(flags.any()), [f"msg {i}" for i in range(n)], flags


def _results(fp=False, offset=True):
    lc = _lc()
    ps = pd.DataFrame({"BLS Periods": np.linspace(0.5, 13, 4000),
                       "BLS SDE": np.random.default_rng(0).random(4000)})
    fit = pd.DataFrame([{"BLS Period [d]": 2.0, "BLS TC [BTJD]": 1.0, "BLS Dur [hrs]": 1.2,
                         "BLS depth [ppt]": 10.0, "BLS SDE": 25.0, "Planet Radius [RE]": 2.1,
                         "LEOVetter_MES": 30.0, "LEOVetter_force_fp": fp,
                         **({"LEOVetter_offset_qual": 1} if offset else {})}])
    return lc, (ps, None, fit, None, None, None)


def test_summary_thins_and_folds():
    lc, tr = _results()
    out = summarize(lc, tr, _fake_check)
    assert out["primary"] == "BLS"
    assert len(out["light_curve"]["time"]) == MAX_LC_POINTS
    bls = out["search"]["BLS"]
    assert bls["params"]["period_d"] == 2.0
    assert min(bls["fold"]["bin_flux"]) < 0.995  # the dip is at phase 0
    assert len(bls["periodogram"]["period"]) <= 2000


def test_leo_pass_and_fp():
    lc, tr = _results()
    leo = summarize(lc, tr, _fake_check)["search"]["BLS"]["leo"]
    assert leo["available"] and leo["disposition"] == "PC"
    assert len(leo["tests"]) == len(LEO_TESTS)
    lc, tr = _results(fp=True, offset=False)
    leo = summarize(lc, tr, _fake_check)["search"]["BLS"]["leo"]
    assert leo["disposition"] == "FP"
    assert len(leo["tests"]) == len(LEO_TESTS) - 1
    assert [t["key"] for t in leo["tests"] if not t["passed"]] == ["odd_even"]


def test_order_change_is_caught():
    with pytest.raises(ValueError):
        label_leo_results({}, [False] * 3, ["a"] * 3)


def test_fa_beats_fp():
    tests = [{"group": "FA", "passed": False}, {"group": "FP", "passed": False}]
    assert leo_disposition(tests) == "FA"


def test_no_transit_search():
    out = summarize(_lc(), None, _fake_check)
    assert out["search"] is None and out["light_curve"]["time"]
