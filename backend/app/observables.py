"""
Predicted Observables for Exoplanets (POE).

Implements the equations from NASA Exoplanet Archive's POE tool
("How the Predicted Observables for Exoplanets are Calculated", NExScI,
last updated 2016-11-02):

  * Stellar luminosity            L*/L_sun = (R*/R_sun)^2 (Teff/T_sun)^4
  * Habitable-zone radii          recent-Venus / centre / early-Mars,
                                  scaled by sqrt(L), in AU and (if d known) mas
  * Maximum projected separation  MPPS = 3600 * 180/pi * arctan(a/d)
  * Orbital period (Kepler III)   P/yr = sqrt((a/AU)^3 (M_sun/M*))
  * Insolation flux               S/S_earth = (L*/L_sun)(AU/a)^2
  * Radial-velocity semi-amp K    Cumming-style, period in days, exact in Mp
  * Astrometric semi-amp          Δθ/μas = 954.3 (Mp/Mjup)/(M*/Msun) (a/AU)/(d/pc)
  * Transit depth                 δ% = 1.049 (Rp/Rjup / (R*/Rsun))^2, capped 100

Planet mass, when not supplied, is estimated from radius using the
Chen & Kipping (2017) "Forecaster" mass–radius relation (Terran and
Neptunian branches; the Jovian branch is radius-degenerate and flagged).

All functions are pure: numeric in, numeric out. ``compute_observables``
is the driver used by the API.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Physical constants / unit conversions
# ---------------------------------------------------------------------------
T_SUN_K = 5772.0          # IAU nominal solar effective temperature
AU_M = 1.495978707e11
PC_M = 3.0856775814913673e16
RSUN_TO_RJUP = 9.73       # matches pipeline.physics_interpretation
RJUP_TO_REARTH = 11.209
MJUP_TO_MEARTH = 317.828

# POE habitable-zone coefficients (Kasting et al. 1993, as used by POE)
HZ_INNER_COEF = 0.75      # recent Venus
HZ_CENTER_COEF = 1.0      # Earth-equivalent
HZ_OUTER_COEF = 1.77      # early Mars

# Chen & Kipping (2017) Forecaster power-law branches (Earth units)
CK_TERRAN_C, CK_TERRAN_EXP = 1.008, 0.2790      # R = C M^exp, M <= 2.04 M_E
CK_TERRAN_MMAX = 2.04                            # M_E
CK_NEPT_EXP = 0.589
CK_NEPT_MMAX_MJUP = 0.414                        # Neptunian -> Jovian transition


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------
@dataclass
class ObservablesResult:
    inputs: dict = field(default_factory=dict)
    luminosity_lsun: Optional[float] = None
    habitable_zone: dict = field(default_factory=dict)
    orbit: dict = field(default_factory=dict)        # a, P, derivation
    insolation_searth: Optional[float] = None
    planet: dict = field(default_factory=dict)       # Rp, Mp, mass source
    radial_velocity: dict = field(default_factory=dict)
    astrometric: dict = field(default_factory=dict)
    transit: dict = field(default_factory=dict)
    max_projected_separation_arcsec: Optional[float] = None
    caveats: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Individual equations
# ---------------------------------------------------------------------------
def luminosity_lsun(teff_k: float, rstar_sun: float) -> float:
    """L*/L_sun = (R*/R_sun)^2 (Teff/T_sun)^4."""
    return (rstar_sun ** 2) * ((teff_k / T_SUN_K) ** 4)


def habitable_zone(l_ratio: float, distance_pc: Optional[float] = None) -> dict:
    """HZ radii in AU (and mas if distance given), scaled by sqrt(L)."""
    sq = math.sqrt(max(l_ratio, 0.0))
    inner = HZ_INNER_COEF * sq
    center = HZ_CENTER_COEF * sq
    outer = HZ_OUTER_COEF * sq
    width = outer - inner
    out = {
        "inner_au": inner,
        "center_au": center,
        "outer_au": outer,
        "width_au": width,
    }
    if distance_pc and distance_pc > 0:
        # POE: RHZ/mas = 1000 * (RHZ/AU) * (pc/d)
        out.update(
            inner_mas=1000.0 * inner / distance_pc,
            center_mas=1000.0 * center / distance_pc,
            outer_mas=1000.0 * outer / distance_pc,
            width_mas=1000.0 * width / distance_pc,
        )
    return out


def semi_major_axis_from_period(period_d: float, mstar_sun: float) -> float:
    """Invert Kepler III: a/AU = (P_yr^2 * M*/M_sun)^(1/3)."""
    p_yr = period_d / 365.25
    return (p_yr ** 2 * mstar_sun) ** (1.0 / 3.0)


def period_from_semi_major_axis(a_au: float, mstar_sun: float) -> float:
    """Kepler III: P_yr = sqrt((a/AU)^3 (M_sun/M*)); returns days."""
    p_yr = math.sqrt((a_au ** 3) / mstar_sun)
    return p_yr * 365.25


def insolation_searth(l_ratio: float, a_au: float) -> float:
    """S/S_earth = (L*/L_sun)(AU/a)^2."""
    return l_ratio * (1.0 / a_au) ** 2


def rv_semi_amplitude_ms(
    period_d: float,
    mp_mjup: float,
    mstar_sun: float,
    inclination_deg: float = 90.0,
    eccentricity: float = 0.0,
) -> float:
    """
    POE radial-velocity semi-amplitude (m/s):

        K = 203 (P/day)^(-1/3) (Mp/Mjup sin i)
              / ((M*/Msun) + 9.548e-4 (Mp/Mjup))^(2/3) / sqrt(1 - e^2)
    """
    sin_i = math.sin(math.radians(inclination_deg))
    denom = (mstar_sun + 9.548e-4 * mp_mjup) ** (2.0 / 3.0)
    ecc = math.sqrt(max(1.0 - eccentricity ** 2, 1e-12))
    return 203.0 * period_d ** (-1.0 / 3.0) * (mp_mjup * sin_i) / denom / ecc


def rv_semi_amplitude_clubb_ms(
    period_d: float,
    mp_mjup: float,
    mstar_sun: float,
    inclination_deg: float = 90.0,
    eccentricity: float = 0.0,
) -> float:
    """
    Textbook closed form from Clubb (2008), "A Detailed Derivation of the
    Radial Velocity Equation" (page-34 reference, days + Jupiter-mass form):

        K = 203.255 m/s (1 day / P)^{1/3} (Mp sin i / Mjup)
              (Msun / M*)^{2/3} / sqrt(1 - e^2)

    This makes the Mp << M* approximation (eq. 57), so it differs from the
    exact POE form (`rv_semi_amplitude_ms`) by <~0.1% in the planetary regime.
    Provided as an independent cross-check / teaching reference. Other
    published constants: 0.6395 (day, Earth-mass), 0.0895 (yr, Earth-mass),
    28.435 (yr, Jupiter-mass).
    """
    sin_i = math.sin(math.radians(inclination_deg))
    ecc = math.sqrt(max(1.0 - eccentricity ** 2, 1e-12))
    return (
        203.255
        * (1.0 / period_d) ** (1.0 / 3.0)
        * (mp_mjup * sin_i)
        * (1.0 / mstar_sun) ** (2.0 / 3.0)
        / ecc
    )


def astrometric_semi_amplitude_uas(
    mp_mjup: float, mstar_sun: float, a_au: float, distance_pc: float
) -> float:
    """Δθ/μas = 954.3 (Mp/Mjup)/(M*/Msun) (a/AU)/(d/pc)."""
    return 954.3 * (mp_mjup / mstar_sun) * (a_au / distance_pc)


def transit_depth_pct(rp_rjup: float, rstar_sun: float) -> float:
    """δ% = 1.049 (Rp/Rjup / (R*/Rsun))^2, capped at 100 when Rp >= R*."""
    rp_rsun = rp_rjup / RSUN_TO_RJUP
    if rp_rsun >= rstar_sun:
        return 100.0
    return 1.049 * (rp_rjup / rstar_sun) ** 2


def max_projected_separation_arcsec(a_au: float, distance_pc: float) -> float:
    """MPPS = 3600 * (180/pi) * arctan(a/d), a and d in metres."""
    a_m = a_au * AU_M
    d_m = distance_pc * PC_M
    return 3600.0 * (180.0 / math.pi) * math.atan(a_m / d_m)


def estimate_mass_from_radius(rp_earth: float) -> dict:
    """
    Chen & Kipping (2017) Forecaster, inverted (radius -> mass).

    Terran and Neptunian branches are monotonic and invertible. Above the
    Neptunian/Jovian transition (~0.414 M_Jup) radius is nearly independent
    of mass, so mass is reported as degenerate (boundary value, flagged).
    """
    r_terran_max = CK_TERRAN_C * CK_TERRAN_MMAX ** CK_TERRAN_EXP  # ~1.23 R_E
    # Neptunian normalisation, continuous at the Terran boundary
    c_nept = r_terran_max / (CK_TERRAN_MMAX ** CK_NEPT_EXP)
    nept_mmax_me = CK_NEPT_MMAX_MJUP * MJUP_TO_MEARTH            # ~131.6 M_E
    r_nept_max = c_nept * nept_mmax_me ** CK_NEPT_EXP            # ~14 R_E

    if rp_earth <= r_terran_max:
        m = (rp_earth / CK_TERRAN_C) ** (1.0 / CK_TERRAN_EXP)
        return {"mp_earth": m, "branch": "Terran", "degenerate": False}
    if rp_earth <= r_nept_max:
        m = (rp_earth / c_nept) ** (1.0 / CK_NEPT_EXP)
        return {"mp_earth": m, "branch": "Neptunian", "degenerate": False}
    # Jovian / degenerate: radius does not constrain mass
    return {
        "mp_earth": nept_mmax_me,
        "branch": "Jovian (radius-degenerate)",
        "degenerate": True,
    }


def estimate_mass_powerlaw(rp_earth: float) -> dict:
    """
    Simple piecewise power-law mass-radius relation (Earth units), the quick
    estimate from the practical TIC->RV pipeline note:

        Rp < 1.5 R_E : Mp = Rp^3.7          (rocky)
        Rp < 4   R_E : Mp = 2.69 Rp^0.93     (Weiss & Marcy 2014-style)
        else         : Mp = 0.5 Rp^1.5       (volatile/giant, rough)

    Coarser than Chen & Kipping but a useful independent comparison; the
    relations diverge most in the sub-Neptune range.
    """
    if rp_earth < 1.5:
        m, branch = rp_earth ** 3.7, "rocky (Rp^3.7)"
    elif rp_earth < 4.0:
        m, branch = 2.69 * rp_earth ** 0.93, "intermediate (2.69 Rp^0.93)"
    else:
        m, branch = 0.5 * rp_earth ** 1.5, "giant (0.5 Rp^1.5)"
    return {"mp_earth": m, "branch": branch, "degenerate": False}


# Registry so callers can select a relation by name.
MR_RELATIONS = {
    "chen_kipping": estimate_mass_from_radius,
    "powerlaw": estimate_mass_powerlaw,
}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def compute_observables(
    *,
    teff_k: Optional[float] = None,
    rstar_sun: Optional[float] = None,
    mstar_sun: Optional[float] = None,
    luminosity_lsun_override: Optional[float] = None,
    distance_pc: Optional[float] = None,
    orbital_period_d: Optional[float] = None,
    semi_major_axis_au: Optional[float] = None,
    rp_rjup: Optional[float] = None,
    rp_earth: Optional[float] = None,
    transit_depth_frac: Optional[float] = None,
    mp_mjup: Optional[float] = None,
    inclination_deg: float = 90.0,
    eccentricity: float = 0.0,
    mr_relation: str = "chen_kipping",
) -> ObservablesResult:
    """
    Compute the full POE observable set. Every output is best-effort: when a
    required input is missing, that observable is left as ``None`` and a
    caveat is recorded. Angular quantities require ``distance_pc``.
    """
    res = ObservablesResult()
    res.inputs = {
        "teff_k": teff_k,
        "rstar_sun": rstar_sun,
        "mstar_sun": mstar_sun,
        "distance_pc": distance_pc,
        "orbital_period_d": orbital_period_d,
        "semi_major_axis_au": semi_major_axis_au,
        "inclination_deg": inclination_deg,
        "eccentricity": eccentricity,
    }
    cav = res.caveats

    # --- Luminosity ---
    l_ratio = luminosity_lsun_override
    if l_ratio is None and teff_k and rstar_sun:
        l_ratio = luminosity_lsun(teff_k, rstar_sun)
    res.luminosity_lsun = l_ratio
    if l_ratio is None:
        cav.append("Luminosity needs Teff and R* (or an explicit L*).")

    # --- Habitable zone ---
    if l_ratio is not None:
        res.habitable_zone = habitable_zone(l_ratio, distance_pc)
        if not distance_pc:
            cav.append("No distance: HZ given in AU only (no angular mas values).")

    # --- Orbit: reconcile a and P via Kepler III ---
    a_au = semi_major_axis_au
    period_d = orbital_period_d
    derivation = "as supplied"
    if mstar_sun:
        if a_au is None and period_d is not None:
            a_au = semi_major_axis_from_period(period_d, mstar_sun)
            derivation = "a derived from P via Kepler III"
        elif period_d is None and a_au is not None:
            period_d = period_from_semi_major_axis(a_au, mstar_sun)
            derivation = "P derived from a via Kepler III"
    elif a_au is None and period_d is not None:
        cav.append("Stellar mass unknown: cannot derive a from P (Kepler III).")
    res.orbit = {
        "semi_major_axis_au": a_au,
        "orbital_period_d": period_d,
        "derivation": derivation,
    }

    # --- Insolation ---
    if l_ratio is not None and a_au:
        res.insolation_searth = insolation_searth(l_ratio, a_au)

    # --- Planet radius (resolve to both Rjup and Rearth) ---
    if rp_rjup is None and rp_earth is not None:
        rp_rjup = rp_earth / RJUP_TO_REARTH
    if rp_rjup is None and transit_depth_frac and rstar_sun:
        ratio = math.sqrt(min(max(transit_depth_frac, 0.0), 0.99))
        rp_rjup = ratio * rstar_sun * RSUN_TO_RJUP
    if rp_earth is None and rp_rjup is not None:
        rp_earth = rp_rjup * RJUP_TO_REARTH

    mass_source = "supplied"
    mass_estimates = None
    if mp_mjup is None and rp_earth is not None:
        # Evaluate every relation so the user sees the spread (the M-R relation
        # is the dominant error in this chain, ~50-100%).
        mass_estimates = {
            name: fn(rp_earth)["mp_earth"] for name, fn in MR_RELATIONS.items()
        }
        chosen_fn = MR_RELATIONS.get(mr_relation, estimate_mass_from_radius)
        est = chosen_fn(rp_earth)
        mp_mjup = est["mp_earth"] / MJUP_TO_MEARTH
        label = "Chen & Kipping 2017" if mr_relation == "chen_kipping" else "power-law"
        mass_source = f"{label} ({est['branch']})"
        if est.get("degenerate"):
            cav.append(
                "Planet radius is in the Jovian regime where radius does not "
                "constrain mass; RV/astrometric amplitudes use a lower-bound "
                "mass and are order-of-magnitude only."
            )
        vals = [v for v in mass_estimates.values() if v]
        if vals and max(vals) / min(vals) > 1.5:
            cav.append(
                f"Mass-radius relations disagree by {max(vals)/min(vals):.1f}x "
                f"({min(vals):.1f}-{max(vals):.1f} M_earth); mass is uncertain."
            )
    res.planet = {
        "rp_rjup": rp_rjup,
        "rp_earth": rp_earth,
        "mp_mjup": mp_mjup,
        "mp_earth": (mp_mjup * MJUP_TO_MEARTH) if mp_mjup is not None else None,
        "mass_source": mass_source,
        "mass_estimates_earth": mass_estimates,   # all relations, for comparison
    }

    # --- Transit depth (predicted) ---
    if rp_rjup is not None and rstar_sun:
        res.transit = {
            "depth_pct": transit_depth_pct(rp_rjup, rstar_sun),
            "capped": (rp_rjup / RSUN_TO_RJUP) >= rstar_sun,
        }

    # --- Radial velocity ---
    if period_d and mp_mjup is not None and mstar_sun:
        k_poe = rv_semi_amplitude_ms(
            period_d, mp_mjup, mstar_sun, inclination_deg, eccentricity
        )
        k_clubb = rv_semi_amplitude_clubb_ms(
            period_d, mp_mjup, mstar_sun, inclination_deg, eccentricity
        )
        res.radial_velocity = {
            "K_ms": k_poe,
            "K_ms_textbook": k_clubb,            # Clubb 2008 closed form
            "K_agreement_pct": (
                100.0 * abs(k_poe - k_clubb) / k_poe if k_poe else None
            ),
            "inclination_deg": inclination_deg,
            "eccentricity": eccentricity,
        }
    else:
        cav.append("RV K needs period, planet mass and stellar mass.")

    # --- Astrometric ---
    if mp_mjup is not None and mstar_sun and a_au and distance_pc:
        res.astrometric = {
            "theta_uas": astrometric_semi_amplitude_uas(
                mp_mjup, mstar_sun, a_au, distance_pc
            )
        }
    elif not distance_pc:
        cav.append("Astrometric Δθ needs a distance to the system.")

    # --- Max projected separation ---
    if a_au and distance_pc:
        res.max_projected_separation_arcsec = max_projected_separation_arcsec(
            a_au, distance_pc
        )

    return res


# ----------------------------------------------------------------------
# ExoFOP-TESS TOI parameter mapping
# ----------------------------------------------------------------------
# BTJD (TESS Barycentric Julian Date) is BJD - 2457000.0. ExoFOP wants the
# transit epoch in full BJD, so any pipeline t0 (which is in BTJD) must have
# 2,457,000 added back before submission.
BTJD_OFFSET = 2_457_000.0


def _g(d, *path, default=None):
    """Safe nested getter: _g(obs, 'orbit', 'semi_major_axis_au')."""
    cur = d
    for k in path:
        if not isinstance(cur, dict) or cur.get(k) is None:
            return default
        cur = cur[k]
    return cur


def eq_temperature_k(insolation_searth):
    """Planet equilibrium temperature (K) from insolation, Bond albedo = 0.
    Teq = 278.3 K * S^(1/4) with S in Earth insolation units."""
    if insolation_searth is None or insolation_searth <= 0:
        return None
    return 278.3 * (insolation_searth ** 0.25)


def exofop_param_rows(
    *,
    period_d=None,
    t0_btjd=None,
    depth_frac=None,
    duration_h=None,
    observables=None,
    tlcm=None,
):
    """Build the ordered list of ExoFOP-TESS TOI parameters from the pipeline's
    measured values plus the predicted-observables / TLCM bundles.

    Returns a list of dicts: ``{"label", "value", "unit", "required"}``.
    ``value`` is a float (or None when not derivable, e.g. argument of
    periastron for a transit-only fit). Formatting is left to the caller.
    The transit epoch is converted from BTJD to full BJD.
    """
    obs = observables or {}
    tl = tlcm or {}

    period = period_d if period_d is not None else _g(obs, "orbit", "orbital_period_d")
    epoch_bjd = (t0_btjd + BTJD_OFFSET) if t0_btjd is not None else None
    depth_ppm = (depth_frac * 1.0e6) if depth_frac is not None else None
    insol = obs.get("insolation_searth")

    rows = [
        {"label": "Orbital Period", "unit": "days", "required": True, "value": period},
        {"label": "Transit Epoch", "unit": "BJD", "required": True, "value": epoch_bjd},
        {"label": "Transit Depth", "unit": "ppm", "required": True, "value": depth_ppm},
        {"label": "Transit Duration", "unit": "hrs", "required": True, "value": duration_h},
        {"label": "Inclination", "unit": "deg", "required": False, "value": tl.get("inclination_deg")},
        {"label": "Impact Parameter b", "unit": "", "required": False, "value": tl.get("impact_parameter_b")},
        {"label": "R_planet/R_star", "unit": "", "required": False, "value": tl.get("radius_ratio_k")},
        {"label": "a/R_star", "unit": "", "required": False, "value": tl.get("a_over_rs")},
        {"label": "Radius", "unit": "R_Earth", "required": False, "value": _g(obs, "planet", "rp_earth")},
        {"label": "Mass", "unit": "M_Earth", "required": False, "value": _g(obs, "planet", "mp_earth")},
        {"label": "Equilibrium Temperature", "unit": "K", "required": False, "value": eq_temperature_k(insol)},
        {"label": "Insolation Flux", "unit": "Flux_Earth", "required": False, "value": insol},
        {"label": "Fitted Stellar Density", "unit": "g/cm^3", "required": False, "value": tl.get("stellar_density_gcc")},
        {"label": "Semi-major Axis", "unit": "AU", "required": False, "value": _g(obs, "orbit", "semi_major_axis_au")},
        {"label": "Eccentricity", "unit": "", "required": False, "value": _g(obs, "orbit", "eccentricity", default=0.0)},
        {"label": "Argument of Periastron \u03c9", "unit": "deg", "required": False, "value": None},
        {"label": "Time of Periastron", "unit": "BJD", "required": False, "value": None},
        {"label": "Velocity Semi-amplitude", "unit": "m/s", "required": False, "value": _g(obs, "radial_velocity", "K_ms")},
    ]
    return rows
