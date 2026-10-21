"""Regression tests for the ExoFOP-block planet radius and the catalogue
"Known Planet" override.

Radius: the report used to take R_p from the discrete-dip dilution-corrected
depth (``R_companion_Rjup``) even when SPOC DV values were available, so the
ExoFOP block printed a Radius that disagreed with its own R_planet/R_star.

Verdict: SIMBAD otype "Pl?" (candidate) matched the "Pl" prefix first and was
promoted to "Known Planet" at 99% (TOI-324.01, a TFOP-retired FP/EB).
"""
from __future__ import annotations

import pytest

from backend.app import gaia_catalog, main, pipeline, tic_catalog


# TIC 255910975, sector 101: DV Rp/R* 0.0224, R* 1.78 R_sun -> ~4.35 R_E.
# The discrete-dip path gave 8.22 R_E (R_companion_Rjup = 8.22 / 11.209).
RPRS = 0.0224
RSTAR = 1.78
EXPECTED_RP = RPRS * RSTAR * 109.08
DIP_RJUP = 8.22 / 11.209


@pytest.fixture
def offline_catalogs(monkeypatch):
    monkeypatch.setattr(
        main, "query_exofop",
        lambda tic: {"star": {}, "tois": [], "source": "exofop"},
    )
    monkeypatch.setattr(tic_catalog, "backfill_star", lambda star, tic: (star, False))
    monkeypatch.setattr(gaia_catalog, "backfill_star_gaia", lambda star, ra, dec: (star, False))


def _bundle(**verdict):
    query = main.HabitabilityQuery(
        tic_id=255910975,
        orbital_period_d=10.0,
        stellar_teff=6200.0,
        stellar_radius_sun=RSTAR,
        stellar_mass_sun=1.25,
        R_companion_Rjup=DIP_RJUP,
        use_archive_rv=False,
        vetting_verdict={"_depth": 0.0018, **verdict},
    )
    return main._habitability_bundle(query)


def test_radius_uses_spoc_dv_rprs_not_discrete_dip(offline_catalogs):
    b = _bundle(_dvt_rprs=RPRS, _dvt_depth_frac=0.00045)
    assert b["tlcm"]["radius_ratio_k"] == pytest.approx(RPRS)
    assert b["planet"]["radius_earth"] == pytest.approx(EXPECTED_RP, rel=2e-3)
    assert b["planet"]["radius_source"].startswith("SPOC DV")
    # Mass and RV K are derived from the corrected radius, not the 8.22 R_E one.
    assert b["observables"]["planet"]["rp_earth"] == pytest.approx(EXPECTED_RP, rel=2e-3)


def test_exofop_rows_radius_matches_printed_ratio(offline_catalogs):
    from backend.app.observables import exofop_param_rows

    b = _bundle(_dvt_rprs=RPRS, _dvt_depth_frac=0.00045)
    rows = {r["label"]: r["value"] for r in exofop_param_rows(
        observables=b["observables"], tlcm=b["tlcm"])}
    assert rows["Radius"] == pytest.approx(rows["R_planet/R_star"] * RSTAR * 109.08, rel=2e-3)


def test_radius_falls_back_to_dv_depth_without_rprs(offline_catalogs):
    depth = RPRS ** 2
    b = _bundle(_dvt_depth_frac=depth)
    assert b["planet"]["radius_earth"] == pytest.approx(EXPECTED_RP, rel=2e-3)


def test_resolve_radius_priority():
    r = main._resolve_planet_radius
    # override wins
    assert r(override_earth=3.0, radius_ratio_k=RPRS, rstar_sun=RSTAR, has_dvt=True)[0] == 3.0
    # DV beats catalogue and the discrete dip
    assert r(radius_ratio_k=RPRS, rstar_sun=RSTAR, has_dvt=True,
             catalog_radius_earth=9.9, r_companion_rjup=DIP_RJUP)[0] == pytest.approx(EXPECTED_RP, rel=2e-3)
    # without DV, the catalogue beats the pipeline depth
    assert r(radius_ratio_k=RPRS, rstar_sun=RSTAR, catalog_radius_earth=9.9)[1] == "exofop"
    # pipeline depth beats the discrete dip
    rp, src = r(radius_ratio_k=RPRS, rstar_sun=RSTAR, r_companion_rjup=DIP_RJUP)
    assert rp == pytest.approx(EXPECTED_RP, rel=2e-3) and src.startswith("pipeline depth")
    # discrete dip only as the last resort
    assert r(r_companion_rjup=DIP_RJUP)[0] == pytest.approx(8.22)
    assert r() == (None, None)


def _crossmatch_with_simbad(monkeypatch, otype):
    monkeypatch.setattr(pipeline, "_query_gaia_dr3", lambda *a: None)
    monkeypatch.setattr(pipeline, "_query_nasa_exoplanet_archive", lambda *a: {"matched": False})
    monkeypatch.setattr(pipeline, "_query_exoplanets_org", lambda *a: {"matched": False})
    monkeypatch.setattr(
        pipeline, "_query_simbad",
        lambda coord, radius: {"main_id": "TOI-324.01", "otype": otype, "ids": ""},
    )
    return pipeline.crossmatch_known_object(8.4819963, -50.7294341)


def test_simbad_planet_candidate_is_not_known_planet(monkeypatch):
    # TOI-324.01 (TIC 156002449): SIMBAD otype "Pl?", ExoFOP TFOP PC retired as FP/EB.
    known = _crossmatch_with_simbad(monkeypatch, "Pl?")
    assert known["headline"] == "Known Planet Candidate"

    verdict = {"headline": "Likely Eclipsing Binary", "category": "eb", "confidence": 0.8}
    pipeline.apply_known_object_override(verdict, known)
    assert verdict["headline"] == "Likely Eclipsing Binary"
    assert verdict["confidence"] == 0.8
    assert "known_object_override" not in verdict.get("flags", [])
    assert "Known Planet Candidate" in verdict["reasons"][0]


def test_simbad_confirmed_planet_still_overrides(monkeypatch):
    known = _crossmatch_with_simbad(monkeypatch, "Pl")
    assert known["headline"] == "Known Planet"

    verdict = {"headline": "Planet Candidate", "category": "pc", "confidence": 0.7}
    pipeline.apply_known_object_override(verdict, known)
    assert verdict["headline"] == "Known Planet"
    assert verdict["confidence"] == 0.99
    assert verdict["original_headline"] == "Planet Candidate"


def test_exoplanets_org_parser_keeps_only_positioned_rows():
    import io

    csv_text = (
        "NAME,PER,RA,DEC,HOSTNAME,EXTRA\n"
        "Foo b,3.5,10.0,-20.0,Foo,x\n"
        "Blank c,1.2,,,Blank,x\n"          # no coordinates: must not sit at (0, 0)
        "Bar b,,11.5,5.25,Bar,x\n"         # missing period -> None, not '--'
        "Bad d,2.0,nan,1.0,Bad,x\n"
    )
    tbl = pipeline._parse_exoplanets_org_csv(io.StringIO(csv_text))
    assert tbl["ra"].tolist() == [10.0, 11.5]
    assert tbl["dec"].tolist() == [-20.0, 5.25]
    assert tbl["name"] == ["Foo b", "Bar b"]
    assert tbl["host"] == ["Foo", "Bar"]
    assert tbl["period"] == ["3.5", None]

    pipeline_cache = pipeline._EXOPLANETS_ORG_CACHE
    try:
        pipeline._EXOPLANETS_ORG_CACHE = tbl
        hit = pipeline._query_exoplanets_org(10.0, -20.0, 5.0)
        assert hit["matched"] and hit["name"] == "Foo b" and hit["period_days"] == "3.5"
        assert pipeline._query_exoplanets_org(0.0, 0.0, 5.0)["matched"] is False
    finally:
        pipeline._EXOPLANETS_ORG_CACHE = pipeline_cache


def test_exoplanets_org_parser_rejects_header_without_coordinates():
    import io

    assert pipeline._parse_exoplanets_org_csv(io.StringIO("NAME,PER\nx,1\n")) is None
    assert pipeline._parse_exoplanets_org_csv(io.StringIO("")) is None
