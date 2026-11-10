"""HCI v2: the sector-count and TOI-disposition sub-scores are gone; cosmic
shoreline, tidal locking and system age take their place. The bundle that
feeds the site, the PDF and the Pi lives in vetstar_core.hci_bundle.
"""
from __future__ import annotations

import inspect

import pytest

from vetstar_core import gaia_catalog, hci_bundle, tic_catalog
from vetstar_core import habitability as hab
from vetstar_core.pipeline import StarInfo, VettingResult

V2_NAMES = [
    "Planet size", "Habitable zone", "Stellar type", "Vetting flags",
    "Cosmic shoreline", "Tidal locking", "System age",
]


def _earth(**kw):
    base = dict(
        radius_earth=1.0, mass_earth=1.0, semi_major_axis_au=1.0,
        orbital_period_d=365.25, stellar_teff=5772.0,
        stellar_radius_sun=1.0, stellar_mass_sun=1.0, age_gyr=4.57,
    )
    base.update(kw)
    return hab.PlanetCandidate(**base)


def _sub(result, name):
    return next(s for s in result.sub_scores if s["name"] == name)


def test_v2_sub_scores_and_weights():
    r = hab.compute_hci(_earth())
    assert [s["name"] for s in r.sub_scores] == V2_NAMES
    assert sum(s["weight"] for s in r.sub_scores) == pytest.approx(1.0)
    assert r.hci_version == "2.0"


def test_sector_counts_are_not_an_input():
    params = inspect.signature(hab.compute_hci).parameters
    assert "n_sectors_observed" not in params
    assert "n_sectors_with_detections" not in params


def test_disposition_does_not_change_the_score():
    cp = hab.compute_hci(_earth(toi_number="1.01", disposition="CP"))
    fp = hab.compute_hci(_earth(toi_number="1.01", disposition="FP"))
    assert cp.hci == fp.hci
    assert any("ExoFOP" in c for c in fp.caveats)


def test_earth_scores_full_on_new_terms():
    r = hab.compute_hci(_earth())
    assert _sub(r, "Cosmic shoreline")["score"] == pytest.approx(1.0)
    assert _sub(r, "Tidal locking")["score"] == pytest.approx(1.0)
    assert _sub(r, "System age")["score"] == pytest.approx(1.0)
    assert r.hci >= 85


# ---- cosmic shoreline ----------------------------------------------------

def test_mars_sits_on_the_shoreline():
    q = hab.shoreline_ratio(mass_earth=0.1074, radius_earth=0.532,
                            insolation_searth=0.431, luminosity_sun=1.0)
    assert q == pytest.approx(1.0, rel=0.02)


def test_hot_small_planet_is_past_the_shoreline():
    r = hab.compute_hci(_earth(radius_earth=0.6, mass_earth=0.2,
                               semi_major_axis_au=0.05))
    s = _sub(r, "Cosmic shoreline")
    assert s["score"] < 0.2
    assert s["label"] == "Airless side"


def test_shoreline_unknown_without_mass_or_flux():
    r = hab.compute_hci(hab.PlanetCandidate(radius_earth=1.0))
    assert _sub(r, "Cosmic shoreline")["label"] == "Unknown"


# ---- tidal locking -------------------------------------------------------

def test_tidal_lock_radius_matches_kasting_at_solar_age():
    assert hab.tidal_lock_radius_au(1.0, 4.5) == pytest.approx(0.46)
    # t^(1/6): 64x younger -> half the radius.
    assert hab.tidal_lock_radius_au(1.0, 4.5 / 64) == pytest.approx(0.23)


def test_m_dwarf_hz_planet_is_tidally_locked():
    r = hab.compute_hci(_earth(
        semi_major_axis_au=0.05, stellar_teff=3200.0,
        stellar_radius_sun=0.26, stellar_mass_sun=0.23, age_gyr=5.0))
    s = _sub(r, "Tidal locking")
    assert s["label"] == "Likely tidally locked"
    assert s["score"] == pytest.approx(0.4)
    assert r.tidal_locking["locked"] is True


# ---- system age ----------------------------------------------------------

def test_teff_to_bv_solar():
    assert hab.teff_to_bv(5778.0) == pytest.approx(0.65, abs=0.01)


def test_gyro_age_recovers_the_sun():
    age = hab.gyro_age_gyr(26.2, hab.teff_to_bv(5772.0))
    assert age == pytest.approx(4.57, rel=0.10)


def test_gyro_age_outside_colour_range_is_none():
    assert hab.gyro_age_gyr(5.0, hab.teff_to_bv(3300.0)) is None


def test_rotation_gives_a_young_age_and_a_low_age_score():
    r = hab.compute_hci(_earth(age_gyr=None, rotation_period_d=3.0))
    assert r.system_age["source"] == "gyrochronology"
    assert r.system_age["age_gyr"] < 0.5
    assert _sub(r, "System age")["score"] < 0.6


def test_unknown_age_is_neutral_and_flagged():
    r = hab.compute_hci(_earth(age_gyr=None))
    assert _sub(r, "System age")["score"] == pytest.approx(0.5)
    assert r.system_age["source"] == "assumed"
    assert r.system_age["age_gyr"] is None


def test_age_past_main_sequence_is_penalised():
    # 1.6 M_sun lives ~3.1 Gyr; a 3 Gyr age is at the end of it.
    r = hab.compute_hci(_earth(stellar_mass_sun=1.6, stellar_teff=7200.0,
                               stellar_radius_sun=1.6, age_gyr=3.0))
    assert _sub(r, "System age")["score"] < 0.6


# ---- bundle (vetstar_core.hci_bundle) -------------------------------------

@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(
        hci_bundle, "query_exofop",
        lambda tic: {"star": {}, "tois": [], "source": "exofop"})
    monkeypatch.setattr(tic_catalog, "backfill_star", lambda star, tic: (star, False))
    monkeypatch.setattr(gaia_catalog, "backfill_star_gaia",
                        lambda star, ra, dec: (star, False))


def test_bundle_carries_v2_blocks(offline):
    b = hci_bundle.build_hci_bundle(
        1, orbital_period_d=365.25, stellar_teff=5772.0,
        stellar_radius_sun=1.0, stellar_mass_sun=1.0, radius_earth=1.0,
        use_archive_rv=False, age_gyr=4.57)
    assert [s["name"] for s in b["hci"]["sub_scores"]] == V2_NAMES
    assert b["hci"]["system_age"]["source"] == "provided"
    assert b["planet"]["radius_source"] == "override"


def _result(ls=None, detrend=None, bls_period=3.0):
    return VettingResult(
        star=StarInfo(tic_id=1, teff=5772.0, radius=1.0, mass=1.0),
        bls={"period": bls_period, "depth": 0.001, "duration": 0.1, "t0": 0.0},
        lomb_scargle=ls or {}, detrend=detrend or {},
        summary={"n_events_detected": 1},
    )


def test_rotation_from_user_period_wins():
    res = _result(detrend={"applied": True, "source": "user_period", "period_days": 7.5},
                  ls={"top_period": 2.0, "top_power": 0.5, "false_alarm_prob": 0.0})
    assert hci_bundle.rotation_period_from_result(res) == (7.5, "user rotation period")


def test_rotation_from_strong_ls_peak():
    res = _result(ls={"top_period": 7.3, "top_power": 0.4, "false_alarm_prob": 1e-9})
    p, src = hci_bundle.rotation_period_from_result(res)
    assert p == pytest.approx(7.3) and "Lomb-Scargle" in src


@pytest.mark.parametrize("ls", [
    {"top_period": 7.3, "top_power": 0.01, "false_alarm_prob": 1e-9},   # weak
    {"top_period": 7.3, "top_power": 0.4, "false_alarm_prob": 0.2},     # not significant
    {"top_period": 3.0, "top_power": 0.4, "false_alarm_prob": 1e-9},    # = transit period
    {"top_period": 1.5, "top_power": 0.4, "false_alarm_prob": 1e-9},    # = P/2
    {"top_period": 0.2, "top_power": 0.4, "false_alarm_prob": 1e-9},    # too short
])
def test_rotation_rejects_doubtful_ls_peaks(ls):
    assert hci_bundle.rotation_period_from_result(_result(ls=ls)) == (None, None)
