"""Matplotlib figures for vetstar's NEMESIS results, in the NEMESIS II style.

The look follows the figures of NEMESIS II (Feliz et al. 2026,
arXiv:2611.21758), especially Figure 8 (folded candidates) and Figure 10
(transit fit): serif STIX text, ticks pointing inward on all four sides,
grey 30-minute cadences, black binned points with error bars, a red model
line, flux in ppt against hours from mid-transit, and a residual panel with
its RMS.

Each function returns a base64 PNG, or None if the inputs are missing.
"""
from __future__ import annotations

import base64
import io
from typing import Optional

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# NEMESIS II paper look.
PAPER_RC = {
    "font.family": "STIXGeneral",
    "mathtext.fontset": "stix",
    "font.size": 10,
    "axes.labelsize": 10,
    "axes.titlesize": 10,
    "axes.linewidth": 0.8,
    "xtick.direction": "in", "ytick.direction": "in",
    "xtick.top": True, "ytick.right": True,
    "xtick.minor.visible": True, "ytick.minor.visible": True,
    "xtick.major.size": 4, "ytick.major.size": 4,
    "xtick.minor.size": 2, "ytick.minor.size": 2,
    "legend.frameon": False, "legend.fontsize": 8.5,
    "savefig.dpi": 150, "figure.dpi": 150,
}
GREY, BLACK, RED, PINK = "#9a9a9a", "black", "red", "#f4a6a6"


def _png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def _arr(df, *names) -> Optional[np.ndarray]:
    if df is None:
        return None
    for n in names:
        if n in getattr(df, "columns", []):
            return np.asarray(df[n], dtype=float)
    return None


def _hours_from_mid(t, period, t0):
    return (((t - t0) / period + 0.5) % 1.0 - 0.5) * period * 24.0


def _bin(x, y, width):
    """Median bins of ``width`` (same units as x); error = 1.4826 MAD / sqrt(n)."""
    if len(x) == 0:
        return np.array([]), np.array([]), np.array([])
    edges = np.arange(x.min(), x.max() + width, width)
    idx = np.digitize(x, edges)
    bx, by, be = [], [], []
    for k in np.unique(idx):
        s = idx == k
        if s.sum() < 2:
            continue
        yy = y[s]
        bx.append(x[s].mean())
        by.append(np.median(yy))
        be.append(1.4826 * np.median(np.abs(yy - np.median(yy))) / np.sqrt(s.sum()))
    return np.array(bx), np.array(by), np.array(be)


def _finite(*arrs):
    m = np.ones(len(arrs[0]), dtype=bool)
    for a in arrs:
        m &= np.isfinite(a)
    return [a[m] for a in arrs]


def fold_figure(lc_df, model_df, params: dict, tic: int, sector: int, cadence_min: float) -> Optional[str]:
    """Figure 10(a) style: folded light curve with model and residuals."""
    t = _arr(lc_df, "Time")
    f = _arr(lc_df, "Detrended Flux")
    P, t0, dur_h = params.get("period_d"), params.get("t0_btjd"), params.get("duration_hr")
    if t is None or f is None or not P or t0 is None:
        return None
    t, f = _finite(t, f)
    h = _hours_from_mid(t, P, t0)
    win = max(3.0 * (dur_h or 2.0), 3.0)
    s = np.abs(h) <= win
    h, y = h[s], (f[s] - 1.0) * 1e3  # ppt
    if len(h) < 5:
        return None

    mt, mf = _arr(model_df, "Time", "time"), _arr(model_df, "Model", "model", "Model Flux")
    model_h = model_y = None
    if mt is not None and mf is not None and len(mt) == len(mf):
        mt, mf = _finite(mt, mf)
        mh = _hours_from_mid(mt, P, t0)
        o = np.argsort(mh)
        mh, mf = mh[o], mf[o]
        keep = np.abs(mh) <= win
        model_h, model_y = mh[keep], (mf[keep] - 1.0) * 1e3

    bin_h = (2.0 / 3.0) * cadence_min / 60.0 if cadence_min >= 20 else 20.0 / 60.0
    bx, by, be = _bin(h, y, bin_h)

    with plt.rc_context(PAPER_RC):
        fig, (ax, ar) = plt.subplots(2, 1, figsize=(4.3, 4.6), sharex=True,
                                     gridspec_kw={"height_ratios": [3, 1.1], "hspace": 0.05})
        ax.scatter(h, y, s=3, c=GREY, lw=0, label=f"{cadence_min:g}-min cadences", zorder=1)
        if model_h is not None and len(model_h):
            ax.plot(model_h, model_y, c=RED, lw=1.4, label=f"{params.get('method', '')} model", zorder=3)
        ax.errorbar(bx, by, be, fmt="o", c=BLACK, ms=3.5, elinewidth=0.9, capsize=0,
                    label=f"{bin_h * 60:.0f}-min bins", zorder=4)
        ax.set_ylabel("Relative flux (ppt)")
        ax.set_title(f"TIC {tic}, Sector {sector}", loc="left")
        ax.legend(loc="lower left")

        if model_h is not None and len(model_h) > 1:
            resid = y - np.interp(h, model_h, model_y)
            rb = by - np.interp(bx, model_h, model_y)
        else:
            resid, rb = y - np.median(y), by - np.median(y)
        rms = 1.4826 * np.median(np.abs(resid - np.median(resid)))
        ar.scatter(h, resid, s=3, c=GREY, lw=0)
        ar.errorbar(bx, rb, be, fmt="o", c=BLACK, ms=3, elinewidth=0.8, capsize=0)
        ar.axhline(0, c=RED, lw=1.2)
        lim = max(4 * rms, np.nanmax(np.abs(rb)) * 1.3 if len(rb) else 4 * rms)
        ar.set_ylim(-lim, lim)
        ar.text(0.02, 0.92, f"RMS {rms:.2f} ppt, {len(h)} cadences", transform=ar.transAxes,
                va="top", fontsize=8.5, bbox=dict(fc="white", ec="black", lw=0.6, pad=2))
        ar.set_ylabel("Residual (ppt)")
        ar.set_xlabel("Hours from mid-transit")
        ar.set_xlim(-win, win)
        return _png(fig)


def periodogram_figure(ps_df, params: dict, method: str) -> Optional[str]:
    """Power spectrum with the best period and its 1/2 and 2x aliases marked."""
    p = _arr(ps_df, f"{method} Periods")
    s = _arr(ps_df, f"{method} SDE")
    if p is None or s is None:
        return None
    p, s = _finite(p, s)
    o = np.argsort(p)
    p, s = p[o], s[o]
    P = params.get("period_d")
    with plt.rc_context(PAPER_RC):
        fig, ax = plt.subplots(figsize=(6.4, 2.4))
        ax.plot(p, s, c=BLACK, lw=0.7)
        if P:
            ax.axvline(P, c=RED, lw=1.2, alpha=0.8, label=f"P = {P:.4f} d")
            for k, lab in ((0.5, r"$P/2$"), (2.0, r"$2P$")):
                if p.min() <= k * P <= p.max():
                    ax.axvline(k * P, c=RED, lw=0.9, ls="--", alpha=0.6, label=lab)
            ax.legend(loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=3,
                      borderaxespad=0.2, handlelength=1.6)
        ax.set_xlabel("Period (days)")
        ax.set_ylabel("SDE")
        bits = [f"{method} power spectrum"]
        if P:
            bits.append(f"period {P:.3f} d")
        if params.get("depth_ppt") is not None:
            bits.append(f"depth {params['depth_ppt']:.2f} ppt")
        if params.get("radius_earth") is not None:
            bits.append(rf"radius {params['radius_earth']:.2f} $R_\oplus$")
        ax.set_title("; ".join(bits), loc="left", pad=18)
        ax.set_xlim(p.min(), p.max())
        ax.set_ylim(bottom=min(0, s.min()))
        return _png(fig)


def light_curve_figure(lc_df, params: dict, tic: int, sector: int) -> Optional[str]:
    """Detrended light curve with the predicted transit times marked."""
    t = _arr(lc_df, "Time")
    f = _arr(lc_df, "Detrended Flux")
    if t is None or f is None:
        return None
    t, f = _finite(t, f)
    y = (f - 1.0) * 1e3
    bx, by, _ = _bin(t, y, 1.0 / 24.0)
    P, t0 = params.get("period_d"), params.get("t0_btjd")
    with plt.rc_context(PAPER_RC):
        fig, ax = plt.subplots(figsize=(6.4, 2.3))
        ax.scatter(t, y, s=2, c=GREY, lw=0)
        ax.plot(bx, by, ".", c=BLACK, ms=2.5)
        if P and t0 is not None:
            n0, n1 = np.ceil((t.min() - t0) / P), np.floor((t.max() - t0) / P)
            for n in np.arange(n0, n1 + 1):
                ax.axvline(t0 + n * P, c=RED, lw=0.8, alpha=0.5, zorder=0)
        ax.set_xlabel("Time (BTJD)")
        ax.set_ylabel("Relative flux (ppt)")
        ax.set_title(f"TIC {tic}, Sector {sector}: detrended light curve; red lines mark predicted transits",
                     loc="left")
        lo, hi = np.percentile(y, [0.5, 99.5])
        pad = 0.15 * (hi - lo)
        ax.set_ylim(lo - pad, hi + pad)
        return _png(fig)


def odd_even_figure(lc_df, params: dict) -> Optional[str]:
    """Odd and even transits side by side, each with its median depth."""
    t = _arr(lc_df, "Time")
    f = _arr(lc_df, "Detrended Flux")
    P, t0, dur_h = params.get("period_d"), params.get("t0_btjd"), params.get("duration_hr")
    if t is None or f is None or not P or t0 is None:
        return None
    t, f = _finite(t, f)
    epoch = np.round((t - t0) / P).astype(int)
    h = _hours_from_mid(t, P, t0)
    win = max(3.0 * (dur_h or 2.0), 3.0)
    half = 0.5 * (dur_h or 1.0)
    y = (f - 1.0) * 1e3
    with plt.rc_context(PAPER_RC):
        fig, axes = plt.subplots(1, 2, figsize=(6.4, 2.4), sharey=True, gridspec_kw={"wspace": 0.05})
        for ax, parity, name in ((axes[0], 1, "Odd"), (axes[1], 0, "Even")):
            s = (np.abs(h) <= win) & (np.abs(epoch) % 2 == parity)
            if s.sum() < 5:
                ax.text(0.5, 0.5, "no data", ha="center", transform=ax.transAxes)
                continue
            ax.scatter(h[s], y[s], s=3, c=GREY, lw=0)
            bx, by, be = _bin(h[s], y[s], 20.0 / 60.0)
            ax.errorbar(bx, by, be, fmt="o", c=BLACK, ms=3, elinewidth=0.8, capsize=0)
            intr = s & (np.abs(h) <= half)
            if intr.any():
                d = float(np.median(y[intr]))
                ax.axhline(d, c=RED, lw=1, ls="--")
                ax.text(0.03, 0.06, f"{name}: depth {-d:.2f} ppt", transform=ax.transAxes, fontsize=8.5)
            ax.set_xlabel("Hours from mid-transit")
            ax.set_xlim(-win, win)
        axes[0].set_ylabel("Relative flux (ppt)")
        lo, hi = np.percentile(y[np.abs(h) <= win], [1, 99.5])
        pad = 0.2 * (hi - lo)
        axes[0].set_ylim(lo - pad, hi + pad)
        return _png(fig)


def cadence_minutes(cadence: str) -> float:
    return {"30 minute": 30.0, "10 minute": 10.0, "2 minute": 2.0, "20 second": 1 / 3}.get(cadence, 30.0)


def all_figures(lc_df, transit_results, primary: Optional[str], params: dict,
                tic: int, sector: int, cadence: str) -> dict:
    """Every figure for the primary search. Failures become None, never errors."""
    if not transit_results or primary not in ("BLS", "TLS"):
        return {"light_curve": _safe(light_curve_figure, lc_df, {}, tic, sector)}
    bls_ps, bls_model, _, tls_ps, tls_model, _ = transit_results
    ps, model = (tls_ps, tls_model) if primary == "TLS" else (bls_ps, bls_model)
    return {
        "fold": _safe(fold_figure, lc_df, model, params, tic, sector, cadence_minutes(cadence)),
        "periodogram": _safe(periodogram_figure, ps, params, primary),
        "light_curve": _safe(light_curve_figure, lc_df, params, tic, sector),
        "odd_even": _safe(odd_even_figure, lc_df, params),
    }


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as exc:  # a figure must never sink the whole result
        print(f"[vetstar] figure {fn.__name__} failed: {exc}", flush=True)
        plt.close("all")
        return None
