from __future__ import annotations

"""
FastAPI server.

Endpoints:
  POST /api/analyze              — upload a FITS or JSON file, get full vetting JSON
  POST /api/report               — same upload, get PDF report as application/pdf
  GET  /api/mast/sectors/{tic}   — list SPOC sectors available for a TIC
  POST /api/mast/analyze         — fetch FITS from MAST by {tic_id, sector}, then analyze
  POST /api/mast/report          — fetch + analyze + return PDF
  GET  /api/health               — liveness check

The built frontend (frontend/dist) is mounted at /, so `python app.py` from
the project root serves the whole app on a single port.
"""
"""
FastAPI backend for TESS vetting app.
"""


import os
import pathlib
import uuid

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .mast_fetch import fetch_spoc_lightcurve, list_available_sectors
from .parsers import parse_upload
from .pipeline import run_full_vetting
from .report import build_pdf


app = FastAPI(
    title="TESS Vetting App",
    version="1.0.0",
)


# -------------------------------------------------
# CORS
# -------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------------------------------------------------
# Health
# -------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


# -------------------------------------------------
# Helpers
# -------------------------------------------------

def _detect_params(threshold: float, snr: float):
    threshold = max(0.95, min(0.999, float(threshold)))
    snr = max(1.0, min(20.0, float(snr)))
    return threshold, snr


def _process_upload(file: UploadFile, threshold: float, snr: float):
    data = parse_upload(file)
    result = run_full_vetting(data, threshold, snr)
    return result


# -------------------------------------------------
# Upload endpoints
# -------------------------------------------------

@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...),
                  detect_threshold: float = 0.997,
                  detect_min_snr: float = 4.0):

    th, snr = _detect_params(detect_threshold, detect_min_snr)
    result = _process_upload(file, th, snr)

    return result.to_dict()


@app.post("/api/report")
async def report(file: UploadFile = File(...),
                 detect_threshold: float = 0.997,
                 detect_min_snr: float = 4.0):

    th, snr = _detect_params(detect_threshold, detect_min_snr)
    result = _process_upload(file, th, snr)

    pdf_bytes = build_pdf(result)

    fname = f"vetting_TIC{result.star.tic_id or uuid.uuid4().hex[:8]}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# -------------------------------------------------
# MAST endpoints
# -------------------------------------------------

class MastQuery(BaseModel):
    tic_id: int
    sector: int
    detect_threshold: float = 0.997
    detect_min_snr: float = 4.0


def _fetch_and_analyze(tic_id: int, sector: int,
                       threshold: float, snr: float):

    info = fetch_spoc_lightcurve(tic_id, sector)

    file_path = info["filename"]

    data = parse_upload(file_path)
    result = run_full_vetting(data, threshold, snr)

    return result, info


@app.get("/api/mast/sectors/")
async def mast_sectors(tic_id: int):
    try:
        sectors = list_available_sectors(tic_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MAST query failed: {e}")

    return {"tic_id": tic_id, "sectors": sectors}


@app.post("/api/mast/analyze")
async def mast_analyze(query: MastQuery):

    th, snr = _detect_params(query.detect_threshold, query.detect_min_snr)

    result, info = _fetch_and_analyze(
        query.tic_id,
        query.sector,
        th,
        snr,
    )

    out = result.to_dict()
    out["mast"] = info

    return out


@app.post("/api/mast/report")
async def mast_report(query: MastQuery):

    th, snr = _detect_params(query.detect_threshold, query.detect_min_snr)

    result, info = _fetch_and_analyze(
        query.tic_id,
        query.sector,
        th,
        snr,
    )

    pdf_bytes = build_pdf(result)

    fname = f"vetting_TIC{query.tic_id}_S{query.sector}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# -------------------------------------------------
# Static frontend
# -------------------------------------------------

_HERE = pathlib.Path(__file__).resolve().parent

CANDIDATES = [
    _HERE.parent.parent / "frontend" / "dist",
    _HERE.parent / "static",
    pathlib.Path(os.environ.get("FRONTEND_DIST", "")),
]

DIST = next((p for p in CANDIDATES if p and p.is_dir()), None)

if DIST:
    app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")



