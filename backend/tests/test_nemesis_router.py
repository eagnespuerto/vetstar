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


def test_report_pdf_from_worker_result(monkeypatch):
    """The site builds a Vetstar PDF from the worker's result JSON."""
    import base64

    from backend.app.routers import nemesis as nm

    png = base64.b64encode(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15"
        b"\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    ).decode()
    params = {"method": "TLS", "period_d": 3.4476, "t0_btjd": 1845.508, "duration_hr": 1.66,
              "depth_ppt": 0.77, "sde": 15.3, "radius_earth": 1.9, "n_transits": 6.0}
    result = {
        "primary": "TLS",
        "search": {"TLS": {"params": params, "leo": {"available": True, "disposition": "PC", "tests": [
            {"key": "weak", "title": "Signal strength", "group": "FA", "passed": True,
             "meaning": "The dip is too weak.", "detail": "MES 30"}]}}},
        "figures": {k: png for k in ("light_curve", "periodogram", "fold", "odd_even")},
        "star": {"tic_id": 103633434, "ra": 152.2, "dec": 69.3, "teff": 3912.0},
        "meta": {"tic": 103633434, "sector": 20, "cadence": "30 minute", "nemesis_version": "2.0",
                 "runtime_s": 116.2, "lite": False},
    }
    monkeypatch.setenv("NEMESIS_WORKER_URL", "http://worker.invalid")
    monkeypatch.setattr(nm, "_call", lambda method, path, body=None, raw=False: result)
    r = client.post("/api/nemesis/jobs/abc123/report",
                    json={"include_ffi": False, "author": "A & <B>", "initials": "ab"})
    assert r.status_code == 200
    assert r.content[:5] == b"%PDF-"
    assert 'vetstar_nemesis_TIC103633434_S020.pdf' in r.headers["content-disposition"]


def test_credit_line():
    from vetstar_core.nemesis_report import credit_line
    assert credit_line("E. A. Puerto", "EA") == "E. A. Puerto (ea)"
    assert credit_line("E. A. Puerto", None) == "E. A. Puerto"
    assert credit_line("", "ea") == "(ea)"
    assert credit_line(None, None) == ""
