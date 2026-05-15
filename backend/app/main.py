"""
FastAPI backend for TESS vetting app.

Key implementation notes:
  * ``parse_upload`` expects a *filesystem path*, not a file object. Every
    upload endpoint writes the uploaded file to a temp path first.
  * Detection sensitivity (``detect_threshold``, ``detect_min_snr``) is
    accepted on all analyze/report endpoints, defaulted to the safe values
    from the pipeline (0.997 and 4.0).
  * Errors return useful messages including the exception type, and the
    full Python traceback is logged to stderr so it shows in Render logs.
"""

from __future__ import annotations

import logging
import os
import pathlib
import tempfile
import traceback
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


log = logging.getLogger("vetting")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Vetstar: TESS Vetting Studio")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------------------------------------------------
# Sensitivity helpers
# -------------------------------------------------

def _clamp_params(detect_threshold: float, detect_min_snr: float):
    """Constrain the sliders to safe ranges."""
    th = max(0.95, min(0.999, float(detect_threshold)))
    snr = max(1.0, min(20.0, float(detect_min_snr)))
    return th, snr


def _run_pipeline(parsed: dict, detect_threshold: float, detect_min_snr: float):
    """Call ``run_full_vetting`` with keyword arguments (the signature is
    keyword-only) and the user's sensitivity settings.

    ``parsed`` comes from ``parsers.parse_upload`` and uses lowercase keys.
    """
    th, snr = _clamp_params(detect_threshold, detect_min_snr)
    return run_full_vetting(
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


def _save_upload_to_tempfile(upload: UploadFile) -> str:
    """Persist an uploaded file to a temp path on disk and return that
    path. ``parsers.parse_upload`` needs a path, not a file object."""
    if not upload.filename:
        raise HTTPException(status_code=400, detail="No filename provided")
    suffix = os.path.splitext(upload.filename)[1].lower() or ".bin"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(upload.file.read())
        tmp.flush()
    finally:
        tmp.close()
    return tmp.name


def _handle_exception(label: str, e: Exception) -> HTTPException:
    """Log the full traceback to stderr (visible in Render) and return an
    HTTPException whose detail names the exception type — much more useful
    than the bare ``str(e)`` we had before."""
    tb = traceback.format_exc()
    log.error("%s crashed:\n%s", label, tb)
    return HTTPException(
        status_code=500,
        detail=f"{label}: {type(e).__name__}: {e}",
    )


# -------------------------------------------------
# Health
# -------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


# -------------------------------------------------
# Upload endpoints
# -------------------------------------------------

@app.post("/api/analyze")
async def analyze(
    file: UploadFile = File(...),
    detect_threshold: float = 0.997,
    detect_min_snr: float = 4.0,
):
    tmp_path = None
    try:
        tmp_path = _save_upload_to_tempfile(file)
        parsed = parse_upload(tmp_path, file.filename)
        if parsed.get("metadata_only"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "This ExoFOP file contains only target metadata (no time "
                    "series). Upload a FITS light curve instead."
                ),
            )
        result = _run_pipeline(parsed, detect_threshold, detect_min_snr)
        return result.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise _handle_exception("analyze", e)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


@app.post("/api/report")
async def report(
    file: UploadFile = File(...),
    detect_threshold: float = 0.997,
    detect_min_snr: float = 4.0,
):
    tmp_path = None
    try:
        tmp_path = _save_upload_to_tempfile(file)
        parsed = parse_upload(tmp_path, file.filename)
        if parsed.get("metadata_only"):
            raise HTTPException(
                status_code=400,
                detail="ExoFOP metadata-only file. Upload a FITS light curve.",
            )
        result = _run_pipeline(parsed, detect_threshold, detect_min_snr)
        pdf = build_pdf(result)
        tic = result.star.tic_id or uuid.uuid4().hex[:8]
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="vetting_TIC{tic}.pdf"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _handle_exception("report", e)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# -------------------------------------------------
# MAST endpoints
# -------------------------------------------------

class MastQuery(BaseModel):
    tic_id: int
    sector: int
    detect_threshold: float = 0.997
    detect_min_snr: float = 4.0


@app.get("/api/mast/sectors/{tic_id}")
async def mast_sectors(tic_id: int):
    """Return ``{"tic_id": ..., "sectors": [{"sector": N, "providers": [...]}, ...]}``."""
    try:
        sectors = list_available_sectors(tic_id)
        return {"tic_id": tic_id, "sectors": sectors}
    except Exception as e:
        raise _handle_exception("mast_sectors", e)


def _mast_fetch_and_analyze(query: MastQuery):
    """Shared body for /api/mast/analyze and /api/mast/report."""
    # 1. Fetch from MAST.
    try:
        info = fetch_spoc_lightcurve(query.tic_id, query.sector)
    except RuntimeError as e:
        # Expected: MAST returned nothing useful. Clean 502 with the helpful
        # message that fetch_spoc_lightcurve assembled.
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise _handle_exception("mast_fetch", e)

    # 2. Parse the FITS file.
    try:
        parsed = parse_upload(info["path"], info["filename"])
    except Exception as e:
        raise _handle_exception(
            f"parse downloaded FITS ({os.path.basename(info.get('path', ''))})", e
        )

    # 3. Run the pipeline.
    try:
        result = _run_pipeline(parsed, query.detect_threshold, query.detect_min_snr)
    except Exception as e:
        raise _handle_exception("pipeline", e)

    return result, info


@app.post("/api/mast/analyze")
async def mast_analyze(query: MastQuery):
    result, info = _mast_fetch_and_analyze(query)
    out = result.to_dict()
    out["mast"] = {
        "filename": os.path.basename(info.get("path", "")),
        "obs_id": info.get("obs_id"),
        "matched_observations": info.get("matched"),
        "author": info.get("author"),
        "exptime": info.get("exptime"),
        "fallback": info.get("fallback", False),
        "tried": info.get("tried", []),
    }
    return out


@app.post("/api/mast/report")
async def mast_report(query: MastQuery):
    result, info = _mast_fetch_and_analyze(query)
    try:
        pdf = build_pdf(result)
    except Exception as e:
        raise _handle_exception("build_pdf", e)
    fname = f"vetting_TIC{query.tic_id}_S{query.sector:03d}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# -------------------------------------------------
# Static frontend mount
# -------------------------------------------------

HERE = pathlib.Path(__file__).resolve().parent
_CANDIDATES = [
    HERE.parent.parent / "frontend" / "dist",      # source layout
    HERE.parent / "frontend" / "dist",
    pathlib.Path(os.environ.get("FRONTEND_DIST", "")),
]
DIST = next((p for p in _CANDIDATES if p and p.is_dir()), None)

if DIST is not None:
    # Mount under /assets for the JS/CSS bundles; SPA fallback for paths.
    app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")

    @app.get("/")
    def _index():
        return FileResponse(str(DIST / "index.html"))

    @app.get("/{full_path:path}")
    def _spa(full_path: str):
        target = DIST / full_path
        if target.is_file():
            return FileResponse(str(target))
        return FileResponse(str(DIST / "index.html"))
else:
    @app.get("/")
    def _no_frontend():
        return {
            "status": "API running",
            "message": "Frontend bundle not found. Build with: cd frontend && npm run build",
        }
