"""
FastAPI backend for TESS vetting app.
"""

from __future__ import annotations

import logging
import os
import pathlib
import tempfile
import traceback
import uuid

import numpy as np

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from typing import Optional

from pydantic import BaseModel

from .mast_fetch import fetch_spoc_lightcurve, list_available_sectors
from .parsers import parse_upload
from .pipeline import (
    run_full_vetting,
    clean_lightcurve,
    measure_shape,
    centroid_check,
)
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
# Module-level LC cache for ExoMiner reuse
# Keyed by (tic_id, sector) or "__last__".
# Process-local — fine for single-worker Render deployment.
# -------------------------------------------------
_lc_cache: dict = {}


def _cache_lc(parsed: dict) -> None:
    """Store cleaned LC arrays after a successful parse so ExoMiner can
    reuse them without re-downloading."""
    try:
        t_c, f_c, _ = clean_lightcurve(
            parsed["t"], parsed["flux"], parsed["flux_err"], parsed["quality"]
        )
        # Mask moments with the SAME finite/quality filter clean_lightcurve
        # applies, so centroid arrays line up 1:1 with t_c/f_c (required by
        # centroid_check, which needs len(mom_x) == len(t)).
        mom_x = parsed.get("mom_x")
        mom_y = parsed.get("mom_y")
        quality = parsed.get("quality")
        if mom_x is not None and quality is not None:
            m = (
                np.isfinite(parsed["t"])
                & np.isfinite(parsed["flux"])
                & (parsed["flux"] > 0)
                & (quality == 0)
            )
            mom_x = mom_x[m]
            mom_y = mom_y[m] if mom_y is not None else None
        star = parsed.get("star")
        tic_id = getattr(star, "tic_id", None)
        sector = getattr(star, "sector", None)
        entry = {
            "t": t_c,
            "f": f_c,
            "mom_x": mom_x,
            "mom_y": mom_y,
        }
        _lc_cache[(tic_id, sector)] = entry
        _lc_cache["__last__"] = entry
    except Exception as exc:
        log.warning("LC cache update failed: %s", exc)


# -------------------------------------------------
# Sensitivity helpers
# -------------------------------------------------

def _clamp_params(detect_threshold: float, detect_min_snr: float):
    th = max(0.95, min(0.999, float(detect_threshold)))
    snr = max(1.0, min(20.0, float(detect_min_snr)))
    return th, snr


def _run_pipeline(parsed: dict, detect_threshold: float, detect_min_snr: float):
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
        _cache_lc(parsed)
        result = _run_pipeline(parsed, detect_threshold, detect_min_snr)
        d = result.to_dict()
        d["lightcurve"] = _downsample_cached_lc(result.star.tic_id, result.star.sector)
        return d
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
    try:
        sectors = list_available_sectors(tic_id)
        return {"tic_id": tic_id, "sectors": sectors}
    except Exception as e:
        raise _handle_exception("mast_sectors", e)


def _mast_fetch_and_analyze(query: MastQuery):
    try:
        info = fetch_spoc_lightcurve(query.tic_id, query.sector)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise _handle_exception("mast_fetch", e)

    try:
        parsed = parse_upload(info["path"], info["filename"])
    except Exception as e:
        raise _handle_exception(
            f"parse downloaded FITS ({os.path.basename(info.get('path', ''))})", e
        )

    _cache_lc(parsed)

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
    out["lightcurve"] = _downsample_cached_lc(result.star.tic_id, result.star.sector)
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
# Habitability + multi-sector endpoints
# -------------------------------------------------

from .habitability import PlanetCandidate, compute_hci
from .exofop import query_exofop
from .pipeline import run_multisector_analysis


class HabitabilityQuery(BaseModel):
    tic_id: int
    radius_earth: Optional[float] = None
    semi_major_axis_au: Optional[float] = None
    orbital_period_d: Optional[float] = None
    stellar_teff: Optional[float] = None
    stellar_radius_sun: Optional[float] = None
    stellar_mass_sun: Optional[float] = None
    R_companion_Rjup: Optional[float] = None
    n_sectors_with_detections: int = 1
    n_sectors_observed: int = 1
    vetting_verdict: Optional[dict] = None


class MultisectorQuery(BaseModel):
    tic_id: int
    sectors: Optional[list] = None
    detect_threshold: float = 0.997
    detect_min_snr: float = 4.0


@app.post("/api/habitability")
async def habitability(query: HabitabilityQuery):
    try:
        exofop = query_exofop(query.tic_id)
    except Exception as e:
        log.warning("ExoFOP query failed for TIC %s: %s", query.tic_id, e)
        exofop = {"star": {}, "tois": [], "source": "unavailable"}

    star = exofop.get("star", {})
    tois = exofop.get("tois", [])

    best_toi = None
    for t in tois:
        d = (t.get("disposition") or "").upper()
        if d in ("PC", "APC", "CP", "KP") or best_toi is None:
            best_toi = t
            if d in ("CP", "KP"):
                break

    RJUP_TO_REARTH = 11.209
    RSUN_TO_RJUP = 9.73

    radius_earth = query.radius_earth
    radius_source = "override"

    if radius_earth is None and best_toi:
        radius_earth = best_toi.get("radius_earth")
        if radius_earth is not None:
            radius_source = "exofop"

    if radius_earth is None and query.R_companion_Rjup is not None:
        radius_earth = query.R_companion_Rjup * RJUP_TO_REARTH
        radius_source = "pipeline (R_companion_Rjup)"

    if radius_earth is None and query.vetting_verdict:
        import math
        depth = None
        events = query.vetting_verdict.get("_events", [])
        if not depth:
            depth = query.vetting_verdict.get("_depth")
        bls_depth = query.vetting_verdict.get("_bls_depth")
        if bls_depth and not depth:
            depth = bls_depth

        stellar_r = (
            query.stellar_radius_sun
            or star.get("radius")
        )
        if depth and depth > 0 and stellar_r and stellar_r > 0:
            ratio = math.sqrt(min(depth, 0.99))
            r_comp_rjup = ratio * stellar_r * RSUN_TO_RJUP
            radius_earth = r_comp_rjup * RJUP_TO_REARTH
            radius_source = f"derived (depth={depth:.5f}, R*={stellar_r:.2f} R_sun)"

    planet = PlanetCandidate(
        radius_earth=radius_earth,
        semi_major_axis_au=(
            query.semi_major_axis_au
            or (best_toi.get("semi_major_axis_au") if best_toi else None)
        ),
        orbital_period_d=(
            query.orbital_period_d
            or (best_toi.get("period_d") if best_toi else None)
        ),
        toi_number=best_toi.get("toi_number") if best_toi else None,
        disposition=best_toi.get("disposition") if best_toi else None,
        stellar_teff=(
            query.stellar_teff
            or star.get("teff")
        ),
        stellar_radius_sun=(
            query.stellar_radius_sun
            or star.get("radius")
        ),
        stellar_mass_sun=(
            query.stellar_mass_sun
            or star.get("mass")
        ),
        depth_ppm=best_toi.get("depth_ppm") if best_toi else None,
        duration_hr=best_toi.get("duration_hr") if best_toi else None,
        source=exofop.get("source", "unknown"),
    )

    hci_result = compute_hci(
        planet=planet,
        vetting_verdict=query.vetting_verdict,
        n_sectors_with_detections=query.n_sectors_with_detections,
        n_sectors_observed=query.n_sectors_observed,
    )

    return {
        "hci": hci_result.to_dict(),
        "planet": {
            "radius_earth": planet.radius_earth,
            "radius_source": radius_source,
            "semi_major_axis_au": planet.semi_major_axis_au,
            "orbital_period_d": planet.orbital_period_d,
            "toi_number": planet.toi_number,
            "disposition": planet.disposition,
            "stellar_teff": planet.stellar_teff,
            "stellar_radius_sun": planet.stellar_radius_sun,
            "stellar_mass_sun": planet.stellar_mass_sun,
        },
        "exofop_source": exofop.get("source"),
        "all_tois": tois,
    }


@app.post("/api/mast/multisector")
async def mast_multisector(query: MultisectorQuery):
    from .mast_fetch import list_available_sectors, fetch_spoc_lightcurve

    try:
        all_sectors = list_available_sectors(query.tic_id)
    except Exception as e:
        raise HTTPException(502, f"MAST sector list failed: {e}")

    if not all_sectors:
        raise HTTPException(404, f"No TESS sectors found for TIC {query.tic_id}.")

    if query.sectors:
        wanted = set(int(s) for s in query.sectors)
        sectors_to_fetch = [s for s in all_sectors if s["sector"] in wanted]
    else:
        sectors_to_fetch = all_sectors[:10]

    sector_results = []
    errors = []

    for sec_info in sectors_to_fetch:
        sec_num = sec_info["sector"]
        try:
            info = fetch_spoc_lightcurve(query.tic_id, sec_num)
            parsed = parse_upload(info["path"], info["filename"])
            result = _run_pipeline(parsed, query.detect_threshold, query.detect_min_snr)
            sector_results.append((sec_num, result))
        except Exception as e:
            log.warning("Sector %s failed for TIC %s: %s", sec_num, query.tic_id, e)
            errors.append({"sector": sec_num, "error": str(e)})

    if not sector_results:
        raise HTTPException(502, f"All sector fetches failed. Errors: {errors}")

    analysis = run_multisector_analysis(
        sector_results,
        detect_threshold=query.detect_threshold,
        detect_min_snr=query.detect_min_snr,
    )
    analysis["errors"] = errors
    analysis["sectors_attempted"] = len(sectors_to_fetch)
    analysis["sectors_succeeded"] = len(sector_results)

    analysis["sector_verdicts"] = [
        {
            "sector": sec,
            "verdict": res.verdict.get("headline"),
            "category": res.verdict.get("category"),
            "n_events": len(res.events),
            "bls_period_d": res.bls.get("period"),
            "bls_sde": res.bls.get("sde"),
        }
        for sec, res in sector_results
    ]

    return analysis


# -------------------------------------------------
# ExoMiner endpoint
# -------------------------------------------------

from .exominer import run_exominer as _run_exominer


class ExominerRequest(BaseModel):
    tic_id: Optional[int] = None
    sector: Optional[int] = None
    period: float
    t0: float
    duration: float
    crowdsap: Optional[float] = None


@app.post("/api/exominer")
def api_exominer(req: ExominerRequest):
    key = (req.tic_id, req.sector) if req.tic_id else "__last__"
    cached = _lc_cache.get(key) or _lc_cache.get("__last__")
    if not cached:
        raise HTTPException(
            status_code=400,
            detail=(
                "No light curve in cache. Run /api/analyze or "
                "/api/mast/analyze first, then call this endpoint."
            ),
        )
    try:
        return _run_exominer(
            t=cached["t"],
            f=cached["f"],
            mom_x=cached.get("mom_x"),
            mom_y=cached.get("mom_y"),
            period=req.period,
            t0=req.t0,
            duration=req.duration,
            crowdsap=req.crowdsap,
        )
    except Exception as e:
        raise _handle_exception("exominer", e)


# -------------------------------------------------
# Manual tiny-dip selector
# -------------------------------------------------

def _downsample_cached_lc(tic_id, sector, max_pts: int = 4000) -> Optional[dict]:
    """Return a transport-friendly {t, f} from the cached cleaned LC."""
    key = (tic_id, sector) if tic_id else "__last__"
    cached = _lc_cache.get(key) or _lc_cache.get("__last__")
    if not cached:
        return None
    t = np.asarray(cached["t"], dtype=float)
    f = np.asarray(cached["f"], dtype=float)
    if t.size == 0:
        return None
    step = max(1, int(np.ceil(t.size / max_pts)))
    return {"t": t[::step].tolist(), "f": f[::step].tolist()}


class ManualDipRequest(BaseModel):
    tic_id: Optional[int] = None
    sector: Optional[int] = None
    t_start: float
    t_end: float
    crowdsap: Optional[float] = None


@app.post("/api/manual_dip")
def api_manual_dip(req: ManualDipRequest):
    """Characterise a user-marked time window as a dip: depth, duration,
    shape (U/V) and — when centroid moments are cached — an on-target test."""
    if req.t_end <= req.t_start:
        raise HTTPException(status_code=400, detail="t_end must be greater than t_start.")

    key = (req.tic_id, req.sector) if req.tic_id else "__last__"
    cached = _lc_cache.get(key) or _lc_cache.get("__last__")
    if not cached:
        raise HTTPException(
            status_code=400,
            detail="No light curve in cache. Run an analysis first.",
        )

    t = np.asarray(cached["t"], dtype=float)
    f = np.asarray(cached["f"], dtype=float)

    in_mask = (t >= req.t_start) & (t <= req.t_end)
    n_in = int(in_mask.sum())
    if n_in < 3:
        raise HTTPException(
            status_code=400,
            detail=f"Only {n_in} points inside the selected window; pick a wider span.",
        )

    # Baseline from a symmetric out-of-transit pad on either side.
    pad = (req.t_end - req.t_start)
    oot_mask = (
        ((t >= req.t_start - pad) & (t < req.t_start))
        | ((t > req.t_end) & (t <= req.t_end + pad))
    )
    baseline = float(np.median(f[oot_mask])) if oot_mask.sum() >= 3 else 1.0
    min_flux = float(np.min(f[in_mask]))
    depth = baseline - min_flux

    out = {
        "t_start": req.t_start,
        "t_end": req.t_end,
        "depth": depth,
        "depth_pct": depth * 100.0,
        "duration_hr": (req.t_end - req.t_start) * 24.0,
        "n_points": n_in,
        "baseline": baseline,
    }

    # Shape (U vs V)
    out["shape"] = measure_shape(t, f, req.t_start, req.t_end)

    # Centroid (only if cached moments line up with t)
    mom_x = cached.get("mom_x")
    mom_y = cached.get("mom_y")
    if (
        mom_x is not None
        and mom_y is not None
        and len(mom_x) == len(t)
        and len(mom_y) == len(t)
    ):
        out["centroid"] = centroid_check(
            t, np.asarray(mom_x, float), np.asarray(mom_y, float),
            req.t_start, req.t_end,
        )
    else:
        out["centroid"] = {"available": False}

    return out


# -------------------------------------------------
# Static frontend mount
# -------------------------------------------------

HERE = pathlib.Path(__file__).resolve().parent
_CANDIDATES = [
    HERE.parent.parent / "frontend" / "dist",
    HERE.parent / "frontend" / "dist",
    pathlib.Path(os.environ.get("FRONTEND_DIST", "")),
]
DIST = next((p for p in _CANDIDATES if p and p.is_dir()), None)

if DIST is not None:
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
