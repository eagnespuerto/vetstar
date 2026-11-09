"""Matplotlib figures for the pipeline, returned as base64 PNGs.
"""
from __future__ import annotations

import base64
import io
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


# ----------------------------------------------------------------------
# Plot helpers
# ----------------------------------------------------------------------
def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ----------------------------------------------------------------------
# Plot generation
# ----------------------------------------------------------------------
def _rolling_median_detrend(t: np.ndarray, f: np.ndarray, window_days: float = 1.0) -> np.ndarray:
    """Lightweight per-cadence detrend for plotting only.

    Subtracts a rolling-median trend in a ``window_days``-wide window and
    re-normalises to median=1. Robust to single-cadence outliers (no
    Gaussian smoothing). This is *plot-only* — BLS still sees the
    unfiltered f_c from run_full_vetting.
    """
    if len(t) < 8:
        return f
    n = len(t)
    out = np.empty(n, dtype=float)
    half = window_days / 2.0
    # Pointers for the sliding window — O(n) over a sorted t.
    lo = 0
    hi = 0
    for i in range(n):
        t_lo = t[i] - half
        t_hi = t[i] + half
        while lo < n and t[lo] < t_lo:
            lo += 1
        while hi < n and t[hi] <= t_hi:
            hi += 1
        seg = f[lo:hi]
        if seg.size == 0:
            out[i] = f[i]
        else:
            out[i] = float(np.nanmedian(seg))
    trend = out
    # Avoid divide-by-zero on a pathological all-zero trend
    safe = np.where(np.isfinite(trend) & (trend != 0), trend, np.nanmedian(f) or 1.0)
    flat = f / safe
    med = np.nanmedian(flat)
    if not np.isfinite(med) or med == 0:
        return flat
    return flat / med


def _time_bin(t: np.ndarray, f: np.ndarray, bin_minutes: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Bin (t, f) by ``bin_minutes`` in time. Returns (t_bin, f_bin, err_bin)
    where err_bin is the standard error of the mean in each bin (empty bins
    are dropped).
    """
    if bin_minutes is None or bin_minutes <= 0 or len(t) == 0:
        return t, f, np.zeros_like(f)
    bin_days = bin_minutes / 1440.0
    t_min = float(np.nanmin(t))
    idx = np.floor((t - t_min) / bin_days).astype(int)
    n_bins = int(idx.max()) + 1
    sums_t = np.zeros(n_bins)
    sums_f = np.zeros(n_bins)
    sums_ff = np.zeros(n_bins)
    counts = np.zeros(n_bins, dtype=int)
    for i in range(len(t)):
        b = idx[i]
        if not (np.isfinite(t[i]) and np.isfinite(f[i])):
            continue
        sums_t[b] += t[i]
        sums_f[b] += f[i]
        sums_ff[b] += f[i] * f[i]
        counts[b] += 1
    mask = counts > 0
    t_b = sums_t[mask] / counts[mask]
    f_b = sums_f[mask] / counts[mask]
    # Standard error of the mean per bin; use ddof=1 where possible.
    mean_sq = sums_ff[mask] / counts[mask]
    var = np.maximum(0.0, mean_sq - f_b * f_b)
    n = counts[mask]
    sem = np.sqrt(var / np.maximum(1, n))
    return t_b, f_b, sem


def make_plots(
    t, f, fe, mom_x, mom_y, events, primary_event,
    bls_periodogram, ls_periodogram,
    detrend_meta: Optional[dict] = None,
    f_raw: Optional[np.ndarray] = None,
    plot_detrend: bool = True,
    bin_minutes: Optional[int] = 30,
) -> dict:
    """Generate diagnostic plots.

    ``events`` is the full list from detect_events (may be empty). ``primary_event``
    is the one chosen for centroid/shape analysis (typically the deepest).
    The full-LC plot shades EVERY event; the zoom plot shows up to 6 events
    in a grid, with the primary one highlighted.

    Plot-presentation kwargs (don't affect detection):
      plot_detrend  — apply a 1-day rolling-median flatten to the LC plot
                      so long-term variability doesn't drown shallow dips.
      bin_minutes   — when set, overplot time-binned points in red. Default
                      30 min beats per-cadence noise down ~3-4× for 2-min LCs.
    """
    plots = {}
    events = events or []

    # 0. Stellar variability detrend (only when actually applied).
    if (
        detrend_meta and detrend_meta.get("applied")
        and f_raw is not None and "fit" in detrend_meta
    ):
        from ..detrend import _design_matrix  # internal but stable for plot use
        fit = detrend_meta["fit"]
        X = _design_matrix(t, fit["period_days"])
        coeffs = np.array([fit["C"], fit["A1"], fit["B1"], fit["A2"], fit["B2"]])
        model = X @ coeffs
        residual = f_raw - model + 1.0

        fig, axes = plt.subplots(3, 1, figsize=(10, 6.5), sharex=True)
        axes[0].plot(t, f_raw, "k.", ms=1, alpha=0.4)
        axes[0].plot(t, model, "C1-", lw=1.0, alpha=0.9, label="sin + 1st harmonic")
        axes[0].set_ylabel("Raw flux")
        axes[0].set_title(
            f"Stellar variability detrend — P = {fit['period_days']:.3f} d, "
            f"amplitude = {detrend_meta['amplitude_ppm']:.0f} ppm, "
            f"RMS reduced {detrend_meta['rms_reduction_pct']:.1f}%"
        )
        axes[0].legend(fontsize=8, loc="upper right")

        axes[1].plot(t, model, "C1-", lw=0.8)
        axes[1].set_ylabel("Fitted model")

        axes[2].plot(t, residual, "k.", ms=1, alpha=0.4)
        axes[2].axhline(1.0, color="gray", ls=":", alpha=0.5)
        axes[2].set_ylabel("Residual (BLS in)")
        axes[2].set_xlabel("Time (BTJD or similar)")

        for ax in axes:
            ax.ticklabel_format(axis="y", useOffset=False, style="plain")
            ax.yaxis.set_major_formatter(plt.ScalarFormatter(useOffset=False))

        fig.tight_layout()
        plots["detrend"] = _fig_to_b64(fig)

    # 1. Full LC with all events shaded.
    # When plot_detrend is on, flatten with a 1-day rolling median so
    # long-period variability doesn't drown shallow dips. Detection
    # (BLS/events) is unaffected — they already ran on f.
    f_plot = _rolling_median_detrend(t, f, window_days=1.0) if plot_detrend else f
    fig, ax = plt.subplots(figsize=(10, 3))
    ax.plot(t, f_plot, "k.", ms=1, alpha=0.35, label="cadences")
    if bin_minutes and len(t) > 0:
        t_b, f_b, e_b = _time_bin(t, f_plot, bin_minutes)
        if len(t_b) > 0:
            ax.errorbar(
                t_b, f_b, yerr=e_b,
                fmt="o", ms=3.2, color="crimson", ecolor="crimson",
                elinewidth=0.6, capsize=0, alpha=0.85,
                label=f"{bin_minutes}-min bins",
            )
        ax.legend(loc="upper right", fontsize=8, framealpha=0.85)
    # Set y-limits explicitly so we can place labels just inside the top edge.
    # Use the 99.7 percentile on top to suppress upward outliers, but the
    # bottom must accommodate the deepest detected dip — a percentile-based
    # lower bound clips event troughs when the in-transit fraction exceeds
    # the percentile (e.g. many dips on a short baseline).
    ymax = float(np.nanpercentile(f_plot, 99.7))
    if events:
        event_mins = []
        for ev in events:
            in_ev = (t >= ev["t_start"]) & (t <= ev["t_end"])
            if np.any(in_ev):
                event_mins.append(float(np.nanmin(f_plot[in_ev])))
        ymin = min(event_mins) if event_mins else float(np.nanpercentile(f_plot, 0.3))
    else:
        ymin = float(np.nanpercentile(f_plot, 0.3))
    y_pad = 0.05 * (ymax - ymin)
    ax.set_ylim(ymin - y_pad, ymax + y_pad)
    # IMPORTANT: disable the "+1" offset notation — it's confusing for shallow dips
    ax.ticklabel_format(axis="y", useOffset=False, style="plain")
    ax.yaxis.set_major_formatter(plt.ScalarFormatter(useOffset=False))
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
    title = "Flattened light curve" if plot_detrend else "Light curve"
    if bin_minutes:
        title += f" — {bin_minutes}-min binned overlay"
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
