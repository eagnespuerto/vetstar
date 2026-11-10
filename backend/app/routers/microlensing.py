"""Microlensing: model-comparison classifier, joint TESS + Gaia fit,
light-curve fetchers, Gaia Alerts, coverage and PDF report.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from vetstar_core.gaia_photometry import (
    fetch_alert_lightcurve as gaia_fetch_alert_lightcurve,
)
from vetstar_core.gaia_photometry import search_alerts_near as gaia_search_alerts_near
from vetstar_core.mast_fetch import fetch_spoc_lightcurve, list_available_sectors
from vetstar_core.microlensing import analyze_event as microlensing_analyze_event
from vetstar_core.microlensing import (
    analyze_event_joint as microlensing_analyze_event_joint,
)
from vetstar_core.microlensing_coverage import (
    evaluate_catalog as microlensing_evaluate_catalog,
)
from vetstar_core.microlensing_coverage import parse_events_csv, resolve_ra_dec_to_tic
from vetstar_core.microlensing_ffi import make_ffi_gaia_cutout
from vetstar_core.microlensing_report import build_microlensing_pdf
from vetstar_core.parsers import parse_upload
from vetstar_core.timeouts import run_with_timeout

from ..cache import _cache_lc
from ..common import _handle_exception

router = APIRouter()

# -------------------------------------------------
# Microlensing (Module A) — model-comparison classifier
# -------------------------------------------------

class MicrolensingWindow(BaseModel):
    t_start: float
    t_end: float


class MicrolensingFitRequest(BaseModel):
    time: list[float]
    flux: list[float]
    flux_err: list[float]
    window: MicrolensingWindow
    t0_guess: float


class MicrolensingLightcurveByCoordsRequest(BaseModel):
    ra: float
    dec: float
    sector: Optional[int] = None
    radius_arcsec: float = 30.0


class MicrolensingLightcurveByTicRequest(BaseModel):
    tic_id: int
    sector: Optional[int] = None


class GaiaAlertLightcurveRequest(BaseModel):
    alert_id: str


@router.post("/api/microlensing/gaia_alert_lightcurve")
def api_microlensing_gaia_alert_lightcurve(req: GaiaAlertLightcurveRequest):
    """Fetch a Gaia Alerts light curve by alert ID (e.g. 'Gaia23bra').

    Returns raw G-band arrays plus the Kruszynska-2022-inflated errors
    used by the joint fit. Read-only HTTP GET to the public feed.
    """
    try:
        lc = gaia_fetch_alert_lightcurve(req.alert_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=f"Gaia Alerts fetch failed: {e}")
    except Exception as e:  # pragma: no cover
        raise _handle_exception("gaia alert lightcurve", e)
    return lc.to_dict()


@router.get("/api/microlensing/gaia_alerts_near")
def api_microlensing_gaia_alerts_near(
    ra: float, dec: float,
    radius_arcsec: float = 60.0,
    microlensing_only: bool = True,
    max_results: int = 20,
):
    """Return published Gaia alerts within `radius_arcsec` of (RA, Dec).

    Filtered to microlensing candidates by default. This hits the Gaia
    Alerts master index CSV (cached in-process, small).
    """
    try:
        hits = gaia_search_alerts_near(
            ra, dec,
            radius_arcsec=radius_arcsec,
            microlensing_only=microlensing_only,
            max_results=max_results,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=f"Gaia Alerts index fetch failed: {e}")
    except Exception as e:  # pragma: no cover
        raise _handle_exception("gaia alerts search", e)
    return {
        "query": {
            "ra": ra, "dec": dec,
            "radius_arcsec": radius_arcsec,
            "microlensing_only": microlensing_only,
        },
        "n_hits": len(hits),
        "alerts": [h.to_dict() for h in hits],
    }


def _fetch_and_pack_lightcurve(tic_id: int, requested_sector: Optional[int],
                                extra: Optional[dict] = None) -> dict:
    """Shared LC-fetch + response-packing used by both coord- and TIC-keyed
    autoload endpoints. Walks available sectors newest-first (preferring
    SPOC / TESS-SPOC / QLP over DVT-only) unless a specific sector was
    requested. `extra` gets merged into the response for endpoint-specific
    metadata (resolved_ra, separation, etc.)."""
    _LC_PROVIDERS = {"SPOC", "TESS-SPOC", "QLP"}

    if requested_sector is not None:
        sectors_to_try = [int(requested_sector)]
    else:
        try:
            available = list_available_sectors(tic_id)
        except Exception as e:  # pragma: no cover
            raise _handle_exception("sector list", e)
        if not available:
            raise HTTPException(
                status_code=404,
                detail=f"TIC {tic_id} has no TESS observations archived at MAST.",
            )
        with_lc = [int(s["sector"]) for s in available
                   if any(p in _LC_PROVIDERS for p in s.get("providers", []))]
        if not with_lc:
            raise HTTPException(
                status_code=404,
                detail=(f"TIC {tic_id} has TESS observations but no SPOC / "
                        f"TESS-SPOC / QLP light-curve product — only DV or "
                        f"other non-LC files. Try uploading manually."),
            )
        sectors_to_try = sorted(with_lc, reverse=True)[:4]

    fetched = None
    parsed = None
    last_err = None
    sector = None
    for candidate in sectors_to_try:
        try:
            f_try = fetch_spoc_lightcurve(tic_id, candidate)
            parsed = parse_upload(f_try["path"], f_try["filename"])
            fetched = f_try
            sector = candidate
            break
        except Exception as e:
            last_err = e
            continue
    if fetched is None or parsed is None:
        raise HTTPException(
            status_code=404,
            detail=f"Could not fetch a usable LC for TIC {tic_id} (tried sectors {sectors_to_try}): {last_err}",
        )

    try:
        _cache_lc(parsed)
    except Exception:
        pass

    t = np.asarray(parsed["t"], dtype=float)
    f = np.asarray(parsed["flux"], dtype=float)
    fe = np.asarray(parsed.get("flux_err"), dtype=float) if parsed.get("flux_err") is not None else None

    mask = np.isfinite(t) & np.isfinite(f) & (f > 0)
    if fe is not None:
        mask = mask & np.isfinite(fe) & (fe > 0)
    quality = parsed.get("quality")
    if quality is not None:
        try:
            mask = mask & (np.asarray(quality) == 0)
        except Exception:
            pass

    t_clean = t[mask].tolist()
    f_clean = f[mask].tolist()
    fe_clean = (fe[mask].tolist()
                if fe is not None
                else [float(np.median(f[mask]) * 1e-4)] * int(mask.sum()))

    if len(t_clean) < 32:
        raise HTTPException(
            status_code=404,
            detail=f"Only {len(t_clean)} usable cadences left after quality filter for TIC {tic_id} S{sector}.",
        )

    payload = {
        "time": t_clean,
        "flux": f_clean,
        "flux_err": fe_clean,
        "tic_id": tic_id,
        "sector": sector,
        "provider": fetched.get("author"),
        "filename": fetched.get("filename"),
        "n_cadences": len(t_clean),
    }
    if extra:
        payload.update(extra)
    return payload


@router.post("/api/microlensing/lightcurve_by_tic")
def api_microlensing_lightcurve_by_tic(req: MicrolensingLightcurveByTicRequest):
    """Fetch a TESS light curve directly by (TIC, sector) — same UX as the
    transit tab's MAST mode, but returns raw arrays suitable for the
    microlensing classifier instead of running the full vetting pipeline.
    """
    return _fetch_and_pack_lightcurve(req.tic_id, req.sector)


@router.post("/api/microlensing/lightcurve_by_coords")
def api_microlensing_lightcurve_by_coords(req: MicrolensingLightcurveByCoordsRequest):
    """Resolve (RA, Dec) → nearest TIC via MAST, then fetch and return the
    raw TESS light curve arrays. Powers the Module B → Module A autoload
    handoff so the classifier doesn't need a manual FITS upload.
    """
    try:
        resolved = resolve_ra_dec_to_tic(req.ra, req.dec, radius_arcsec=req.radius_arcsec)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:  # pragma: no cover — network / import failures
        raise _handle_exception("TIC coord resolution", e)

    return _fetch_and_pack_lightcurve(
        resolved["tic_id"],
        req.sector,
        extra={
            "resolved_ra": resolved["resolved_ra"],
            "resolved_dec": resolved["resolved_dec"],
            "separation_arcsec": resolved["separation_arcsec"],
            "tmag": resolved["tmag"],
        },
    )


class MicrolensingFfiCutoutRequest(BaseModel):
    ra: float
    dec: float
    sector: Optional[int] = None
    tic_id: Optional[int] = None
    size_px: int = 15
    gaia_mag_limit: float = 20.0
    gaia_max_sources: int = 40


@router.post("/api/microlensing/ffi_cutout")
def api_microlensing_ffi_cutout(req: MicrolensingFfiCutoutRequest):
    """TESScut FFI cutout + Gaia DR3 overlay for source-blending diagnosis."""
    # Bounded like _get_ffi_cutout so a stuck MAST/Gaia call can't hang the
    # request until the gateway gives up.
    try:
        result = run_with_timeout(
            make_ffi_gaia_cutout,
            ra=req.ra, dec=req.dec,
            sector=req.sector, tic_id=req.tic_id,
            size_px=req.size_px,
            gaia_mag_limit=req.gaia_mag_limit,
            gaia_max_sources=req.gaia_max_sources,
            timeout_s=60.0,
        )
    except TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="TESScut/Gaia took too long to respond. Please try again.",
        )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=("No TESScut cutout produced. Possible causes: coords "
                    "outside all TESS sectors, MAST/TESScut temporary failure, "
                    "or sector not yet public."),
        )
    return result


@router.post("/api/microlensing/coverage")
def api_microlensing_coverage(
    file: UploadFile = File(...),
    margin_te: float = 1.0,
):
    """Take a CSV of microlensing events and return TESS sector coverage per row.

    CSV columns: event_id, ra, dec, t0 (BTJD), tE (days, optional — defaults to 20).
    """
    try:
        contents = file.file.read()
        text = contents.decode("utf-8", errors="replace")
        events = parse_events_csv(text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # pragma: no cover
        raise _handle_exception("microlensing coverage upload", e)
    return microlensing_evaluate_catalog(events, margin_te=margin_te)


class MicrolensingReportRequest(BaseModel):
    """Payload for the microlensing PDF report endpoint.

    Accepts a fit result dict verbatim (as returned by /api/microlensing/fit)
    plus optional metadata (event_id, TIC, coords, sector) and two optional
    plot PNGs (base64, no data-URL prefix) to embed: the classifier
    light-curve overlay and the FFI + Gaia cutout. The endpoint doesn't
    re-fit — the client already has the numbers, we just render them.
    """
    result: dict
    metadata: Optional[dict] = None
    plot_png_base64: Optional[str] = None
    ffi_png_base64: Optional[str] = None


@router.post("/api/microlensing/report")
def api_microlensing_report(req: MicrolensingReportRequest):
    try:
        pdf_bytes = build_microlensing_pdf(
            result=req.result,
            metadata=req.metadata or {},
            plot_png_b64=req.plot_png_base64,
            ffi_png_b64=req.ffi_png_base64,
        )
    except KeyError as e:
        raise HTTPException(status_code=400,
                            detail=f"Malformed result payload: missing key {e}")
    except Exception as e:  # pragma: no cover
        raise _handle_exception("microlensing report", e)

    meta = req.metadata or {}
    id_bits = [
        meta.get("event_id"),
        f"TIC{meta['tic_id']}" if meta.get("tic_id") else None,
        f"S{int(meta['sector']):03d}" if meta.get("sector") is not None else None,
    ]
    stem = "_".join(b for b in id_bits if b) or "microlensing_event"
    # Sanitize for a Content-Disposition filename.
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{safe}_report.pdf"'
        },
    )


class MicrolensingJointFitRequest(BaseModel):
    """Body for the joint TESS + Gaia fit endpoint.

    TESS side: raw flux + err arrays (same shape as /fit).
    Gaia side: JD_TCB times + G-band magnitudes + errors (from either
    the Gaia Alerts CSV via /gaia_alert_lightcurve, or a user upload).
    """
    tess_time: list[float]
    tess_flux: list[float]
    tess_flux_err: list[float]
    gaia_time_jd: list[float]
    gaia_mag: list[float]
    gaia_mag_err: list[float]
    window: MicrolensingWindow
    t0_guess: float
    gaia_baseline_window_d: float = 200.0


@router.post("/api/microlensing/fit_joint")
def api_microlensing_fit_joint(req: MicrolensingJointFitRequest):
    """Joint TESS + Gaia PSPL fit. See analyze_event_joint for the recipe."""
    if req.window.t_end <= req.window.t_start:
        raise HTTPException(status_code=400,
                            detail="window.t_end must be greater than window.t_start.")
    if not (len(req.tess_time) == len(req.tess_flux) == len(req.tess_flux_err)):
        raise HTTPException(status_code=400,
                            detail="tess arrays must all have the same length.")
    if not (len(req.gaia_time_jd) == len(req.gaia_mag) == len(req.gaia_mag_err)):
        raise HTTPException(status_code=400,
                            detail="gaia arrays must all have the same length.")
    try:
        return microlensing_analyze_event_joint(
            tess_time=np.asarray(req.tess_time, dtype=float),
            tess_flux=np.asarray(req.tess_flux, dtype=float),
            tess_flux_err=np.asarray(req.tess_flux_err, dtype=float),
            gaia_time_jd=np.asarray(req.gaia_time_jd, dtype=float),
            gaia_mag=np.asarray(req.gaia_mag, dtype=float),
            gaia_mag_err=np.asarray(req.gaia_mag_err, dtype=float),
            t_start=req.window.t_start,
            t_end=req.window.t_end,
            t0_guess=req.t0_guess,
            gaia_baseline_window_d=req.gaia_baseline_window_d,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # pragma: no cover
        raise _handle_exception("microlensing joint fit", e)


@router.post("/api/microlensing/fit")
def api_microlensing_fit(req: MicrolensingFitRequest):
    """Fit PSPL + Davenport-2014 flare + null on a user-flagged window and
    return the verdict, ΔBIC diagnostics, and per-model fits.
    """
    if req.window.t_end <= req.window.t_start:
        raise HTTPException(status_code=400, detail="window.t_end must be greater than window.t_start.")
    if not (len(req.time) == len(req.flux) == len(req.flux_err)):
        raise HTTPException(status_code=400, detail="time, flux, flux_err must all have the same length.")
    try:
        result = microlensing_analyze_event(
            time=np.asarray(req.time, dtype=float),
            flux=np.asarray(req.flux, dtype=float),
            flux_err=np.asarray(req.flux_err, dtype=float),
            t_start=req.window.t_start,
            t_end=req.window.t_end,
            t0_guess=req.t0_guess,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # pragma: no cover — surfaced as 500 with trace
        raise _handle_exception("microlensing fit", e)
    return result
