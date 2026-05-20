"""
backend/exominer.py
-------------------
ExoMiner feature extraction — replicates the full TFRecord feature set
from Valizadegan et al. 2022 (ExoMiner, ApJ 926 120).

Views produced
--------------
global_view          : 2001-bin phase-folded, SG-detrended flux
local_view           : 201-bin, ±2× transit duration
secondary_view       : 201-bin, centred at phase 0.5
odd_transit_view     : local view from odd-numbered transit epochs only
even_transit_view    : local view from even-numbered transit epochs only
centroid_global_view : 2001-bin phase-folded centroid motion (global)
centroid_local_view  : 201-bin phase-folded centroid motion (local)

Scalar diagnostics
------------------
period_d, duration_h, depth_ppm, transit_count,
odd_even_sigma, secondary_depth_sigma, centroid_shift_sigma,
scatter_mad, crowdsap, sg_detrend_window_h

API endpoint (add to main FastAPI app)
---------------------------------------
POST /api/exominer
  Request body: ExominerRequest (see below)
  Response: ExominerResult dict

The endpoint relies on the backend caching the most-recently parsed
light-curve arrays in a simple process-level dict keyed by (tic_id, sector)
or a fallback "last_parsed" key.  See the route stub at the bottom of this
file for the expected integration pattern.
"""
from __future__ import annotations

import base64
import io
import math
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import savgol_filter


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _phase_fold(t: np.ndarray, period: float, t0: float) -> np.ndarray:
    phi = ((t - t0) / period) % 1.0
    phi[phi >= 0.5] -= 1.0
    return phi


def _median_bin(
    phi: np.ndarray,
    values: np.ndarray,
    n_bins: int,
    phi_min: float = -0.5,
    phi_max: float = 0.5,
) -> tuple[np.ndarray, np.ndarray]:
    edges = np.linspace(phi_min, phi_max, n_bins + 1)
    centres = 0.5 * (edges[:-1] + edges[1:])
    binned = np.full(n_bins, np.nan)
    for i in range(n_bins):
        mask = (phi >= edges[i]) & (phi < edges[i + 1])
        if mask.sum() > 0:
            binned[i] = float(np.median(values[mask]))
    nans = np.isnan(binned)
    if nans.all():
        binned[:] = 0.0
    elif nans.any():
        idx = np.arange(n_bins)
        binned[nans] = np.interp(idx[nans], idx[~nans], binned[~nans])
    return centres, binned


def _sg_detrend(t: np.ndarray, f: np.ndarray, duration_d: float) -> np.ndarray:
    cadence_d = float(np.median(np.diff(t)))
    win = max(5, int(round(3.0 * duration_d / cadence_d)))
    if win % 2 == 0:
        win += 1
    win = min(win, len(f) - (1 if len(f) % 2 == 0 else 0))
    if win < 5:
        return f.copy()
    trend = savgol_filter(f, window_length=win, polyorder=3)
    return f / np.where(np.abs(trend) < 1e-6, 1.0, trend)


def _normalise_view(arr: np.ndarray) -> np.ndarray:
    arr = arr - np.nanmedian(arr)
    scale = max(abs(float(np.nanmin(arr))), 1e-9)
    return arr / scale


def _plot_view(centres, binned, title, colour="steelblue") -> str:
    fig, ax = plt.subplots(figsize=(7, 2.8))
    ax.plot(centres, binned, color=colour, lw=1.2)
    ax.axhline(0.0, color="gray", lw=0.6, ls=":")
    ax.set_xlabel("Phase"); ax.set_ylabel("Normalised flux")
    ax.set_title(title, fontsize=9)
    fig.tight_layout()
    return _fig_to_b64(fig)


def _plot_multi_view(panels, title) -> str:
    n = len(panels)
    fig, axes = plt.subplots(1, n, figsize=(4 * n, 2.8), sharey=True)
    if n == 1:
        axes = [axes]
    colours = ["steelblue", "tomato"]
    for ax, (c, b, sub), col in zip(axes, panels, colours):
        ax.plot(c, b, color=col, lw=1.2)
        ax.axhline(0.0, color="gray", lw=0.6, ls=":")
        ax.set_xlabel("Phase"); ax.set_title(sub, fontsize=8)
    axes[0].set_ylabel("Normalised flux")
    fig.suptitle(title, fontsize=9); fig.tight_layout()
    return _fig_to_b64(fig)


# ─────────────────────────────────────────────────────────────────────────────
# Core function
# ─────────────────────────────────────────────────────────────────────────────

def run_exominer(
    t: np.ndarray,
    f: np.ndarray,
    mom_x: Optional[np.ndarray],
    mom_y: Optional[np.ndarray],
    period: float,
    t0: float,
    duration: float,
    crowdsap: Optional[float] = None,
) -> dict:
    """Full ExoMiner feature extraction. Returns dict {scalars, arrays, plots}."""

    f_sg = _sg_detrend(t, f, duration)
    phi = _phase_fold(t, period, t0)
    half_local = min(2.0 * duration / period, 0.49)

    # Views
    gc, gb = _median_bin(phi, f_sg, 2001)
    gv = _normalise_view(gb)

    lc, lb = _median_bin(phi, f_sg, 201, -half_local, half_local)
    lv = _normalise_view(lb)

    phi_sec = phi.copy()
    phi_sec[phi_sec < 0] += 1.0
    phi_sec -= 0.5
    phi_sec[phi_sec < -0.5] += 1.0
    sc, sb = _median_bin(phi_sec, f_sg, 201, -half_local, half_local)
    sv = _normalise_view(sb)

    epoch = np.round((t - t0) / period).astype(int)
    odd_mask  = (epoch % 2 == 1)
    even_mask = (epoch % 2 == 0)

    def _safe_local(mask):
        if mask.sum() < 10:
            return lc, np.zeros_like(lc)
        _, b = _median_bin(phi[mask], f_sg[mask], 201, -half_local, half_local)
        return lc, _normalise_view(b)

    _, ov = _safe_local(odd_mask)
    _, ev = _safe_local(even_mask)

    # Centroid
    has_centroid = (
        mom_x is not None and mom_y is not None
        and len(mom_x) == len(t) and len(mom_y) == len(t)
        and np.isfinite(mom_x).sum() > 10
    )
    cen_global_arr = cen_local_arr = None
    cgb = clb = None
    if has_centroid:
        cx = mom_x - np.nanmedian(mom_x)
        cy = mom_y - np.nanmedian(mom_y)
        cen = np.sign(cx) * np.sqrt(cx**2 + cy**2)
        _, cgb = _median_bin(phi, cen, 2001)
        cgb -= np.nanmedian(cgb)
        cen_global_arr = cgb.tolist()
        _, clb = _median_bin(phi, cen, 201, -half_local, half_local)
        clb -= np.nanmedian(clb)
        cen_local_arr = clb.tolist()

    # Scalars
    in_local = np.abs(phi) <= half_local

    def _depth(mask):
        sel = in_local & mask
        return float(1.0 - np.median(f_sg[sel])) if sel.sum() > 5 else float("nan")

    d_odd, d_even = _depth(odd_mask), _depth(even_mask)
    std_oe = float(np.nanstd(f_sg[in_local])) if in_local.sum() > 5 else 1e-6
    odd_even_sigma = (
        float(abs(d_odd - d_even) / max(std_oe * math.sqrt(2), 1e-9))
        if not (math.isnan(d_odd) or math.isnan(d_even))
        else 0.0
    )

    in_sec = np.abs(phi_sec) <= half_local
    oot = ~in_local & ~in_sec
    sec_depth = float(1.0 - np.median(f_sg[in_sec])) if in_sec.sum() > 5 else 0.0
    std_oot = float(np.std(f_sg[oot])) if oot.sum() > 5 else 1e-6
    sec_sigma = float(abs(sec_depth) / max(std_oot, 1e-9))

    cen_sigma = 0.0
    if has_centroid:
        oot_t = ~in_local
        if in_local.sum() > 5 and oot_t.sum() > 5:
            delta = float(abs(np.median(cen[in_local]) - np.median(cen[oot_t])))
            cen_sigma = float(delta / max(float(np.std(cen[oot_t])), 1e-9))

    transit_count = int(len(np.unique(epoch[in_local]))) if in_local.sum() > 0 else 0
    scatter_mad   = float(1.4826 * np.median(np.abs(f_sg - np.median(f_sg))))
    depth_ppm     = float(abs(np.nanmin(lv))) * 1e6

    scalars = {
        "period_d":              round(period, 6),
        "duration_h":            round(duration * 24.0, 4),
        "depth_ppm":             round(depth_ppm, 1),
        "transit_count":         transit_count,
        "odd_even_sigma":        round(odd_even_sigma, 3),
        "secondary_depth_sigma": round(sec_sigma, 3),
        "centroid_shift_sigma":  round(cen_sigma, 3),
        "scatter_mad":           round(scatter_mad, 6),
        "crowdsap":              crowdsap,
        "sg_detrend_window_h":   round(3.0 * duration * 24.0, 2),
    }

    # Plots
    plots: dict[str, str] = {}

    fig, ax = plt.subplots(figsize=(10, 3))
    ax.plot(gc, gv, "k-", lw=0.7)
    ax.axhline(0, color="gray", lw=0.6, ls=":")
    ax.set_xlabel("Phase"); ax.set_ylabel("Normalised flux")
    ax.set_title("Global view  (2001 bins, SG-detrended, full phase)", fontsize=9)
    fig.tight_layout()
    plots["global_view"] = _fig_to_b64(fig)

    plots["local_view"] = _plot_view(
        lc, lv,
        f"Local view  (201 bins, ±{half_local:.3f} phase = ±2× transit duration)",
    )
    plots["secondary_view"] = _plot_view(
        sc, sv, "Secondary view  (201 bins, centred at phase 0.5)", colour="darkorange",
    )
    plots["odd_even_view"] = _plot_multi_view(
        [(lc, ov, "Odd transits"), (lc, ev, "Even transits")],
        f"Odd vs even transit views  (odd/even σ = {odd_even_sigma:.2f})",
    )

    if has_centroid:
        plots["centroid_global_view"] = _plot_view(
            gc, cgb, "Centroid global view  (2001 bins)", colour="seagreen",
        )
        clc_x = np.linspace(-half_local, half_local, 201)
        plots["centroid_local_view"] = _plot_view(
            clc_x, np.array(clb),
            f"Centroid local view  (centroid σ = {cen_sigma:.2f})", colour="seagreen",
        )

    flag_vals = [odd_even_sigma, sec_sigma, cen_sigma]
    fig, ax = plt.subplots(figsize=(6, 3))
    bars = ax.bar(["Odd/Even σ", "Secondary σ", "Centroid σ"], flag_vals,
                  color=["#4c8ed9", "#e06c4c", "#4cad7a"])
    ax.axhline(3, color="red", lw=1, ls="--", label="3σ threshold")
    ax.set_ylabel("Sigma"); ax.set_title("ExoMiner diagnostic sigmas", fontsize=9)
    ax.legend(fontsize=8)
    for bar, v in zip(bars, flag_vals):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.05,
                f"{v:.2f}", ha="center", va="bottom", fontsize=8)
    fig.tight_layout()
    plots["diagnostic_sigmas"] = _fig_to_b64(fig)

    return {
        "scalars": scalars,
        "arrays": {
            "global_view":          gv.tolist(),
            "local_view":           lv.tolist(),
            "secondary_view":       sv.tolist(),
            "odd_transit_view":     ov.tolist(),
            "even_transit_view":    ev.tolist(),
            "centroid_global_view": cen_global_arr,
            "centroid_local_view":  cen_local_arr,
        },
        "plots": plots,
    }


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI route stub (paste into main.py / router file)
# ─────────────────────────────────────────────────────────────────────────────
#
# Add this to your existing FastAPI app.  The backend must cache the last
# parsed light-curve in a module-level dict so ExoMiner can reuse it without
# re-downloading from MAST.
#
# In your main pipeline routes, after parsing, do:
#   _lc_cache[(tic_id, sector)] = {"t": t_c, "f": f_c, "mom_x": mx, "mom_y": my}
#   _lc_cache["__last__"] = _lc_cache[(tic_id, sector)]
#
# ---
# from pydantic import BaseModel
# from fastapi import HTTPException
# from .exominer import run_exominer
#
# _lc_cache: dict = {}   # module-level, shared across requests in same process
#
# class ExominerRequest(BaseModel):
#     tic_id:   int | None = None
#     sector:   int | None = None
#     period:   float
#     t0:       float
#     duration: float
#     crowdsap: float | None = None
#
# @app.post("/api/exominer")
# def api_exominer(req: ExominerRequest):
#     key = (req.tic_id, req.sector) if req.tic_id else "__last__"
#     cached = _lc_cache.get(key) or _lc_cache.get("__last__")
#     if not cached:
#         raise HTTPException(400, "No light curve in cache. Run /api/analyze first.")
#     return run_exominer(
#         t=cached["t"],
#         f=cached["f"],
#         mom_x=cached.get("mom_x"),
#         mom_y=cached.get("mom_y"),
#         period=req.period,
#         t0=req.t0,
#         duration=req.duration,
#         crowdsap=req.crowdsap,
#     )
