"""OGLE-style (Mróz et al. 2019) event search on synthetic TESS light curves."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from vetstar_core.microlensing import flare_flux, pspl_flux
from vetstar_core.microlensing_ogle import find_bumps, ogle_search

T = np.arange(0, 27, 0.02083)  # 30-min cadence over one sector
SIG = 0.002
rng = np.random.default_rng(42)


def _noisy(f):
    return f + rng.normal(0, SIG, len(f)), np.full(len(f), SIG)


def _cut(rep, name):
    return next(c for c in rep["cuts"] if c["name"] == name)


def test_pspl_event_passes():
    f, e = _noisy(pspl_flux(T, 13.0, 2.5, 0.15, 1.0, 0.0))
    rep = ogle_search(T, f, e)
    assert rep["verdict"] == "ogle_event", rep["summary"]
    fit = rep["fit"]
    assert fit["t0"] == pytest.approx(13.0, abs=0.05)
    assert fit["tE"] == pytest.approx(2.5, rel=0.15)
    assert fit["u0"] == pytest.approx(0.15, rel=0.2)
    assert _cut(rep, "n_DIA")["passed"] is None  # not applicable to TESS


def test_flat_star_has_no_event():
    f, e = _noisy(np.ones_like(T))
    rep = ogle_search(T, f, e)
    assert rep["verdict"] == "no_event" and not rep["passed"]


def test_two_bumps_fail_single_bump_cut():
    base = pspl_flux(T, 6.0, 0.8, 0.2, 1.0, 0.0) + pspl_flux(T, 20.0, 0.8, 0.2, 1.0, 0.0) - 1
    f, e = _noisy(base)
    rep = ogle_search(T, f, e)
    assert _cut(rep, "n_bump")["passed"] is False
    assert rep["verdict"] == "fails_ogle_cuts"


def test_flare_is_rejected():
    f, e = _noisy(flare_flux(T, 13.0, 0.5, 0.05, 1.0))
    rep = ogle_search(T, f, e)
    assert rep["verdict"] != "ogle_event"


def test_find_bumps_needs_three_in_a_row():
    f = np.ones(20)
    f[5:7] = 1.1  # only two points
    assert find_bumps(f, np.full(20, 0.01), 1.0, 0.01, 3, 3.0) == []
    f[5:8] = 1.1
    (b,) = find_bumps(f, np.full(20, 0.01), 1.0, 0.01, 3, 3.0)
    assert b["n"] == 3 and b["chi2_3plus"] == pytest.approx(30.0)


def test_endpoint():
    c = TestClient(app)
    f, e = _noisy(pspl_flux(T, 13.0, 2.5, 0.15, 1.0, 0.0))
    r = c.post("/api/microlensing/ogle_search",
               json={"time": T.tolist(), "flux": f.tolist(), "flux_err": e.tolist()})
    assert r.status_code == 200 and r.json()["paper"]["doi"] == "10.3847/1538-4365/ab426b"
    assert "chi2_3plus_min" in c.get("/api/microlensing/ogle_cuts").json()
