"""
Habitability Chance Index (HCI) — grounded in Hill et al. (2026), STEHM.

The score is built from six sub-components derived directly from STEHM
results and the paper's sensitivity analyses:

  1. Planet size           — STEHM's primary result: ≥0.8 R⊕ retains atmosphere
  2. Stellar type          — Sun-like (FGK) stars are the STEHM target; M-dwarfs
                             have different XUV histories (not modelled)
  3. Habitable zone        — Kopparapu et al. (2013/2014) bounds from STEHM §5.5
  4. TOI disposition       — ExoFOP vetting flag downweights known FPs/EBs
  5. Vetting flags         — signals from our own centroid/odd-even/secondary tests
  6. Multi-sector          — more transits = tighter period, less chance of artefact

Each sub-component returns a value in [0, 1].  Final score = weighted average
× 100, rounded to one decimal.

Reference: Hill, M. L., Kane, S. R., Foley, B. J., & Schaefer, L. K. (2026).
  Smaller Than Earth Habitability Model (STEHM): The Lower Size Limit for
  Atmosphere Retention in the Habitable Zone.  arXiv:2605.00170v1.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Habitable-zone constants (Kopparapu et al. 2013/2014, used by STEHM §5.5)
# ---------------------------------------------------------------------------
HZ_INNER_CHZ_AU = 0.95   # Conservative HZ inner (runaway greenhouse)
HZ_OUTER_CHZ_AU = 1.676  # Conservative HZ outer (maximum greenhouse)
HZ_INNER_OHZ_AU = 0.75   # Optimistic HZ inner  (recent Venus)
HZ_OUTER_OHZ_AU = 1.765  # Optimistic HZ outer  (early Mars)


# ---------------------------------------------------------------------------
# STEHM planet-size thresholds
# ---------------------------------------------------------------------------
STEHM_SAFE_RE   = 0.8   # Default threshold: ≥0.8 R⊕ retains atmosphere
STEHM_MARGIN_RE = 0.7   # Possible under favourable conditions
STEHM_UPPER_RE  = 2.2   # Above this: likely sub-Neptune, not rocky/habitable

# Bulk-density composition bands (Earth densities; rho_Earth = 5.51 g/cm^3).
# Used only when an absolute mass (e.g. from RV) is available alongside radius.
RHO_EARTH_GCC = 5.51
RHO_ROCKY_REL = 0.6     # >= 0.6 rho_E (~3.3 g/cm^3): rocky-consistent
RHO_VOLATILE_REL = 0.4  # <  0.4 rho_E (~2.2 g/cm^3): volatile/gas-rich envelope

# Sun's effective temperature (IAU nominal) and Earth's a/R_sun.
SOLAR_TEFF = 5772.0
A_EARTH_OVER_RSUN = 215.03   # 1 AU / R_sun
RSUN_IN_REARTH = 109.18      # R_sun / R_earth


# ---------------------------------------------------------------------------
# Stellar type from the light curve (no spectroscopy needed)
# ---------------------------------------------------------------------------
# A transiting planet fixes the mean stellar density directly (Seager &
# Mallen-Ornelas 2003, ApJ 585, 1038). On the dwarf sequence the mean density
# is a monotonic function of effective temperature (Pecaut & Mamajek 2013,
# ApJS 208, 9), so an observed rho_* can be inverted for an approximate
# spectral type / Teff / radius — the standard "known method" for typing a
# host star from photometry alone when no spectroscopic Teff is available.
#
# Dwarf-sequence anchor points: (spectral type, Teff [K], M [Msun], R [Rsun]).
_MS_SEQUENCE = [
    ("A0V", 9700, 2.18, 2.19), ("A5V", 8080, 1.86, 1.79),
    ("F0V", 7220, 1.61, 1.61), ("F5V", 6510, 1.33, 1.33),
    ("G0V", 5930, 1.06, 1.06), ("G2V", 5772, 1.00, 1.00),
    ("G8V", 5490, 0.93, 0.91), ("K0V", 5280, 0.87, 0.85),
    ("K3V", 4830, 0.78, 0.75), ("K5V", 4410, 0.68, 0.66),
    ("K7V", 4070, 0.58, 0.61), ("M0V", 3850, 0.57, 0.59),
    ("M2V", 3550, 0.44, 0.44), ("M3V", 3400, 0.37, 0.39),
    ("M4V", 3200, 0.23, 0.26), ("M5V", 3030, 0.16, 0.20),
]
# (rho/rho_sun, Teff, M, R, sptype), ascending in density.
_MS_BY_RHO = sorted(
    ((m / r ** 3, teff, m, r, sp) for sp, teff, m, r in _MS_SEQUENCE),
    key=lambda x: x[0],
)


def estimate_stellar_from_teff(teff_k: Optional[float]) -> Optional[dict]:
    """
    Interpolate Pecaut & Mamajek (2013) dwarf sequence in Teff to estimate
    main-sequence radius and mass when the catalogues are silent (e.g. faint
    TIC targets with only Tmag filled in). Returns None if Teff is missing.
    """
    if not teff_k or teff_k <= 0:
        return None
    rows = sorted(_MS_SEQUENCE, key=lambda r: r[1])  # ascending Teff
    teffs = [r[1] for r in rows]
    extrapolated = teff_k < teffs[0] or teff_k > teffs[-1]
    if teff_k <= teffs[0]:
        sp, _, mass, rad = rows[0]
    elif teff_k >= teffs[-1]:
        sp, _, mass, rad = rows[-1]
    else:
        for i in range(1, len(rows)):
            if teff_k <= teffs[i]:
                f = (teff_k - teffs[i - 1]) / (teffs[i] - teffs[i - 1])
                sp0, _, m0, r0 = rows[i - 1]
                sp1, _, m1, r1 = rows[i]
                mass = m0 + f * (m1 - m0)
                rad = r0 + f * (r1 - r0)
                sp = sp0 if f < 0.5 else sp1
                break
    return {
        "teff": round(teff_k),
        "sptype": sp,
        "radius_sun": round(rad, 3),
        "mass_sun": round(mass, 3),
        "extrapolated": extrapolated,
        "method": "Teff -> main-sequence (Pecaut & Mamajek 2013)",
    }


def estimate_stellar_from_density(rho_sun: Optional[float]) -> Optional[dict]:
    """
    Invert a transit-derived mean stellar density (solar units) for an
    estimated main-sequence spectral type, Teff, mass and radius.

    Interpolates the Pecaut & Mamajek (2013) dwarf sequence in log(rho).
    Density is single-valued in Teff only on the main sequence, so the result
    is flagged as extrapolated outside the tabulated range.
    """
    if not rho_sun or rho_sun <= 0:
        return None
    rows = _MS_BY_RHO
    xs = [math.log10(r[0]) for r in rows]
    x = math.log10(rho_sun)
    extrapolated = x < xs[0] or x > xs[-1]
    if x <= xs[0]:
        _, teff, mass, rad, sp = rows[0]
    elif x >= xs[-1]:
        _, teff, mass, rad, sp = rows[-1]
    else:
        for i in range(1, len(rows)):
            if x <= xs[i]:
                f = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
                _, t0, m0, r0, sp0 = rows[i - 1]
                _, t1, m1, r1, sp1 = rows[i]
                teff = t0 + f * (t1 - t0)
                mass = m0 + f * (m1 - m0)
                rad = r0 + f * (r1 - r0)
                sp = sp0 if f < 0.5 else sp1
                break
    return {
        "teff": round(teff),
        "sptype": sp,
        "radius_sun": round(rad, 3),
        "mass_sun": round(mass, 3),
        "rho_sun": rho_sun,
        "extrapolated": extrapolated,
        "method": ("transit density -> main-sequence type "
                   "(Seager & Mallen-Ornelas 2003; Pecaut & Mamajek 2013)"),
    }


# ---------------------------------------------------------------------------
# Habitable-zone flux boundaries (Kopparapu et al. 2013/2014)
# ---------------------------------------------------------------------------
# Seff(Teff) = S0 + a t + b t^2 + c t^3 + d t^4, with t = Teff - 5780 K.
_KOPP_SEFF = {
    "recent_venus":       (1.776, 2.136e-4, 2.533e-8, -1.332e-11, -3.097e-15),
    "runaway_greenhouse": (1.107, 1.332e-4, 1.580e-8, -8.308e-12, -1.931e-15),
    "maximum_greenhouse": (0.356, 6.171e-5, 1.698e-9, -3.198e-12, -5.575e-16),
    "early_mars":         (0.320, 5.547e-5, 1.526e-9, -2.874e-12, -5.011e-16),
}


def kopparapu_seff(teff: float, kind: str) -> float:
    s0, a, b, c, d = _KOPP_SEFF[kind]
    t = teff - 5780.0
    return s0 + a * t + b * t ** 2 + c * t ** 3 + d * t ** 4


def insolation_from_a_over_rs(a_over_rs: float, teff: float) -> float:
    """
    Instellation in Earth units straight from the scaled semi-major axis:
        S/S_earth = (Teff/Teff_sun)^4 (a_earth/R_sun)^2 / (a/Rs)^2
    Both a/Rs and (via the transit) Teff are light-curve observables, so this
    needs no catalogue luminosity or distance.
    """
    return (teff / SOLAR_TEFF) ** 4 * (A_EARTH_OVER_RSUN / a_over_rs) ** 2


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------
@dataclass
class PlanetCandidate:
    radius_earth: Optional[float] = None
    orbital_period_d: Optional[float] = None
    semi_major_axis_au: Optional[float] = None
    toi_number: Optional[str] = None
    disposition: Optional[str] = None
    stellar_radius_sun: Optional[float] = None
    stellar_teff: Optional[float] = None
    stellar_mass_sun: Optional[float] = None
    mass_earth: Optional[float] = None        # absolute companion mass (e.g. RV)
    mass_source: Optional[str] = None
    depth_ppm: Optional[float] = None
    duration_hr: Optional[float] = None
    source: str = "unknown"


@dataclass
class SubScore:
    name: str
    score: float
    weight: float
    label: str
    explanation: str
    score_low: Optional[float] = None
    score_high: Optional[float] = None


@dataclass
class HabitabilityResult:
    hci: float
    tier: str
    tier_color: str
    hci_low: Optional[float] = None
    hci_high: Optional[float] = None
    sub_scores: list = field(default_factory=list)
    caveats: list = field(default_factory=list)
    stellar_estimate: Optional[dict] = None
    insolation_searth: Optional[float] = None
    paper_ref: str = "Hill et al. (2026), arXiv:2605.00170 — STEHM"

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Sub-score functions
# ---------------------------------------------------------------------------

def _density_verdict(rho_rel: float):
    """Return (score_multiplier, label_override_or_None, note) for a density."""
    rho_gcc = rho_rel * RHO_EARTH_GCC
    if rho_rel >= RHO_ROCKY_REL:
        return 1.1, None, f"{rho_gcc:.1f} g/cm³ (~{rho_rel:.1f} ρ⊕): rocky-consistent"
    if rho_rel < RHO_VOLATILE_REL:
        return 0.35, "Volatile-rich", f"{rho_gcc:.1f} g/cm³ (~{rho_rel:.1f} ρ⊕): too low for rock"
    return 0.7, None, f"{rho_gcc:.1f} g/cm³ (~{rho_rel:.1f} ρ⊕): intermediate"


def _score_planet_size(rp: Optional[float], mp_earth: Optional[float] = None,
                       mass_source: Optional[str] = None,
                       mass_estimates: Optional[list] = None) -> SubScore:
    w = 0.30
    if rp is None:
        return SubScore("Planet size", 0.5, w, "Unknown",
                        "Planet radius unavailable — cannot apply STEHM size threshold.")
    if rp > STEHM_UPPER_RE:
        return SubScore("Planet size", 0.05, w, "Too large",
                        f"{rp:.2f} R⊕ exceeds the rocky-planet cap (~2.2 R⊕); "
                        f"likely a sub-Neptune.")
    if rp < 0.5:
        return SubScore("Planet size", 0.02, w, "Too small",
                        f"{rp:.2f} R⊕ is below the STEHM model range; "
                        f"atmosphere retention is effectively zero.")
    if rp >= STEHM_SAFE_RE:
        s = 0.75 + 0.25 * min((rp - STEHM_SAFE_RE) / (1.0 - STEHM_SAFE_RE), 1.0)
        base = SubScore("Planet size", s, w, "Favourable",
                        f"{rp:.2f} R⊕ ≥ 0.8 R⊕ STEHM threshold — can retain a "
                        f"long-term CO₂ atmosphere under Earth-like conditions.")
    elif rp >= STEHM_MARGIN_RE:
        s = 0.35 + 0.40 * (rp - STEHM_MARGIN_RE) / (STEHM_SAFE_RE - STEHM_MARGIN_RE)
        base = SubScore("Planet size", s, w, "Marginal",
                        f"{rp:.2f} R⊕ is below the default STEHM threshold (0.8 R⊕). "
                        f"Atmosphere retention requires favourable formation conditions "
                        f"(high carbon, cool mantle start, low CRF — Hill et al. 2026 §5).")
    else:
        s = 0.05 + 0.30 * (rp - 0.5) / (STEHM_MARGIN_RE - 0.5)
        base = SubScore("Planet size", s, w, "Unlikely",
                        f"{rp:.2f} R⊕ < 0.7 R⊕ — STEHM predicts rapid atmosphere loss "
                        f"even under the most favourable conditions (Hill et al. 2026 Fig 5).")

    # Refine with bulk density. A measured mass (RV) gives a single density; a
    # mass from the M-R relations gives a *range*, which we propagate into the
    # score so the dominant M-R uncertainty is visible in the final HCI.
    masses = [m for m in (mass_estimates or ([mp_earth] if mp_earth else [])) if m and m > 0]
    if masses and rp > 0:
        radius_only = base.score
        scored = [min(1.0, radius_only * _density_verdict(m / rp ** 3)[0]) for m in masses]
        central_mp = mp_earth if (mp_earth and mp_earth > 0) else sorted(masses)[len(masses) // 2]
        mult, label, note = _density_verdict(central_mp / rp ** 3)
        base.score = min(1.0, radius_only * mult)
        if label:
            base.label = label
        src = f" ({mass_source})" if mass_source else ""
        base.explanation += f" Mass {central_mp:.1f} M⊕{src} ⇒ density {note}."
        if len(scored) > 1 and max(scored) - min(scored) > 0.02:
            base.score_low, base.score_high = min(scored), max(scored)
            base.explanation += (
                f" Mass–radius spread ({min(masses):.1f}–{max(masses):.1f} M⊕) maps "
                f"to a size score of {base.score_low:.2f}–{base.score_high:.2f}.")
    return base


def _score_habitable_zone_insolation(a_over_rs, teff, teff_estimated=False) -> SubScore:
    """HZ score from instellation (a/Rs + Teff) against Kopparapu flux limits."""
    w = 0.25
    s_in = insolation_from_a_over_rs(a_over_rs, teff)
    rv = kopparapu_seff(teff, "recent_venus")
    rg = kopparapu_seff(teff, "runaway_greenhouse")
    mg = kopparapu_seff(teff, "maximum_greenhouse")
    em = kopparapu_seff(teff, "early_mars")
    src = " (Teff from transit density)" if teff_estimated else ""
    base = (f"S={s_in:.2f} S⊕ from a/Rs={a_over_rs:.1f}{src}; Kopparapu flux HZ "
            f"spans {em:.2f}–{rv:.2f} S⊕ for Teff={teff:.0f} K.")
    if s_in > rv:
        return SubScore("Habitable zone", 0.05, w, "Too hot",
                        base + " Inside the recent-Venus limit — runaway greenhouse.")
    if s_in < em:
        return SubScore("Habitable zone", 0.10, w, "Too cold",
                        base + " Beyond the early-Mars limit — surface water likely frozen.")
    if s_in > rg:   # recent-Venus .. runaway: optimistic warm edge
        frac = (rv - s_in) / max(rv - rg, 1e-6)
        return SubScore("Habitable zone", 0.30 + 0.25 * frac, w, "Warm edge (OHZ)",
                        base + " Between recent-Venus and runaway-greenhouse limits.")
    if s_in < mg:   # max-greenhouse .. early-Mars: optimistic cool edge
        frac = (s_in - em) / max(mg - em, 1e-6)
        return SubScore("Habitable zone", 0.65 + 0.30 * frac, w, "Cool edge (OHZ)",
                        base + " Between max-greenhouse and early-Mars limits; "
                        "outer-HZ retention is favoured (STEHM §5.5).")
    frac = (s_in - mg) / max(rg - mg, 1e-6)   # 0 at outer, 1 at inner
    s = 0.75 + 0.20 * (1 - abs(frac - 0.5) * 2)
    return SubScore("Habitable zone", min(s, 1.0), w, "Conservative HZ",
                    base + " Inside the conservative HZ — best case for liquid water.")


def _score_habitable_zone(a_au, teff, rstar, mstar) -> SubScore:
    w = 0.25
    if a_au is None:
        return SubScore("Habitable zone", 0.5, w, "Unknown",
                        "Semi-major axis unavailable — cannot place planet in HZ.")
    teff = teff or 5778.0
    rstar = rstar or 1.0
    l_ratio = (rstar ** 2) * ((teff / 5778.0) ** 4)
    sq = math.sqrt(max(l_ratio, 0.01))
    inner_ohz = HZ_INNER_OHZ_AU * sq
    outer_ohz = HZ_OUTER_OHZ_AU * sq
    inner_chz = HZ_INNER_CHZ_AU * sq
    outer_chz = HZ_OUTER_CHZ_AU * sq
    if a_au < inner_ohz:
        return SubScore("Habitable zone", 0.05, w, "Too hot",
                        f"{a_au:.3f} AU is inside the optimistic HZ inner edge "
                        f"({inner_ohz:.3f} AU). Runaway greenhouse expected.")
    if a_au > outer_ohz:
        return SubScore("Habitable zone", 0.10, w, "Too cold",
                        f"{a_au:.3f} AU is beyond the optimistic HZ outer edge "
                        f"({outer_ohz:.3f} AU). Surface water likely frozen.")
    if a_au < inner_chz:
        frac = (a_au - inner_ohz) / max(inner_chz - inner_ohz, 1e-6)
        return SubScore("Habitable zone", 0.30 + 0.25 * frac, w, "Warm edge (OHZ)",
                        f"{a_au:.3f} AU — between recent Venus and runaway greenhouse limits.")
    if a_au > outer_chz:
        frac = (outer_ohz - a_au) / max(outer_ohz - outer_chz, 1e-6)
        return SubScore("Habitable zone", 0.65 + 0.30 * frac, w, "Cool edge (OHZ)",
                        f"{a_au:.3f} AU — between max greenhouse and early Mars limits. "
                        f"STEHM §5.5: outer-HZ planets retain atmospheres more easily.")
    mid = (inner_chz + outer_chz) / 2
    frac = (a_au - inner_chz) / max(outer_chz - inner_chz, 1e-6)
    s = 0.75 + 0.20 * (1 - abs(frac - 0.5) * 2)
    return SubScore("Habitable zone", min(s, 1.0), w, "Conservative HZ",
                    f"{a_au:.3f} AU — inside the CHZ ({inner_chz:.3f}–{outer_chz:.3f} AU). "
                    f"Best-case for liquid surface water.")


def _score_stellar_type(teff: Optional[float], source: Optional[str] = None,
                        sptype: Optional[str] = None) -> SubScore:
    w = 0.15
    if teff is None:
        return SubScore("Stellar type", 0.5, w, "Unknown",
                        "Stellar Teff unavailable — cannot assess XUV environment.")
    if 5000 <= teff < 6000:
        score, label, expl = (0.90, "G dwarf (solar analog)",
            f"Teff={teff:.0f} K — exactly the regime STEHM was calibrated for.")
    elif 6000 <= teff <= 7500:
        score, label, expl = (0.80, "F dwarf",
            f"Teff={teff:.0f} K (F-dwarf). STEHM results broadly transferable.")
    elif 3700 <= teff < 5000:
        score, label, expl = (0.65, "K dwarf",
            f"Teff={teff:.0f} K (K-dwarf). Modestly higher XUV than Sun; "
            f"safe-size boundary may shift slightly upward.")
    elif teff < 3700:
        score, label, expl = (0.30, "M dwarf",
            f"Teff={teff:.0f} K (M-dwarf). STEHM is not calibrated for M-dwarf XUV "
            f"histories. Non-thermal escape and flares can substantially worsen "
            f"atmosphere retention (Hill et al. 2026 §6).")
    else:
        score, label, expl = (0.40, "Hot star",
            f"Teff={teff:.0f} K — hotter than F. Intense radiation and short "
            f"main-sequence lifetime reduce habitability prospects.")
    if source:
        tag = f"{sptype}, " if sptype else ""
        expl += f" [{tag}estimated from {source}]"
    return SubScore("Stellar type", score, w, label, expl)


def _score_toi_disposition(disposition: Optional[str], toi: Optional[str]) -> SubScore:
    w = 0.15
    if toi is None:
        return SubScore("TOI disposition", 0.50, w, "No TOI",
                        "No TOI designation on ExoFOP-TESS. Signal may be new or "
                        "not yet ingested into the community catalog.")
    if disposition is None:
        return SubScore("TOI disposition", 0.55, w, "Unclassified TOI",
                        f"TOI {toi} exists but has no disposition yet.")
    d = disposition.strip().upper()
    if d in ("CP", "KP"):
        return SubScore("TOI disposition", 1.00, w, "Confirmed/Known planet",
                        f"TOI {toi}: {d} — independently confirmed. Highest confidence.")
    if d in ("PC", "APC"):
        return SubScore("TOI disposition", 0.75, w, "Planet candidate",
                        f"TOI {toi}: {d} — passed TESS pipeline vetting.")
    if d in ("FP", "FA"):
        return SubScore("TOI disposition", 0.05, w, "False positive/alarm",
                        f"TOI {toi}: {d} — ExoFOP flags this as a false positive or alarm.")
    return SubScore("TOI disposition", 0.50, w, f"Disposition: {d}",
                    f"TOI {toi}: unrecognised disposition code '{d}'.")


def _score_vetting_flags(verdict: Optional[dict]) -> SubScore:
    w = 0.10
    if verdict is None:
        return SubScore("Vetting flags", 0.50, w, "Not vetted",
                        "No vetting result available.")
    cat = verdict.get("category", "")
    flags = verdict.get("flags", [])
    eb_flags = {"secondary_eclipse_detected", "odd_even_mismatch",
                "companion_too_large_for_planet", "centroid_offset"}
    if cat in ("eclipsing_binary_candidate", "false_positive_blend"):
        return SubScore("Vetting flags", 0.05, w, "EB / blend",
                        "Pipeline flags this as an eclipsing binary or background blend — "
                        "not a transiting planet.")
    if cat == "no_signal":
        return SubScore("Vetting flags", 0.40, w, "No signal",
                        "No significant transit/eclipse signal detected.")
    if any(f in flags for f in eb_flags):
        raised = ", ".join(f for f in flags if f in eb_flags)
        return SubScore("Vetting flags", 0.10, w, "EB indicators",
                        f"Flags raised: {raised}.")
    if cat == "planet_candidate":
        clean = "centroid_offset" not in flags
        return SubScore("Vetting flags", 0.85 if clean else 0.55, w,
                        "Planet candidate",
                        "Pipeline classifies as planet candidate" +
                        (" with on-target centroid." if clean else " (centroid unclear)."))
    return SubScore("Vetting flags", 0.45, w, "Ambiguous",
                    "Vetting result is ambiguous — manual review recommended.")


def _score_multisector(n_det: int, n_obs: int) -> SubScore:
    w = 0.05
    if n_obs == 0:
        return SubScore("Multi-sector", 0.50, w, "No data", "No multi-sector data.")
    if n_obs == 1:
        return SubScore("Multi-sector", 0.40, w, "Single sector",
                        "Only one sector observed — period is unconstrained.")
    if n_det == 0:
        return SubScore("Multi-sector", 0.30, w, "No detections",
                        f"0/{n_obs} observed sectors show a dip — "
                        f"inconsistent with a real periodic transit.")
    frac = n_det / n_obs
    s = 0.40 + 0.60 * frac
    tier = "Consistent" if frac >= 0.6 else "Partial"
    return SubScore("Multi-sector", min(s, 1.0), w, f"{tier} ({n_det}/{n_obs} sectors)",
                    f"Dip detected in {n_det} of {n_obs} observed sectors — "
                    + ("consistent with a real periodic signal."
                       if frac >= 0.6 else "more sectors needed to confirm periodicity."))


# ---------------------------------------------------------------------------
# Master function
# ---------------------------------------------------------------------------

def compute_hci(
    planet: PlanetCandidate,
    vetting_verdict: Optional[dict] = None,
    n_sectors_with_detections: int = 1,
    n_sectors_observed: int = 1,
    mass_estimates_earth: Optional[list] = None,
    a_over_rs: Optional[float] = None,
    radius_ratio_k: Optional[float] = None,
    stellar_density_rho_sun: Optional[float] = None,
) -> HabitabilityResult:
    # --- Resolve effective stellar parameters -----------------------------
    # Prefer catalogue/spectroscopic values. When Teff or R* is missing, type
    # the star from the transit-derived mean density (main-sequence assumption)
    # so the stellar-type, HZ, and planet-size terms still have a real basis.
    teff_eff = planet.stellar_teff
    rstar_eff = planet.stellar_radius_sun
    teff_estimated = planet.stellar_teff is None
    rstar_estimated = planet.stellar_radius_sun is None
    stellar_est = None
    if (teff_eff is None or rstar_eff is None) and stellar_density_rho_sun:
        stellar_est = estimate_stellar_from_density(stellar_density_rho_sun)
        if stellar_est:
            if teff_eff is None:
                teff_eff = stellar_est["teff"]
            if rstar_eff is None:
                rstar_eff = stellar_est["radius_sun"]

    # --- Effective planet radius: Rp = k x R* when no radius is given ------
    rp_eff = planet.radius_earth
    rp_from_ratio = False
    if rp_eff is None and radius_ratio_k and rstar_eff:
        rp_eff = radius_ratio_k * rstar_eff * RSUN_IN_REARTH
        rp_from_ratio = True

    # --- Build sub-scores --------------------------------------------------
    size_sub = _score_planet_size(rp_eff, planet.mass_earth,
                                  planet.mass_source, mass_estimates_earth)
    if rp_from_ratio:
        size_sub.explanation += (
            f" Radius derived from k=Rp/R★={radius_ratio_k:.3f} × "
            f"R★={rstar_eff:.2f} R⊙ ⇒ {rp_eff:.2f} R⊕"
            + (" (R★ estimated from transit density)." if rstar_estimated else "."))

    # Prefer the catalogue/derived semi-major axis in AU; fall back to the
    # instellation from a/Rs (a direct transit observable) when no a_au exists.
    if planet.semi_major_axis_au:
        hz_sub = _score_habitable_zone(
            planet.semi_major_axis_au, teff_eff, rstar_eff, planet.stellar_mass_sun)
    elif a_over_rs and teff_eff:
        hz_sub = _score_habitable_zone_insolation(
            a_over_rs, teff_eff, teff_estimated=teff_estimated)
    else:
        hz_sub = _score_habitable_zone(
            planet.semi_major_axis_au, teff_eff, rstar_eff, planet.stellar_mass_sun)

    star_sub = _score_stellar_type(
        teff_eff,
        source="transit density" if (stellar_est and teff_estimated) else None,
        sptype=stellar_est["sptype"] if stellar_est else None,
    )

    subs = [
        size_sub,
        hz_sub,
        star_sub,
        _score_toi_disposition(planet.disposition, planet.toi_number),
        _score_vetting_flags(vetting_verdict),
        _score_multisector(n_sectors_with_detections, n_sectors_observed),
    ]

    insol = (insolation_from_a_over_rs(a_over_rs, teff_eff)
             if (a_over_rs and teff_eff) else None)

    total_w = sum(s.weight for s in subs)
    hci = sum(s.score * s.weight for s in subs) / total_w * 100

    # Propagate the size sub-score range (driven by the mass-radius spread)
    # into an HCI range; only the size term varies with mass.
    size = subs[0]
    hci_low = hci_high = None
    if size.score_low is not None and size.score_high is not None:
        f = size.weight / total_w * 100
        hci_low = max(0.0, hci - (size.score - size.score_low) * f)
        hci_high = min(100.0, hci + (size.score_high - size.score) * f)

    if hci >= 70:
        tier, color = "Promising", "bg-emerald-100 border-emerald-500 text-emerald-900"
    elif hci >= 45:
        tier, color = "Marginal", "bg-amber-100 border-amber-500 text-amber-900"
    elif hci >= 20:
        tier, color = "Unlikely", "bg-rose-100 border-rose-500 text-rose-900"
    else:
        tier, color = "Very unlikely", "bg-slate-100 border-slate-400 text-slate-700"

    # Hard override for confirmed EB/FP
    if vetting_verdict and vetting_verdict.get("category") in (
        "eclipsing_binary_candidate", "false_positive_blend"
    ):
        hci = min(hci, 12.0)
        hci_low = hci_high = None
        tier, color = "Very unlikely", "bg-slate-100 border-slate-400 text-slate-700"

    caveats = []
    if planet.stellar_teff and planet.stellar_teff < 3700:
        caveats.append(
            "STEHM is calibrated for Sun-like stars. M-dwarf XUV environments are "
            "more hostile — the safe-size threshold may be larger than 0.8 R⊕."
        )
    if planet.radius_earth and 0.7 <= planet.radius_earth < 0.8:
        caveats.append(
            "Planet is in the STEHM marginal zone (0.7–0.8 R⊕). Retention depends "
            "critically on initial carbon inventory, HPE, and core radius fraction "
            "(Hill et al. 2026 §5.1–5.4)."
        )
    if n_sectors_observed == 1:
        caveats.append(
            "Only one TESS sector observed. Period is unconstrained — "
            "cannot verify periodicity or consistent transit timing."
        )
    if rp_eff is None:
        caveats.append(
            "Planet radius unknown — STEHM size threshold (0.8 R⊕) could not be "
            "evaluated. A neutral placeholder is used for this component."
        )
    elif planet.radius_earth is None and rp_from_ratio:
        caveats.append(
            "Planet radius was derived from the transit radius ratio k=√depth × R★ "
            "rather than a catalogue value; it inherits any error in R★ and assumes "
            "a non-grazing, flat-bottomed transit."
        )
    if stellar_est and teff_estimated:
        ex = " (outside the tabulated dwarf range)" if stellar_est.get("extrapolated") else ""
        caveats.append(
            "No spectroscopic Teff — stellar type was inferred from the transit-derived "
            f"mean density (ρ★≈{stellar_density_rho_sun:.2f} ρ⊙ ⇒ ~{stellar_est['sptype']}, "
            f"Teff≈{stellar_est['teff']} K{ex}), assuming a main-sequence star and a "
            "central transit (b=0). A subgiant/giant or grazing geometry biases this; "
            "ρ★ from duration is an upper bound when b is unknown."
        )
    caveats.append(
        "STEHM models a pure CO₂ stagnant-lid planet as a best-case for atmosphere "
        "retention. Non-thermal escape, magnetic fields, and plate tectonics are "
        "excluded (Hill et al. 2026 §6). This score is a first-order estimate only."
    )

    return HabitabilityResult(
        hci=round(hci, 1),
        tier=tier,
        tier_color=color,
        hci_low=round(hci_low, 1) if hci_low is not None else None,
        hci_high=round(hci_high, 1) if hci_high is not None else None,
        sub_scores=[asdict(s) for s in subs],
        caveats=caveats,
        stellar_estimate=stellar_est if (stellar_est and (teff_estimated or rstar_estimated)) else None,
        insolation_searth=round(insol, 4) if insol is not None else None,
    )
