"""
FastAPI backend for TESS vetting app.
"""

from __future__ import annotations

import logging
import math
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

from pydantic import BaseModel, Field, field_validator

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

# FFI cutout cache so the on-screen panel and the PDF don't each hit TESScut.
# Keyed by (tic_id, sector). Process-local.
_ffi_cache: dict = {}


def _get_ffi_cutout(tic_id, sector, ra, dec, size_px: int = 15, timeout_s: float = 45.0):
    """Return a TESScut FFI cutout dict for the target, fetching once and
    caching by (tic_id, sector). Fails soft (returns None). The TESScut
    fetch is bounded by a timeout and run in a worker thread so a slow or
    stuck MAST request can never hang the request and trip a gateway 502."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
    from .ffi_cutout import make_ffi_cutout

    key = (tic_id, sector)
    if key in _ffi_cache:
        return _ffi_cache[key]

    cutout = None
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(
                make_ffi_cutout, ra=ra, dec=dec, sector=sector,
                tic_id=tic_id, size_px=size_px,
            )
            cutout = fut.result(timeout=timeout_s)
    except FutureTimeout:
        log.warning("FFI cutout timed out after %.0fs for TIC %s S%s", timeout_s, tic_id, sector)
        cutout = None  # don't cache a timeout — a retry may succeed
        return None
    except Exception as e:
        log.warning("FFI cutout error for TIC %s S%s: %s", tic_id, sector, e)
        cutout = None

    _ffi_cache[key] = cutout  # cache success or a clean "no cutout" (None)
    return cutout


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
            "star": star,
        }
        _lc_cache[(tic_id, sector)] = entry
        _lc_cache["__last__"] = entry
    except Exception as exc:
        log.warning("LC cache update failed: %s", exc)


# -------------------------------------------------
# Sensitivity helpers
# -------------------------------------------------

def _clamp_params(detect_threshold: float, detect_min_snr: float):
    # Widened to support the log-scale slider range: depth 0.01%..9%.
    th = max(0.91, min(0.9999, float(detect_threshold)))
    snr = max(0.5, min(32.0, float(detect_min_snr)))
    return th, snr


def _validate_secondary_sigma(secondary_sigma: float) -> float:
    if not (0.5 <= float(secondary_sigma) <= 20.0):
        raise HTTPException(
            status_code=422,
            detail=f"secondary_sigma must be in [0.5, 20.0], got {secondary_sigma}.",
        )
    return float(secondary_sigma)


def _validate_odd_even_sigma(odd_even_sigma: float) -> float:
    if not (0.5 <= float(odd_even_sigma) <= 20.0):
        raise HTTPException(
            status_code=422,
            detail=f"odd_even_sigma must be in [0.5, 20.0], got {odd_even_sigma}.",
        )
    return float(odd_even_sigma)


def _validate_known_period_days(v: Optional[float]) -> None:
    if v is None:
        return
    if (
        not math.isfinite(v)
        or v <= 0
        or v > 1000
    ):
        raise HTTPException(
            status_code=422,
            detail="known_period_days must be a finite number in (0, 1000].",
        )


def _run_pipeline(parsed: dict, detect_threshold: float, detect_min_snr: float,
                  high_variability: bool = False,
                  rotation_period_days: Optional[float] = None,
                  secondary_sigma: float = 3.0,
                  odd_even_sigma: float = 3.0,
                  known_period_days: Optional[float] = None):
    th, snr = _clamp_params(detect_threshold, detect_min_snr)
    sec_sig = _validate_secondary_sigma(secondary_sigma)
    oe_sig = _validate_odd_even_sigma(odd_even_sigma)
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
        high_variability=high_variability,
        rotation_period_days=rotation_period_days,
        known_period_days=known_period_days,
        secondary_sigma=sec_sig,
        odd_even_sigma=oe_sig,
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
        _cache_lc(parsed)
        result = _run_pipeline(
            parsed, detect_threshold, detect_min_snr,
            high_variability=high_variability,
            rotation_period_days=rotation_period_days,
            secondary_sigma=secondary_sigma,
            odd_even_sigma=odd_even_sigma,
            known_period_days=known_period_days,
        )
        _attach_hci_summary_to_plots(result)
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
        _cache_lc(parsed)
        # Opportunistic SPOC DVT fetch — adds the DV phase-fold image and
        # fitted geometry when the TIC has been processed by SPOC DV.
        dvt = None
        if result.star.tic_id:
            try:
                from .dvt_fetch import fetch_dvt
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

    # Opportunistically fetch the companion DVT file produced by SPOC DV.
    # Provides fitted period, a/R★ (ARAT), impact parameter, and a phase-fold
    # plot with the transit model overlay — cleaner than BLS-only estimates.
    # Fails soft: None when not yet available (recent sector, FFI-only target).
    try:
        from .dvt_fetch import fetch_dvt
        info["dvt"] = fetch_dvt(query.tic_id, query.sector)
    except Exception as _e:
        log.warning("DVT fetch skipped for TIC %s S%s: %s", query.tic_id, query.sector, _e)
        info["dvt"] = None

    try:
        parsed = parse_upload(info["path"], info["filename"])
    except Exception as e:
        raise _handle_exception(
            f"parse downloaded FITS ({os.path.basename(info.get('path', ''))})", e
        )

    _cache_lc(parsed)

    try:
        result = _run_pipeline(
            parsed, query.detect_threshold, query.detect_min_snr,
            high_variability=query.high_variability,
            rotation_period_days=query.rotation_period_days,
            secondary_sigma=query.secondary_sigma,
            odd_even_sigma=query.odd_even_sigma,
            known_period_days=query.known_period_days,
        )
    except Exception as e:
        raise _handle_exception("pipeline", e)

    return result, info


@app.post("/api/mast/analyze")
async def mast_analyze(query: MastQuery):
    result, info = _mast_fetch_and_analyze(query)
    _attach_hci_summary_to_plots(result, dvt=info.get("dvt"))
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
    # Include DVT summary for the frontend (phase-fold plot + fitted parameters).
    out["dvt"] = _summarize_dvt(info.get("dvt"))
    return out


@app.post("/api/mast/report")
async def mast_report(query: MastQuery):
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


@app.get("/api/mast/download/{tic_id}/{sector}")
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


# -------------------------------------------------
# Habitability + multi-sector endpoints
# -------------------------------------------------

from .habitability import PlanetCandidate, compute_hci
from .exofop import query_exofop


class HabitabilityQuery(BaseModel):
    tic_id: int
    radius_earth: Optional[float] = None
    semi_major_axis_au: Optional[float] = None
    orbital_period_d: Optional[float] = None
    stellar_teff: Optional[float] = None
    stellar_radius_sun: Optional[float] = None
    stellar_mass_sun: Optional[float] = None
    R_companion_Rjup: Optional[float] = None
    planet_mass_earth: Optional[float] = None    # absolute mass override (RV, etc.)
    use_archive_rv: bool = True                  # auto-derive mass from archive RV K
    n_sectors_with_detections: int = 1
    n_sectors_observed: int = 1
    vetting_verdict: Optional[dict] = None


class MultisectorQuery(BaseModel):
    tic_id: int
    sectors: Optional[list] = None
    detect_threshold: float = 0.997
    detect_min_snr: float = 4.0
    high_variability: bool = False
    rotation_period_days: Optional[float] = None
    secondary_sigma: float = 3.0
    odd_even_sigma: float = 3.0
    # How many distinct objects to keep when clustering per-sector events by
    # transit duration. Default 2 mirrors the historical behaviour; bumping it
    # up to 10 (MAX_SECTORS * EVENTS_PER_SECTOR) lets a real TOI surface when
    # the deepest dips belong to a brighter contaminant.
    max_objects: int = 2
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


def _habitability_bundle(query: HabitabilityQuery) -> dict:
    """Compute the full HCI + observables + TLCM bundle for a target.

    Factored out of the /api/habitability route so the PDF report flow can
    reuse exactly the same computation server-side (no frontend plumbing).
    """
    try:
        exofop = query_exofop(query.tic_id)
    except Exception as e:
        log.warning("ExoFOP query failed for TIC %s: %s", query.tic_id, e)
        exofop = {"star": {}, "tois": [], "source": "unavailable"}

    star = exofop.get("star", {})
    tois = exofop.get("tois", [])

    # ExoFOP often returns empty stellar fields, and FFI/QLP headers lack
    # TEFF/RADIUS/LOGG. Walk the same backfill chain /api/observables uses
    # so the HCI bundle (and therefore the PDF report) gets the same
    # populated stellar parameters as the standalone observables panel.
    try:
        from .tic_catalog import backfill_star
        star, _used_tic = backfill_star(star, query.tic_id)
    except Exception as e:
        log.warning("TIC v8 backfill failed for TIC %s: %s", query.tic_id, e)
    gaia_used = False
    try:
        from .gaia_catalog import backfill_star_gaia
        star, gaia_used = backfill_star_gaia(star, star.get("ra"), star.get("dec"))
    except Exception as e:
        log.warning("Gaia DR3 backfill failed for TIC %s: %s", query.tic_id, e)

    # Teff -> Pecaut & Mamajek main-sequence fallback for missing R*/M*.
    # Runs before PlanetCandidate is built so TLCM (which needs R*) and the
    # downstream POE call both see the estimated values, and before density
    # is computed because density-from-Teff is much more reliable than the
    # b=0 density inversion for faint K/M dwarfs.
    teff_for_est = (query.stellar_teff or star.get("teff"))
    rstar_known = (query.stellar_radius_sun or star.get("radius")) is not None
    mstar_known = (query.stellar_mass_sun or star.get("mass")) is not None
    stellar_estimate_used = None
    if teff_for_est and (not rstar_known or not mstar_known):
        from .habitability import estimate_stellar_from_teff
        est = estimate_stellar_from_teff(teff_for_est)
        if est:
            if not rstar_known:
                star["radius"] = est["radius_sun"]
            if not mstar_known:
                star["mass"] = est["mass_sun"]
            stellar_estimate_used = est

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

    # Resolve an absolute companion mass to feed the size sub-score (density).
    # Priority: explicit override -> archive RV semi-amplitude -> none.
    if query.planet_mass_earth is not None:
        planet.mass_earth = query.planet_mass_earth
        planet.mass_source = "provided"
    elif query.use_archive_rv and planet.orbital_period_d and planet.stellar_mass_sun:
        from .rv_fetch import fetch_rv_from_archive
        from .tlcm_geometry import planet_mass_from_rv
        rv = fetch_rv_from_archive(query.tic_id)
        if rv.get("available") and rv.get("k_ms"):
            period_rv = rv.get("orbital_period_d") or planet.orbital_period_d
            ecc = rv.get("eccentricity") or 0.0
            comp = planet_mass_from_rv(
                rv["k_ms"], period_rv, planet.stellar_mass_sun,
                inclination_deg=90.0, e=ecc,   # transiting => edge-on
            )
            planet.mass_earth = comp["mp_earth"]
            planet.mass_source = "RV (archive)"

    # TLCM transit geometry: model-independent stellar density and photometric
    # semi-major axis.  When DVT parameters are available we pass the SPOC
    # DV-fitted a/R★ (ARAT) and impact parameter directly, replacing the
    # b=0 central-transit assumption used in the BLS-only path.  This gives
    # a cleaner a_au_photometric and therefore a cleaner semi-major axis.
    from .tlcm_geometry import compute_tlcm_geometry
    vv = query.vetting_verdict or {}

    # Duration: prefer DVT (hours → days), then shape measure, then BLS
    dvt_dur_h = vv.get("_dvt_duration_h")
    t14_d = (dvt_dur_h / 24.0) if dvt_dur_h else (
        vv.get("_t14_d") or vv.get("_duration") or vv.get("_bls_duration")
    )
    # Depth: prefer DVT depth_frac, then event/BLS depth
    depth_frac = (
        vv.get("_dvt_depth_frac")
        or vv.get("_depth")
        or vv.get("_bls_depth")
    )
    tlcm_geo = compute_tlcm_geometry(
        period_d=planet.orbital_period_d,
        t14_d=t14_d,
        depth_frac=depth_frac,
        rstar_sun=planet.stellar_radius_sun,
        impact_parameter=vv.get("_dvt_impact_b"),   # fitted b from SPOC DV
        a_over_rs_spoc=vv.get("_dvt_a_over_rs"),    # fitted ARAT from SPOC DV
        grazing=str(vv.get("_shape_class", "")).startswith("V"),
        mstar_sun=planet.stellar_mass_sun,
    ).to_dict()

    # Density-driven MS fallback for the case where even Teff was missing
    # (i.e. neither catalogues nor the Teff fallback above produced R*/M*).
    # Mirrors the rule used in /api/observables.
    density_estimate_used = None
    rho_sun = tlcm_geo.get("stellar_density_rho_sun")
    if (rho_sun
            and planet.stellar_radius_sun is None
            and planet.stellar_mass_sun is None
            and planet.stellar_teff is None):
        from .habitability import estimate_stellar_from_density
        dens_est = estimate_stellar_from_density(rho_sun)
        if dens_est:
            planet.stellar_radius_sun = dens_est["radius_sun"]
            planet.stellar_mass_sun = dens_est["mass_sun"]
            planet.stellar_teff = dens_est["teff"]
            density_estimate_used = dens_est

    a_source = "exofop/override"
    if planet.semi_major_axis_au is None and tlcm_geo.get("a_au_photometric"):
        # Prefer the photometric a — independent of the catalogue stellar mass.
        planet.semi_major_axis_au = tlcm_geo["a_au_photometric"]
        a_source = "TLCM photometric (a/Rs x R*)"
    elif planet.semi_major_axis_au is None and planet.orbital_period_d and planet.stellar_mass_sun:
        # Fall back to Kepler's third law from period + stellar mass.
        from .observables import semi_major_axis_from_period
        planet.semi_major_axis_au = semi_major_axis_from_period(
            planet.orbital_period_d, planet.stellar_mass_sun
        )
        a_source = "Kepler III (P + M*)"

    # When no measured mass is available, estimate it from the radius with
    # every M-R relation so the spread propagates into the HCI as a range.
    mass_estimates = None
    if planet.mass_earth is None and planet.radius_earth:
        from .observables import MR_RELATIONS
        mass_estimates = [
            fn(planet.radius_earth)["mp_earth"] for fn in MR_RELATIONS.values()
        ]
        planet.mass_source = "M–R relations"

    hci_result = compute_hci(
        planet=planet,
        vetting_verdict=query.vetting_verdict,
        n_sectors_with_detections=query.n_sectors_with_detections,
        n_sectors_observed=query.n_sectors_observed,
        mass_estimates_earth=mass_estimates,
        a_over_rs=tlcm_geo.get("a_over_rs"),
        radius_ratio_k=tlcm_geo.get("radius_ratio_k"),
        stellar_density_rho_sun=tlcm_geo.get("stellar_density_rho_sun"),
    )

    # POE predicted observables for this candidate (insolation, RV, etc.)
    from .observables import compute_observables as _compute_obs
    poe = _compute_obs(
        teff_k=planet.stellar_teff,
        rstar_sun=planet.stellar_radius_sun,
        mstar_sun=planet.stellar_mass_sun,
        distance_pc=star.get("distance"),
        orbital_period_d=planet.orbital_period_d,
        semi_major_axis_au=planet.semi_major_axis_au,
        rp_earth=planet.radius_earth,
    ).to_dict()

    # Surface the backfill provenance in the POE caveats so the PDF report
    # shows the same "estimated from Teff" / "distance from Gaia" notes the
    # standalone observables panel does.
    if gaia_used:
        poe.setdefault("caveats", []).append(
            "Distance / Teff backfilled from Gaia DR3 (GSP-Phot)."
        )
    if stellar_estimate_used:
        poe.setdefault("caveats", []).append(
            f"Catalogues had only Teff for this target; R*={stellar_estimate_used['radius_sun']} Rsun "
            f"and M*={stellar_estimate_used['mass_sun']} Msun were estimated from Teff using "
            f"{stellar_estimate_used['method']} ({stellar_estimate_used['sptype']})."
        )
    if density_estimate_used:
        poe.setdefault("caveats", []).append(
            f"No catalogue Teff; R*={density_estimate_used['radius_sun']} Rsun, "
            f"M*={density_estimate_used['mass_sun']} Msun and Teff={density_estimate_used['teff']} K "
            f"were inferred from the transit-derived stellar density "
            f"(Seager & Mallen-Ornelas 2003 + Pecaut & Mamajek 2013, {density_estimate_used['sptype']})."
        )

    return {
        "hci": hci_result.to_dict(),
        "observables": poe,
        "tlcm": tlcm_geo,
        "semi_major_axis_source": a_source,
        "planet": {
            "radius_earth": planet.radius_earth,
            "mass_earth": planet.mass_earth,
            "mass_source": planet.mass_source,
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


def _attach_hci_summary_to_plots(result, *, dvt: Optional[dict] = None) -> None:
    """Compute the HCI summary PNG for ``result`` and stash it into
    ``result.plots['hci_summary']`` so the frontend ZIP exporter picks it
    up without a separate /api/habitability round-trip.

    Fails soft: any error (missing TIC, ExoFOP outage, image-render failure)
    just leaves ``plots`` untouched.
    """
    tic = getattr(result.star, "tic_id", None)
    if not tic:
        return
    try:
        from .dvt_fetch import best_tce as _best_tce
        tce = _best_tce(dvt)
        dvt_period = tce.get("period_d") if tce else None
        period = (result.bls.get("period") if result.bls else None)
        if dvt_period:
            period = dvt_period

        enriched_verdict = dict(result.verdict or {})
        enriched_verdict.update(
            {
                "_depth": (result.events[0]["depth"] if result.events else None),
                "_bls_depth": result.bls.get("depth") if result.bls else None,
                "_t14_d": result.shape.get("t14_d") if result.shape else None,
                "_bls_duration": result.bls.get("duration") if result.bls else None,
                "_shape_class": result.shape.get("shape_class") if result.shape else None,
                "_events": result.events,
                "_dvt_period_d": dvt_period,
                "_dvt_duration_h": tce.get("duration_h") if tce else None,
                "_dvt_depth_frac": tce.get("depth_frac") if tce else None,
                "_dvt_impact_b": tce.get("impact_b") if tce else None,
                "_dvt_a_over_rs": tce.get("a_over_rs") if tce else None,
            }
        )
        n_det = 1 if result.summary.get("n_events_detected", 0) > 0 else 0
        hq = HabitabilityQuery(
            tic_id=tic,
            orbital_period_d=period,
            stellar_teff=result.star.teff,
            stellar_radius_sun=result.star.radius,
            stellar_mass_sun=getattr(result.star, "mass", None),
            R_companion_Rjup=(result.physics or {}).get("R_companion_Rjup"),
            n_sectors_with_detections=n_det,
            n_sectors_observed=1,
            vetting_verdict=enriched_verdict,
        )
        bundle = _habitability_bundle(hq)
        _attach_hci_image(bundle, title=f"Habitability Chance Index — TIC {tic}")
        if bundle.get("hci_image"):
            result.plots["hci_summary"] = bundle["hci_image"]
    except Exception as e:
        log.warning("HCI summary attach failed for TIC %s: %s", tic, e)


def _attach_hci_image(bundle: dict, title: Optional[str] = None) -> dict:
    """Render the combined HCI/observables/TLCM summary PNG into the bundle."""
    try:
        from .hci_image import make_hci_summary_image
        bundle["hci_image"] = make_hci_summary_image(
            bundle.get("hci"),
            bundle.get("observables"),
            bundle.get("tlcm"),
            planet=bundle.get("planet"),
            title=title,
        )
    except Exception as e:  # never let image rendering break the response
        log.warning("HCI summary image failed: %s", e)
        bundle["hci_image"] = None
    return bundle


@app.post("/api/habitability")
async def habitability(query: HabitabilityQuery):
    bundle = _habitability_bundle(query)
    tic = query.tic_id
    return _attach_hci_image(bundle, title=f"Habitability Chance Index — TIC {tic}")


def _build_report_extras(
    result,
    n_sectors_observed: int = 1,
    n_sectors_with_detections: Optional[int] = None,
    period_override: Optional[float] = None,
    dvt: Optional[dict] = None,
) -> dict:
    """Server-side recompute of HCI, observables, TLCM and ExoMiner for a
    finished VettingResult, so the PDF (and the multi-sector panel) can embed
    *all current analyses* without the frontend forwarding its panel state.

    ``dvt`` is the parsed DVT dict from ``dvt_fetch.fetch_dvt``.  When
    present its TCE-0 parameters (period, duration, depth, impact parameter,
    and the SPOC-fitted a/R★) are propagated into the enriched verdict and
    used by ``_habitability_bundle`` for a cleaner TLCM geometry and
    semi-major axis.

    Every step fails safe: a missing TIC, an offline catalogue, or an
    ExoMiner error simply omits that block.
    """
    extras: dict = {"hci_bundle": None, "exominer": None, "ffi_cutout": None}
    star = result.star
    tic = getattr(star, "tic_id", None)
    period = period_override or (result.bls.get("period") if result.bls else None)

    # Extract the best DVT TCE parameters (None when DVT unavailable).
    from .dvt_fetch import best_tce as _best_tce
    tce = _best_tce(dvt)
    dvt_period = tce.get("period_d") if tce else None
    # DVT period is more precise (multi-sector fold) — prefer it over BLS.
    if dvt_period and period_override is None:
        period = dvt_period

    # --- HCI + observables + TLCM (needs a TIC to query ExoFOP) ---------
    if tic:
        try:
            enriched_verdict = dict(result.verdict or {})
            enriched_verdict.update(
                {
                    "_depth": (result.events[0]["depth"] if result.events else None),
                    "_bls_depth": result.bls.get("depth") if result.bls else None,
                    "_t14_d": result.shape.get("t14_d") if result.shape else None,
                    "_bls_duration": result.bls.get("duration") if result.bls else None,
                    "_shape_class": result.shape.get("shape_class") if result.shape else None,
                    "_events": result.events,
                    # DVT-derived parameters (None when DVT unavailable)
                    "_dvt_period_d": dvt_period,
                    "_dvt_duration_h": tce.get("duration_h") if tce else None,
                    "_dvt_depth_frac": tce.get("depth_frac") if tce else None,
                    "_dvt_impact_b": tce.get("impact_b") if tce else None,
                    "_dvt_a_over_rs": tce.get("a_over_rs") if tce else None,
                }
            )
            if n_sectors_with_detections is not None:
                n_det = n_sectors_with_detections
            else:
                n_det = 1 if result.summary.get("n_events_detected", 0) > 0 else 0
            hq = HabitabilityQuery(
                tic_id=tic,
                orbital_period_d=period,
                stellar_teff=star.teff,
                stellar_radius_sun=star.radius,
                stellar_mass_sun=getattr(star, "mass", None),
                R_companion_Rjup=(result.physics or {}).get("R_companion_Rjup"),
                n_sectors_with_detections=n_det,
                n_sectors_observed=n_sectors_observed,
                vetting_verdict=enriched_verdict,
            )
            bundle = _habitability_bundle(hq)
            _attach_hci_image(bundle, title=f"Habitability Chance Index — TIC {tic}")
            extras["hci_bundle"] = bundle
        except Exception as e:
            log.warning("Report HCI recompute failed for TIC %s: %s", tic, e)

    # --- ExoMiner (needs the cached light curve + a period) -------------
    try:
        key = (tic, star.sector) if tic else "__last__"
        cached = _lc_cache.get(key) or _lc_cache.get("__last__")
        t0 = result.bls.get("t0") if result.bls else None
        duration = result.bls.get("duration") if result.bls else None
        if cached and period and duration:
            extras["exominer"] = _run_exominer(
                t=cached["t"],
                f=cached["f"],
                mom_x=cached.get("mom_x"),
                mom_y=cached.get("mom_y"),
                period=period,
                t0=t0 if t0 is not None else 0.0,
                duration=duration,
                crowdsap=getattr(star, "crowdsap", None),
            )
    except Exception as e:
        log.warning("Report ExoMiner recompute failed: %s", e)

    # --- FFI cutout (needs coordinates; fetched/cached from TESScut) -----
    try:
        if getattr(star, "ra", None) is not None and getattr(star, "dec", None) is not None:
            extras["ffi_cutout"] = _get_ffi_cutout(
                tic, star.sector, star.ra, star.dec
            )
    except Exception as e:
        log.warning("Report FFI cutout failed: %s", e)

    return extras


@app.post("/api/mast/multisector")
async def mast_multisector(query: MultisectorQuery):
    result, info = _run_mast_multisector(query)
    _attach_hci_summary_to_plots(result, dvt=info.get("dvt"))
    out = result.to_dict()
    out["mast"] = {
        "sectors_used": info.get("sectors_used", []),
        "sectors_attempted": info.get("sectors_attempted", 0),
        "sectors_succeeded": info.get("sectors_succeeded", 0),
        "errors": info.get("errors", []),
    }
    out["lightcurve"] = _downsample_cached_lc(result.star.tic_id, None)
    out["dvt"] = _summarize_dvt(info.get("dvt"))
    return out


@app.post("/api/mast/multisector/report")
async def mast_multisector_report(query: MultisectorQuery):
    """Build a PDF report from the stitched multi-sector light curve — uses
    the single-sector report layout because multi-sector is now just one
    long lightcurve through the same pipeline."""
    result, info = _run_mast_multisector(query)
    try:
        dvt = info.get("dvt")
        extras = _build_report_extras(
            result,
            n_sectors_observed=len(info.get("sectors_used", [])) or 1,
            dvt=dvt,
        )
        pdf = build_pdf(
            result,
            hci_bundle=extras.get("hci_bundle"),
            exominer=extras.get("exominer"),
            ffi_cutout=extras.get("ffi_cutout"),
            dvt=dvt,
        )
    except Exception as e:
        raise _handle_exception("build_pdf", e)

    fname = f"vetting_TIC{query.tic_id}_multisector.pdf"
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


def _run_mast_multisector(query: MultisectorQuery):
    """Fetch each requested sector's lightcurve, stitch them together by
    BJD/BTJD, and run the standard single-sector pipeline once on the
    combined series.

    Stitching by time (rather than running the pipeline per sector and
    reconciling afterwards) keeps peak RAM under ~512 MB: only the raw
    arrays survive each iteration, and each FITS file is deleted as soon
    as it's parsed. Returns ``(VettingResult, info_dict)``.
    """
    from .mast_fetch import list_available_sectors, fetch_spoc_lightcurve

    try:
        all_sectors = list_available_sectors(query.tic_id)
    except Exception as e:
        raise HTTPException(502, f"MAST sector list failed: {e}")

    if not all_sectors:
        raise HTTPException(404, f"No TESS sectors found for TIC {query.tic_id}.")

    from .pipeline import MAX_SECTORS

    if query.sectors:
        wanted = set(int(s) for s in query.sectors)
        sectors_to_fetch = [s for s in all_sectors if s["sector"] in wanted][:MAX_SECTORS]
    else:
        sectors_to_fetch = all_sectors[-MAX_SECTORS:]

    t_parts: list = []
    flux_parts: list = []
    err_parts: list = []
    qual_parts: list = []
    momx_parts: list = []
    momy_parts: list = []
    rep_star = None
    sectors_used: list = []
    errors: list = []

    for sec_info in sectors_to_fetch:
        sec_num = sec_info["sector"]
        path = None
        try:
            fetched = fetch_spoc_lightcurve(query.tic_id, sec_num)
            path = fetched.get("path")
            parsed = parse_upload(path, fetched["filename"])
            if parsed.get("t") is None or parsed.get("flux") is None:
                raise RuntimeError("parser returned no time series")
            t_parts.append(np.asarray(parsed["t"], dtype=float))
            flux_parts.append(np.asarray(parsed["flux"], dtype=float))
            n = len(parsed["t"])
            err = parsed.get("flux_err")
            err_parts.append(
                np.asarray(err, dtype=float) if err is not None
                else np.full(n, np.nan)
            )
            q = parsed.get("quality")
            qual_parts.append(
                np.asarray(q, dtype=int) if q is not None else np.zeros(n, dtype=int)
            )
            mx = parsed.get("mom_x")
            my = parsed.get("mom_y")
            momx_parts.append(
                np.asarray(mx, dtype=float) if mx is not None
                else np.full(n, np.nan)
            )
            momy_parts.append(
                np.asarray(my, dtype=float) if my is not None
                else np.full(n, np.nan)
            )
            if rep_star is None:
                rep_star = parsed.get("star")
            sectors_used.append(sec_num)
            del parsed
        except Exception as e:
            log.warning("Sector %s failed for TIC %s: %s", sec_num, query.tic_id, e)
            errors.append({"sector": sec_num, "error": str(e)})
        finally:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    if not sectors_used:
        raise HTTPException(502, f"All sector fetches failed. Errors: {errors}")

    t_all = np.concatenate(t_parts)
    flux_all = np.concatenate(flux_parts)
    err_all = np.concatenate(err_parts)
    qual_all = np.concatenate(qual_parts)
    momx_all = np.concatenate(momx_parts)
    momy_all = np.concatenate(momy_parts)
    # Free the per-sector arrays before allocating the sorted copies.
    t_parts.clear(); flux_parts.clear(); err_parts.clear()
    qual_parts.clear(); momx_parts.clear(); momy_parts.clear()

    order = np.argsort(t_all, kind="mergesort")
    parsed_all = {
        "t": t_all[order],
        "flux": flux_all[order],
        "flux_err": err_all[order],
        "quality": qual_all[order],
        "mom_x": momx_all[order],
        "mom_y": momy_all[order],
        "star": rep_star,
    }

    result = _run_pipeline(
        parsed_all,
        query.detect_threshold, query.detect_min_snr,
        high_variability=query.high_variability,
        rotation_period_days=query.rotation_period_days,
        secondary_sigma=query.secondary_sigma,
        odd_even_sigma=query.odd_even_sigma,
        known_period_days=query.known_period_days,
    )
    _cache_lc(parsed_all)

    dvt = None
    try:
        from .dvt_fetch import fetch_dvt
        dvt = fetch_dvt(query.tic_id, None)
    except Exception as _e:
        log.warning("DVT fetch skipped for TIC %s (multisector): %s", query.tic_id, _e)

    info = {
        "sectors_used": sectors_used,
        "sectors_attempted": len(sectors_to_fetch),
        "sectors_succeeded": len(sectors_used),
        "errors": errors,
        "dvt": dvt,
    }
    return result, info


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


@app.post("/api/ffi_cutout")
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
# Predicted Observables for Exoplanets (POE)
# -------------------------------------------------
from .observables import compute_observables, RSUN_TO_RJUP as _RSUN_TO_RJUP


class ObservablesQuery(BaseModel):
    # Optional: auto-fill stellar params from ExoFOP/TIC for an object
    tic_id: Optional[int] = None
    # Stellar (override / user-specified)
    stellar_teff: Optional[float] = None
    stellar_radius_sun: Optional[float] = None
    stellar_mass_sun: Optional[float] = None
    luminosity_lsun: Optional[float] = None
    distance_pc: Optional[float] = None
    # Orbit (supply either period or a; the other is derived via Kepler III)
    orbital_period_d: Optional[float] = None
    semi_major_axis_au: Optional[float] = None
    # Planet (supply radius in any of these; mass optional)
    rp_rjup: Optional[float] = None
    rp_earth: Optional[float] = None
    transit_depth_frac: Optional[float] = None
    mp_mjup: Optional[float] = None
    inclination_deg: float = 90.0
    eccentricity: float = 0.0
    # TLCM transit geometry inputs (Csizmadia 2020)
    t14_d: Optional[float] = None            # full transit duration (days)
    impact_parameter: Optional[float] = None # b; if absent, central transit assumed
    grazing: bool = False
    k_rv_ms: Optional[float] = None          # RV semi-amplitude -> absolute mass
    mr_relation: str = "chen_kipping"        # or "powerlaw" (mass from radius)
    # Auto-detected mode: pull period/depth from a vetting result
    vetting_verdict: Optional[dict] = None


@app.post("/api/observables")
async def observables(query: ObservablesQuery):
    teff = query.stellar_teff
    rstar = query.stellar_radius_sun
    mstar = query.stellar_mass_sun
    dist = query.distance_pc
    period = query.orbital_period_d
    depth = query.transit_depth_frac
    rp_rjup = query.rp_rjup

    # Auto-fill stellar params from ExoFOP/TIC when a TIC is given. Also
    # cross-match to Gaia DR3 so distance (and Teff/radius when GSP-Phot has
    # them) populates for targets that ExoFOP/TIC v8 leave bare — without
    # this, HZ-mas / astrometric / max projected separation always read
    # "needs distance" even for well-observed Gaia sources.
    exofop_source = None
    gaia_used = False
    if query.tic_id is not None:
        star = {}
        try:
            exofop = query_exofop(query.tic_id)
            star = exofop.get("star", {}) or {}
            exofop_source = exofop.get("source")
        except Exception as e:
            log.warning("ExoFOP lookup failed for TIC %s: %s", query.tic_id, e)
        try:
            from .tic_catalog import backfill_star
            star, _used_tic = backfill_star(star, query.tic_id)
        except Exception as e:
            log.warning("TIC v8 backfill failed for TIC %s: %s", query.tic_id, e)
        try:
            from .gaia_catalog import backfill_star_gaia
            star, gaia_used = backfill_star_gaia(star, star.get("ra"), star.get("dec"))
        except Exception as e:
            log.warning("Gaia DR3 backfill failed for TIC %s: %s", query.tic_id, e)
        teff = teff if teff is not None else star.get("teff")
        rstar = rstar if rstar is not None else star.get("radius")
        mstar = mstar if mstar is not None else star.get("mass")
        dist = dist if dist is not None else star.get("distance")

    # Last-resort fallback: if Teff is the only stellar parameter we have
    # (common for faint TIC targets — e.g. TIC 330014070, where both ExoFOP
    # and TIC v8 carry only Tmag), estimate R* and M* from the Pecaut &
    # Mamajek main-sequence relation so the observables panel can populate
    # luminosity, HZ, insolation, RV K, etc. The values are flagged as
    # estimates via a caveat in the response.
    stellar_estimate_used = None
    if teff is not None and (rstar is None or mstar is None):
        from .habitability import estimate_stellar_from_teff
        est = estimate_stellar_from_teff(teff)
        if est:
            if rstar is None:
                rstar = est["radius_sun"]
            if mstar is None:
                mstar = est["mass_sun"]
            stellar_estimate_used = est

    # Auto-detected: harvest period / depth / Rp from a vetting verdict blob.
    # SPOC DV-fitted values (DVT) take precedence over BLS estimates so the
    # standalone observables panel shows the same cleaner a/Rs and semi-major
    # axis as the HCI/PDF paths.
    t14 = query.t14_d
    grazing = query.grazing
    impact_parameter = query.impact_parameter
    a_over_rs_spoc = None
    if query.vetting_verdict:
        vv = query.vetting_verdict
        dvt_dur_h = vv.get("_dvt_duration_h")
        period = period or vv.get("_dvt_period_d") or vv.get("_bls_period") or vv.get("_period")
        depth = depth or vv.get("_dvt_depth_frac") or vv.get("_depth") or vv.get("_bls_depth")
        t14 = (
            t14
            or (dvt_dur_h / 24.0 if dvt_dur_h else None)
            or vv.get("_t14_d") or vv.get("_duration") or vv.get("_bls_duration")
        )
        if impact_parameter is None and vv.get("_dvt_impact_b") is not None:
            impact_parameter = vv.get("_dvt_impact_b")
        a_over_rs_spoc = vv.get("_dvt_a_over_rs")
        if not grazing and str(vv.get("_shape_class", "")).startswith("V"):
            grazing = True
        if rp_rjup is None and vv.get("_R_companion_Rjup") is not None:
            rp_rjup = vv.get("_R_companion_Rjup")

    # TLCM transit geometry: radius ratio, a/Rs, model-independent stellar
    # density, photometric semi-major axis, and (with RV) absolute mass.
    # When a SPOC DV a/R* (ARAT) is available it replaces the b=0 duration route.
    from .tlcm_geometry import compute_tlcm_geometry
    tlcm = compute_tlcm_geometry(
        period_d=period,
        t14_d=t14,
        depth_frac=depth,
        rstar_sun=rstar,
        impact_parameter=impact_parameter,
        a_over_rs_spoc=a_over_rs_spoc,
        grazing=grazing,
        k_rv_ms=query.k_rv_ms,
        eccentricity=query.eccentricity,
        mstar_sun=mstar,
        inclination_deg=query.inclination_deg,
    ).to_dict()

    # Density-driven fallback for when Teff is also missing. TLCM gives a
    # model-independent ρ★ (Seager & Mallen-Ornelas 2003) from a/Rs and P
    # alone; inverting it against the Pecaut & Mamajek MS sequence yields
    # R★/M★ when the catalogues had nothing. Only runs when Teff was not
    # available either — catalogue Teff is the safer anchor when both
    # exist, because the density inversion is biased by the b=0 a/Rs
    # assumption and by any non-MS evolution of the host.
    density_estimate_used = None
    rho_sun = tlcm.get("stellar_density_rho_sun")
    if rho_sun and rstar is None and mstar is None and teff is None:
        from .habitability import estimate_stellar_from_density
        dens_est = estimate_stellar_from_density(rho_sun)
        if dens_est:
            rstar = dens_est["radius_sun"]
            mstar = dens_est["mass_sun"]
            if teff is None:
                teff = dens_est["teff"]
            density_estimate_used = dens_est

    # Prefer the light-curve-derived (photometric) semi-major axis when the
    # user did not supply one: it does not depend on a catalogue stellar mass.
    a_au = query.semi_major_axis_au
    a_source = "user/Kepler"
    if a_au is None and tlcm.get("a_au_photometric"):
        a_au = tlcm["a_au_photometric"]
        a_source = "TLCM photometric (a/Rs x R*)"
    # If geometry pinned an absolute companion mass from RV, use it.
    mp_mjup = query.mp_mjup
    if mp_mjup is None and tlcm.get("radial_velocity", {}).get("mp_mjup"):
        mp_mjup = tlcm["radial_velocity"]["mp_mjup"]

    result = compute_observables(
        teff_k=teff,
        rstar_sun=rstar,
        mstar_sun=mstar,
        luminosity_lsun_override=query.luminosity_lsun,
        distance_pc=dist,
        orbital_period_d=period,
        semi_major_axis_au=a_au,
        rp_rjup=rp_rjup,
        rp_earth=query.rp_earth,
        transit_depth_frac=depth,
        mp_mjup=mp_mjup,
        inclination_deg=query.inclination_deg,
        eccentricity=query.eccentricity,
        mr_relation=query.mr_relation,
    )
    out = result.to_dict()
    out["tlcm"] = tlcm
    out["semi_major_axis_source"] = a_source
    out["exofop_source"] = exofop_source
    if stellar_estimate_used:
        out["stellar_estimate"] = stellar_estimate_used
        out["caveats"].append(
            f"Catalogues had only Teff for this target; R*={stellar_estimate_used['radius_sun']} Rsun "
            f"and M*={stellar_estimate_used['mass_sun']} Msun were estimated from Teff using "
            f"{stellar_estimate_used['method']} ({stellar_estimate_used['sptype']})."
        )
    if density_estimate_used:
        out["density_estimate"] = density_estimate_used
        out["caveats"].append(
            f"No catalogue Teff; R*={density_estimate_used['radius_sun']} Rsun, "
            f"M*={density_estimate_used['mass_sun']} Msun and Teff={density_estimate_used['teff']} K "
            f"were inferred from the transit-derived stellar density "
            f"(Seager & Mallen-Ornelas 2003 + Pecaut & Mamajek 2013, {density_estimate_used['sptype']})."
        )
    if gaia_used:
        out["caveats"].append(
            "Distance / Teff backfilled from Gaia DR3 (GSP-Phot)."
        )
    return out


# -------------------------------------------------
# Radial velocity -> absolute mass (mass function)
# -------------------------------------------------
class RVQuery(BaseModel):
    # Archive-first: resolve K (and orbit/stellar params) from a TIC ...
    tic_id: Optional[int] = None
    # ... or supply them directly (upload / manual fallback):
    orbital_period_d: Optional[float] = None
    stellar_mass_sun: Optional[float] = None
    inclination_deg: float = 90.0
    eccentricity: float = 0.0
    k_ms: Optional[float] = None              # direct semi-amplitude, OR ...
    rv_values_ms: Optional[list] = None       # ... an RV time series (min/max)
    rv_reduce_method: str = "minmax"


@app.post("/api/rv")
async def radial_velocity(query: RVQuery):
    from .tlcm_geometry import (
        reduce_rv_timeseries, planet_mass_from_rv, rv_mass_function_sun,
    )
    from .rv_fetch import fetch_rv_from_archive

    k_ms = query.k_ms
    period = query.orbital_period_d
    e = query.eccentricity
    inc = query.inclination_deg
    mstar = query.stellar_mass_sun
    reduction = None
    archive = None
    source = "manual"

    # Manual / uploaded RV time series takes precedence when given.
    if k_ms is None and query.rv_values_ms:
        reduction = reduce_rv_timeseries(query.rv_values_ms, query.rv_reduce_method)
        if reduction.get("available"):
            k_ms = reduction["K_estimate"]
            source = "rv_timeseries_upload"

    # Otherwise try the NASA Exoplanet Archive, then fall back to upload.
    if k_ms is None and query.tic_id is not None:
        archive = fetch_rv_from_archive(query.tic_id)
        if archive.get("available"):
            k_ms = archive["k_ms"]
            source = archive["source"]
            period = period or archive.get("orbital_period_d")
            if query.eccentricity == 0.0 and archive.get("eccentricity") is not None:
                e = archive["eccentricity"]
            if inc == 90.0 and archive.get("inclination_deg") is not None:
                inc = archive["inclination_deg"]
            mstar = mstar or archive.get("stellar_mass_sun")

    if k_ms is None:
        return {
            "available": False,
            "source": "none",
            "archive": archive,
            "fallback": "upload",
            "detail": "No catalog RV semi-amplitude found; upload an RV time "
                      "series (rv_values_ms) or supply k_ms directly.",
        }
    if period is None:
        raise HTTPException(
            status_code=400,
            detail="orbital_period_d is required to compute the mass function.",
        )

    out = {
        "available": True,
        "source": source,
        "K_ms": k_ms,
        "orbital_period_d": period,
        "eccentricity": e,
        "inclination_deg": inc,
        "rv_reduction": reduction,
        "archive": archive,
        "mass_function_msun": rv_mass_function_sun(k_ms, period, e),
    }
    if mstar:
        out["companion"] = planet_mass_from_rv(k_ms, period, mstar, inc, e)
    return out


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
    from .dvt_fetch import best_tce
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
