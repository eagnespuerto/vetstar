"""NEMESIS proxy router: behaviour with and without a worker configured."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_status_disabled_without_worker(monkeypatch):
    monkeypatch.delenv("NEMESIS_WORKER_URL", raising=False)
    assert client.get("/api/nemesis/status").json() == {"enabled": False}
    r = client.post("/api/nemesis/jobs", json={"tic": 1, "sector": 1})
    assert r.status_code == 503


def test_proxies_to_worker(monkeypatch):
    seen = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            seen["token"] = self.headers.get("X-Worker-Token")
            if self.path == "/health":
                return self._send({"ok": True, "nemesis_version": "2.0"})
            self._send({"detail": "unknown job"}, 404)

        def do_POST(self):
            n = int(self.headers["Content-Length"])
            seen["body"] = json.loads(self.rfile.read(n))
            self._send({"job_id": "abc123", "position": 1})

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("NEMESIS_WORKER_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("NEMESIS_WORKER_TOKEN", "sekrit")
    try:
        st = client.get("/api/nemesis/status").json()
        assert st["online"] and st["nemesis_version"] == "2.0"
        assert seen["token"] == "sekrit"
        r = client.post("/api/nemesis/jobs", json={"tic": 5, "sector": 7})
        assert r.json()["job_id"] == "abc123"
        assert seen["body"] == {"tic": 5, "sector": 7, "cadence": "30 minute"}
        assert client.get("/api/nemesis/jobs/zzz").status_code == 404
    finally:
        srv.shutdown()


def _leo(disposition, failed=()):
    keys = [("weak", "FA"), ("odd_even", "FP"), ("vshaped", "FP"),
            ("secondary", "FP"), ("offset", "FP")]
    return {"available": True, "disposition": disposition,
            "tests": [{"key": k, "group": g, "passed": k not in failed} for k, g in keys]}


def test_leo_to_verdict():
    from backend.app.routers.nemesis import leo_to_vetting_verdict as f
    assert f({"available": False}) is None
    assert f(_leo("PC")) == {"category": "planet_candidate", "flags": []}
    assert f(_leo("FA", ["weak"]))["category"] == "ambiguous"
    v = f(_leo("FP", ["odd_even", "secondary"]))
    assert v["category"] == "eclipsing_binary_candidate"
    assert v["flags"] == ["odd_even_mismatch", "secondary_eclipse_detected"]
    assert f(_leo("FP", ["offset", "odd_even"]))["category"] == "false_positive_blend"
    assert f(_leo("FP", ["vshaped"])) == {"category": "ambiguous", "flags": []}
