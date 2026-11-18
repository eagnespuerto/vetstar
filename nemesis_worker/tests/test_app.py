"""Job-server smoke test with the NEMESIS child process stubbed out."""
import json
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NEMESIS_JOBS_DIR", str(tmp_path))
    monkeypatch.setenv("WORKER_TOKEN", "t0k")
    import importlib
    import worker.app as app_mod
    importlib.reload(app_mod)

    def fake_run(job_id):
        d = app_mod.JOBS_DIR / job_id
        (d / "log.txt").write_text("[vetstar] NEMESIS\nBeginning Transit Search!\n[vetstar] done\n")
        (d / "result.json").write_text(json.dumps({"primary": None, "search": None}))
    monkeypatch.setattr(app_mod, "_run", fake_run)
    with TestClient(app_mod.app) as c:
        yield c


def test_auth_required(client):
    assert client.post("/jobs", json={"tic": 1, "sector": 1}).status_code == 401


def test_job_lifecycle(client):
    h = {"X-Worker-Token": "t0k"}
    r = client.post("/jobs", json={"tic": 103633434, "sector": 20}, headers=h)
    assert r.status_code == 200
    jid = r.json()["job_id"]
    for _ in range(50):
        s = client.get(f"/jobs/{jid}", headers=h).json()
        if s["state"] == "done":
            break
        time.sleep(0.05)
    assert s["state"] == "done" and s["stage"] == "done"
    assert client.get(f"/jobs/{jid}/result", headers=h).json()["primary"] is None
    assert client.get(f"/jobs/{jid}/files", headers=h).json() == []
    assert client.get("/jobs/notajob!/result", headers=h).status_code == 404


def test_bad_period_range(client):
    r = client.post("/jobs", json={"tic": 1, "sector": 1, "minP": 5, "maxP": 2},
                    headers={"X-Worker-Token": "t0k"})
    assert r.status_code == 422
