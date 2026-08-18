"""Transit / eclipse vetting pipeline — compact Pi port of ``backend/app/pipeline.py``.

Keeps the science-defining pieces (BLS, Lomb-Scargle, adaptive event
detection with gap handling, centroid check, odd/even, secondary eclipse,
shape, physics, verdict) and drops the online cross-match / detrend /
HCI / ExoMiner / DVT / multi-sector layers, all of which need extra deps
or remote services.

All functions are pure — arrays in, dicts out — so the GUI, CLI, and PDF
report all reuse the same code path.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np
from astropy.timeseries import BoxLeastSquares, LombScargle
from scipy.ndimage import median_filter

from .fitsio import LightCurve, StarInfo


DURATION_MATCH_TOL_H = 0.05
COMPANION_PLANET_CAP_RJUP = 2.2
COMPANION_EB_HARD_RJUP = 4.0


@dataclass
class VettingResult:
    star: StarInfo
    summary: dict = field(default_factory=dict)
    bls: dict = field(default_factory=dict)
    lomb_scargle: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    centroid: dict = field(default_factory=dict)
    odd_even: dict = field(default_factory=dict)
    secondary: dict = field(default_factory=dict)
    shape: dict = field(default_factory=dict)
    physics: dict = field(default_factory=dict)
    verdict: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["star"] = asdict(self.star)
        return d


# ----------------------------------------------------------------------
# Cleaning
# ----------------------------------------------------------------------
def clean_lightcurve(lc: LightCurve):
    """Apply quality mask + finite filter + normalise by median.

    Returns ``(t, f, fe, mom_x, mom_y)`` — centroid arrays aligned to the
    surviving samples so :func:`centroid_check` never sees a length
    mismatch.
    """
    t = lc.t
    flux = lc.flux
    flux_err = lc.flux_err
    quality = lc.quality
    mask = np.isfinite(t) & np.isfinite(flux) & (flux > 0)
    if quality is not None:
        mask &= quality == 0
    if flux_err is not None:
        mask &= np.isfinite(flux_err)
    t = t[mask]
    flux = flux[mask]
    fe = flux_err[mask] if flux_err is not None else np.full_like(flux, np.nanstd(flux))
    med = float(np.nanmedian(flux))
    if not np.isfinite(med) or med == 0:
        med = 1.0
    f = flux / med
    fe = fe / med
    mx = lc.mom_x[mask] if lc.mom_x is not None else None
    my = lc.mom_y[mask] if lc.mom_y is not None else None
    return t, f, fe, mx, my


# ----------------------------------------------------------------------
# Periodograms
# ----------------------------------------------------------------------
def run_lomb_scargle(t, f, fe, p_min=0.1, p_max=20.0) -> dict:
    ls = LombScargle(t, f, fe)
    freq, power = ls.autopower(
        minimum_frequency=1.0 / p_max,
        maximum_frequency=1.0 / p_min,
        samples_per_peak=15,
    )
    periods = 1.0 / freq
    ip = int(np.argmax(power))
    try:
        fap = float(ls.false_alarm_probability(power[ip]))
    except Exception:
        fap = None
    return {
        "top_period": float(periods[ip]),
        "top_power": float(power[ip]),
        "false_alarm_prob": fap,
        "periodogram": {
            "periods": periods.tolist()[::10],
            "power": power.tolist()[::10],
        },
    }


def run_bls(t, f, fe, p_min=0.5, p_max=None, n_periods=8000) -> dict:
    """Blind BLS scan. ``n_periods`` is capped to 8000 by default (vs 20k
    on the server) — a 1 GB Pi 4 finishes a single-sector run in ~8 s
    and stays under 250 MB RSS."""
    span = float(t.max() - t.min())
    if p_max is None:
        p_max = max(p_min * 2, span * 0.7)
    durations = np.array([0.05, 0.1, 0.15, 0.2, 0.3])
    bls = BoxLeastSquares(t, f, fe)
    periods = np.linspace(p_min, p_max, n_periods)
    res = bls.power(periods, durations)
    ib = int(np.argmax(res.power))
    std = float(np.std(res.power))
    sde = float((res.power[ib] - np.median(res.power)) / std) if std > 0 else 0.0
    return {
        "period": float(res.period[ib]),
        "t0": float(res.transit_time[ib]),
        "duration": float(res.duration[ib]),
        "depth": float(res.depth[ib]),
        "power": float(res.power[ib]),
        "sde": sde,
        "n_transits_in_window": int(
            np.floor((t.max() - res.transit_time[ib]) / res.period[ib])
            - np.ceil((t.min() - res.transit_time[ib]) / res.period[ib])
            + 1
        ),
        "periodogram": {
            "periods": periods.tolist()[::20],
            "power": res.power.tolist()[::20],
        },
    }


# ----------------------------------------------------------------------
# Adaptive event detection (same algorithm as the server, trimmed comments)
# ----------------------------------------------------------------------
def detect_events(t, f, threshold=0.997, min_pts=10, min_snr=4.0, max_gap=5) -> list:
    fs = median_filter(f, size=21)
    baseline = float(np.nanmedian(fs))

    t_arr = np.asarray(t)
    dt = np.diff(t_arr)
    pos_dt = dt[dt > 0]
    if pos_dt.size:
        cadence = float(np.median(pos_dt))
        gap_threshold = max(5 * cadence, 0.2)
        gap_after = np.concatenate([dt > gap_threshold, [False]])
    else:
        gap_after = np.zeros(len(t_arr), dtype=bool)
        cadence = 0.0

    raw_mad = float(1.4826 * np.nanmedian(np.abs(f - baseline)))
    rough_mask = np.abs(fs - baseline) < 3 * max(raw_mad, 1e-6)
    if rough_mask.sum() > 100:
        scatter = float(1.4826 * np.nanmedian(np.abs(f[rough_mask] - np.nanmedian(f[rough_mask]))))
    else:
        scatter = raw_mad
    if scatter <= 0:
        scatter = 1e-5

    adaptive_threshold = baseline - min_snr * scatter
    effective_threshold = max(threshold, adaptive_threshold)

    in_dip = fs < effective_threshold

    # Suppress median-filter edge artefacts near time gaps.
    edge_pad = 10
    n = len(in_dip)
    for gi in np.where(gap_after)[0]:
        lo = max(0, gi - edge_pad + 1)
        hi = min(n, gi + edge_pad + 1)
        in_dip[lo:hi] = False

    # Bridge tiny gaps inside a real dip.
    if max_gap > 0:
        i = 0
        while i < n:
            if not in_dip[i]:
                start = i
                while i < n and not in_dip[i]:
                    i += 1
                if (
                    0 < start and i < n
                    and (i - start) <= max_gap
                    and not gap_after[start - 1 : i].any()
                ):
                    in_dip[start:i] = True
            else:
                i += 1

    events = []
    i = 0
    while i < len(t):
        if in_dip[i]:
            start = i
            while i < len(t) and in_dip[i]:
                if gap_after[i]:
                    i += 1
                    break
                i += 1
            end = i
            if end - start >= min_pts:
                seg = f[start:end]
                n_pts = end - start
                seg_min = float(fs[start:end].min())
                depth = float(baseline - seg_min)
                mean_depth = float(baseline - np.nanmean(seg))
                snr = mean_depth * np.sqrt(n_pts) / scatter if scatter > 0 else 0.0
                if snr >= min_snr and mean_depth > 0:
                    events.append({
                        "t_start": float(t[start]),
                        "t_end": float(t[end - 1]),
                        "duration_d": float(t[end - 1] - t[start]),
                        "min_flux": seg_min,
                        "depth": depth,
                        "depth_snr": float(snr),
                        "n_points": int(n_pts),
                    })
        else:
            i += 1
    return events


# ----------------------------------------------------------------------
# Per-event diagnostics
# ----------------------------------------------------------------------
def centroid_check(t, mom_x, mom_y, t_start, t_end, pad=0.5) -> dict:
    if mom_x is None or mom_y is None:
        return {"available": False, "reason": "no centroid columns"}
    in_mask = (t >= t_start) & (t <= t_end)
    oot_mask = ((t >= t_start - pad) & (t < t_start)) | (
        (t > t_end) & (t <= t_end + pad)
    )
    if in_mask.sum() < 5 or oot_mask.sum() < 5:
        return {"available": False, "reason": "insufficient baseline"}
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


def odd_even_check(t, f, period, t0, duration, sigma_thr: float = 3.0) -> dict:
    if period is None or period <= 0:
        return {"available": False, "reason": "no period"}
    cycle = np.round((t - t0) / period).astype(int)
    half = duration * 0.7
    in_tr = np.abs((t - t0 - cycle * period)) < half
    if in_tr.sum() < 5:
        return {"available": False, "reason": "no in-transit points"}
    odd = in_tr & (cycle % 2 == 1)
    even = in_tr & (cycle % 2 == 0)
    if odd.sum() < 3 or even.sum() < 3:
        return {"available": False, "reason": "insufficient odd/even transits",
                "n_odd": int(odd.sum()), "n_even": int(even.sum())}
    d_odd = 1.0 - np.median(f[odd])
    d_even = 1.0 - np.median(f[even])
    err_odd = np.std(f[odd]) / np.sqrt(odd.sum())
    err_even = np.std(f[even]) / np.sqrt(even.sum())
    diff = d_odd - d_even
    diff_err = math.sqrt(err_odd ** 2 + err_even ** 2)
    sigma = abs(diff) / diff_err if diff_err > 0 else 0.0
    return {
        "available": True,
        "depth_odd": float(d_odd),
        "depth_even": float(d_even),
        "difference": float(diff),
        "sigma": float(sigma),
        "n_odd": int(odd.sum()),
        "n_even": int(even.sum()),
        "flag_eb": bool(sigma > sigma_thr),
        "threshold_sigma": float(sigma_thr),
    }


def secondary_eclipse_search(t, f, period, t0, duration, sigma_thr: float = 3.0) -> dict:
    if period is None or period <= 0:
        return {"available": False, "reason": "no period"}
    phase = ((t - t0) / period) % 1.0
    in_sec = np.abs(phase - 0.5) < (duration / period / 1.5)
    if in_sec.sum() < 5:
        return {"available": False, "reason": "no phase-0.5 coverage"}
    f_sec = np.median(f[in_sec])
    oot = ((phase > 0.2) & (phase < 0.4)) | ((phase > 0.6) & (phase < 0.8))
    if oot.sum() < 10:
        return {"available": False, "reason": "no oot baseline"}
    baseline = np.median(f[oot])
    depth = baseline - f_sec
    noise = np.std(f[oot]) / np.sqrt(in_sec.sum())
    sigma = depth / noise if noise > 0 else 0.0
    return {
        "available": True,
        "depth": float(depth),
        "sigma": float(sigma),
        "detected": bool(sigma > sigma_thr),
        "threshold_sigma": float(sigma_thr),
    }


def measure_shape(t, f, t_start, t_end) -> dict:
    pad = 0.3 * (t_end - t_start)
    m = (t > t_start - pad) & (t < t_end + pad)
    if m.sum() < 30:
        return {"available": False}
    tt, ff = t[m], f[m]
    fs = median_filter(ff, size=11)
    baseline = float(np.median(fs[(tt < t_start) | (tt > t_end)]))
    minf = float(fs.min())
    half_depth = baseline - 0.5 * (baseline - minf)
    cross = fs < half_depth
    if cross.sum() < 5:
        return {"available": False}
    idx = np.where(cross)[0]
    t14 = float(tt[idx[-1]] - tt[idx[0]])
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
        "t23_over_t14": float(t23 / t14) if t14 > 0 else 0.0,
        "shape_class": (
            "U (flat-bottomed)" if t14 > 0 and t23 / t14 > 0.4
            else "V (grazing/pointed)" if t14 > 0
            else "unknown"
        ),
    }


def physics_interpretation(star: StarInfo, depth: float, t14_d: float) -> dict:
    if star.radius is None or depth is None:
        return {"available": False}
    obs_depth = depth
    true_depth = obs_depth / star.crowdsap if star.crowdsap else obs_depth
    true_depth = min(max(true_depth, 0.0), 0.99)
    ratio = math.sqrt(true_depth)
    R_sun_in_R_jup = 9.73
    R_comp_Rsun = ratio * star.radius
    R_comp_Rjup = R_comp_Rsun * R_sun_in_R_jup

    M_sun_est = None
    if star.logg is not None and star.radius is not None:
        G = 6.674e-11
        Rsun_m = 6.96e8
        Msun_kg = 1.989e30
        g_cgs = 10 ** star.logg
        g_si = g_cgs / 100.0
        M = g_si * (star.radius * Rsun_m) ** 2 / G
        M_sun_est = M / Msun_kg

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
        P_central_s = G * Mstar * math.pi * T14_s ** 3 / (4 * Rstar ** 3)
        P_central_d = float(P_central_s / day)

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


def make_verdict(n_events, physics, centroid, odd_even, secondary, bls_sde) -> dict:
    flags = []
    reasons = []

    if n_events == 0 and bls_sde < 7:
        return {
            "headline": "No significant transit/eclipse signal",
            "category": "no_signal",
            "confidence": 0.8,
            "flags": [],
            "reasons": ["No discrete dip events found and BLS SDE < 7."],
        }

    companion_borderline = False
    if physics.get("available"):
        R = physics["R_companion_Rjup"]
        if R > COMPANION_EB_HARD_RJUP:
            flags.append("companion_too_large_for_planet")
            reasons.append(
                f"Implied companion radius {R:.1f} R_Jup ({physics['category']}) — "
                f"far above the planetary cap (~{COMPANION_PLANET_CAP_RJUP} R_Jup)."
            )
        elif R > COMPANION_PLANET_CAP_RJUP:
            companion_borderline = True
            flags.append("companion_borderline_large")
            reasons.append(
                f"Implied companion radius {R:.1f} R_Jup is just above the "
                f"~{COMPANION_PLANET_CAP_RJUP} R_Jup planetary cap — borderline."
            )

    if centroid.get("available") and centroid.get("on_target") is False:
        flags.append("centroid_offset")
        reasons.append("In-transit centroid shifts >3σ — possible background blend.")
    elif centroid.get("available") and centroid.get("on_target"):
        reasons.append("Centroid is on-target — not a background blend.")

    if odd_even.get("available") and odd_even.get("flag_eb"):
        flags.append("odd_even_mismatch")
        reasons.append(
            f"Odd vs even transits differ at {odd_even['sigma']:.1f}σ — eclipsing-binary indicator."
        )

    if secondary.get("available") and secondary.get("detected"):
        flags.append("secondary_eclipse_detected")
        reasons.append(
            f"Secondary eclipse detected at phase 0.5 ({secondary['sigma']:.1f}σ) — eclipsing-binary indicator."
        )

    if n_events == 1:
        reasons.append("Only one event — period unconstrained; need follow-up or more data.")

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


# ----------------------------------------------------------------------
# Top-level driver
# ----------------------------------------------------------------------
def run_vetting(
    lc: LightCurve,
    detect_threshold: float = 0.997,
    detect_min_snr: float = 4.0,
    secondary_sigma: float = 3.0,
    odd_even_sigma: float = 3.0,
) -> VettingResult:
    """Run the whole pipeline on a :class:`LightCurve` and return a
    :class:`VettingResult`.  Safe on 1 GB RAM.
    """
    t, f, fe, mx, my = clean_lightcurve(lc)
    if len(t) < 50:
        raise ValueError(
            f"Only {len(t)} usable cadences after quality/finite filtering — "
            "not enough to run the pipeline."
        )

    ls = run_lomb_scargle(t, f, fe)
    bls = run_bls(t, f, fe)
    events = detect_events(t, f, threshold=detect_threshold, min_snr=detect_min_snr)

    if events:
        primary = max(events, key=lambda e: e["depth"])
    else:
        primary = None

    if primary is not None:
        centroid = centroid_check(t, mx, my, primary["t_start"], primary["t_end"])
        shape = measure_shape(t, f, primary["t_start"], primary["t_end"])
    else:
        centroid = {"available": False, "reason": "no events"}
        shape = {"available": False}

    if bls and bls.get("period") and bls.get("t0") is not None and bls.get("duration"):
        odd_even = odd_even_check(
            t, f, bls["period"], bls["t0"], bls["duration"], sigma_thr=odd_even_sigma,
        )
        secondary = secondary_eclipse_search(
            t, f, bls["period"], bls["t0"], bls["duration"], sigma_thr=secondary_sigma,
        )
    else:
        odd_even = {"available": False, "reason": "no BLS period"}
        secondary = {"available": False, "reason": "no BLS period"}

    depth = primary["depth"] if primary else bls.get("depth")
    t14_d = shape.get("t14_d") if shape.get("available") else bls.get("duration")
    physics = physics_interpretation(lc.star, depth, t14_d)

    verdict = make_verdict(
        n_events=len(events),
        physics=physics,
        centroid=centroid,
        odd_even=odd_even,
        secondary=secondary,
        bls_sde=bls.get("sde", 0.0),
    )

    summary = {
        "n_cadences_used": int(len(t)),
        "time_span_d": float(t.max() - t.min()),
        "n_events_detected": len(events),
        "detect_threshold": detect_threshold,
        "detect_min_snr": detect_min_snr,
    }

    return VettingResult(
        star=lc.star,
        summary=summary,
        bls=bls,
        lomb_scargle=ls,
        events=events,
        centroid=centroid,
        odd_even=odd_even,
        secondary=secondary,
        shape=shape,
        physics=physics,
        verdict=verdict,
    )
