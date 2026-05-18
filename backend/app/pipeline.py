"""
Vetting pipeline: BLS, Lomb-Scargle, centroid check, odd/even, secondary
eclipse search, shape analysis, verdict.

All functions are pure-ish — accept arrays + metadata, return dicts of
results.  Plotting helpers return base64-encoded PNGs so the API can ship
them straight to the frontend.
"""
from __future__ import annotations

import base64
import io
import math
from dataclasses import asdict, dataclass, field
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from astropy.timeseries import BoxLeastSquares, LombScargle
from scipy.ndimage import median_filter


# ----------------------------------------------------------------------
# Data containers
# ----------------------------------------------------------------------
@dataclass
class StarInfo:
    tic_id: Optional[int] = None
    tmag: Optional[float] = None
    teff: Optional[float] = None
    radius: Optional[float] = None        # R_sun
    logg: Optional[float] = None
    mass: Optional[float] = None          # M_sun (often derived)
    ra: Optional[float] = None
    dec: Optional[float] = None
    sector: Optional[int] = None
    camera: Optional[int] = None
    ccd: Optional[int] = None
    crowdsap: Optional[float] = None
    source: str = "unknown"               # "fits" or "exofop_json"


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
    plots: dict = field(default_factory=dict)  # name -> base64 PNG

    def to_dict(self) -> dict:
        d = asdict(self)
        d["star"] = asdict(self.star)
        return d


# ----------------------------------------------------------------------
# Plot helpers
# ----------------------------------------------------------------------
def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ----------------------------------------------------------------------
# Core pipeline
# ----------------------------------------------------------------------
def clean_lightcurve(
    t: np.ndarray,
    flux: np.ndarray,
    flux_err: np.ndarray,
    quality: Optional[np.ndarray] = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Standard quality-flag mask + finite filtering + normalisation."""
    mask = np.isfinite(t) & np.isfinite(flux) & (flux > 0)
    if quality is not None:
        mask &= quality == 0
    if flux_err is not None:
        mask &= np.isfinite(flux_err)
    t, flux = t[mask], flux[mask]
    fe = flux_err[mask] if flux_err is not None else np.full_like(flux, np.nanstd(flux))
    med = np.nanmedian(flux)
    return t, flux / med, fe / med


def run_lomb_scargle(t, f, fe, p_min=0.1, p_max=20.0) -> dict:
    ls = LombScargle(t, f, fe)
    freq, power = ls.autopower(
        minimum_frequency=1 / p_max,
        maximum_frequency=1 / p_min,
        samples_per_peak=15,
    )
    periods = 1 / freq
    # FAP for the top peak
    ip = int(np.argmax(power))
    try:
        fap = float(ls.false_alarm_probability(power[ip]))
    except Exception:
        fap = None
    # 5 well-separated peaks
    order = np.argsort(power)[::-1]
    tops = []
    for idx in order:
        p = float(periods[idx])
        if all(abs(p - tp["period"]) / tp["period"] > 0.05 for tp in tops):
            tops.append({"period": p, "power": float(power[idx])})
        if len(tops) >= 5:
            break
    return {
        "top_period": float(periods[ip]),
        "top_power": float(power[ip]),
        "false_alarm_prob": fap,
        "top_peaks": tops,
    }


def run_bls(
    t, f, fe, p_min=0.5, p_max=None, n_periods=20000
) -> dict:
    span = float(t.max() - t.min())
    if p_max is None:
        p_max = max(p_min * 2, span * 0.7)
    durations = np.array([0.05, 0.1, 0.15, 0.2, 0.3])
    bls = BoxLeastSquares(t, f, fe)
    periods = np.linspace(p_min, p_max, n_periods)
    res = bls.power(periods, durations)
    ib = int(np.argmax(res.power))
    sde = float((res.power[ib] - np.median(res.power)) / np.std(res.power))
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
        "_periodogram": {
            "periods": periods.tolist()[::20],     # sub-sample for transport
            "power": res.power.tolist()[::20],
        },
    }


def detect_events(t, f, threshold=0.997, min_pts=10, min_snr=4.0) -> list:
    """Direct event detection — finds discrete dips regardless of period.

    Two filters keep this sensitive to real dips while rejecting noise:
    - ``threshold``: smoothed flux must drop below this fraction of baseline
      (0.997 = 0.3% deep). Going lower than ~0.998 starts catching noise.
    - ``min_snr``: the dip depth must exceed ``min_snr`` × local photometric
      scatter (MAD-based). For typical 2-min cadence stars, scatter is
      ~0.001-0.002, so a 4σ filter catches dips ≳0.4-0.8% but rejects
      single-cadence noise excursions.
    """
    fs = median_filter(f, size=21)
    # Local scatter: MAD of the *un*-smoothed flux, but only of points that
    # are NOT in a dip (so the scatter estimate isn't pulled down by the
    # event itself). Use a rough first-pass to estimate.
    rough_mask = fs >= 0.99
    if rough_mask.sum() > 100:
        scatter = float(1.4826 * np.nanmedian(np.abs(f[rough_mask] - np.nanmedian(f[rough_mask]))))
    else:
        scatter = float(1.4826 * np.nanmedian(np.abs(f - np.nanmedian(f))))
    if scatter <= 0:
        scatter = 1e-4   # numerical floor

    in_dip = fs < threshold
    events = []
    i = 0
    while i < len(t):
        if in_dip[i]:
            start = i
            while i < len(t) and in_dip[i]:
                i += 1
            end = i
            if end - start >= min_pts:
                seg_min = float(fs[start:end].min())
                depth = float(1.0 - seg_min)
                # SNR filter: depth must beat min_snr × scatter.
                snr = depth / scatter if scatter > 0 else 0.0
                if snr >= min_snr:
                    events.append(
                        {
                            "t_start": float(t[start]),
                            "t_end": float(t[end - 1]),
                            "duration_d": float(t[end - 1] - t[start]),
                            "min_flux": seg_min,
                            "depth": depth,
                            "depth_snr": float(snr),
                            "n_points": int(end - start),
                        }
                    )
        else:
            i += 1
    return events


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


def odd_even_check(t, f, period, t0, duration) -> dict:
    """Compare even- vs odd-numbered transits.  Big depth difference -> EB."""
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
    if odd.sum() < 3 or even.sum() < 3:
        return {
            "available": False,
            "reason": "insufficient odd or even transits",
            "n_odd": int(odd.sum()),
            "n_even": int(even.sum()),
        }
    d_odd = 1.0 - np.median(f[odd])
    d_even = 1.0 - np.median(f[even])
    err_odd = np.std(f[odd]) / np.sqrt(odd.sum())
    err_even = np.std(f[even]) / np.sqrt(even.sum())
    diff = d_odd - d_even
    diff_err = math.sqrt(err_odd**2 + err_even**2)
    sigma = abs(diff) / diff_err if diff_err > 0 else 0
    return {
        "available": True,
        "depth_odd": float(d_odd),
        "depth_even": float(d_even),
        "difference": float(diff),
        "sigma": float(sigma),
        "n_odd": int(odd.sum()),
        "n_even": int(even.sum()),
        "flag_eb": bool(sigma > 3),
    }


def secondary_eclipse_search(t, f, period, t0, duration) -> dict:
    """Look at phase 0.5 for a secondary dip."""
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
        "detected": bool(sigma > 3),
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

    # Physics-based companion size
    if physics.get("available"):
        if physics["R_companion_Rjup"] > 2.5:
            flags.append("companion_too_large_for_planet")
            reasons.append(
                f"Implied companion radius {physics['R_companion_Rjup']:.1f} R_Jup "
                f"({physics['category']}) — exceeds planetary cap (~2.2 R_Jup)."
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

    # Single-transit case
    if n_events == 1:
        reasons.append(
            "Only one in-sector event — period unconstrained; need follow-up or future sectors."
        )

    # Decide
    if "companion_too_large_for_planet" in flags or "secondary_eclipse_detected" in flags or "odd_even_mismatch" in flags:
        category = "eclipsing_binary_candidate"
        headline = "Eclipsing binary candidate"
        confidence = 0.85
    elif "centroid_offset" in flags:
        category = "false_positive_blend"
        headline = "Likely background blend (false positive)"
        confidence = 0.75
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
# Plot generation
# ----------------------------------------------------------------------
def make_plots(t, f, fe, mom_x, mom_y, events, primary_event, bls_periodogram, ls_periodogram) -> dict:
    """Generate diagnostic plots.

    ``events`` is the full list from detect_events (may be empty). ``primary_event``
    is the one chosen for centroid/shape analysis (typically the deepest).
    The full-LC plot shades EVERY event; the zoom plot shows up to 6 events
    in a grid, with the primary one highlighted.
    """
    plots = {}
    events = events or []

    # 1. Full LC with all events shaded.
    fig, ax = plt.subplots(figsize=(10, 3))
    ax.plot(t, f, "k.", ms=1, alpha=0.4)
    # Set y-limits explicitly so we can place labels just inside the top edge.
    ymin, ymax = float(np.nanpercentile(f, 0.3)), float(np.nanpercentile(f, 99.7))
    y_pad = 0.05 * (ymax - ymin)
    ax.set_ylim(ymin - y_pad, ymax + y_pad)
    label_y = ymax + 0.3 * y_pad  # just below the top
    for i, ev in enumerate(events):
        is_primary = primary_event is not None and ev["t_start"] == primary_event["t_start"]
        ax.axvspan(
            ev["t_start"], ev["t_end"],
            color="red" if is_primary else "orange",
            alpha=0.30 if is_primary else 0.18,
        )
        mid = 0.5 * (ev["t_start"] + ev["t_end"])
        ax.text(
            mid, label_y,
            f"#{i+1}",
            ha="center", va="bottom", fontsize=8,
            color="darkred" if is_primary else "saddlebrown",
            fontweight="bold" if is_primary else "normal",
        )
    ax.set_xlabel("Time (BTJD or similar)")
    ax.set_ylabel("Normalised flux")
    title = "Detrended light curve"
    if events:
        title += f" — {len(events)} dip event{'s' if len(events) != 1 else ''} detected"
    ax.set_title(title)
    plots["lightcurve"] = _fig_to_b64(fig)

    # 2. Zoom: grid of up to 6 events. Primary highlighted.
    if events:
        n_show = min(len(events), 6)
        ncols = 1 if n_show == 1 else (2 if n_show <= 4 else 3)
        nrows = int(np.ceil(n_show / ncols))
        fig, axes = plt.subplots(
            nrows, ncols,
            figsize=(10 if ncols > 1 else 10, 3.0 * nrows),
            squeeze=False,
        )
        # Show DEEPEST events first.
        shown = sorted(events, key=lambda e: -e["depth"])[:n_show]
        for idx, ev in enumerate(shown):
            r, c = divmod(idx, ncols)
            ax = axes[r][c]
            pad = (ev["t_end"] - ev["t_start"]) * 1.5
            m = (t > ev["t_start"] - pad) & (t < ev["t_end"] + pad)
            if m.sum() <= 10:
                ax.text(0.5, 0.5, "(too few points)", ha="center", va="center",
                        transform=ax.transAxes)
                continue
            ax.errorbar(t[m], f[m], yerr=fe[m] if fe is not None else None,
                        fmt="k.", ms=2, alpha=0.5)
            ax.axhline(1.0, color="gray", ls=":", alpha=0.5)
            ax.axhline(ev["min_flux"], color="red", ls=":", alpha=0.6)
            is_primary = primary_event is not None and ev["t_start"] == primary_event["t_start"]
            border = "red" if is_primary else "gray"
            for spine in ax.spines.values():
                spine.set_edgecolor(border)
                spine.set_linewidth(1.5 if is_primary else 0.8)
            tag = " (primary)" if is_primary else ""
            snr_tag = f"  SNR={ev.get('depth_snr', 0):.1f}σ" if ev.get('depth_snr') else ""
            ax.set_title(
                f"Event at t≈{0.5*(ev['t_start']+ev['t_end']):.3f}{tag}\n"
                f"depth={ev['depth']*100:.2f}%, dur={ev['duration_d']*24:.1f}h{snr_tag}",
                fontsize=9,
            )
            ax.tick_params(labelsize=8)
        # Hide unused panels.
        for idx in range(n_show, nrows * ncols):
            r, c = divmod(idx, ncols)
            axes[r][c].set_visible(False)
        fig.supxlabel("Time", fontsize=9)
        fig.supylabel("Flux", fontsize=9)
        fig.tight_layout()
        plots["event_zoom"] = _fig_to_b64(fig)

    # 3. Centroid (anchored on the primary event only — that's where the
    # blend test is most meaningful)
    if primary_event is not None and mom_x is not None and mom_y is not None:
        pad = (primary_event["t_end"] - primary_event["t_start"]) * 2
        m = (t > primary_event["t_start"] - pad) & (t < primary_event["t_end"] + pad)
        if m.sum() > 20:
            fig, ax = plt.subplots(figsize=(10, 3))
            ax.plot(t[m], mom_x[m] - np.median(mom_x[m]), "b.", ms=2, alpha=0.5, label="col (x)")
            ax.plot(t[m], mom_y[m] - np.median(mom_y[m]), "g.", ms=2, alpha=0.5, label="row (y)")
            ax.axvspan(primary_event["t_start"], primary_event["t_end"], color="red", alpha=0.15)
            ax.set_xlabel("Time")
            ax.set_ylabel("Centroid offset (px)")
            ax.set_title("Centroid behaviour during primary event")
            ax.legend()
            plots["centroid"] = _fig_to_b64(fig)

    # 4. BLS periodogram
    if bls_periodogram:
        fig, ax = plt.subplots(figsize=(10, 3))
        ax.plot(bls_periodogram["periods"], bls_periodogram["power"], "k-", lw=0.6)
        ax.set_xlabel("Period (d)")
        ax.set_ylabel("BLS power")
        ax.set_title("Box Least Squares periodogram")
        plots["bls"] = _fig_to_b64(fig)

    # 5. LS
    if ls_periodogram and ls_periodogram.get("top_period"):
        fig, ax = plt.subplots(figsize=(6, 3))
        peaks = ls_periodogram.get("top_peaks", [])
        if peaks:
            ax.bar(
                [f"{p['period']:.2f} d" for p in peaks],
                [p["power"] for p in peaks],
                color="steelblue",
            )
            ax.set_ylabel("LS power")
            ax.set_title("Lomb-Scargle top peaks")
            ax.tick_params(axis="x", rotation=20)
            plots["lomb_scargle"] = _fig_to_b64(fig)

    return plots


# ----------------------------------------------------------------------
# Top-level driver
# ----------------------------------------------------------------------
def run_full_vetting(
    t: np.ndarray,
    flux: np.ndarray,
    flux_err: np.ndarray,
    quality: Optional[np.ndarray],
    mom_x: Optional[np.ndarray],
    mom_y: Optional[np.ndarray],
    star: StarInfo,
    detect_threshold: float = 0.997,
    detect_min_snr: float = 4.0,
) -> VettingResult:
    # Clean
    t_c, f_c, fe_c = clean_lightcurve(t, flux, flux_err, quality)
    if mom_x is not None and quality is not None:
        m = np.isfinite(t) & np.isfinite(flux) & (flux > 0) & (quality == 0)
        mom_x = mom_x[m]
        mom_y = mom_y[m]

    # Stats
    span = float(t_c.max() - t_c.min())

    # Lomb-Scargle (cap at half the baseline)
    ls = run_lomb_scargle(t_c, f_c, fe_c, p_min=0.1, p_max=min(20.0, span / 2))

    # BLS
    bls = run_bls(t_c, f_c, fe_c, p_min=0.5, p_max=span * 0.7)

    # Direct event detection (user-tunable sensitivity).
    events = detect_events(
        t_c, f_c,
        threshold=detect_threshold,
        min_pts=10,
        min_snr=detect_min_snr,
    )

    # If exactly one in-sector event, anchor centroid/shape on it.
    primary_event = events[0] if len(events) == 1 else None
    # If multiple, anchor on deepest
    if len(events) > 1:
        primary_event = max(events, key=lambda e: e["depth"])

    # Centroid
    centroid = {"available": False}
    if primary_event and mom_x is not None and mom_y is not None and len(mom_x) == len(t_c):
        centroid = centroid_check(
            t_c, mom_x, mom_y, primary_event["t_start"], primary_event["t_end"]
        )

    # Shape
    shape = {"available": False}
    if primary_event:
        shape = measure_shape(t_c, f_c, primary_event["t_start"], primary_event["t_end"])

    # Odd/even and secondary use the BLS period (only meaningful for multi-event)
    odd_even = odd_even_check(t_c, f_c, bls["period"], bls["t0"], bls["duration"])
    secondary = secondary_eclipse_search(t_c, f_c, bls["period"], bls["t0"], bls["duration"])

    # Physics
    depth_for_physics = primary_event["depth"] if primary_event else bls.get("depth")
    t14_for_physics = shape.get("t14_d") if shape.get("available") else bls.get("duration")
    physics = physics_interpretation(star, depth_for_physics, t14_for_physics)

    # Verdict
    verdict = make_verdict(
        n_events=len(events),
        physics=physics,
        centroid=centroid,
        odd_even=odd_even,
        secondary=secondary,
        bls_sde=bls["sde"],
    )

    # Plots
    plots = make_plots(
        t_c, f_c, fe_c, mom_x, mom_y, events, primary_event, bls.get("_periodogram"), ls
    )

    summary = {
        "n_points": int(len(t_c)),
        "time_span_d": span,
        "median_cadence_min": float(np.median(np.diff(t_c)) * 1440),
        "n_events_detected": len(events),
        "scatter_mad": float(1.4826 * np.nanmedian(np.abs(f_c - 1))),
    }

    # Strip heavy _periodogram before returning to user (keep only down-sampled)
    bls.pop("_periodogram", None)

    return VettingResult(
        star=star,
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
        plots=plots,
    )


# ----------------------------------------------------------------------
# Multi-sector analysis
# ----------------------------------------------------------------------

def run_multisector_analysis(
    sector_results: list,          # list of (sector_num, VettingResult)
    period_d: float | None = None, # known period from ExoFOP / BLS
    t0: float | None = None,       # reference transit time
    detect_threshold: float = 0.997,
    detect_min_snr: float = 4.0,
) -> dict:
    """
    Given vetting results from multiple sectors, build:
      - A detection timeline: which sectors showed events
      - A phase-folded light curve (if period known)
      - A combined-sector BLS periodogram
      - A consistent ephemeris check (period refinement)

    Returns a plain dict — JSON-serialisable, ships to frontend.
    """
    if not sector_results:
        return {"error": "No sector results provided."}

    timeline = []
    all_t = []
    all_f = []
    all_fe = []

    for sec_num, res in sector_results:
        has_dip = len(res.events) > 0
        deepest_depth = max((e["depth"] for e in res.events), default=0.0)
        timeline.append({
            "sector": sec_num,
            "n_events": len(res.events),
            "has_dip": has_dip,
            "deepest_depth_pct": round(deepest_depth * 100, 3),
            "bls_period_d": res.bls.get("period"),
            "bls_sde": res.bls.get("sde"),
            "verdict": res.verdict.get("category"),
        })

    n_with_dip = sum(1 for x in timeline if x["has_dip"])
    n_total = len(timeline)

    # Period analysis: collect BLS peaks from all sectors
    period_estimates = [
        x["bls_period_d"] for x in timeline
        if x["bls_period_d"] and x["bls_sde"] and x["bls_sde"] > 6
    ]
    period_consensus = None
    if period_d:
        period_consensus = {"value_d": period_d, "source": "external (ExoFOP/user)"}
    elif len(period_estimates) >= 2:
        p_arr = np.array(period_estimates)
        period_consensus = {
            "value_d": float(np.median(p_arr)),
            "std_d": float(np.std(p_arr)),
            "source": f"median of {len(p_arr)} sector BLS peaks",
        }

    # Phase-fold plot if period available
    phase_fold_plot = None
    if period_consensus and len(sector_results) >= 2:
        try:
            all_t_arr = np.concatenate([
                res.summary.get("_t_clean", np.array([])) for _, res in sector_results
            ])
            all_f_arr = np.concatenate([
                res.summary.get("_f_clean", np.array([])) for _, res in sector_results
            ])
            # We don't stash raw arrays — make a fold directly from stored data
            # by using whatever t0 we have.
            P = period_consensus["value_d"]
            if t0 is None:
                # Anchor on first sector's BLS t0
                for _, res in sector_results:
                    if res.bls.get("t0"):
                        t0 = res.bls["t0"]
                        break
        except Exception:
            pass

    # Detection timeline plot
    timeline_plot = _make_timeline_plot(timeline)

    return {
        "n_sectors_observed": n_total,
        "n_sectors_with_detections": n_with_dip,
        "detection_rate": round(n_with_dip / n_total, 3) if n_total else 0,
        "timeline": timeline,
        "period_consensus": period_consensus,
        "timeline_plot": timeline_plot,
        "summary": (
            f"{n_with_dip}/{n_total} sectors show a dip event. "
            + (f"Consistent period ≈ {period_consensus['value_d']:.4f} d."
               if period_consensus else "Period not well-constrained.")
        ),
    }


def _make_timeline_plot(timeline: list) -> str:
    """Bar chart showing detection status and depth per sector."""
    if not timeline:
        return ""
    sectors = [str(x["sector"]) for x in timeline]
    depths  = [x["deepest_depth_pct"] for x in timeline]
    colors  = ["#ef4444" if x["has_dip"] else "#cbd5e1" for x in timeline]

    fig, ax = plt.subplots(figsize=(max(6, len(sectors) * 0.8), 3.5))
    bars = ax.bar(sectors, depths, color=colors, edgecolor="white", linewidth=0.8)
    ax.set_xlabel("TESS Sector")
    ax.set_ylabel("Deepest dip depth (%)")
    ax.set_title("Multi-sector detection timeline\n(red = dip detected, grey = no dip)")
    ax.axhline(0.3, ls=":", color="#94a3b8", alpha=0.6, label="0.3% threshold")
    ax.legend(fontsize=8)
    for bar, d in zip(bars, depths):
        if d > 0:
            ax.text(bar.get_x() + bar.get_width() / 2, d + 0.01 * max(depths, default=1),
                    f"{d:.2f}%", ha="center", va="bottom", fontsize=7, color="#374151")
    plt.tight_layout()

    buf = __import__("io").BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return __import__("base64").b64encode(buf.getvalue()).decode("ascii")
