"""Light-curve cleaning before the (unchanged) OGLE search."""
import numpy as np
import pytest

from vetstar_core.lc_clean import clean_for_event_search
from vetstar_core.microlensing import flare_flux, pspl_flux
from vetstar_core.microlensing_ogle import ogle_search

T = np.arange(0, 27, 0.02083)
SIG = 0.002
rng = np.random.default_rng(7)
# Spotted star, +/-3 % over 8 d (like TIC 279614617 S9, where OGLE found no bump).
VAR = 1 + 0.03 * np.sin(2 * np.pi * T / 8.0)


def _run(f):
    f = f + rng.normal(0, SIG, len(f))
    t, fc, ec, info = clean_for_event_search(T, f, np.full(len(f), SIG))
    return ogle_search(t, fc, ec), info


def test_raw_variable_star_does_not_pass():
    f = VAR * pspl_flux(T, 13.0, 0.25, 0.1, 1.0, 0.0) + rng.normal(0, SIG, len(T))
    assert ogle_search(T, f, np.full(len(T), SIG))["verdict"] != "ogle_event"


@pytest.mark.parametrize("amp", [0.03, 0.006])  # 15x and 3x the noise
def test_cleaned_variable_star_short_event_is_found_and_fitted(amp):
    # The bump is found and fitted at the right time and tE. Cleaning leaves
    # residuals of about 1 sigma under the event, so OGLE's strict chi2 cuts
    # (<= 2, same as OGLE) may still fail it; OGLE now sees and judges it.
    var = 1 + amp * np.sin(2 * np.pi * T / 8.0)
    rep, info = _run(var * pspl_flux(T, 13.0, 0.25, 0.1, 1.0, 0.0))
    assert info is not None
    assert rep["verdict"] != "no_event", rep["summary"]
    assert rep["fit"]["t0"] == pytest.approx(13.0, abs=0.05)
    assert rep["fit"]["tE"] == pytest.approx(0.25, rel=0.3)


def test_cleaned_variable_star_flare_is_seen_but_rejected():
    rep, info = _run(VAR * flare_flux(T, 13.0, 0.05, 0.4))
    assert info is not None and rep["bumps"]
    assert rep["verdict"] == "fails_ogle_cuts", rep["summary"]


def test_quiet_star_is_left_alone():
    f = pspl_flux(T, 13.0, 2.5, 0.15, 1.0, 0.0) + rng.normal(0, SIG, len(T))
    t, fc, ec, info = clean_for_event_search(T, f, np.full(len(T), SIG))
    assert info is None and np.allclose(fc, f)
    assert ogle_search(t, fc, ec)["verdict"] == "ogle_event"


def test_long_event_on_quiet_star_is_not_flattened():
    f = pspl_flux(T, 13.0, 8.0, 0.3, 1.0, 0.0) + rng.normal(0, SIG, len(T))
    assert clean_for_event_search(T, f, np.full(len(T), SIG))[3] is None
