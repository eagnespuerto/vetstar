"""MAST-backed vetting: sector lookup, single-sector analysis, progress-
tracked jobs (SSE), vet + PDF jobs, stored reports and FITS download.
"""
from __future__ import annotations

import logging
import math
import os
import uuid
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from vetstar_core.exofop import query_exofop
from vetstar_core.mast_fetch import fetch_spoc_lightcurve, list_available_sectors
from vetstar_core.parsers import parse_upload
from vetstar_core.pipeline import CROSSMATCH_TIMEOUT_S, crossmatch_known_object
from vetstar_core.progress import JOBS, JobState, ProgressReporter, make_noop_reporter
from vetstar_core.report import build_pdf

from .. import cache
from ..cache import _cache_lc, _put_ffi
from ..common import (
    _handle_exception,
    _run_pipeline,
    _save_upload_to_tempfile,
    _validate_known_period_days,
    _validate_odd_even_sigma,
    _validate_secondary_sigma,
)
from ..prefetch import REPORTS, VET_SLOTS, Prefetcher, release_memory
from .habitability import _UNSET, _attach_hci_summary_to_plots, _build_report_extras
from .transit import _downsample_cached_lc, _summarize_dvt

log = logging.getLogger("vetting")

router = APIRouter()

# -------------------------------------------------
# MAST endpoints
# -------------------------------------------------

class MastQuery(BaseModel):
    tic_id: int
    sector: int
    detect_threshold: float = 0.997
    detect_min_snr: float = 4.0
    high_variability: bool = False
    rotation_period_days: Optional[float] = None
    secondary_sigma: float = 3.0
    odd_even_sigma: float = 3.0
    # Plot-presentation knobs — do not affect detection.
    plot_detrend: bool = True            # 1-day rolling-median flatten on the LC plot
    plot_bin_minutes: Optional[int] = 30 # set null to hide the binned overlay
    known_period_days: Optional[float] = Field(
        default=None,
        description=(
            "Optional. If set, BLS searches a ±2% window around this "
            "period (and its P/2 and 2P harmonics) instead of a blind "
            "sweep. Must be in (0, 1000] days."
        ),
    )

    @field_validator("known_period_days")
    @classmethod
    def _validate_known_period_days(cls, v):
        if v is None:
            return v
        if not math.isfinite(v) or v <= 0 or v > 1000:
            raise ValueError(
                "known_period_days must be a finite number in (0, 1000]."
            )
        return v


@router.get("/api/mast/sectors/{tic_id}")
def mast_sectors(tic_id: int):
    try:
        sectors = list_available_sectors(tic_id)
        return {"tic_id": tic_id, "sectors": sectors}
    except Exception as e:
        raise _handle_exception("mast_sectors", e)


def _fetch_dvt_soft(tic_id: int, sector: Optional[int]) -> Optional[dict]:
    try:
        from vetstar_core.dvt_fetch import fetch_dvt
        return fetch_dvt(tic_id, sector)
    except Exception as e:
        log.warning("DVT fetch skipped for TIC %s S%s: %s", tic_id, sector, e)
        return None


def _start_prefetch(pf: Prefetcher, tic_id, sector, ra, dec, *, report: bool = True) -> None:
    """Kick off every remote fetch that doesn't need the pipeline result,
    heaviest first so the slow ones finish while BLS runs: the TESScut FFI
    cube, the SPOC DVT download, the Gaia/SIMBAD/NEA cross-match, then
    ExoFOP. ``report=False`` skips the PDF-only fetches (FFI, ExoFOP).

    The FFI cube is only *downloaded* here; ``_ffi_cutout_from_prefetch``
    reads it after the pipeline so it never sits in memory alongside the
    light curve and BLS arrays (512 MB budget)."""
    have_coords = ra is not None and dec is not None
    if report and have_coords and (tic_id, sector) not in cache._ffi_cache:
        from vetstar_core.ffi_cutout import download_ffi_cube
        pf.submit("ffi_cube", download_ffi_cube, ra, dec, sector=sector)
    if tic_id:
        pf.submit("dvt", _fetch_dvt_soft, tic_id, sector)
    if have_coords:
        pf.submit("crossmatch", crossmatch_known_object, ra, dec)
    if report and tic_id:
        pf.submit("exofop", query_exofop, tic_id)


def _ffi_cutout_from_prefetch(pf: Prefetcher, tic_id, sector, ra, dec):
    """Render the prefetched FFI cube (cached like ``_get_ffi_cutout``, so
    the on-page cutout panel reuses it). ``_UNSET`` when no download was
    started, so the caller falls back to a normal fetch."""
    from vetstar_core.ffi_cutout import discard_ffi_cube, render_ffi_cube

    key = (tic_id, sector)
    if key in cache._ffi_cache:
        return cache._ffi_cache[key]
    if not pf.has("ffi_cube"):
        return _UNSET
    cube = pf.get("ffi_cube", timeout=60)
    if cube is None:
        # Failed, or still downloading: drop its temp dir whenever it lands.
        pf.abandon("ffi_cube", discard_ffi_cube)
        return None
    cutout = render_ffi_cube(cube, ra, dec, tic_id=tic_id)
    _put_ffi(key, cutout)
    return cutout


def _prefetched_crossmatch(pf: Prefetcher):
    """``known_object`` callable for the pipeline: the prefetched cross-match,
    or None (the pipeline then queries itself) when it wasn't started."""
    if not pf.has("crossmatch"):
        return None
    # The cross-match runs two bounded rounds (main sources, exoplanets.org).
    return lambda: pf.get("crossmatch", timeout=2 * CROSSMATCH_TIMEOUT_S + 5)


def _mast_fetch_and_analyze(query: MastQuery, reporter: Optional[ProgressReporter] = None,
                            pf: Optional[Prefetcher] = None, report: bool = False):
    """Fetch the SPOC light curve for (TIC, sector) and vet it.

    The target's TIC v8 coordinates are looked up first so the slow remote
    fetches (``_start_prefetch``) run in parallel with the light-curve
    download and the pipeline. Pass ``pf`` to keep the prefetcher (and its
    FFI/ExoFOP results) for the PDF; otherwise it is closed here.
    ``info["dvt"]`` holds the SPOC DVT dict, or None.
    """
    rep = reporter or make_noop_reporter()
    own_pf = pf is None
    pf = pf or Prefetcher()
    try:
        rep("mast_fetch", 0.0)
        try:
            from vetstar_core.tic_catalog import fetch_tic_stellar
            tic_star = fetch_tic_stellar(query.tic_id)  # lru-cached; reused by the HCI backfill
        except Exception as e:
            log.warning("TIC coordinate lookup failed for TIC %s: %s", query.tic_id, e)
            tic_star = {}
        _start_prefetch(pf, query.tic_id, query.sector,
                        tic_star.get("ra"), tic_star.get("dec"), report=report)

        try:
            info = fetch_spoc_lightcurve(query.tic_id, query.sector)
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e))
        except Exception as e:
            raise _handle_exception("mast_fetch", e)
        rep("mast_fetch", 1.0)

        rep("parse", 0.0)
        try:
            parsed = parse_upload(info["path"], info["filename"])
        except Exception as e:
            raise _handle_exception(
                f"parse downloaded FITS ({os.path.basename(info.get('path', ''))})", e
            )
        rep("parse", 1.0)

        _cache_lc(parsed)

        try:
            result = _run_pipeline(
                parsed, query.detect_threshold, query.detect_min_snr,
                high_variability=query.high_variability,
                rotation_period_days=query.rotation_period_days,
                secondary_sigma=query.secondary_sigma,
                odd_even_sigma=query.odd_even_sigma,
                known_period_days=query.known_period_days,
                reporter=rep,
                plot_detrend=query.plot_detrend,
                plot_bin_minutes=query.plot_bin_minutes,
                known_object=_prefetched_crossmatch(pf),
            )
        except Exception as e:
            raise _handle_exception("pipeline", e)

        # SPOC DV companion file: fitted period, a/R*, impact parameter and a
        # phase fold with the transit model. None when not yet available
        # (recent sector, FFI-only target).
        rep("dvt_fetch", 0.0)
        info["dvt"] = pf.get("dvt", timeout=120)
        rep("dvt_fetch", 1.0)
        return result, info
    finally:
        if own_pf:
            pf.close()


def _mast_meta(info: dict) -> dict:
    return {
        "filename": os.path.basename(info.get("path", "")),
        "obs_id": info.get("obs_id"),
        "matched_observations": info.get("matched"),
        "author": info.get("author"),
        "exptime": info.get("exptime"),
        "fallback": info.get("fallback", False),
        "tried": info.get("tried", []),
    }


@router.post("/api/mast/analyze")
def mast_analyze(query: MastQuery):
    result, info = _mast_fetch_and_analyze(query)
    _attach_hci_summary_to_plots(result, dvt=info.get("dvt"))
    out = result.to_dict()
    out["mast"] = _mast_meta(info)
    out["lightcurve"] = _downsample_cached_lc(result.star.tic_id, result.star.sector)
    # Include DVT summary for the frontend (phase-fold plot + fitted parameters).
    out["dvt"] = _summarize_dvt(info.get("dvt"))
    return out


# -------------------------------------------------
# Async progress-tracked variant of /api/mast/analyze.
#
# Client flow:
#   1. POST /api/mast/analyze_async  → {job_id}
#   2. GET  /api/jobs/{job_id}/stream  (Server-Sent Events)
#        Yields lines like: data: {"type":"progress","stage":"bls","percent":42,"message":"..."}
#        Final line is either {"type":"done","result":{...}} or {"type":"error","message":"..."}
#
# Falls back gracefully: clients that don't support SSE can keep using the
# synchronous /api/mast/analyze endpoint above.
# -------------------------------------------------

def _run_mast_job(query: MastQuery, job: JobState) -> None:
    """Worker-thread body: run the full analyze flow and push events."""
    reporter = JOBS.reporter_for(job)
    try:
        result, info = _mast_fetch_and_analyze(query, reporter=reporter)
        _attach_hci_summary_to_plots(result, dvt=info.get("dvt"))
        out = result.to_dict()
        out["mast"] = _mast_meta(info)
        out["lightcurve"] = _downsample_cached_lc(result.star.tic_id, result.star.sector)
        out["dvt"] = _summarize_dvt(info.get("dvt"))
        JOBS.finish_ok(job, out)
    except HTTPException as e:
        JOBS.finish_err(job, f"{e.status_code}: {e.detail}")
    except Exception as e:
        log.exception("async mast job failed")
        JOBS.finish_err(job, f"{type(e).__name__}: {e}")


@router.post("/api/mast/analyze_async")
def mast_analyze_async(query: MastQuery):
    job = JOBS.create()
    import threading as _t
    _t.Thread(target=_run_mast_job, args=(query, job), daemon=True).start()
    return {"job_id": job.job_id}


@router.get("/api/jobs/{job_id}/stream")
async def job_stream(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job_id: {job_id}")

    import asyncio
    import json

    async def event_gen():
        # If the job already finished before the client connected, drain
        # the queue (which still holds the final event) and exit.
        timeout_ticks = 0
        while True:
            try:
                # poll the synchronous queue without blocking the event loop
                evt = await asyncio.to_thread(job.queue.get, True, 0.5)
                timeout_ticks = 0
                yield f"data: {json.dumps(evt)}\n\n"
                if evt.get("type") in ("done", "error"):
                    return
            except Exception:
                # queue.Empty raises; turn it into a heartbeat comment so
                # proxies (nginx, Cloudflare) don't drop the connection.
                timeout_ticks += 1
                yield ": keepalive\n\n"
                # Safety valve: if the worker died without writing a final
                # event, give up after ~10 min so the client gets an error.
                if timeout_ticks > 1200:
                    yield f"data: {json.dumps({'type':'error','message':'stream timed out'})}\n\n"
                    return

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx response buffering
            "Connection": "keep-alive",
        },
    )


# -------------------------------------------------
# Vet + PDF report in one job.
#
# One click vets the target, shows the result on the page, then builds the
# PDF from that same result (no second MAST download or pipeline run):
#   POST /api/vet_async          (MastQuery JSON)        → {job_id}
#   POST /api/vet_async/upload   (multipart, same query
#                                 params as /api/analyze) → {job_id}
#   GET  /api/jobs/{job_id}/stream
#        progress … {"type":"result","result":{...}}   ← render now
#        progress … {"type":"done","result":{"report":{"report_id","filename"}|null,
#                                             "report_error":str|null}}
#   GET  /api/reports/{report_id}  → the PDF (kept ~30 min, last few only)
# -------------------------------------------------

def _run_vet_job(job: JobState, work) -> None:
    """Worker-thread wrapper: hold a vetting slot (one job at a time on a
    512 MB box), own the prefetcher, and turn failures into SSE errors."""
    rep = JOBS.reporter_for(job)
    pf = Prefetcher()
    if not VET_SLOTS.acquire(blocking=False):
        rep("queued", 0.0)
        VET_SLOTS.acquire()
    try:
        work(job, rep, pf)
    except HTTPException as e:
        JOBS.finish_err(job, f"{e.status_code}: {e.detail}")
    except Exception as e:
        log.exception("vet job failed")
        JOBS.finish_err(job, f"{type(e).__name__}: {e}")
    finally:
        # A job that failed before the PDF step still owns the cube's temp dir.
        from vetstar_core.ffi_cutout import discard_ffi_cube
        pf.abandon("ffi_cube", discard_ffi_cube)
        pf.close()
        release_memory()
        VET_SLOTS.release()


def _publish_and_report(job: JobState, rep: ProgressReporter, pf: Prefetcher,
                        result, dvt: Optional[dict], out_extra: dict, fname: str) -> None:
    """Shared tail of the vet job: HCI → push the result to the page →
    ExoMiner + prefetched FFI cutout → PDF → done."""
    rep("habitability", 0.0)
    bundle = _attach_hci_summary_to_plots(
        result, dvt=dvt, exofop=pf.get("exofop", timeout=60),
    )
    rep("habitability", 1.0)

    out = result.to_dict()
    out.update(out_extra)
    out["lightcurve"] = _downsample_cached_lc(result.star.tic_id, result.star.sector)
    out["dvt"] = _summarize_dvt(dvt)
    JOBS.push_result(job, out)
    del out

    rep("report", 0.0)
    report, report_error = None, None
    try:
        star = result.star
        extras = _build_report_extras(
            result, dvt=dvt, hci_bundle=bundle,
            ffi_cutout=_ffi_cutout_from_prefetch(
                pf, star.tic_id, star.sector,
                getattr(star, "ra", None), getattr(star, "dec", None),
            ),
        )
        rep("report", 0.6)
        pdf = build_pdf(
            result,
            hci_bundle=extras.get("hci_bundle"),
            exominer=extras.get("exominer"),
            ffi_cutout=extras.get("ffi_cutout"),
            dvt=dvt,
        )
        report = {"report_id": REPORTS.put(pdf, fname), "filename": fname}
    except Exception as e:
        log.exception("PDF build failed in vet job")
        report_error = f"{type(e).__name__}: {e}"
    JOBS.finish_ok(job, {"report": report, "report_error": report_error})


def _vet_mast_work(query: MastQuery):
    def work(job, rep, pf):
        result, info = _mast_fetch_and_analyze(query, reporter=rep, pf=pf, report=True)
        fname = f"vetting_TIC{query.tic_id}_S{query.sector:03d}.pdf"
        _publish_and_report(job, rep, pf, result, info.get("dvt"),
                            {"mast": _mast_meta(info)}, fname)
    return work


def _vet_upload_work(tmp_path: str, filename: str, pipeline_kwargs: dict):
    def work(job, rep, pf):
        try:
            rep("parse", 0.0)
            parsed = parse_upload(tmp_path, filename)
            if parsed.get("metadata_only"):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "This ExoFOP file contains only target metadata (no time "
                        "series). Upload a FITS light curve instead."
                    ),
                )
            rep("parse", 1.0)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        star = parsed["star"]
        _start_prefetch(pf, star.tic_id, star.sector, star.ra, star.dec)
        _cache_lc(parsed)
        result = _run_pipeline(parsed, reporter=rep,
                               known_object=_prefetched_crossmatch(pf),
                               **pipeline_kwargs)
        del parsed
        rep("dvt_fetch", 0.0)
        dvt = pf.get("dvt", timeout=120)
        rep("dvt_fetch", 1.0)
        tic = result.star.tic_id or uuid.uuid4().hex[:8]
        _publish_and_report(job, rep, pf, result, dvt, {}, f"vetting_TIC{tic}.pdf")
    return work


def _start_vet_job(work) -> dict:
    job = JOBS.create()
    import threading as _t
    _t.Thread(target=_run_vet_job, args=(job, work), daemon=True).start()
    return {"job_id": job.job_id}


@router.post("/api/vet_async")
def vet_async(query: MastQuery):
    return _start_vet_job(_vet_mast_work(query))


@router.post("/api/vet_async/upload")
def vet_async_upload(
    file: UploadFile = File(...),
    detect_threshold: float = 0.997,
    detect_min_snr: float = 4.0,
    high_variability: bool = False,
    rotation_period_days: Optional[float] = None,
    secondary_sigma: float = 3.0,
    odd_even_sigma: float = 3.0,
    known_period_days: Optional[float] = None,
    plot_detrend: bool = True,
    plot_bin_minutes: Optional[int] = 30,
):
    _validate_known_period_days(known_period_days)
    _validate_secondary_sigma(secondary_sigma)
    _validate_odd_even_sigma(odd_even_sigma)
    tmp_path = _save_upload_to_tempfile(file)  # the job deletes it after parsing
    return _start_vet_job(_vet_upload_work(tmp_path, file.filename, {
        "detect_threshold": detect_threshold,
        "detect_min_snr": detect_min_snr,
        "high_variability": high_variability,
        "rotation_period_days": rotation_period_days,
        "secondary_sigma": secondary_sigma,
        "odd_even_sigma": odd_even_sigma,
        "known_period_days": known_period_days,
        "plot_detrend": plot_detrend,
        "plot_bin_minutes": plot_bin_minutes,
    }))


@router.get("/api/reports/{report_id}")
def get_report(report_id: str):
    item = REPORTS.get(report_id)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail="Report expired or unknown — run the vetting again to rebuild it.",
        )
    pdf, fname = item
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/api/mast/report")
def mast_report(query: MastQuery):
    result, info = _mast_fetch_and_analyze(query)
    try:
        dvt = info.get("dvt")
        extras = _build_report_extras(result, dvt=dvt)
        pdf = build_pdf(
            result,
            hci_bundle=extras.get("hci_bundle"),
            exominer=extras.get("exominer"),
            ffi_cutout=extras.get("ffi_cutout"),
            dvt=dvt,
        )
    except Exception as e:
        raise _handle_exception("build_pdf", e)
    fname = f"vetting_TIC{query.tic_id}_S{query.sector:03d}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/api/mast/download/{tic_id}/{sector}")
def mast_download_fits(tic_id: int, sector: int):
    """Download the raw SPOC light-curve FITS for a (TIC, sector).

    Re-runs the MAST fetch; astroquery serves the cached file if it was
    already downloaded during analysis, so this is cheap on a repeat.
    """
    try:
        info = fetch_spoc_lightcurve(tic_id, sector)
    except Exception as e:
        raise HTTPException(
            502, f"Could not fetch FITS for TIC {tic_id} sector {sector}: {e}"
        )
    path = info.get("path")
    if not path or not os.path.exists(path):
        raise HTTPException(404, "FITS file not found on server after fetch.")
    fname = f"TIC{tic_id}_S{int(sector):03d}.fits"
    return FileResponse(path, media_type="application/fits", filename=fname)
