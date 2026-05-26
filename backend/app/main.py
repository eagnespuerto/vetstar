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


@app.post("/api/habitability")
async def habitability(query: HabitabilityQuery):
    try:
        exofop = query_exofop(query.tic_id)
    except Exception as e:
        log.warning("ExoFOP query failed for TIC %s: %s", query.tic_id, e)
        exofop = {"star": {}, "tois": [], "source": "unavailable"}

    star = exofop.get("star", {})
    tois = exofop.get("tois", [])

    # ExoFOP often returns empty stellar fields, and FFI/QLP headers lack
    # TEFF/RADIUS/LOGG. Backfill from TIC v8 so POE/physics/density can run.
    from .tic_catalog import backfill_star
    star, _used_tic = backfill_star(star, query.tic_id)

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

    # TLCM transit geometry: a model-independent stellar density and a
    # photometric semi-major axis (a/Rs x R*) that needs no catalogue mass.
    from .tlcm_geometry import compute_tlcm_geometry
    vv = query.vetting_verdict or {}
    tlcm_geo = compute_tlcm_geometry(
        period_d=planet.orbital_period_d,
        t14_d=vv.get("_t14_d") or vv.get("_duration") or vv.get("_bls_duration"),
        depth_frac=vv.get("_depth") or vv.get("_bls_depth"),
        rstar_sun=planet.stellar_radius_sun,
        grazing=str(vv.get("_shape_class", "")).startswith("V"),
        mstar_sun=planet.stellar_mass_sun,
    ).to_dict()

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

    # Auto-fill stellar params from ExoFOP/TIC when a TIC is given
    exofop_source = None
    if query.tic_id is not None:
        try:
            exofop = query_exofop(query.tic_id)
            star = exofop.get("star", {}) or {}
            exofop_source = exofop.get("source")
            from .tic_catalog import backfill_star
            star, _used_tic = backfill_star(star, query.tic_id)
            teff = teff if teff is not None else star.get("teff")
            rstar = rstar if rstar is not None else star.get("radius")
            mstar = mstar if mstar is not None else star.get("mass")
            dist = dist if dist is not None else star.get("distance")
        except Exception as e:
            log.warning("ExoFOP lookup failed for TIC %s: %s", query.tic_id, e)

    # Auto-detected: harvest period / depth / Rp from a vetting verdict blob
    t14 = query.t14_d
    grazing = query.grazing
    if query.vetting_verdict:
        vv = query.vetting_verdict
        period = period or vv.get("_bls_period") or vv.get("_period")
        depth = depth or vv.get("_depth") or vv.get("_bls_depth")
        t14 = t14 or vv.get("_t14_d") or vv.get("_duration") or vv.get("_bls_duration")
        if not grazing and str(vv.get("_shape_class", "")).startswith("V"):
            grazing = True
        if rp_rjup is None and vv.get("_R_companion_Rjup") is not None:
            rp_rjup = vv.get("_R_companion_Rjup")

    # TLCM transit geometry: radius ratio, a/Rs, model-independent stellar
    # density, photometric semi-major axis, and (with RV) absolute mass.
    from .tlcm_geometry import compute_tlcm_geometry
    tlcm = compute_tlcm_geometry(
        period_d=period,
        t14_d=t14,
        depth_frac=depth,
        rstar_sun=rstar,
        impact_parameter=query.impact_parameter,
        grazing=grazing,
        k_rv_ms=query.k_rv_ms,
        eccentricity=query.eccentricity,
        mstar_sun=mstar,
        inclination_deg=query.inclination_deg,
    ).to_dict()

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
