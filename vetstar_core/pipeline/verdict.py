"""Vetting checks (centroid, odd/even, secondary eclipse, shape, physics)
and the final verdict.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
from scipy.ndimage import median_filter

from .models import COMPANION_EB_HARD_RJUP, COMPANION_PLANET_CAP_RJUP, DURATION_MATCH_TOL_H, StarInfo

def centroid_check(
    t, mom_x, mom_y, t_start, t_end, pad=0.5
) -> dict:
    """Compare in-event vs out-of-event centroids."""
    in_mask = (t >= t_start) & (t <= t_end)
    oot_mask = ((t >= t_start - pad) & (t < t_start)) | (
        (t > t_end) & (t <= t_end + pad)
    )
    if in_mask.sum() < 5 or oot_mask.sum() < 5:
        return {"available": False}
    mx_oot, my_oot = np.median(mom_x[oot_mask]), np.median(mom_y[oot_mask])
    mx_in, my_in = np.median(mom_x[in_mask]), np.median(mom_y[in_mask])
    mx_std = max(np.std(mom_x[oot_mask]), 1e-6)
    my_std = max(np.std(mom_y[oot_mask]), 1e-6)
    return {
        "available": True,
        "shift_col_px": float(mx_in - mx_oot),
        "shift_row_px": float(my_in - my_oot),
        "shift_col_sigma": float((mx_in - mx_oot) / mx_std),
        "shift_row_sigma": float((my_in - my_oot) / my_std),
        "on_target": bool(
            abs((mx_in - mx_oot) / mx_std) < 3
            and abs((my_in - my_oot) / my_std) < 3
        ),
    }


def count_events_by_parity(events, period, t0, half_window) -> tuple:
    """Count detected events that fall inside an odd vs an even folded window.

    An event counts when its midpoint lies within ``half_window`` (plus the
    event's own half-width) of a predicted transit at ``t0 + n·period``.
    Returns ``(n_odd, n_even)``.
    """
    n_odd = n_even = 0
    for ev in events or ():
        t_mid = 0.5 * (ev["t_start"] + ev["t_end"])
        cyc = int(np.round((t_mid - t0) / period))
        offset = abs(t_mid - t0 - cyc * period)
        if offset > half_window + 0.5 * (ev["t_end"] - ev["t_start"]):
            continue
        if cyc % 2 == 1:
            n_odd += 1
        else:
            n_even += 1
    return n_odd, n_even


def odd_even_check(t, f, period, t0, duration, odd_even_sigma: float = 3.0,
                   events: Optional[list] = None) -> dict:
    """Compare even- vs odd-numbered transits.  Big depth difference -> EB.

    ``odd_even_sigma`` is the user-tunable EB-flag threshold (default 3σ).

    ``events`` are the discrete dips from :func:`detect_events`. When given,
    the test only runs if at least one real event lands in an odd window and
    one in an even window. Counting in-window *points* is not enough: with a
    single transit and a spurious BLS period, one parity holds the transit and
    the other holds only out-of-transit noise, which reads as a huge mismatch.
    """
    if period is None or period <= 0:
        return {"available": False, "reason": "no period"}
    # phase folded; cycle number
    cycle = np.round((t - t0) / period).astype(int)
    half = duration * 0.7
    in_tr = np.abs((t - t0 - cycle * period)) < half
    if in_tr.sum() < 5:
        return {"available": False, "reason": "no in-transit points"}
    odd = in_tr & (cycle % 2 == 1)
    even = in_tr & (cycle % 2 == 0)
    n_odd_ev = n_even_ev = None
    if events is not None:
        n_odd_ev, n_even_ev = count_events_by_parity(events, period, t0, half)
    if (odd.sum() < 3 or even.sum() < 3
            or (events is not None and (n_odd_ev < 1 or n_even_ev < 1))):
        out = {
            "available": False,
            "reason": "insufficient odd or even transits",
            "n_odd": int(odd.sum()),
            "n_even": int(even.sum()),
        }
        if events is not None:
            out["n_odd_events"] = n_odd_ev
            out["n_even_events"] = n_even_ev
        return out
    d_odd = 1.0 - np.median(f[odd])
    d_even = 1.0 - np.median(f[even])
    err_odd = np.std(f[odd]) / np.sqrt(odd.sum())
    err_even = np.std(f[even]) / np.sqrt(even.sum())
    diff = d_odd - d_even
    diff_err = math.sqrt(err_odd**2 + err_even**2)
    sigma = abs(diff) / diff_err if diff_err > 0 else 0
    out = {
        "available": True,
        "depth_odd": float(d_odd),
        "depth_even": float(d_even),
        "difference": float(diff),
        "sigma": float(sigma),
        "n_odd": int(odd.sum()),
        "n_even": int(even.sum()),
        "flag_eb": bool(sigma > odd_even_sigma),
        "threshold_sigma": float(odd_even_sigma),
    }
    if events is not None:
        out["n_odd_events"] = n_odd_ev
        out["n_even_events"] = n_even_ev
    return out


def secondary_eclipse_search(t, f, period, t0, duration, secondary_sigma: float = 3.0) -> dict:
    """Look at phase 0.5 for a secondary dip.

    ``secondary_sigma`` is the user-tunable detection threshold (default 3σ).
    """
    if period is None or period <= 0:
        return {"available": False, "reason": "no period"}
    phase = ((t - t0) / period) % 1.0
    in_sec = np.abs(phase - 0.5) < (duration / period / 1.5)
    if in_sec.sum() < 5:
        return {"available": False, "reason": "no phase-0.5 coverage"}
    f_sec = np.median(f[in_sec])
    oot = (phase > 0.2) & (phase < 0.4) | (phase > 0.6) & (phase < 0.8)
    if oot.sum() < 10:
        return {"available": False, "reason": "no oot baseline"}
    baseline = np.median(f[oot])
    depth = baseline - f_sec
    noise = np.std(f[oot]) / np.sqrt(in_sec.sum())
    sigma = depth / noise if noise > 0 else 0
    return {
        "available": True,
        "depth": float(depth),
        "sigma": float(sigma),
        "detected": bool(sigma > secondary_sigma),
        "threshold_sigma": float(secondary_sigma),
    }


def measure_shape(t, f, t_start, t_end) -> dict:
    """Estimate ingress/egress and flat-bottom durations from a single event."""
    pad = 0.3 * (t_end - t_start)
    m = (t > t_start - pad) & (t < t_end + pad)
    if m.sum() < 30:
        return {"available": False}
    tt, ff = t[m], f[m]
    fs = median_filter(ff, size=11)
    baseline = np.median(fs[(tt < t_start) | (tt > t_end)])
    minf = float(fs.min())
    half_depth = baseline - 0.5 * (baseline - minf)
    # Find first/last point that crosses half-depth
    cross = fs < half_depth
    if cross.sum() < 5:
        return {"available": False}
    idx = np.where(cross)[0]
    t14 = float(tt[idx[-1]] - tt[idx[0]])
    # Find flat-bottom (within 10% of min)
    flat = fs < (minf + 0.10 * (baseline - minf))
    if flat.sum() >= 3:
        idx2 = np.where(flat)[0]
        t23 = float(tt[idx2[-1]] - tt[idx2[0]])
    else:
        t23 = 0.0
    return {
        "available": True,
        "t14_d": t14,
        "t23_d": t23,
        "t14_hours": t14 * 24,
        "t23_hours": t23 * 24,
        "t23_over_t14": float(t23 / t14) if t14 > 0 else 0,
        "ingress_d": float((t14 - t23) / 2) if t14 > t23 else 0.0,
        "shape_class": (
            "U (flat-bottomed)"
            if t14 > 0 and t23 / t14 > 0.4
            else "V (grazing/pointed)"
            if t14 > 0
            else "unknown"
        ),
    }


def physics_interpretation(star: StarInfo, depth: float, t14_d: float) -> dict:
    """
    Compute companion radius and a sanity-check period for a CENTRAL transit.
    Apply CROWDSAP dilution correction if available.
    """
    if star.radius is None or depth is None:
        return {"available": False}

    obs_depth = depth
    true_depth = obs_depth / star.crowdsap if star.crowdsap else obs_depth
    # Cap to physical range
    true_depth = min(max(true_depth, 0.0), 0.99)
    ratio = math.sqrt(true_depth)
    R_sun_in_R_jup = 9.73
    R_comp_Rsun = ratio * star.radius
    R_comp_Rjup = R_comp_Rsun * R_sun_in_R_jup

    # Estimate mass if missing (rough): use radius+logg if available
    M_sun_est = None
    if star.logg is not None and star.radius is not None:
        # g = GM/R^2 -> M = g R^2 / G
        G = 6.674e-11
        Rsun_m = 6.96e8
        Msun_kg = 1.989e30
        g_cgs = 10 ** star.logg          # cm/s^2
        g_si = g_cgs / 100.0
        M = g_si * (star.radius * Rsun_m) ** 2 / G
        M_sun_est = M / Msun_kg

    # Central-transit period from T14 (rough)
    P_central_d = None
    if t14_d and t14_d > 0 and (star.mass or M_sun_est):
        Mstar_Msun = star.mass or M_sun_est
        G = 6.674e-11
        Msun = 1.989e30
        Rsun_m = 6.96e8
        day = 86400
        Mstar = Mstar_Msun * Msun
        Rstar = star.radius * Rsun_m
        T14_s = t14_d * day
        P_central_s = G * Mstar * math.pi * T14_s**3 / (4 * Rstar**3)
        P_central_d = float(P_central_s / day)

    # Categorize companion
    if R_comp_Rjup < 2.2:
        category = "Planet-sized"
    elif R_comp_Rjup < 7:
        category = "Brown dwarf / very-low-mass star sized"
    elif R_comp_Rjup < 20:
        category = "M-dwarf sized"
    else:
        category = "Stellar (G-K or earlier)"

    return {
        "available": True,
        "observed_depth": obs_depth,
        "dilution_corrected_depth": true_depth,
        "ratio_companion_over_star": ratio,
        "R_companion_Rsun": R_comp_Rsun,
        "R_companion_Rjup": R_comp_Rjup,
        "category": category,
        "is_planet_candidate": R_comp_Rjup < 2.2,
        "M_star_estimated_Msun": M_sun_est,
        "P_central_implied_d": P_central_d,
    }


def make_verdict(
    n_events: int,
    physics: dict,
    centroid: dict,
    odd_even: dict,
    secondary: dict,
    bls_sde: float,
    duration_consistent: Optional[bool] = None,
    duration_tol_h: float = DURATION_MATCH_TOL_H,
    bls_variability: bool = False,
) -> dict:
    flags = []
    reasons = []
    confidence = 0.5

    # No detection at all
    if n_events == 0 and bls_sde < 7:
        return {
            "headline": "No significant transit/eclipse signal",
            "category": "no_signal",
            "confidence": 0.8,
            "flags": [],
            "reasons": ["No discrete dip events found and BLS SDE < 7."],
        }
    # A high SDE alone is not a detection when the peak is stellar
    # variability: BLS fits a box to anything periodic.
    if n_events == 0 and bls_variability:
        return {
            "headline": "No significant transit/eclipse signal",
            "category": "no_signal",
            "confidence": 0.7,
            "flags": ["bls_peak_is_variability"],
            "reasons": [
                f"No discrete dip events found. The BLS peak (SDE {bls_sde:.1f}) "
                "follows smooth, sinusoid-like variability, not a box-shaped dip. "
                "Try the high stellar variability detrend."
            ],
        }

    # Physics-based companion size. A radius just over the planetary cap is
    # BORDERLINE, not an automatic EB — only a radius well above the cap is a
    # hard EB indicator on size alone.
    companion_borderline = False
    if physics.get("available"):
        R = physics["R_companion_Rjup"]
        if R > COMPANION_EB_HARD_RJUP:
            flags.append("companion_too_large_for_planet")
            reasons.append(
                f"Implied companion radius {R:.1f} R_Jup ({physics['category']}) — "
                f"far above the planetary cap (~{COMPANION_PLANET_CAP_RJUP} R_Jup); "
                "stellar / brown-dwarf sized."
            )
        elif R > COMPANION_PLANET_CAP_RJUP:
            companion_borderline = True
            flags.append("companion_borderline_large")
            reasons.append(
                f"Implied companion radius {R:.1f} R_Jup is just above the "
                f"~{COMPANION_PLANET_CAP_RJUP} R_Jup planetary cap — borderline. "
                "Size alone is not treated as proof of an eclipsing binary; an "
                "eclipse signature (secondary or odd/even) or an RV mass is "
                "needed to confirm."
            )

    # Centroid offset = blend
    if centroid.get("available") and centroid.get("on_target") is False:
        flags.append("centroid_offset")
        reasons.append("In-transit centroid shifts >3σ — possible background blend.")
    elif centroid.get("available") and centroid.get("on_target"):
        reasons.append("Centroid is on-target — not a background blend.")

    # Odd/even
    if odd_even.get("available") and odd_even.get("flag_eb"):
        flags.append("odd_even_mismatch")
        reasons.append(
            f"Odd vs even transits differ at {odd_even['sigma']:.1f}σ — eclipsing-binary indicator."
        )

    # Secondary eclipse
    if secondary.get("available") and secondary.get("detected"):
        flags.append("secondary_eclipse_detected")
        reasons.append(
            f"Secondary eclipse detected at phase 0.5 ({secondary['sigma']:.1f}σ) — eclipsing-binary indicator."
        )

    # Transit-duration corroboration (±duration_tol_h). Consistent durations
    # across events support a single real transiting body; large variation is a
    # caution flag (blends / multiple signals) but is not, by itself, an EB call.
    if duration_consistent is True:
        reasons.append(
            f"Transit durations agree across events to within ±{duration_tol_h:.2f} h "
            "— consistent with a single repeating transit."
        )
    elif duration_consistent is False:
        flags.append("duration_inconsistent")
        reasons.append(
            f"Transit durations vary by more than ±{duration_tol_h:.2f} h between "
            "events — check for blends or more than one signal."
        )

    # Single-transit case
    if n_events == 1:
        reasons.append(
            "Only one in-sector event — period unconstrained; need follow-up or future sectors."
        )

    # Decide
    has_eclipse_indicator = (
        "secondary_eclipse_detected" in flags or "odd_even_mismatch" in flags
    )
    if "companion_too_large_for_planet" in flags or has_eclipse_indicator:
        category = "eclipsing_binary_candidate"
        headline = "Eclipsing binary candidate"
        confidence = 0.85
    elif "centroid_offset" in flags:
        category = "false_positive_blend"
        headline = "Likely background blend (false positive)"
        confidence = 0.75
    elif companion_borderline:
        # Borderline size, no corroborating eclipse signature -> large planet
        # candidate pending RV (matches mature-pipeline behaviour).
        category = "planet_candidate"
        headline = "Large planet candidate (RV needed to exclude brown dwarf)"
        confidence = 0.55
    elif physics.get("available") and physics.get("is_planet_candidate"):
        category = "planet_candidate"
        headline = "Planet candidate (further vetting required)"
        confidence = 0.60
    else:
        category = "ambiguous"
        headline = "Ambiguous signal — manual review needed"
        confidence = 0.40

    return {
        "headline": headline,
        "category": category,
        "confidence": confidence,
        "flags": flags,
        "reasons": reasons,
    }
