"""Matplotlib plot helpers. All figures use the Agg backend so the Pi runs
headless without a display server. Public entry points return either a
saved PNG path (:func:`save_png`) or a live ``Figure`` (``build_*``) so the
Tkinter GUI can embed it via :class:`~matplotlib.backends.backend_tkagg.FigureCanvasTkAgg`.
"""
from __future__ import annotations

import io
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # noqa: E402
import matplotlib.pyplot as plt
import numpy as np


# ----------------------------------------------------------------------
# Transit plots
# ----------------------------------------------------------------------
def build_transit_overview(result, t, f) -> plt.Figure:
    """3-panel: full light curve with event markers, BLS periodogram, LS periodogram."""
    fig, axes = plt.subplots(3, 1, figsize=(9, 8))

    ax = axes[0]
    ax.plot(t, f, "k.", ms=1.5, alpha=0.5)
    for i, ev in enumerate(result.events, start=1):
        ax.axvspan(ev["t_start"], ev["t_end"], color="C1", alpha=0.25)
        ax.text(
            0.5 * (ev["t_start"] + ev["t_end"]),
            ax.get_ylim()[1] if ax.get_ylim()[1] < 2 else 1.02,
            f"E{i}", color="C1", fontsize=8, ha="center", va="bottom",
        )
    ax.set_xlabel("Time (BTJD or similar)")
    ax.set_ylabel("Normalized flux")
    tic = result.star.tic_id
    ax.set_title(f"Light curve — TIC {tic}" if tic else "Light curve")

    ax = axes[1]
    if result.bls.get("periodogram"):
        p = result.bls["periodogram"]
        ax.plot(p["periods"], p["power"], "C0-", lw=0.8)
        ax.axvline(result.bls["period"], color="C3", ls="--", lw=1,
                   label=f"P = {result.bls['period']:.4f} d  SDE={result.bls['sde']:.1f}")
        ax.legend(fontsize=8, loc="upper right")
    ax.set_xlabel("Period (d)")
    ax.set_ylabel("BLS power")
    ax.set_title("Box Least Squares")

    ax = axes[2]
    if result.lomb_scargle.get("periodogram"):
        p = result.lomb_scargle["periodogram"]
        ax.plot(p["periods"], p["power"], "C2-", lw=0.8)
        ax.axvline(result.lomb_scargle["top_period"], color="C3", ls="--", lw=1,
                   label=f"P = {result.lomb_scargle['top_period']:.4f} d")
        ax.legend(fontsize=8, loc="upper right")
    ax.set_xlabel("Period (d)")
    ax.set_ylabel("LS power")
    ax.set_title("Lomb-Scargle")

    fig.tight_layout()
    return fig


def build_transit_zoom(result, t, f) -> Optional[plt.Figure]:
    """Grid of up to 6 events, one per panel."""
    events = result.events[:6]
    if not events:
        return None
    n = len(events)
    cols = min(3, n)
    rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(3.2 * cols, 2.2 * rows), squeeze=False)
    for i, ev in enumerate(events):
        ax = axes[i // cols][i % cols]
        pad = 0.4 * ev["duration_d"] + 0.05
        m = (t > ev["t_start"] - pad) & (t < ev["t_end"] + pad)
        ax.plot(t[m], f[m], "k.", ms=2)
        ax.axvspan(ev["t_start"], ev["t_end"], color="C1", alpha=0.25)
        ax.set_title(
            f"E{i + 1}  depth={ev['depth'] * 1e2:.2f}%  SNR={ev['depth_snr']:.1f}",
            fontsize=9,
        )
        ax.tick_params(labelsize=7)
    # Blank the leftover axes
    for j in range(n, rows * cols):
        axes[j // cols][j % cols].axis("off")
    fig.tight_layout()
    return fig


# ----------------------------------------------------------------------
# Microlensing plot
# ----------------------------------------------------------------------
def build_microlens_fit(result) -> plt.Figure:
    """LC in the fit window with all three model overlays + BIC bar chart."""
    fig, axes = plt.subplots(2, 1, figsize=(8, 6.5), gridspec_kw={"height_ratios": [3, 1]})

    ax = axes[0]
    t = result.t
    ax.errorbar(t, result.flux_n, yerr=result.flux_err_n,
                fmt="k.", ms=2, elinewidth=0.5, alpha=0.7)
    order = np.argsort(t)
    if result.pspl.model_flux is not None:
        ax.plot(t[order], result.pspl.model_flux[order], "C0-", lw=1.5,
                label=f"PSPL  BIC={result.pspl.bic:.1f}")
    if result.flare.model_flux is not None:
        ax.plot(t[order], result.flare.model_flux[order], "C1--", lw=1.2,
                label=f"Flare BIC={result.flare.bic:.1f}")
    ax.axhline(result.null.params["baseline"], color="C7", ls=":", lw=1,
               label=f"Null  BIC={result.null.bic:.1f}")
    ax.set_xlabel("Time")
    ax.set_ylabel("Normalized flux")
    ax.set_title(
        f"Verdict: {result.verdict.upper()}  (confidence {result.confidence:.2f})"
    )
    ax.legend(fontsize=8, loc="best")

    ax = axes[1]
    names = ["PSPL", "Flare", "Null"]
    bics = [result.pspl.bic, result.flare.bic, result.null.bic]
    ax.bar(names, bics, color=["C0", "C1", "C7"])
    ax.set_ylabel("BIC (lower = better)")

    fig.tight_layout()
    return fig


# ----------------------------------------------------------------------
# I/O
# ----------------------------------------------------------------------
def save_png(fig: plt.Figure, path: str, dpi: int = 120) -> str:
    fig.savefig(path, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return path


def fig_to_png_bytes(fig: plt.Figure, dpi: int = 120) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
