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


def test_index_page_is_friendly(client):
    r = client.get("/")
    assert r.status_code == 200 and "vetstar NEMESIS worker" in r.text


def test_watch_kills_a_stalled_download(tmp_path, monkeypatch):
    import subprocess, sys
    from worker import app as w
    log = tmp_path / "log.txt"
    log.write_text("[vetstar] NEMESIS 2.0 TIC 1 S1\n[lksearch] trying radius 5.00 px\n")
    monkeypatch.setattr(w, "STALL_S", 1.0)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    assert w._watch(proc, log, poll_s=0.2) is True
    assert proc.returncode is not None


def test_watch_lets_quiet_tls_run(tmp_path, monkeypatch):
    import subprocess, sys
    from worker import app as w
    log = tmp_path / "log.txt"
    log.write_text("[vetstar] NEMESIS\nBeginning Transit Search\ndoing TLS search\n")
    monkeypatch.setattr(w, "STALL_S", 0.5)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    assert w._watch(proc, log, poll_s=0.2) is False
    assert proc.returncode == 0
