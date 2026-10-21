"""Combined vet + PDF job: prefetch ordering, report store, SSE protocol."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import numpy as np
from fastapi.testclient import TestClient

from backend.app import main, pipeline
from backend.app.prefetch import Prefetcher, ReportStore
from backend.app.progress import STAGE_LABELS, STAGE_WEIGHTS

from backend.tests.conftest import inject_box_transit


def test_stage_weights_sum_to_100_and_all_labelled():
    assert sum(STAGE_WEIGHTS.values()) == 100
    assert set(STAGE_WEIGHTS) == set(STAGE_LABELS)


def test_prefetcher_returns_default_on_failure_and_timeout():
    pf = Prefetcher(max_workers=2)
    try:
        pf.submit("ok", lambda: 7)
        pf.submit("boom", lambda: 1 / 0)
        pf.submit("slow", time.sleep, 2)
        assert pf.get("ok", timeout=5) == 7
        assert pf.get("boom", timeout=5, default="d") == "d"
        assert pf.get("slow", timeout=0.05, default="late") == "late"
        assert pf.get("never-submitted", default=None) is None
        assert pf.has("ok") and not pf.has("never-submitted")
    finally:
        pf.close()


def test_report_store_is_bounded():
    store = ReportStore(max_items=2)
    a = store.put(b"a", "a.pdf")
    b = store.put(b"b", "b.pdf")
    c = store.put(b"c", "c.pdf")
    assert store.get(a) is None
    assert store.get(b) == (b"b", "b.pdf")
    assert store.get(c) == (b"c", "c.pdf")


def test_start_prefetch_submits_heaviest_first():
    class Recorder:
        def __init__(self):
            self.names = []

        def submit(self, name, *_a, **_k):
            self.names.append(name)

    rec = Recorder()
    main._start_prefetch(rec, 123, 5, 10.0, -20.0)
    assert rec.names == ["ffi", "dvt", "crossmatch", "exofop"]

    rec = Recorder()
    main._start_prefetch(rec, 123, 5, 10.0, -20.0, report=False)
    assert rec.names == ["dvt", "crossmatch"]


def test_pipeline_uses_prefetched_crossmatch(monkeypatch, rng):
    def _no_network(*_a, **_k):
        raise AssertionError("pipeline queried catalogues despite a prefetched result")

    monkeypatch.setattr(pipeline, "crossmatch_known_object", _no_network)
    t = np.arange(0, 20, 2 / 1440)
    f = inject_box_transit(t, 1 + 1e-4 * rng.standard_normal(t.size),
                           period=3.0, t0=1.0, depth=0.01, duration=0.1)
    known = {"available": True, "matched": True, "headline": "Known Planet",
             "name": "Test b", "description": "NASA Exoplanet Archive: Test b"}
    star = pipeline.StarInfo(tic_id=1, sector=1, ra=10.0, dec=-20.0)
    res = pipeline.run_full_vetting(
        t=t, flux=f, flux_err=np.full(t.size, 1e-4), quality=np.zeros(t.size, int),
        mom_x=None, mom_y=None, star=star, known_object=lambda: known,
    )
    assert res.verdict["headline"] == "Known Planet"


def _read_events(resp) -> list:
    return [json.loads(line[6:]) for line in resp.iter_lines()
            if line.startswith("data: ")]


def test_vet_job_streams_result_then_report(monkeypatch):
    plots = {}
    fake_result = SimpleNamespace(
        star=SimpleNamespace(tic_id=42, sector=7),
        plots=plots,
        to_dict=lambda: {"verdict": {"headline": "Planet candidate"}, "plots": plots},
    )

    def fake_fetch(query, reporter=None, pf=None, report=False):
        assert pf is not None and report is True
        return fake_result, {"path": "x.fits", "dvt": None}

    monkeypatch.setattr(main, "_mast_fetch_and_analyze", fake_fetch)
    monkeypatch.setattr(main, "_attach_hci_summary_to_plots", lambda *a, **k: None)
    monkeypatch.setattr(main, "_build_report_extras", lambda *a, **k: {})
    monkeypatch.setattr(main, "build_pdf", lambda *a, **k: b"%PDF-fake")
    monkeypatch.setattr(main, "_downsample_cached_lc", lambda *a, **k: None)

    client = TestClient(main.app)
    job_id = client.post("/api/vet_async", json={"tic_id": 42, "sector": 7}).json()["job_id"]
    with client.stream("GET", f"/api/jobs/{job_id}/stream") as resp:
        events = _read_events(resp)

    kinds = [e["type"] for e in events if e["type"] != "progress"]
    assert kinds == ["result", "done"]
    assert events[-1]["percent"] == 100.0
    result_evt = next(e for e in events if e["type"] == "result")
    assert result_evt["result"]["verdict"]["headline"] == "Planet candidate"
    assert result_evt["result"]["mast"]["filename"] == "x.fits"

    report = events[-1]["result"]["report"]
    assert report["filename"] == "vetting_TIC42_S007.pdf"
    pdf = client.get(f"/api/reports/{report['report_id']}")
    assert pdf.status_code == 200
    assert pdf.content == b"%PDF-fake"
    assert client.get("/api/reports/nope").status_code == 404


def test_vet_job_reports_pdf_failure_without_losing_result(monkeypatch):
    fake_result = SimpleNamespace(
        star=SimpleNamespace(tic_id=42, sector=7), plots={},
        to_dict=lambda: {"verdict": {}},
    )
    monkeypatch.setattr(main, "_mast_fetch_and_analyze",
                        lambda *a, **k: (fake_result, {"dvt": None}))
    monkeypatch.setattr(main, "_attach_hci_summary_to_plots", lambda *a, **k: None)
    monkeypatch.setattr(main, "_build_report_extras", lambda *a, **k: {})
    monkeypatch.setattr(main, "_downsample_cached_lc", lambda *a, **k: None)

    def broken_pdf(*_a, **_k):
        raise ValueError("font missing")

    monkeypatch.setattr(main, "build_pdf", broken_pdf)

    client = TestClient(main.app)
    job_id = client.post("/api/vet_async", json={"tic_id": 42, "sector": 7}).json()["job_id"]
    with client.stream("GET", f"/api/jobs/{job_id}/stream") as resp:
        events = _read_events(resp)
    assert [e["type"] for e in events if e["type"] != "progress"] == ["result", "done"]
    done = events[-1]["result"]
    assert done["report"] is None
    assert "font missing" in done["report_error"]
