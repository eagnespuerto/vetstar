"""
TLCM-based transit geometry and absolute parameters.

Implements the model-independent relations from Csizmadia (2020), "The Transit
and Light Curve Modeller", MNRAS 496, 4442, that can be evaluated directly from
quantities our pipeline already measures (period, transit depth, T14 duration)
plus an optional radial-velocity semi-amplitude:

  * Radius ratio            k = Rp/Rs = sqrt(depth)        (eq. A1, flat-bottom)
  * Scaled semi-major axis  a/Rs from transit duration     (eq. 70, inverted)
  * Impact parameter / i    b = (a/Rs) cos i               (circular)
  * Stellar density         rho_* = 3pi/(G P^2 (1+q)) (a/Rs)^3  (eq. 59 / eq. 5)
  * Mass function (SB1)      f(m) = K^3 P (1-e^2)^{3/2} / (2 pi G)  (eq. 58)
  * Absolute companion mass solving  M2^3 sin^3 i / (M1+M2)^2 = f(m)

The stellar density is the key result: it depends only on the light curve
(P and a/Rs), so it gives an absolute, catalogue-independent constraint that
both sharpens the semi-major axis and lets us cross-check / derive the stellar
mass when a radius is known.

Grazing / limb-darkening caveats from the paper (§5, Appendix A/B) are surfaced
rather than silently ignored: for grazing transits depth no longer equals k^2,
and k is only a lower limit.

All functions are pure. ``compute_tlcm_geometry`` is the driver used by the API.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional

# Physical constants (SI)
G = 6.674e-11
M_SUN = 1.989e30
R_SUN = 6.957e8
RHO_SUN = 1408.0          # kg m^-3 (mean solar density)
DAY = 86400.0
RSUN_TO_AU = 0.00465047
M_SUN_TO_M_JUP = 1047.565
M_SUN_TO_M_EARTH = 332946.0


@dataclass
class TLCMGeometry:
    inputs: dict = field(default_factory=dict)
    radius_ratio_k: Optional[float] = None
    a_over_rs: Optional[float] = None
    a_over_rs_assumption: Optional[str] = None
    a_over_rs_dynamical: Optional[float] = None      # Kepler III / density route
    a_over_rs_agreement_pct: Optional[float] = None  # duration vs dynamical
    impact_parameter_b: Optional[float] = None
    inclination_deg: Optional[float] = None
    stellar_density_kgm3: Optional[float] = None
    stellar_density_gcc: Optional[float] = None
    stellar_density_rho_sun: Optional[float] = None
    a_au_photometric: Optional[float] = None
    mstar_from_density_sun: Optional[float] = None
    radial_velocity: dict = field(default_factory=dict)
    caveats: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
def radius_ratio_from_depth(depth_frac: float) -> float:
    """k = Rp/Rs = sqrt(transit depth) for a flat-bottomed (non-grazing) transit."""
    return math.sqrt(max(depth_frac, 0.0))


def a_over_rs_from_duration(
    period_d: float, t14_d: float, k: float, b: float = 0.0
) -> Optional[float]:
    """
    Invert TLCM eq. (70) for a circular orbit:
        T14 = (P/pi) (Rs/a) sqrt((1+k)^2 - b^2)
    ->  a/Rs = (P / (pi T14)) sqrt((1+k)^2 - b^2)
    """
    if t14_d <= 0 or period_d <= 0:
        return None
    val = (1.0 + k) ** 2 - b ** 2
    if val <= 0:
        return None
    return (period_d / (math.pi * t14_d)) * math.sqrt(val)


def a_over_rs_from_density(period_d: float, mstar_sun: float,
                           rstar_sun: float) -> Optional[float]:
    """
    a/Rs from Kepler's third law (Mp << M*), independent of the transit shape:

        a/Rs = (G M* P^2 / (4 pi^2 R*^3))^(1/3)

    Equivalent to inverting the stellar-density relation (TLCM eq. 59), since
    rho_* fixes a/Rs for a given period. Uses only catalogue quantities we
    already have: period, stellar mass, stellar radius.
    """
    if not (mstar_sun and rstar_sun) or period_d <= 0:
        return None
    p = period_d * DAY
    m = mstar_sun * M_SUN
    r = rstar_sun * R_SUN
    return (G * m * p ** 2 / (4.0 * math.pi ** 2 * r ** 3)) ** (1.0 / 3.0)


def stellar_density(period_d: float, a_over_rs: float, q: float = 0.0):
    """
    TLCM eq. (59) / eq. (5):  rho_* = 3 pi / (G P^2 (1+q)) (a/Rs)^3.
    Returns (kg/m^3, g/cm^3, rho/rho_sun).
    """
    p = period_d * DAY
    rho = 3.0 * math.pi / (G * p * p * (1.0 + q)) * a_over_rs ** 3
    return rho, rho / 1000.0, rho / RHO_SUN


def inclination_from_b(a_over_rs: float, b: float) -> Optional[float]:
    if a_over_rs <= 0:
        return None
    c = max(min(b / a_over_rs, 1.0), -1.0)
    return math.degrees(math.acos(c))


def mass_from_density_radius(rho_kgm3: float, rstar_sun: float) -> float:
    """M_* = rho * (4/3) pi R^3, returned in solar masses."""
    r = rstar_sun * R_SUN
    m = rho_kgm3 * (4.0 / 3.0) * math.pi * r ** 3
    return m / M_SUN


# ---------------------------------------------------------------------------
# Radial velocity -> absolute mass
# ---------------------------------------------------------------------------
def rv_mass_function_sun(k_ms: float, period_d: float, e: float = 0.0) -> float:
    """SB1 mass function (eq. 58), in solar masses."""
    p = period_d * DAY
    f_kg = k_ms ** 3 * p * (1.0 - e ** 2) ** 1.5 / (2.0 * math.pi * G)
    return f_kg / M_SUN


def planet_mass_from_rv(
    k_ms: float, period_d: float, mstar_sun: float,
    inclination_deg: float = 90.0, e: float = 0.0,
) -> dict:
    """
    Solve M2^3 sin^3 i / (M1 + M2)^2 = f(m) for the companion mass M2.
    Newton iteration; seeded with the Mp << M* approximation.
    """
    fm = rv_mass_function_sun(k_ms, period_d, e)
    sin_i = math.sin(math.radians(inclination_deg))
    if sin_i <= 0:
        sin_i = 1.0
    m1 = mstar_sun
    # seed: M2 ~ (f M1^2)^{1/3} / sin i
    m2 = (fm * m1 ** 2) ** (1.0 / 3.0) / sin_i
    for _ in range(60):
        g = m2 ** 3 * sin_i ** 3 / (m1 + m2) ** 2 - fm
        dg = (3 * m2 ** 2 * sin_i ** 3 * (m1 + m2) ** 2
              - m2 ** 3 * sin_i ** 3 * 2 * (m1 + m2)) / (m1 + m2) ** 4
        if dg == 0:
            break
        step = g / dg
        m2 -= step
        if abs(step) < 1e-12:
            break
    return {
        "mass_function_msun": fm,
        "mp_sun": m2,
        "mp_mjup": m2 * M_SUN_TO_M_JUP,
        "mp_earth": m2 * M_SUN_TO_M_EARTH,
        "inclination_deg": inclination_deg,
        "eccentricity": e,
    }


def reduce_rv_timeseries(rv_values, method: str = "minmax") -> dict:
    """
    Collapse an RV time series to a semi-amplitude K and systemic velocity.

    "minmax": K = (max - min) / 2, Vgamma = (max + min) / 2 — the intro-level
    visual estimate. Robust to sampling but biased low if extrema are unsampled.
    """
    vals = [float(v) for v in rv_values if v is not None]
    if len(vals) < 2:
        return {"available": False, "reason": "need >= 2 RV points"}
    vmax, vmin = max(vals), min(vals)
    return {
        "available": True,
        "method": method,
        "K_estimate": (vmax - vmin) / 2.0,
        "v_gamma_estimate": (vmax + vmin) / 2.0,
        "n_points": len(vals),
        "note": "min/max estimate; under-samples K if peak/trough not observed",
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def compute_tlcm_geometry(
    *,
    period_d: Optional[float] = None,
    t14_d: Optional[float] = None,
    depth_frac: Optional[float] = None,
    rstar_sun: Optional[float] = None,
    impact_parameter: Optional[float] = None,
    grazing: bool = False,
    k_rv_ms: Optional[float] = None,
    eccentricity: float = 0.0,
    mstar_sun: Optional[float] = None,
    inclination_deg: Optional[float] = None,
) -> TLCMGeometry:
    res = TLCMGeometry()
    res.inputs = {
        "period_d": period_d, "t14_d": t14_d, "depth_frac": depth_frac,
        "rstar_sun": rstar_sun, "impact_parameter": impact_parameter,
        "grazing": grazing, "k_rv_ms": k_rv_ms, "eccentricity": eccentricity,
        "mstar_sun": mstar_sun,
    }
    cav = res.caveats

    # Radius ratio
    k = None
    if depth_frac is not None and depth_frac > 0:
        k = radius_ratio_from_depth(depth_frac)
        res.radius_ratio_k = k
        if grazing:
            cav.append(
                "Grazing transit: depth no longer equals k^2 (TLCM §5); the "
                "radius ratio is a LOWER limit only."
            )

    # Scaled semi-major axis from duration
    b = impact_parameter if impact_parameter is not None else 0.0
    if period_d and t14_d and k is not None:
        a_rs = a_over_rs_from_duration(period_d, t14_d, k, b)
        res.a_over_rs = a_rs
        res.a_over_rs_assumption = (
            "circular orbit; b supplied" if impact_parameter is not None
            else "circular orbit; central transit assumed (b=0)"
        )
        if impact_parameter is None:
            cav.append(
                "Impact parameter unknown — a/Rs assumes a central transit "
                "(b=0). a/Rs and stellar density are upper-bound estimates."
            )

    # Stellar density (model-independent) + photometric a + mass
    if res.a_over_rs and period_d:
        rho, gcc, rho_sun = stellar_density(period_d, res.a_over_rs)
        res.stellar_density_kgm3 = rho
        res.stellar_density_gcc = gcc
        res.stellar_density_rho_sun = rho_sun
        if rstar_sun:
            res.a_au_photometric = res.a_over_rs * rstar_sun * RSUN_TO_AU
            res.mstar_from_density_sun = mass_from_density_radius(rho, rstar_sun)

    # Independent dynamical route (Kepler III / density) from catalogue M*, R*.
    res.a_over_rs_dynamical = a_over_rs_from_density(period_d, mstar_sun, rstar_sun)
    if res.a_over_rs and res.a_over_rs_dynamical:
        ad, ag = res.a_over_rs_dynamical, res.a_over_rs
        res.a_over_rs_agreement_pct = 100.0 * abs(ag - ad) / ad
        if res.a_over_rs_agreement_pct > 20.0:
            cav.append(
                f"a/Rs from transit duration ({ag:.1f}) and from Kepler/stellar "
                f"density ({ad:.1f}) disagree by {res.a_over_rs_agreement_pct:.0f}% "
                f"— possible eccentric orbit, grazing transit, dilution/blend, or "
                f"inaccurate stellar parameters (TLCM §2.12, Appendix A)."
            )

    # Inclination from geometry
    if res.a_over_rs and impact_parameter is not None:
        res.inclination_deg = inclination_from_b(res.a_over_rs, impact_parameter)
    inc = inclination_deg or res.inclination_deg or 90.0

    # RV -> absolute mass
    if k_rv_ms and period_d and mstar_sun:
        res.radial_velocity = planet_mass_from_rv(
            k_rv_ms, period_d, mstar_sun, inc, eccentricity
        )
    elif k_rv_ms and period_d:
        res.radial_velocity = {
            "mass_function_msun": rv_mass_function_sun(k_rv_ms, period_d, eccentricity),
            "note": "stellar mass needed to convert mass function to companion mass",
        }

    return res
