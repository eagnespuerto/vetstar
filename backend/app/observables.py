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
    if mp_mjup is None and rp_earth is not None:
        est = estimate_mass_from_radius(rp_earth)
        mp_mjup = est["mp_earth"] / MJUP_TO_MEARTH
        mass_source = f"Chen & Kipping 2017 ({est['branch']})"
        if est["degenerate"]:
            cav.append(
                "Planet radius is in the Jovian regime where radius does not "
                "constrain mass; RV/astrometric amplitudes use a lower-bound "
                "mass and are order-of-magnitude only."
            )
    res.planet = {
        "rp_rjup": rp_rjup,
        "rp_earth": rp_earth,
        "mp_mjup": mp_mjup,
        "mp_earth": (mp_mjup * MJUP_TO_MEARTH) if mp_mjup is not None else None,
        "mass_source": mass_source,
    }

    # --- Transit depth (predicted) ---
    if rp_rjup is not None and rstar_sun:
        res.transit = {
            "depth_pct": transit_depth_pct(rp_rjup, rstar_sun),
            "capped": (rp_rjup / RSUN_TO_RJUP) >= rstar_sun,
        }

    # --- Radial velocity ---
    if period_d and mp_mjup is not None and mstar_sun:
        res.radial_velocity = {
            "K_ms": rv_semi_amplitude_ms(
                period_d, mp_mjup, mstar_sun, inclination_deg, eccentricity
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
