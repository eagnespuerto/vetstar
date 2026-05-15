"""
FastAPI backend for TESS vetting app.
"""

from __future__ import annotations

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


app = FastAPI()


# -------------------------------------------------
# CORS
# -------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
# Helper: run pipeline safely
# -------------------------------------------------

def run_pipeline_from_data(data):
    """Handles both object-like, dict-like, and uppercase FITS fields."""

    def get(*names):
        for name in names:
            # attribute access
            if hasattr(data, name):
                return getattr(data, name)

            # dict access
            if isinstance(data, dict) and name in data:
                return data[name]

        raise RuntimeError(f"Missing field: {names}")

    # spport lowercase + uppercase keys
    return run_full_vetting(
        get("time", "TIME"),
        get("flux", "FLUX"),
        get("flux_err", "FLUX_ERR"),
        get("quality", "QUALITY"),
        get("mom_x", "MOM_CENTR1", "centroid_col"),
        get("mom_y", "MOM_CENTR2", "centroid_row"),
        get("star", "STAR"),
    )


# -------------------------------------------------
# Upload endpoints
# -------------------------------------------------

@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...)):
    try:
        data = parse_upload(file.file, filename=file.filename)
        result = run_pipeline_from_data(data)
        return result.to_dict()

    except Exception as e:
        raise HTTPException(500, f"Analyze failed: {str(e)}")


@app.post("/api/report")
async def report(file: UploadFile = File(...)):
    try:
        data = parse_upload(file.file, filename=file.filename)
        result = run_pipeline_from_data(data)

        pdf = build_pdf(result)

        fname = f"vetting_{uuid.uuid4().hex[:6]}.pdf"

        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    except Exception as e:
        raise HTTPException(500, f"Report failed: {str(e)}")


# -------------------------------------------------
# MAST endpoints
# -------------------------------------------------

class MastQuery(BaseModel):
    tic_id: int
    sector: int


@app.get("/api/mast/sectors/")
async def mast_sectors(tic_id: int):
    try:
        sectors = list_available_sectors(tic_id)
        return {"tic_id": tic_id, "sectors": sectors}

    except Exception as e:
        raise HTTPException(500, f"MAST sectors failed: {str(e)}")


@app.post("/api/mast/analyze")
async def mast_analyze(query: MastQuery):
    try:
        info = fetch_spoc_lightcurve(query.tic_id, query.sector)

        file_path = info["filename"]

        with open(file_path, "rb") as f:
            data = parse_upload(f, filename=file_path)

        result = run_pipeline_from_data(data)

        out = result.to_dict()
        out["mast"] = info

        return out

    except Exception as e:
        raise HTTPException(500, f"MAST analyze failed: {str(e)}")


@app.post("/api/mast/report")
async def mast_report(query: MastQuery):
    try:
        info = fetch_spoc_lightcurve(query.tic_id, query.sector)

        file_path = info["filename"]

        with open(file_path, "rb") as f:
            data = parse_upload(f, filename=file_path)

        result = run_pipeline_from_data(data)

        pdf = build_pdf(result)

        fname = f"vetting_TIC{query.tic_id}_S{query.sector}.pdf"

        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    except Exception as e:
        raise HTTPException(500, f"MAST report failed: {str(e)}")


# -------------------------------------------------
# Static frontend
# -------------------------------------------------

HERE = pathlib.Path(__file__).resolve().parent
DIST = HERE.parent.parent / "frontend" / "dist"

if DIST.exists():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="frontend")
else:
    @app.get("/")
    def no_frontend():
        return {
            "status": "API running",
            "message": "Frontend not built. Run: cd frontend && npm run build"
        }
