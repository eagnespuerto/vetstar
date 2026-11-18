"""NEMESIS worker: a small job server that vetstar's API calls.

POST /jobs                 queue a NEMESIS run, returns {"job_id", "position"}
GET  /jobs/{id}            status, stage, queue position, log tail
GET  /jobs/{id}/result     JSON summary (light curve, periodogram, fold, LEO-Vetter)
GET  /jobs/{id}/files      report files NEMESIS wrote (PDF/PNG)
GET  /jobs/{id}/files/{n}  download one report file
GET  /health               {"ok": true, "nemesis_version": ...}

One job runs at a time (NEMESIS is CPU- and memory-heavy). Each job runs in a
child process so a crash or OOM kill cannot take the server down. Set
WORKER_TOKEN to require an ``X-Worker-Token`` header on every request.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

JOBS_DIR = Path(os.environ.get("NEMESIS_JOBS_DIR", "/tmp/nemesis_jobs"))
JOB_TTL_S = float(os.environ.get("NEMESIS_JOB_TTL_HOURS", "24")) * 3600
JOB_TIMEOUT_S = float(os.environ.get("NEMESIS_JOB_TIMEOUT_MIN", "40")) * 60
MAX_QUEUE = int(os.environ.get("NEMESIS_MAX_QUEUE", "10"))
TOKEN = os.environ.get("WORKER_TOKEN", "")

# Log markers -> (stage key, human label). Later markers win.
STAGES = [
    ("[vetstar] NEMESIS", "download", "Downloading TESS full-frame-image pixels"),
    ("Beginning Transit Search", "bls", "Searching for transits (BLS)"),
    ("doing TLS search", "tls", "Refining with Transit Least Squares"),
    ("[vetstar] summarising", "report", "Running LEO-Vetter and building the report"),
    ("[vetstar] done", "done", "Finished"),
]

app = FastAPI(title="vetstar NEMESIS worker")
_queue: deque[str] = deque()
_lock = threading.Lock()
_running: Optional[str] = None
_wake = threading.Event()


def _auth(x_worker_token: str = Header(default="")):
    if TOKEN and x_worker_token != TOKEN:
        raise HTTPException(401, "bad worker token")


class JobRequest(BaseModel):
    tic: int = Field(gt=0)
    sector: int = Field(ge=1, le=200)
    cadence: str = "30 minute"
    minP: Optional[float] = Field(default=None, gt=0)
    maxP: Optional[float] = Field(default=None, gt=0)
    N_transits: Optional[int] = Field(default=None, ge=1, le=10)
    do_TLS_Search: Optional[bool] = None
    SDE_threshold: Optional[float] = Field(default=None, ge=0)


def _job_dir(job_id: str) -> Path:
    d = JOBS_DIR / job_id
    if not job_id.isalnum() or not d.is_dir():
        raise HTTPException(404, "unknown job")
    return d


def _stage(log_text: str) -> tuple[str, str]:
    key, label = "queued", "Waiting in the queue"
    for marker, k, lab in STAGES:
        if marker in log_text:
            key, label = k, lab
    return key, label


def _status(job_id: str) -> dict:
    d = _job_dir(job_id)
    log = (d / "log.txt").read_text(errors="replace") if (d / "log.txt").exists() else ""
    if (d / "result.json").exists():
        state = "done"
    elif (d / "error.json").exists():
        state = "error"
    elif job_id == _running:
        state = "running"
    else:
        state = "queued"
    stage, label = _stage(log)
    out = {"job_id": job_id, "state": state, "stage": stage, "stage_label": label,
           "log_tail": log.splitlines()[-15:]}
    if state == "queued":
        with _lock:
            out["position"] = list(_queue).index(job_id) + 1 if job_id in _queue else None
    if state == "error":
        out.update(json.loads((d / "error.json").read_text()))
    return out


def _run(job_id: str) -> None:
    d = JOBS_DIR / job_id
    with open(d / "log.txt", "ab") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "worker.run_job", str(d)],
            stdout=log, stderr=subprocess.STDOUT,
            cwd=str(Path(__file__).resolve().parent.parent),
            env={**os.environ, "MPLBACKEND": "Agg", "PYTHONUNBUFFERED": "1"},
        )
        try:
            proc.wait(timeout=JOB_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    if not (d / "result.json").exists() and not (d / "error.json").exists():
        msg = ("Timed out" if proc.returncode is None or proc.returncode < 0
               else f"Worker process exited with code {proc.returncode}")
        if proc.returncode in (-9, 137):
            msg = "The worker ran out of memory on this star. Try the 30-minute cadence."
        (d / "error.json").write_text(json.dumps({"error": msg}))


def _cleanup() -> None:
    now = time.time()
    for d in JOBS_DIR.glob("*"):
        if d.is_dir() and now - d.stat().st_mtime > JOB_TTL_S and d.name != _running:
            shutil.rmtree(d, ignore_errors=True)


def _loop() -> None:
    global _running
    while True:
        _wake.wait(timeout=600)
        _wake.clear()
        _cleanup()
        while True:
            with _lock:
                if not _queue:
                    break
                _running = _queue.popleft()
            try:
                _run(_running)
            finally:
                _running = None


@app.on_event("startup")
def _start() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=_loop, daemon=True).start()


@app.get("/health")
def health():
    try:
        from importlib.metadata import version
        v = version("nemesis-tess")
    except Exception:
        v = None
    with _lock:
        depth = len(_queue) + (1 if _running else 0)
    return {"ok": True, "nemesis_version": v, "queue_depth": depth}


@app.get("/", response_class=HTMLResponse)
def index():
    """Human-readable status page, so the Space's App tab is not a bare 404."""
    h = health()
    up = "running" if h["ok"] else "down"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>vetstar NEMESIS worker</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:40rem;margin:3rem auto;padding:0 1rem;
color:#454545;background:#FFF5E9}}h1{{color:#453C67}}code{{background:#FFE6C7;padding:0 .3em;border-radius:4px}}
.ok{{color:#00864F;font-weight:600}}</style></head><body>
<h1>vetstar NEMESIS worker</h1>
<p>Status: <span class="ok">{up}</span> &middot; NEMESIS {h['nemesis_version'] or 'unknown'}
&middot; jobs in queue: {h['queue_depth']}</p>
<p>This is the back end that runs <a href="https://github.com/daxfeliz/NEMESIS">NEMESIS</a>
(Feliz et al. 2021; NEMESIS II, Feliz et al. 2026) with LEO-Vetter for
<a href="https://github.com/eagnespuerto/vetstar">vetstar</a>. It has no web interface of its own:
use vetstar's guided NEMESIS mode to search a star.</p>
<p>API: <code>GET /health</code>, <code>POST /jobs</code>, <code>GET /jobs/{{id}}</code>,
<code>GET /jobs/{{id}}/result</code>.</p>
</body></html>"""


@app.post("/jobs", dependencies=[Depends(_auth)])
def create_job(req: JobRequest):
    if req.minP and req.maxP and req.minP >= req.maxP:
        raise HTTPException(422, "minP must be below maxP")
    with _lock:
        if len(_queue) >= MAX_QUEUE:
            raise HTTPException(503, "The NEMESIS queue is full. Please try again later.")
        job_id = uuid.uuid4().hex
        d = JOBS_DIR / job_id
        d.mkdir(parents=True)
        (d / "request.json").write_text(req.model_dump_json())
        _queue.append(job_id)
        position = len(_queue) + (1 if _running else 0)
    _wake.set()
    return {"job_id": job_id, "position": position}


@app.get("/jobs/{job_id}", dependencies=[Depends(_auth)])
def job_status(job_id: str):
    return _status(job_id)


@app.get("/jobs/{job_id}/result", dependencies=[Depends(_auth)])
def job_result(job_id: str):
    d = _job_dir(job_id)
    if not (d / "result.json").exists():
        raise HTTPException(409, "result not ready")
    return json.loads((d / "result.json").read_text())


def _report_files(d: Path) -> list[Path]:
    out = d / "out"
    files = [p for p in out.rglob("*") if p.suffix.lower() in (".pdf", ".png")
             and "cache" not in p.parts]
    return sorted(files, key=lambda p: p.name)


@app.get("/jobs/{job_id}/files", dependencies=[Depends(_auth)])
def job_files(job_id: str):
    d = _job_dir(job_id)
    return [{"name": p.name, "bytes": p.stat().st_size} for p in _report_files(d)]


@app.get("/jobs/{job_id}/files/{name}", dependencies=[Depends(_auth)])
def job_file(job_id: str, name: str):
    d = _job_dir(job_id)
    for p in _report_files(d):
        if p.name == name:
            return FileResponse(str(p), filename=p.name)
    raise HTTPException(404, "no such file")
