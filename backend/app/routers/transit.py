"""Uploaded light curves and the per-target panels: analyze, report,
ExoMiner, FFI cutout and the manual dip tool.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

import numpy as np
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from vetstar_core.exominer import run_exominer as _run_exominer
from vetstar_core.observables import compute_observables
from vetstar_core.parsers import parse_upload
from vetstar_core.pipeline import centroid_check, measure_shape
from vetstar_core.report import build_pdf

from ..cache import _cache_lc, _get_ffi_cutout, _lookup_lc
from ..common import (
    _handle_exception,
    _run_pipeline,
    _save_upload_to_tempfile,
    _validate_known_period_days,
)
from .habitability import _attach_hci_summary_to_plots, _build_report_extras

log = logging.getLogger("vetting")

router = APIRouter()

# -------------------------------------------------
# Upload endpoints
# -------------------------------------------------

@router.post("/api/analyze")
def analyze(
    file: UploadFile = File(...),
    detect_threshold: float = 0.997,
    detect_min_snr: float = 4.0,
    high_variability: bool = False,
    rotation_period_days: Optional[float] = None,
    secondary_sigma: float = 3.0,
    odd_even_sigma: float = 3.0,
    known_period_days: Optional[float] = None,
):
    _validate_known_period_days(known_period_days)
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
        lc_token = _cache_lc(parsed)
        result = _run_pipeline(
            parsed, detect_threshold, detect_min_snr,
            high_variability=high_variability,
            rotation_period_days=rotation_period_days,
            secondary_sigma=secondary_sigma,
            odd_even_sigma=odd_even_sigma,
            known_period_days=known_period_days,
        )
        result.lc_token = lc_token
        _attach_hci_summary_to_plots(result)
        d = result.to_dict()
        d["lightcurve"] = _downsample_cached_lc(
            result.star.tic_id, result.star.sector, lc_token)
        # Uploads with no TIC: the page sends this back to reach its own curve.
        d["lc_token"] = lc_token
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


@router.post("/api/report")
def report(
    file: UploadFile = File(...),
    detect_threshold: float = 0.997,
    detect_min_snr: float = 4.0,
    high_variability: bool = False,
    rotation_period_days: Optional[float] = None,
    secondary_sigma: float = 3.0,
    odd_even_sigma: float = 3.0,
    known_period_days: Optional[float] = None,
):
    _validate_known_period_days(known_period_days)
    tmp_path = None
    try:
        tmp_path = _save_upload_to_tempfile(file)
        parsed = parse_upload(tmp_path, file.filename)
        if parsed.get("metadata_only"):
            raise HTTPException(
                status_code=400,
                detail="ExoFOP metadata-only file. Upload a FITS light curve.",
            )
        result = _run_pipeline(
            parsed, detect_threshold, detect_min_snr,
            high_variability=high_variability,
            rotation_period_days=rotation_period_days,
            secondary_sigma=secondary_sigma,
            odd_even_sigma=odd_even_sigma,
            known_period_days=known_period_days,
        )
        result.lc_token = _cache_lc(parsed)
        # Opportunistic SPOC DVT fetch — adds the DV phase-fold image and
        # fitted geometry when the TIC has been processed by SPOC DV.
        dvt = None
        if result.star.tic_id:
            try:
                from vetstar_core.dvt_fetch import fetch_dvt
                dvt = fetch_dvt(result.star.tic_id, result.star.sector)
            except Exception as _e:
                log.warning("DVT fetch skipped for upload TIC %s: %s",
                            result.star.tic_id, _e)
                dvt = None
        extras = _build_report_extras(result, dvt=dvt)
        pdf = build_pdf(
            result,
            hci_bundle=extras.get("hci_bundle"),
            exominer=extras.get("exominer"),
            ffi_cutout=extras.get("ffi_cutout"),
            dvt=dvt,
        )
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
# ExoMiner endpoint
# -------------------------------------------------


class ExominerRequest(BaseModel):
    tic_id: Optional[int] = None
    sector: Optional[int] = None
    period: float
    t0: float
    duration: float
    crowdsap: Optional[float] = None
    lc_token: Optional[str] = None   # for an upload with no TIC


@router.post("/api/exominer")
def api_exominer(req: ExominerRequest):
    cached = _lookup_lc(req.tic_id, req.sector, req.lc_token)
    if not cached:
        raise HTTPException(
            status_code=400,
            detail=(
                "No light curve in cache. Run /api/analyze or "
                "/api/mast/analyze first, then call this endpoint."
            ),
        )
    try:
        result = _run_exominer(
            t=cached["t"],
            f=cached["f"],
            mom_x=cached.get("mom_x"),
            mom_y=cached.get("mom_y"),
            period=req.period,
            t0=req.t0,
            duration=req.duration,
            crowdsap=req.crowdsap,
        )
        # Integrate POE predicted observables alongside the ML feature set.
        star = cached.get("star")
        depth_frac = None
        try:
            depth_frac = float(result["scalars"]["depth_ppm"]) / 1e6
        except Exception:
            pass
        obs = compute_observables(
            teff_k=getattr(star, "teff", None),
            rstar_sun=getattr(star, "radius", None),
            mstar_sun=getattr(star, "mass", None),
            distance_pc=getattr(star, "distance", None),
            orbital_period_d=req.period,
            transit_depth_frac=depth_frac,
        ).to_dict()
        result["observables"] = obs
        # Flatten the headline observables into the scalar feature vector so
        # downstream ExoMiner consumers can use them directly.
        result["scalars"]["a_au"] = (
            round(obs["orbit"]["semi_major_axis_au"], 6)
            if obs["orbit"].get("semi_major_axis_au") is not None else None
        )
        result["scalars"]["insolation_searth"] = (
            round(obs["insolation_searth"], 4)
            if obs.get("insolation_searth") is not None else None
        )
        result["scalars"]["rv_k_ms"] = (
            round(obs["radial_velocity"]["K_ms"], 4)
            if obs.get("radial_velocity", {}).get("K_ms") is not None else None
        )
        result["scalars"]["transit_depth_pred_pct"] = (
            round(obs["transit"]["depth_pct"], 5)
            if obs.get("transit", {}).get("depth_pct") is not None else None
        )
        return result
    except Exception as e:
        raise _handle_exception("exominer", e)


# -------------------------------------------------
# FFI cutout endpoint (on-screen panel)
# -------------------------------------------------
class FfiCutoutRequest(BaseModel):
    ra: float
    dec: float
    sector: Optional[int] = None
    tic_id: Optional[int] = None
    size_px: int = 15


@router.post("/api/ffi_cutout")
def api_ffi_cutout(req: FfiCutoutRequest):
    """Return a rendered TESScut FFI cutout for the target. Always returns
    HTTP 200 — on any failure it returns ``{"available": False, "reason": ...}``
    so the panel shows a friendly message instead of a hard error."""
    try:
        cutout = _get_ffi_cutout(
            req.tic_id, req.sector, req.ra, req.dec, size_px=req.size_px
        )
        if not cutout:
            return {
                "available": False,
                "reason": (
                    "No FFI cutout available — TESScut may not cover this "
                    "sector/position yet, or the request to MAST timed out."
                ),
            }
        return {"available": True, **cutout}
    except Exception as e:
        log.warning("FFI cutout endpoint failed: %s", e)
        return {
            "available": False,
            "reason": "FFI cutout could not be generated right now. Please try again.",
        }


# -------------------------------------------------
# Manual tiny-dip selector
# -------------------------------------------------

def _summarize_dvt(dvt: Optional[dict]) -> Optional[dict]:
    """Build a frontend-friendly DVT summary from a parsed DVT dict.

    Strips heavy array data; keeps scalar parameters and the phase-fold plot
    (base64 PNG).  Returns None when dvt is None or contains no TCEs.
    """
    if not dvt or not dvt.get("tces"):
        return None
    from vetstar_core.dvt_fetch import best_tce
    tce = best_tce(dvt)
    if tce is None:
        return None
    return {
        "available": True,
        "star": dvt.get("star", {}),
        "tce": {
            k: v for k, v in tce.items()
            if k not in ("columns_available",)  # not needed by frontend
        },
        "n_tces": len(dvt["tces"]),
    }


def _downsample_cached_lc(tic_id, sector, token: Optional[str] = None,
                          max_pts: int = 4000) -> Optional[dict]:
    """Return a transport-friendly {t, f} from the cached cleaned LC."""
    cached = _lookup_lc(tic_id, sector, token)
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
    lc_token: Optional[str] = None   # for an upload with no TIC


@router.post("/api/manual_dip")
def api_manual_dip(req: ManualDipRequest):
    """Characterise a user-marked time window as a dip: depth, duration,
    shape (U/V) and — when centroid moments are cached — an on-target test."""
    if req.t_end <= req.t_start:
        raise HTTPException(status_code=400, detail="t_end must be greater than t_start.")

    cached = _lookup_lc(req.tic_id, req.sector, req.lc_token)
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
