"""Tests for the Gaia Alerts photometry fetcher.

HTTP fetches are mocked — we don't hit gsaweb.ast.cam.ac.uk in unit tests.
"""
from __future__ import annotations

import math
from unittest.mock import patch

import pytest

from app.gaia_photometry import (
    GaiaAlertEntry,
    GaiaLightcurve,
    _angular_separation_arcsec,
    _kruszynska_inflated_error,
    _lightcurve_url,
    fetch_alert_lightcurve,
    parse_alerts_index_csv,
    parse_lightcurve_csv,
    search_alerts_near,
)


# ---------------------------------------------------------------------------
# URL construction + input validation
# ---------------------------------------------------------------------------

def test_lightcurve_url_accepts_gaia_id():
    url = _lightcurve_url("Gaia23bra")
    assert url.startswith("https://gsaweb.ast.cam.ac.uk/alerts/alert/Gaia23bra/")
    assert url.endswith("/lightcurve.csv/")


def test_lightcurve_url_rejects_bad_id():
    with pytest.raises(ValueError, match="alert_id must match"):
        _lightcurve_url("../etc/passwd")
    with pytest.raises(ValueError):
        _lightcurve_url("Gaia23bra;rm -rf /")


# ---------------------------------------------------------------------------
# Kruszynska error inflation
# ---------------------------------------------------------------------------

def test_kruszynska_error_inflation_scales_reported():
    # α = 1.5, σ_sys = 3 mmag → for reported 5 mmag we get √((1.5·0.005)² + 0.003²)
    got = _kruszynska_inflated_error(mag=16.0, reported_err=0.005)
    expected = math.sqrt((1.5 * 0.005) ** 2 + 0.003 ** 2)
    assert got == pytest.approx(expected, abs=1e-9)


def test_kruszynska_error_inflation_fills_missing_error():
    # For a bright source (G=14) with no reported error, we get ~5 mmag floor
    # scaled by α and floor-added σ_sys.
    got = _kruszynska_inflated_error(mag=14.0, reported_err=float("nan"))
    # Should be finite and > σ_sys but < 0.1
    assert 0.005 < got < 0.05
    # Faint source → larger inflated error
    got_faint = _kruszynska_inflated_error(mag=19.0, reported_err=float("nan"))
    assert got_faint > got


# ---------------------------------------------------------------------------
# LC CSV parsing — permissive on header format
# ---------------------------------------------------------------------------

_SAMPLE_LC_CSV = """\
# Gaia Alert lightcurve
#Date,JD(TCB),averagemag,averagemag_err
2018-01-01,2458119.5,17.234,0.008
2018-01-08,2458126.5,17.198,0.007
2018-01-15,2458133.5,17.150,0.009
2018-01-22,2458140.5,16.980,0.011
2018-01-29,2458147.5,16.512,0.020
2018-02-05,2458154.5,15.821,0.031
2018-02-12,2458161.5,16.610,0.019
2018-02-19,2458168.5,17.109,0.010
"""


def test_parse_lightcurve_csv_extracts_arrays():
    lc = parse_lightcurve_csv(_SAMPLE_LC_CSV, alert_id="GaiaTest", source_url="test")
    assert lc.n_points == 8
    assert lc.time_jd[0] == pytest.approx(2458119.5)
    assert lc.mag[5] == pytest.approx(15.821)
    # Error should be inflated relative to reported
    assert lc.mag_err[0] > lc.mag_err_reported[0]
    assert lc.alert_id == "GaiaTest"


def test_parse_lightcurve_csv_drops_sentinel_null_rows():
    csv_with_null = _SAMPLE_LC_CSV + "2018-02-26,2458175.5,99.999,0.0\n"
    lc = parse_lightcurve_csv(csv_with_null, "GaiaTest", "test")
    # Sentinel row should be dropped (mag > 90 filter)
    assert lc.n_points == 8


def test_parse_lightcurve_csv_rejects_empty():
    with pytest.raises(ValueError, match="Empty CSV"):
        parse_lightcurve_csv("", "GaiaTest", "test")


def test_parse_lightcurve_csv_rejects_no_recognisable_header():
    with pytest.raises(ValueError, match="Could not locate"):
        parse_lightcurve_csv("foo,bar,baz\n1,2,3\n", "GaiaTest", "test")


def test_parse_lightcurve_csv_rejects_too_few_rows():
    tiny = "JD,mag\n2458000,17\n2458001,17\n"  # only 2 rows
    with pytest.raises(ValueError, match="Only 2 usable rows"):
        parse_lightcurve_csv(tiny, "GaiaTest", "test")


# ---------------------------------------------------------------------------
# Fetcher — mock the HTTP
# ---------------------------------------------------------------------------

def test_fetch_alert_lightcurve_end_to_end_with_mock():
    with patch("app.gaia_photometry._http_get", return_value=_SAMPLE_LC_CSV):
        lc = fetch_alert_lightcurve("Gaia23bra")
    assert isinstance(lc, GaiaLightcurve)
    assert lc.alert_id == "Gaia23bra"
    assert lc.n_points == 8
    assert lc.source_url.endswith("Gaia23bra/lightcurve.csv/")


# ---------------------------------------------------------------------------
# Alerts index + cone search
# ---------------------------------------------------------------------------

_SAMPLE_INDEX_CSV = """\
#Name,Date,RaDeg,DecDeg,AlertMag,HistoricMag,HistoricStdDev,Class,Published,Comment
Gaia23bra,2023-04-15,155.1234,-58.6789,17.5,20.1,0.12,microlensing,2023-04-16 12:00:00,
Gaia23cxy,2023-05-01,155.1300,-58.6800,18.2,21.0,0.10,microlensing candidate,2023-05-02 09:00:00,
Gaia23sne,2023-05-10,10.0000,45.0000,19.0,22.0,0.20,SN candidate,2023-05-11 09:00:00,
Gaia22bul,2022-06-01,266.4000,-29.0000,15.5,17.8,0.08,microlensing,2022-06-02 08:00:00,
"""


def test_parse_alerts_index_csv_reads_all_rows():
    rows = parse_alerts_index_csv(_SAMPLE_INDEX_CSV)
    assert len(rows) == 4
    assert rows[0]["name"] == "Gaia23bra"
    assert rows[0]["ra"] == pytest.approx(155.1234)
    assert rows[0]["class"] == "microlensing"


def test_search_alerts_near_returns_close_hits_only():
    with patch("app.gaia_photometry._http_get", return_value=_SAMPLE_INDEX_CSV):
        # Search at Gaia23bra's coords, tight radius — should return
        # Gaia23bra and Gaia23cxy (the second is ~24" away), NOT Gaia22bul
        # (bulge, far south) or Gaia23sne (SN, filtered out anyway).
        hits = search_alerts_near(
            ra=155.1234, dec=-58.6789,
            radius_arcsec=60.0,
            microlensing_only=True,
        )
    ids = [h.alert_id for h in hits]
    assert "Gaia23bra" in ids
    assert "Gaia23cxy" in ids
    assert "Gaia22bul" not in ids
    assert "Gaia23sne" not in ids  # filtered by class
    # Sorted nearest first
    assert hits[0].alert_id == "Gaia23bra"
    assert hits[0].separation_arcsec < 1.0


def test_search_alerts_near_can_include_non_microlensing():
    with patch("app.gaia_photometry._http_get", return_value=_SAMPLE_INDEX_CSV):
        hits = search_alerts_near(
            ra=10.0, dec=45.0,
            radius_arcsec=60.0,
            microlensing_only=False,
        )
    assert any(h.alert_id == "Gaia23sne" for h in hits)


def test_angular_separation_arcsec_matches_known():
    # 1 degree separation at δ=0 → 3600 arcsec
    sep = _angular_separation_arcsec(0.0, 0.0, 1.0, 0.0)
    assert sep == pytest.approx(3600.0, rel=1e-3)
    # Zero separation
    assert _angular_separation_arcsec(155.0, -58.0, 155.0, -58.0) == pytest.approx(0.0, abs=1e-9)
