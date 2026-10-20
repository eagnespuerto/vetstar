"""ExoFOP target-JSON parsing, against a trimmed copy of the live schema.

The parser used to look for ``period`` / ``depth`` / ``prad`` /
``disposition`` in ``planet_parameters``, but ExoFOP rows use ``per`` /
``dep_p`` / ``rad``, and dispositions live in the top-level ``tois`` list.
Every TOI field therefore came back None, so the catalogue radius and
disposition were never used.
"""
from __future__ import annotations

import io
import json
import urllib.request
from pathlib import Path

import pytest

from backend.app import exofop
from backend.app.habitability import _score_toi_disposition

FIXTURE = Path(__file__).parent / "fixtures" / "exofop_tic156002449.json"


@pytest.fixture
def toi324_json():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def serve(monkeypatch):
    def _serve(payload: dict):
        body = json.dumps(payload).encode()
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda *a, **k: io.BytesIO(body)
        )
    return _serve


def test_toi324_fields_parsed(serve, toi324_json):
    # TIC 156002449 / TOI-324.01: TFOP PC, TESS-project EB.
    serve(toi324_json)
    out = exofop.query_exofop(156002449)
    assert out["source"] == "exofop"
    assert out["star"]["radius"] == pytest.approx(1.27295)
    [toi] = out["tois"]
    assert toi["toi_number"] == "324.01"
    assert toi["period_d"] == pytest.approx(5.284869067171003)
    assert toi["depth_ppm"] == pytest.approx(27277.099, rel=1e-6)
    assert toi["duration_hr"] == pytest.approx(4.7725494)
    assert toi["disposition"] == "PC"
    assert toi["disposition_tess"] == "EB"
    assert "retired" in toi["notes"]
    assert toi["sectors"] == ["2", "29", "69"]


def test_user_uploaded_rows_are_ignored(serve, toi324_json):
    # The ctoi row (rad 27.07, a vetstar upload) must not leak into the
    # catalogue values: only the TESS-project row (rad 21.38) counts.
    serve(toi324_json)
    [toi] = exofop.query_exofop(156002449)["tois"]
    assert toi["radius_earth"] == pytest.approx(21.377066)
    assert toi["period_d"] != pytest.approx(5.2835)
    # sma is absent from the TESS row, so a comes from Kepler III, not the upload.
    assert toi["semi_major_axis_au"] != pytest.approx(0.0588)
    assert toi["semi_major_axis_au"] == pytest.approx(
        (1.15 * (5.284869067171003 / 365.25) ** 2) ** (1 / 3), rel=1e-4
    )


def test_tess_disposition_is_fallback_and_meta_only_tois_kept(toi324_json):
    data = dict(toi324_json)
    data["tois"] = [
        {**toi324_json["tois"][0], "disp_tfop": ""},
        {"toi": "TOI 324.02", "disp_tfop": "", "disp_tess": "PC", "sector": "69"},
    ]
    tois = exofop._parse_tois(data, {"mass": 1.15})
    assert [t["toi_number"] for t in tois] == ["324.01", "324.02"]
    assert tois[0]["disposition"] == "EB"
    assert tois[1]["disposition"] == "PC" and tois[1]["period_d"] is None


def test_small_dep_p_is_not_rescaled_as_percent():
    # dep_p is always ppm; a 60 ppm super-Earth must not become 600,000 ppm.
    data = {
        "planet_parameters": [
            {"prov": "toi", "prov_num": "1"},
            {"toi": "TOI 100.01", "per": "3.0", "dep_p": "60", "rad": "1.1"},
        ],
        "tois": [],
    }
    [toi] = exofop._parse_tois(data, {})
    assert toi["depth_ppm"] == pytest.approx(60.0)


@pytest.mark.parametrize("raw", ["TOI 324.01", "TOI-324.01", "324.01", "324.01."])
def test_toi_key_normalisation(raw):
    assert exofop._toi_key(raw) == "324.01"


def test_eb_disposition_ranks_as_false_positive():
    assert _score_toi_disposition("EB", "324.01").score == pytest.approx(0.05)


def test_semi_major_axis_source_tagged(toi324_json):
    [toi] = exofop._parse_tois(toi324_json, {"mass": 1.15})
    assert toi["semi_major_axis_source"].startswith("Kepler III")
    data = json.loads(json.dumps(toi324_json))
    data["planet_parameters"][1]["sma"] = "0.0612"
    [toi] = exofop._parse_tois(data, {"mass": 1.15})
    assert toi["semi_major_axis_au"] == pytest.approx(0.0612)
    assert toi["semi_major_axis_source"] == "exofop"
