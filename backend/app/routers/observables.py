"""Predicted Observables for Exoplanets (POE) and the radial-velocity
mass function.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from vetstar_core.exofop import query_exofop
from vetstar_core.observables import compute_observables

from .habitability import _resolve_planet_radius

log = logging.getLogger("vetting")

router = APIRouter()

# -------------------------------------------------
# Predicted Observables for Exoplanets (POE)
# -------------------------------------------------


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


@router.post("/api/observables")
def observables(query: ObservablesQuery):
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
            from vetstar_core.tic_catalog import backfill_star
            star, _used_tic = backfill_star(star, query.tic_id)
        except Exception as e:
            log.warning("TIC v8 backfill failed for TIC %s: %s", query.tic_id, e)
        try:
            from vetstar_core.gaia_catalog import backfill_star_gaia
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
        from vetstar_core.habitability import estimate_stellar_from_teff
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
    rprs_spoc = None
    r_companion_rjup = None
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
        rprs_spoc = vv.get("_dvt_rprs")
        if not grazing and str(vv.get("_shape_class", "")).startswith("V"):
            grazing = True
        # Discrete-dip radius: last resort only (see _resolve_planet_radius).
        r_companion_rjup = vv.get("_R_companion_Rjup")

    # TLCM transit geometry: radius ratio, a/Rs, model-independent stellar
    # density, photometric semi-major axis, and (with RV) absolute mass.
    # When a SPOC DV a/R* (ARAT) is available it replaces the b=0 duration route.
    from vetstar_core.tlcm_geometry import compute_tlcm_geometry
    tlcm = compute_tlcm_geometry(
        period_d=period,
        t14_d=t14,
        depth_frac=depth,
        rstar_sun=rstar,
        impact_parameter=impact_parameter,
        a_over_rs_spoc=a_over_rs_spoc,
        radius_ratio_spoc=rprs_spoc,
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
        from vetstar_core.habitability import estimate_stellar_from_density
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

    # Radius from the same Rp/R* the TLCM block reports; compute_observables
    # would otherwise fall back to sqrt(depth) and ignore a DV RPRS fit.
    if rp_rjup is None and query.rp_earth is None:
        rp_earth_k, _ = _resolve_planet_radius(
            radius_ratio_k=tlcm.get("radius_ratio_k"),
            rstar_sun=rstar,
            r_companion_rjup=r_companion_rjup,
        )
        if rp_earth_k is not None:
            rp_rjup = rp_earth_k / 11.209

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


@router.post("/api/rv")
def radial_velocity(query: RVQuery):
    from vetstar_core.rv_fetch import fetch_rv_from_archive
    from vetstar_core.tlcm_geometry import (
        planet_mass_from_rv,
        reduce_rv_timeseries,
        rv_mass_function_sun,
    )

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
