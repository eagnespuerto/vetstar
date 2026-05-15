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
from __future__ import annotations

import os
import pathlib
import tempfile
import uuid

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .mast_fetch import fetch_spoc_lightcurve, list_available_sectors
from .parsers import parse_upload
from .pipeline import run_full_vetting
from .report import build_pdf


app = FastAPI(
    title="TESS Vetting App",
    description="Upload a TESS/Kepler FITS light curve or an ExoFOP JSON dump "
    "and receive a full transit/eclipse vetting report.",
    version="1.0.0",
)

# CORS: in production set this to your real frontend origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


def _process_upload(file: UploadFile, detect_threshold: float = 0.997,
                    detect_min_snr: float = 4.0):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    suffix = os.path.splitext(file.filename)[1].lower() or ".bin"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(file.file.read())
        tmp.flush()
        tmp.close()
        parsed = parse_upload(tmp.name, file.filename)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse file: {e}")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    if parsed.get("metadata_only"):
        raise HTTPException(
            status_code=400,
            detail=(
                "This ExoFOP file contains only target metadata (no time series). "
                "Upload a FITS light curve or a JSON containing arrays 't', 'flux'."
            ),
        )

    result = run_full_vetting(
        t=parsed["t"],
        flux=parsed["flux"],
        flux_err=parsed["flux_err"],
        quality=parsed["quality"],
        mom_x=parsed["mom_x"],
        mom_y=parsed["mom_y"],
        star=parsed["star"],
        detect_threshold=detect_threshold,
        detect_min_snr=detect_min_snr,
    )
    return result


# Sensitivity params shared by all analyze endpoints. As query params so
# they work cleanly with multipart uploads.
def _detect_params(
    detect_threshold: float = 0.997,
    detect_min_snr: float = 4.0,
):
    # Clamp to safe ranges so the UI can't pass nonsense.
    detect_threshold = max(0.95, min(0.999, float(detect_threshold)))
    detect_min_snr = max(1.0, min(20.0, float(detect_min_snr)))
    return detect_threshold, detect_min_snr


@app.post("/api/analyze")
async def analyze(
    file: UploadFile = File(...),
    detect_threshold: float = 0.997,
    detect_min_snr: float = 4.0,
):
    th, snr = _detect_params(detect_threshold, detect_min_snr)
    result = _process_upload(file, th, snr)
    return result.to_dict()


@app.post("/api/report")
async def report(
    file: UploadFile = File(...),
    detect_threshold: float = 0.997,
    detect_min_snr: float = 4.0,
):
    th, snr = _detect_params(detect_threshold, detect_min_snr)
    result = _process_upload(file, th, snr)
    pdf_bytes = build_pdf(result)
    fname = f"vetting_TIC{result.star.tic_id or uuid.uuid4().hex[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ----------------------------------------------------------------------
# MAST fetch endpoints
# ----------------------------------------------------------------------
class MastQuery(BaseModel):
    tic_id: int
    sector: int
    detect_threshold: float = 0.997
    detect_min_snr: float = 4.0


def _fetch_and_analyze(tic_id: int, sector: int,
                       detect_threshold: float = 0.997,
                       detect_min_snr: float = 4.0):
    try:
        info = fetch_spoc_lightcurve(tic_id, sector)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    try:
        parsed = parse_upload(info["path"], info["filename"])
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Downloaded the FITS but failed to parse it: {e}",
        )

    th, snr = _detect_params(detect_threshold, detect_min_snr)
    result = run_full_vetting(
        t=parsed["t"],
        flux=parsed["flux"],
        flux_err=parsed["flux_err"],
        quality=parsed["quality"],
        mom_x=parsed["mom_x"],
        mom_y=parsed["mom_y"],
        star=parsed["star"],
        detect_threshold=th,
        detect_min_snr=snr,
    )
    return result, info


@app.get("/api/mast/sectors/{tic_id}")
async def mast_sectors(tic_id: int):
    """List SPOC sectors available at MAST for a given TIC."""
    try:
        sectors = list_available_sectors(tic_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MAST query failed: {e}")
    return {"tic_id": tic_id, "sectors": sectors}


@app.post("/api/mast/analyze")
async def mast_analyze(query: MastQuery):
    """Fetch SPOC light curve from MAST by TIC + sector, then run vetting."""
    result, info = _fetch_and_analyze(
        query.tic_id, query.sector,
        query.detect_threshold, query.detect_min_snr,
    )
    out = result.to_dict()
    out["mast"] = {
        "filename": info["filename"],
        "obs_id": info["obs_id"],
        "matched_observations": info["matched"],
        "author": info.get("author"),
        "exptime": info.get("exptime"),
        "fallback": info.get("fallback", False),
        "tried": info.get("tried", []),
    }
    return out


@app.post("/api/mast/report")
async def mast_report(query: MastQuery):
    """Fetch from MAST + analyze + return PDF."""
    result, info = _fetch_and_analyze(
        query.tic_id, query.sector,
        query.detect_threshold, query.detect_min_snr,
    )
    pdf_bytes = build_pdf(result)
    fname = f"vetting_TIC{query.tic_id}_S{query.sector:03d}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ----------------------------------------------------------------------
# Static SPA mount  (serves the built frontend at /)
# ----------------------------------------------------------------------
_HERE = pathlib.Path(__file__).resolve().parent
# Look for dist in a few standard places.
_CANDIDATES = [
    _HERE.parent.parent / "frontend" / "dist",         # dev tree
    _HERE.parent / "static",                            # bundled inside backend/static
    pathlib.Path(os.environ.get("FRONTEND_DIST", "")),  # explicit override
]
_DIST = next((p for p in _CANDIDATES if p and p.is_dir()), None)

if _DIST is not None:
    # Static assets (JS/CSS bundles)
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")

    @app.get("/")
    def _index():
        return FileResponse(str(_DIST / "index.html"))

    # SPA catch-all: anything not under /api or /assets returns index.html
    @app.get("/{full_path:path}")
    def _spa_catchall(full_path: str):
        target = _DIST / full_path
        if target.is_file():
            return FileResponse(str(target))
        return FileResponse(str(_DIST / "index.html"))
else:
    @app.get("/")
    def _no_frontend():
        return {
            "status": "API only",
            "message": (
                "Frontend not built. Run `cd frontend && npm install && npm run build` "
                "or set FRONTEND_DIST to a built dist directory."
            ),
        }

