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


@dataclass
class HabitabilityResult:
    hci: float
    tier: str
    tier_color: str
    sub_scores: list = field(default_factory=list)
    caveats: list = field(default_factory=list)
    paper_ref: str = "Hill et al. (2026), arXiv:2605.00170 — STEHM"

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Sub-score functions
# ---------------------------------------------------------------------------

def _score_planet_size(rp: Optional[float]) -> SubScore:
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
        return SubScore("Planet size", s, w, "Favourable",
                        f"{rp:.2f} R⊕ ≥ 0.8 R⊕ STEHM threshold — can retain a "
                        f"long-term CO₂ atmosphere under Earth-like conditions.")
    if rp >= STEHM_MARGIN_RE:
        s = 0.35 + 0.40 * (rp - STEHM_MARGIN_RE) / (STEHM_SAFE_RE - STEHM_MARGIN_RE)
        return SubScore("Planet size", s, w, "Marginal",
                        f"{rp:.2f} R⊕ is below the default STEHM threshold (0.8 R⊕). "
                        f"Atmosphere retention requires favourable formation conditions "
                        f"(high carbon, cool mantle start, low CRF — Hill et al. 2026 §5).")
    s = 0.05 + 0.30 * (rp - 0.5) / (STEHM_MARGIN_RE - 0.5)
    return SubScore("Planet size", s, w, "Unlikely",
                    f"{rp:.2f} R⊕ < 0.7 R⊕ — STEHM predicts rapid atmosphere loss "
                    f"even under the most favourable conditions (Hill et al. 2026 Fig 5).")


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


def _score_stellar_type(teff: Optional[float]) -> SubScore:
    w = 0.15
    if teff is None:
        return SubScore("Stellar type", 0.5, w, "Unknown",
                        "Stellar Teff unavailable — cannot assess XUV environment.")
    if teff >= 5000 and teff < 6000:
        return SubScore("Stellar type", 0.90, w, "G dwarf (solar analog)",
                        f"Teff={teff:.0f} K — exactly the regime STEHM was calibrated for.")
    if teff >= 6000 and teff <= 7500:
        return SubScore("Stellar type", 0.80, w, "F dwarf",
                        f"Teff={teff:.0f} K (F-dwarf). STEHM results broadly transferable.")
    if teff >= 3700 and teff < 5000:
        return SubScore("Stellar type", 0.65, w, "K dwarf",
                        f"Teff={teff:.0f} K (K-dwarf). Modestly higher XUV than Sun; "
                        f"safe-size boundary may shift slightly upward.")
    if teff < 3700:
        return SubScore("Stellar type", 0.30, w, "M dwarf",
                        f"Teff={teff:.0f} K (M-dwarf). STEHM is not calibrated for "
                        f"M-dwarf XUV histories. Non-thermal escape and flares can "
                        f"substantially worsen atmosphere retention (Hill et al. 2026 §6).")
    return SubScore("Stellar type", 0.40, w, "Hot star",
                    f"Teff={teff:.0f} K — hotter than F. Intense radiation and "
                    f"short main-sequence lifetime reduce habitability prospects.")


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
) -> HabitabilityResult:
    subs = [
        _score_planet_size(planet.radius_earth),
        _score_habitable_zone(
            planet.semi_major_axis_au, planet.stellar_teff,
            planet.stellar_radius_sun, planet.stellar_mass_sun,
        ),
        _score_stellar_type(planet.stellar_teff),
        _score_toi_disposition(planet.disposition, planet.toi_number),
        _score_vetting_flags(vetting_verdict),
        _score_multisector(n_sectors_with_detections, n_sectors_observed),
    ]

    total_w = sum(s.weight for s in subs)
    hci = sum(s.score * s.weight for s in subs) / total_w * 100

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
    if planet.radius_earth is None:
        caveats.append(
            "Planet radius unknown — STEHM size threshold (0.8 R⊕) could not be "
            "evaluated. A neutral placeholder is used for this component."
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
        sub_scores=[asdict(s) for s in subs],
        caveats=caveats,
    )
