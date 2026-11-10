"""The HCI + predicted-observables + TLCM bundle for one target.

One place builds it, so the site (page, PDF, multi-sector) and the Pi front
end score the same target the same way. Front ends call
``hci_bundle_for_result`` with a finished ``VettingResult``, or
``build_hci_bundle`` with plain parameters (the /api/habitability route, the
Pi ``habitability`` sub-command).
"""
from __future__ import annotations

import logging
from typing import Optional

from .exofop import query_exofop
from .habitability import (
    PlanetCandidate,
    compute_hci,
    estimate_stellar_from_density,
    estimate_stellar_from_teff,
)
from .locks import PLOT_LOCK

log = logging.getLogger("vetting")

R_SUN_IN_R_EARTH = 109.076   # IAU nominal R_sun / R_earth (equatorial)
R_JUP_IN_R_EARTH = 11.209

# A Lomb-Scargle peak is taken as the rotation period only when it is strong,
# long enough to be spots rather than pulsation, and not the transit itself.
ROTATION_MIN_POWER = 0.05      # standard-normalised LS power
ROTATION_MAX_FAP = 1e-3
ROTATION_MIN_PERIOD_D = 0.5
ROTATION_HARMONIC_TOL = 0.05   # fractional distance from P_orb x {1/2, 1, 2}


def resolve_planet_radius(
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
        return r_companion_rjup * R_JUP_IN_R_EARTH, "pipeline (R_companion_Rjup, discrete dip)"
    return None, None


def pick_best_toi(tois: list) -> Optional[dict]:
    """First confirmed/known planet, else the last candidate, else the first TOI."""
    best = None
    for t in tois or []:
        d = (t.get("disposition") or "").upper()
        if d in ("PC", "APC", "CP", "KP") or best is None:
            best = t
            if d in ("CP", "KP"):
                break
    return best


def rotation_period_from_result(result) -> tuple:
    """(period_days, source) for gyrochronology, or (None, None).

    A rotation period the user gave for the variability detrend wins.
    Otherwise the top Lomb-Scargle peak is used when it passes the
    ``ROTATION_*`` gates above.
    """
    det = getattr(result, "detrend", None) or {}
    if det.get("source") == "user_period" and det.get("period_days"):
        return float(det["period_days"]), "user rotation period"

    ls = getattr(result, "lomb_scargle", None) or {}
    p, power, fap = ls.get("top_period"), ls.get("top_power"), ls.get("false_alarm_prob")
    if not p or power is None or fap is None:
        return None, None
    if p < ROTATION_MIN_PERIOD_D or power < ROTATION_MIN_POWER or fap > ROTATION_MAX_FAP:
        return None, None
    p_orb = (getattr(result, "bls", None) or {}).get("period")
    if p_orb:
        for h in (0.5, 1.0, 2.0):
            if abs(p - h * p_orb) / (h * p_orb) < ROTATION_HARMONIC_TOL:
                return None, None
    return float(p), "Lomb-Scargle peak"


def build_hci_bundle(
    tic_id: int,
    *,
    radius_earth: Optional[float] = None,
    semi_major_axis_au: Optional[float] = None,
    orbital_period_d: Optional[float] = None,
    stellar_teff: Optional[float] = None,
    stellar_radius_sun: Optional[float] = None,
    stellar_mass_sun: Optional[float] = None,
    r_companion_rjup: Optional[float] = None,
    planet_mass_earth: Optional[float] = None,
    use_archive_rv: bool = True,
    vetting_verdict: Optional[dict] = None,
    age_gyr: Optional[float] = None,
    rotation_period_d: Optional[float] = None,
    rotation_source: Optional[str] = None,
    exofop: Optional[dict] = None,
) -> dict:
    """Compute the full HCI + observables + TLCM bundle for a target.

    Explicit arguments override catalogue values. ``exofop`` is an
    already-fetched ``query_exofop`` result (prefetch). ``vetting_verdict``
    is the pipeline verdict, optionally enriched with ``_depth``, ``_t14_d``,
    ``_bls_*``, ``_shape_class`` and the SPOC DV ``_dvt_*`` keys.
    """
    if exofop is None:
        try:
            exofop = query_exofop(tic_id)
        except Exception as e:
            log.warning("ExoFOP query failed for TIC %s: %s", tic_id, e)
            exofop = {"star": {}, "tois": [], "source": "unavailable"}

    # Copy: the backfills below mutate the star dict.
    star = dict(exofop.get("star", {}) or {})
    tois = exofop.get("tois", []) or []

    # ExoFOP often returns empty stellar fields, and FFI/QLP headers lack
    # TEFF/RADIUS/LOGG. Walk the same backfill chain /api/observables uses.
    try:
        from .tic_catalog import backfill_star
        star, _used_tic = backfill_star(star, tic_id)
    except Exception as e:
        log.warning("TIC v8 backfill failed for TIC %s: %s", tic_id, e)
    gaia_used = False
    try:
        from .gaia_catalog import backfill_star_gaia
        star, gaia_used = backfill_star_gaia(star, star.get("ra"), star.get("dec"))
    except Exception as e:
        log.warning("Gaia DR3 backfill failed for TIC %s: %s", tic_id, e)

    # Teff -> Pecaut & Mamajek main-sequence fallback for missing R*/M*.
    # Runs before PlanetCandidate is built so TLCM (which needs R*) and the
    # downstream POE call both see the estimated values, and before density
    # is computed because density-from-Teff is much more reliable than the
    # b=0 density inversion for faint K/M dwarfs.
    teff_for_est = stellar_teff or star.get("teff")
    rstar_known = (stellar_radius_sun or star.get("radius")) is not None
    mstar_known = (stellar_mass_sun or star.get("mass")) is not None
    stellar_estimate_used = None
    if teff_for_est and (not rstar_known or not mstar_known):
        est = estimate_stellar_from_teff(teff_for_est)
        if est:
            if not rstar_known:
                star["radius"] = est["radius_sun"]
            if not mstar_known:
                star["mass"] = est["mass_sun"]
            stellar_estimate_used = est

    best_toi = pick_best_toi(tois)

    # Radius is resolved after the TLCM geometry below, so it can use the
    # same Rp/R* that the report prints.
    planet = PlanetCandidate(
        radius_earth=None,
        # Only a catalogue-measured a; a Kepler III value derived by the
        # ExoFOP parser would pre-empt the TLCM photometric a below.
        semi_major_axis_au=(
            semi_major_axis_au
            or (best_toi.get("semi_major_axis_au")
                if best_toi and best_toi.get("semi_major_axis_source") == "exofop"
                else None)
        ),
        orbital_period_d=orbital_period_d or (best_toi.get("period_d") if best_toi else None),
        toi_number=best_toi.get("toi_number") if best_toi else None,
        disposition=best_toi.get("disposition") if best_toi else None,
        stellar_teff=stellar_teff or star.get("teff"),
        stellar_radius_sun=stellar_radius_sun or star.get("radius"),
        stellar_mass_sun=stellar_mass_sun or star.get("mass"),
        depth_ppm=best_toi.get("depth_ppm") if best_toi else None,
        duration_hr=best_toi.get("duration_hr") if best_toi else None,
        source=exofop.get("source", "unknown"),
        age_gyr=age_gyr or star.get("age_gyr"),
        rotation_period_d=rotation_period_d,
        rotation_source=rotation_source,
    )

    # Resolve an absolute companion mass to feed the size sub-score (density).
    # Priority: explicit override -> archive RV semi-amplitude -> none.
    if planet_mass_earth is not None:
        planet.mass_earth = planet_mass_earth
        planet.mass_source = "provided"
    elif use_archive_rv and planet.orbital_period_d and planet.stellar_mass_sun:
        from .rv_fetch import fetch_rv_from_archive
        from .tlcm_geometry import planet_mass_from_rv
        rv = fetch_rv_from_archive(tic_id)
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
    # semi-major axis. When DVT parameters are available we pass the SPOC
    # DV-fitted a/R* (ARAT) and impact parameter directly, replacing the
    # b=0 central-transit assumption used in the BLS-only path.
    from .tlcm_geometry import compute_tlcm_geometry
    vv = vetting_verdict or {}

    # Duration: prefer DVT (hours -> days), then shape measure, then BLS.
    dvt_dur_h = vv.get("_dvt_duration_h")
    t14_d = (dvt_dur_h / 24.0) if dvt_dur_h else (
        vv.get("_t14_d") or vv.get("_duration") or vv.get("_bls_duration")
    )
    depth_frac = vv.get("_dvt_depth_frac") or vv.get("_depth") or vv.get("_bls_depth")
    if depth_frac is None and planet.depth_ppm:
        depth_frac = planet.depth_ppm / 1e6      # no light curve: ExoFOP depth
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
    # (neither catalogues nor the Teff fallback above produced R*/M*).
    density_estimate_used = None
    rho_sun = tlcm_geo.get("stellar_density_rho_sun")
    if (rho_sun
            and planet.stellar_radius_sun is None
            and planet.stellar_mass_sun is None
            and planet.stellar_teff is None):
        dens_est = estimate_stellar_from_density(rho_sun)
        if dens_est:
            planet.stellar_radius_sun = dens_est["radius_sun"]
            planet.stellar_mass_sun = dens_est["mass_sun"]
            planet.stellar_teff = dens_est["teff"]
            density_estimate_used = dens_est

    planet.radius_earth, radius_source = resolve_planet_radius(
        override_earth=radius_earth,
        radius_ratio_k=tlcm_geo.get("radius_ratio_k"),
        rstar_sun=planet.stellar_radius_sun,
        has_dvt=bool(vv.get("_dvt_rprs") or vv.get("_dvt_depth_frac")),
        catalog_radius_earth=best_toi.get("radius_earth") if best_toi else None,
        r_companion_rjup=r_companion_rjup,
    )

    a_source = "exofop/override"
    if planet.semi_major_axis_au is None and tlcm_geo.get("a_au_photometric"):
        # Prefer the photometric a — independent of the catalogue stellar mass.
        planet.semi_major_axis_au = tlcm_geo["a_au_photometric"]
        a_source = "TLCM photometric (a/Rs x R*)"
    elif planet.semi_major_axis_au is None and planet.orbital_period_d and planet.stellar_mass_sun:
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
        vetting_verdict=vetting_verdict,
        mass_estimates_earth=mass_estimates,
        a_over_rs=tlcm_geo.get("a_over_rs"),
        radius_ratio_k=tlcm_geo.get("radius_ratio_k"),
        stellar_density_rho_sun=tlcm_geo.get("stellar_density_rho_sun"),
    )

    # POE predicted observables for this candidate (insolation, RV, etc.)
    from .observables import compute_observables
    poe = compute_observables(
        teff_k=planet.stellar_teff,
        rstar_sun=planet.stellar_radius_sun,
        mstar_sun=planet.stellar_mass_sun,
        distance_pc=star.get("distance"),
        orbital_period_d=planet.orbital_period_d,
        semi_major_axis_au=planet.semi_major_axis_au,
        rp_earth=planet.radius_earth,
    ).to_dict()

    # Surface the backfill provenance in the POE caveats so the PDF report
    # shows the same notes the standalone observables panel does.
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

    hci = hci_result.to_dict()
    return {
        "hci": hci,
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
            "age_gyr": (hci.get("system_age") or {}).get("age_gyr"),
            "age_source": (hci.get("system_age") or {}).get("source"),
        },
        "exofop_source": exofop.get("source"),
        "all_tois": tois,
    }


def attach_hci_image(bundle: dict, title: Optional[str] = None) -> dict:
    """Render the combined HCI/observables/TLCM summary PNG into the bundle."""
    try:
        from .hci_image import make_hci_summary_image
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


def enriched_verdict(result, dvt: Optional[dict] = None) -> dict:
    """The pipeline verdict plus the depth/duration/shape and SPOC DV values
    the TLCM geometry reads."""
    from .dvt_fetch import best_tce
    tce = best_tce(dvt) or {}
    bls = result.bls or {}
    shape = result.shape or {}
    out = dict(result.verdict or {})
    out.update({
        "_depth": (result.events[0]["depth"] if result.events else None),
        "_bls_depth": bls.get("depth"),
        "_t14_d": shape.get("t14_d"),
        "_bls_duration": bls.get("duration"),
        "_shape_class": shape.get("shape_class"),
        "_events": result.events,
        # DVT-derived parameters (None when DVT unavailable)
        "_dvt_period_d": tce.get("period_d"),
        "_dvt_duration_h": tce.get("duration_h"),
        "_dvt_depth_frac": tce.get("depth_frac"),
        "_dvt_impact_b": tce.get("impact_b"),
        "_dvt_a_over_rs": tce.get("a_over_rs"),
        "_dvt_rprs": tce.get("rprs"),
    })
    return out


def hci_bundle_for_result(
    result,
    *,
    dvt: Optional[dict] = None,
    period_override: Optional[float] = None,
    exofop: Optional[dict] = None,
    age_gyr: Optional[float] = None,
    use_archive_rv: bool = True,
) -> Optional[dict]:
    """HCI bundle (with its summary PNG) for a finished VettingResult, or
    None without a TIC or on any failure.

    ``dvt`` is the parsed DVT dict from ``dvt_fetch.fetch_dvt``; its best
    TCE's period, duration, depth, impact parameter and a/R* give a cleaner
    TLCM geometry. ``exofop`` is a prefetched ``query_exofop`` result.
    """
    tic = getattr(result.star, "tic_id", None)
    if not tic:
        return None
    try:
        verdict = enriched_verdict(result, dvt)
        period = period_override or (result.bls.get("period") if result.bls else None)
        # DVT period is more precise (multi-sector fold) — prefer it over BLS.
        if verdict.get("_dvt_period_d") and period_override is None:
            period = verdict["_dvt_period_d"]
        p_rot, rot_src = rotation_period_from_result(result)
        bundle = build_hci_bundle(
            tic,
            orbital_period_d=period,
            stellar_teff=result.star.teff,
            stellar_radius_sun=result.star.radius,
            stellar_mass_sun=getattr(result.star, "mass", None),
            r_companion_rjup=(result.physics or {}).get("R_companion_Rjup"),
            vetting_verdict=verdict,
            age_gyr=age_gyr,
            rotation_period_d=p_rot,
            rotation_source=rot_src,
            use_archive_rv=use_archive_rv,
            exofop=exofop,
        )
        return attach_hci_image(bundle, title=f"Habitability Chance Index — TIC {tic}")
    except Exception as e:
        log.warning("HCI bundle failed for TIC %s: %s", tic, e)
        return None
