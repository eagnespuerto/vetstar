"""Habitability Chance Index, planet-radius resolution and the extra
sections (HCI, ExoMiner, DVT, FFI) that go into PDF reports.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from vetstar_core.exofop import query_exofop
from vetstar_core.exominer import run_exominer as _run_exominer
from vetstar_core.habitability import PlanetCandidate, compute_hci

from ..cache import _get_ffi_cutout, _lookup_lc
from ..prefetch import PLOT_LOCK

log = logging.getLogger("vetting")

router = APIRouter()

# -------------------------------------------------
# Habitability + multi-sector endpoints
# -------------------------------------------------


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


R_SUN_IN_R_EARTH = 109.076   # IAU nominal R_sun / R_earth (equatorial)


def _resolve_planet_radius(
    *,
    override_earth: Optional[float] = None,
    radius_ratio_k: Optional[float] = None,
    rstar_sun: Optional[float] = None,
    has_dvt: bool = False,
    catalog_radius_earth: Optional[float] = None,
    r_companion_rjup: Optional[float] = None,
) -> tuple:
    """Pick the planet radius (R_earth) and say where it came from.

    ``radius_ratio_k`` is the Rp/R* the TLCM geometry (and the ExoFOP block)
    reports: the SPOC DV RPRS fit, else sqrt(DV depth), else sqrt(pipeline
    depth). The radius must be k * R* so the two printed values agree.

    Priority: explicit override -> SPOC DV k * R* -> ExoFOP catalogue radius
    -> pipeline k * R* -> discrete-dip R_companion_Rjup. The discrete-dip
    value comes from the single deepest dilution-corrected event, which can
    be several times the phase-folded depth, so it is the last resort.
    """
    if override_earth is not None:
        return override_earth, "override"
    k_radius = (
        radius_ratio_k * rstar_sun * R_SUN_IN_R_EARTH
        if radius_ratio_k and rstar_sun else None
    )
    if k_radius is not None and has_dvt:
        return k_radius, f"SPOC DV (Rp/R*={radius_ratio_k:.4f}, R*={rstar_sun:.2f} R_sun)"
    if catalog_radius_earth is not None:
        return catalog_radius_earth, "exofop"
    if k_radius is not None:
        return k_radius, f"pipeline depth (Rp/R*={radius_ratio_k:.4f}, R*={rstar_sun:.2f} R_sun)"
    if r_companion_rjup is not None:
        return r_companion_rjup * 11.209, "pipeline (R_companion_Rjup, discrete dip)"
    return None, None


def _habitability_bundle(query: HabitabilityQuery, exofop: Optional[dict] = None) -> dict:
    """Compute the full HCI + observables + TLCM bundle for a target.

    Factored out of the /api/habitability route so the PDF report flow can
    reuse exactly the same computation server-side (no frontend plumbing).
    ``exofop`` is an already-fetched ``query_exofop`` result (prefetch).
    """
    if exofop is None:
        try:
            exofop = query_exofop(query.tic_id)
        except Exception as e:
            log.warning("ExoFOP query failed for TIC %s: %s", query.tic_id, e)
            exofop = {"star": {}, "tois": [], "source": "unavailable"}

    # Copy: the backfills below mutate the star dict.
    star = dict(exofop.get("star", {}))
    tois = exofop.get("tois", [])

    # ExoFOP often returns empty stellar fields, and FFI/QLP headers lack
    # TEFF/RADIUS/LOGG. Walk the same backfill chain /api/observables uses
    # so the HCI bundle (and therefore the PDF report) gets the same
    # populated stellar parameters as the standalone observables panel.
    try:
        from vetstar_core.tic_catalog import backfill_star
        star, _used_tic = backfill_star(star, query.tic_id)
    except Exception as e:
        log.warning("TIC v8 backfill failed for TIC %s: %s", query.tic_id, e)
    gaia_used = False
    try:
        from vetstar_core.gaia_catalog import backfill_star_gaia
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
        from vetstar_core.habitability import estimate_stellar_from_teff
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

    # Radius is resolved after the TLCM geometry below, so it can use the
    # same Rp/R* that the report prints.
    planet = PlanetCandidate(
        radius_earth=None,
        # Only a catalogue-measured a; a Kepler III value derived by the
        # ExoFOP parser would pre-empt the TLCM photometric a below.
        semi_major_axis_au=(
            query.semi_major_axis_au
            or (best_toi.get("semi_major_axis_au")
                if best_toi and best_toi.get("semi_major_axis_source") == "exofop"
                else None)
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
        from vetstar_core.rv_fetch import fetch_rv_from_archive
        from vetstar_core.tlcm_geometry import planet_mass_from_rv
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
    from vetstar_core.tlcm_geometry import compute_tlcm_geometry
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
        radius_ratio_spoc=vv.get("_dvt_rprs"),      # fitted RPRS from SPOC DV
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
        from vetstar_core.habitability import estimate_stellar_from_density
        dens_est = estimate_stellar_from_density(rho_sun)
        if dens_est:
            planet.stellar_radius_sun = dens_est["radius_sun"]
            planet.stellar_mass_sun = dens_est["mass_sun"]
            planet.stellar_teff = dens_est["teff"]
            density_estimate_used = dens_est

    planet.radius_earth, radius_source = _resolve_planet_radius(
        override_earth=query.radius_earth,
        radius_ratio_k=tlcm_geo.get("radius_ratio_k"),
        rstar_sun=planet.stellar_radius_sun,
        has_dvt=bool(vv.get("_dvt_rprs") or vv.get("_dvt_depth_frac")),
        catalog_radius_earth=best_toi.get("radius_earth") if best_toi else None,
        r_companion_rjup=query.R_companion_Rjup,
    )

    a_source = "exofop/override"
    if planet.semi_major_axis_au is None and tlcm_geo.get("a_au_photometric"):
        # Prefer the photometric a — independent of the catalogue stellar mass.
        planet.semi_major_axis_au = tlcm_geo["a_au_photometric"]
        a_source = "TLCM photometric (a/Rs x R*)"
    elif planet.semi_major_axis_au is None and planet.orbital_period_d and planet.stellar_mass_sun:
        # Fall back to Kepler's third law from period + stellar mass.
        from vetstar_core.observables import semi_major_axis_from_period
        planet.semi_major_axis_au = semi_major_axis_from_period(
            planet.orbital_period_d, planet.stellar_mass_sun
        )
        a_source = "Kepler III (P + M*)"

    # When no measured mass is available, estimate it from the radius with
    # every M-R relation so the spread propagates into the HCI as a range.
    mass_estimates = None
    if planet.mass_earth is None and planet.radius_earth:
        from vetstar_core.observables import MR_RELATIONS
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
    from vetstar_core.observables import compute_observables as _compute_obs
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


def _hci_bundle_for_result(
    result,
    *,
    dvt: Optional[dict] = None,
    n_sectors_observed: int = 1,
    n_sectors_with_detections: Optional[int] = None,
    period_override: Optional[float] = None,
    exofop: Optional[dict] = None,
) -> Optional[dict]:
    """HCI + observables + TLCM bundle (with its summary PNG) for a finished
    VettingResult, or None without a TIC or on any failure.

    ``dvt`` is the parsed DVT dict from ``dvt_fetch.fetch_dvt``. When
    present its TCE-0 parameters (period, duration, depth, impact parameter,
    and the SPOC-fitted a/R★) are propagated into the enriched verdict for a
    cleaner TLCM geometry and semi-major axis. ``exofop`` is a prefetched
    ``query_exofop`` result.
    """
    tic = getattr(result.star, "tic_id", None)
    if not tic:
        return None
    try:
        from vetstar_core.dvt_fetch import best_tce as _best_tce
        tce = _best_tce(dvt)
        dvt_period = tce.get("period_d") if tce else None
        period = period_override or (result.bls.get("period") if result.bls else None)
        # DVT period is more precise (multi-sector fold) — prefer it over BLS.
        if dvt_period and period_override is None:
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
                # DVT-derived parameters (None when DVT unavailable)
                "_dvt_period_d": dvt_period,
                "_dvt_duration_h": tce.get("duration_h") if tce else None,
                "_dvt_depth_frac": tce.get("depth_frac") if tce else None,
                "_dvt_impact_b": tce.get("impact_b") if tce else None,
                "_dvt_a_over_rs": tce.get("a_over_rs") if tce else None,
                "_dvt_rprs": tce.get("rprs") if tce else None,
            }
        )
        if n_sectors_with_detections is None:
            n_sectors_with_detections = (
                1 if result.summary.get("n_events_detected", 0) > 0 else 0
            )
        hq = HabitabilityQuery(
            tic_id=tic,
            orbital_period_d=period,
            stellar_teff=result.star.teff,
            stellar_radius_sun=result.star.radius,
            stellar_mass_sun=getattr(result.star, "mass", None),
            R_companion_Rjup=(result.physics or {}).get("R_companion_Rjup"),
            n_sectors_with_detections=int(n_sectors_with_detections),
            n_sectors_observed=int(n_sectors_observed),
            vetting_verdict=enriched_verdict,
        )
        bundle = _habitability_bundle(hq, exofop=exofop)
        return _attach_hci_image(bundle, title=f"Habitability Chance Index — TIC {tic}")
    except Exception as e:
        log.warning("HCI bundle failed for TIC %s: %s", tic, e)
        return None


def _attach_hci_summary_to_plots(result, **kwargs) -> Optional[dict]:
    """Compute the HCI bundle for ``result`` and stash its summary PNG into
    ``result.plots['hci_summary']`` so the frontend ZIP exporter picks it
    up without a separate /api/habitability round-trip. Returns the bundle
    so the PDF can reuse it. Fails soft: leaves ``plots`` untouched.
    """
    bundle = _hci_bundle_for_result(result, **kwargs)
    if bundle and bundle.get("hci_image"):
        result.plots["hci_summary"] = bundle["hci_image"]
    return bundle


def _attach_hci_image(bundle: dict, title: Optional[str] = None) -> dict:
    """Render the combined HCI/observables/TLCM summary PNG into the bundle."""
    try:
        from vetstar_core.hci_image import make_hci_summary_image
        with PLOT_LOCK:
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


@router.post("/api/habitability")
def habitability(query: HabitabilityQuery):
    bundle = _habitability_bundle(query)
    tic = query.tic_id
    return _attach_hci_image(bundle, title=f"Habitability Chance Index — TIC {tic}")


_UNSET = object()


def _build_report_extras(
    result,
    n_sectors_observed: int = 1,
    n_sectors_with_detections: Optional[int] = None,
    period_override: Optional[float] = None,
    dvt: Optional[dict] = None,
    hci_bundle: Optional[dict] = None,
    ffi_cutout=_UNSET,
) -> dict:
    """Server-side recompute of HCI, observables, TLCM and ExoMiner for a
    finished VettingResult, so the PDF (and the multi-sector panel) can embed
    *all current analyses* without the frontend forwarding its panel state.

    Pass ``hci_bundle`` / ``ffi_cutout`` when the caller already has them
    (the vet-and-report job computes the HCI for the page and prefetches
    the cutout) to skip the recompute / TESScut fetch.

    Every step fails safe: a missing TIC, an offline catalogue, or an
    ExoMiner error simply omits that block.
    """
    extras: dict = {"hci_bundle": None, "exominer": None, "ffi_cutout": None}
    star = result.star
    tic = getattr(star, "tic_id", None)
    period = period_override or (result.bls.get("period") if result.bls else None)

    from vetstar_core.dvt_fetch import best_tce as _best_tce
    tce = _best_tce(dvt)
    dvt_period = tce.get("period_d") if tce else None
    if dvt_period and period_override is None:
        period = dvt_period

    # --- HCI + observables + TLCM (needs a TIC to query ExoFOP) ---------
    extras["hci_bundle"] = hci_bundle or _hci_bundle_for_result(
        result,
        dvt=dvt,
        n_sectors_observed=n_sectors_observed,
        n_sectors_with_detections=n_sectors_with_detections,
        period_override=period_override,
    )

    # --- ExoMiner (needs the cached light curve + a period) -------------
    try:
        cached = _lookup_lc(tic, star.sector)
        t0 = result.bls.get("t0") if result.bls else None
        duration = result.bls.get("duration") if result.bls else None
        if cached and period and duration:
            with PLOT_LOCK:
                extras["exominer"] = _run_exominer(
                    t=cached["t"],
                    f=cached["f"],
                    mom_x=cached.get("mom_x"),
                    mom_y=cached.get("mom_y"),
                    period=period,
                    t0=t0 if t0 is not None else 0.0,
                    duration=duration,
                    crowdsap=getattr(star, "crowdsap", None),
                    events=result.events,
                )
    except Exception as e:
        log.warning("Report ExoMiner recompute failed: %s", e)

    # --- FFI cutout (needs coordinates; fetched/cached from TESScut) -----
    if ffi_cutout is not _UNSET:
        extras["ffi_cutout"] = ffi_cutout
    else:
        try:
            if getattr(star, "ra", None) is not None and getattr(star, "dec", None) is not None:
                extras["ffi_cutout"] = _get_ffi_cutout(
                    tic, star.sector, star.ra, star.dec
                )
        except Exception as e:
            log.warning("Report FFI cutout failed: %s", e)

    return extras
