"""GUI-only matplotlib helpers.

The vendored ``pipeline.make_plots`` and ``report.build_pdf`` already
produce all the plots and PDF sections shown on screen and in the report.
This module is a small extra: it builds a *live* matplotlib Figure the
Tkinter canvas can embed (so the plot resizes with the window, has native
zoom, etc.), without going through the base64 round-trip that the PDF
pipeline uses.
"""
from __future__ import annotations

from typing import Optional

import matplotlib
matplotlib.use("Agg")  # noqa: E402  (TkAgg gets picked up when gui.py imports it)
import matplotlib.pyplot as plt
import numpy as np


def build_transit_overview(result, t, f):
    """Two-panel figure: full LC with event shading + zoom of the deepest event."""
    events = result.events or []
    fig, axes = plt.subplots(2, 1, figsize=(8.5, 6.0))

    ax = axes[0]
    ax.plot(t, f, "k.", ms=1.4, alpha=0.5)
    for i, ev in enumerate(events, start=1):
        ax.axvspan(ev["t_start"], ev["t_end"], color="C1", alpha=0.25)
    ax.set_xlabel("Time (BTJD or similar)")
    ax.set_ylabel("Normalised flux")
    tic = result.star.tic_id
    ax.set_title(f"Light curve — TIC {tic}" if tic else "Light curve")

    ax = axes[1]
    if events:
        primary = max(events, key=lambda e: e["depth"])
        pad = max(0.4 * primary["duration_d"], 0.05)
        m = (t > primary["t_start"] - pad) & (t < primary["t_end"] + pad)
        ax.plot(t[m], f[m], "k.", ms=2.2)
        ax.axvspan(primary["t_start"], primary["t_end"], color="C1", alpha=0.25)
        ax.set_title(
            f"Deepest event: depth={primary['depth'] * 100:.3f}%  "
            f"SNR={primary['depth_snr']:.1f}",
            fontsize=10,
        )
    else:
        ax.text(0.5, 0.5, "no events detected", ha="center", va="center",
                transform=ax.transAxes, color="grey")
        ax.set_xticks([])
        ax.set_yticks([])
    ax.set_xlabel("Time")
    ax.set_ylabel("Flux")

    fig.tight_layout()
    return fig


def build_microlens_fit(result) -> plt.Figure:
    """LC overlaid with all three model fits + BIC bar chart.

    ``result`` is the dict returned by :func:`microlensing.analyze_event`.
    """
    fig, axes = plt.subplots(2, 1, figsize=(8.5, 6.5),
                             gridspec_kw={"height_ratios": [3, 1]})

    ax = axes[0]
    t = np.asarray(result["time_windowed"])
    f_n = np.asarray(result["flux_normalized"])
    fe_n = np.asarray(result["flux_err_normalized"])
    order = np.argsort(t)
    ax.errorbar(t, f_n, yerr=fe_n, fmt="k.", ms=2, elinewidth=0.5, alpha=0.7)

    pspl = result["models"].get("pspl") or {}
    flare = result["models"].get("flare") or {}
    null_ = result["models"].get("null") or {}
    if pspl.get("model_flux"):
        ax.plot(t[order], np.asarray(pspl["model_flux"])[order], "C0-", lw=1.6,
                label=f"PSPL  BIC={pspl.get('bic'):.1f}")
    if flare.get("model_flux"):
        ax.plot(t[order], np.asarray(flare["model_flux"])[order], "C1--", lw=1.2,
                label=f"Flare BIC={flare.get('bic'):.1f}")
    if null_.get("model_flux"):
        ax.axhline(np.asarray(null_["model_flux"]).mean(), color="C7", ls=":", lw=1,
                   label=f"Null  BIC={null_.get('bic'):.1f}")
    ax.set_xlabel("Time")
    ax.set_ylabel("Normalised flux")
    conf = result.get("confidence", 0.0)
    ax.set_title(f"Verdict: {result.get('verdict', '?').upper()}  (confidence {conf:.2f})")
    ax.legend(fontsize=8, loc="best")

    ax = axes[1]
    names = ["PSPL", "Flare", "Null"]
    bics = [pspl.get("bic", np.nan), flare.get("bic", np.nan), null_.get("bic", np.nan)]
    ax.bar(names, bics, color=["C0", "C1", "C7"])
    ax.set_ylabel("BIC (lower = better)")

    fig.tight_layout()
    return fig


def build_raw_lc(t, flux, flux_err=None, title="Raw light curve") -> plt.Figure:
    fig, ax = plt.subplots(figsize=(8.5, 4.5))
    ax.errorbar(t, flux, yerr=flux_err, fmt="k.", ms=2,
                elinewidth=0.3 if flux_err is not None else 0, alpha=0.7)
    ax.set_xlabel("Time")
    ax.set_ylabel("Flux")
    ax.set_title(title)
    fig.tight_layout()
    return fig


def build_coverage_summary(coverage: dict) -> Optional[plt.Figure]:
    """One-panel bar chart of per-event observability (green = observable)."""
    events = coverage.get("events") or []
    if not events:
        return None
    labels = [str(e.get("event_id", i)) for i, e in enumerate(events)]
    obs = [1 if e.get("observable") else 0 for e in events]
    fig, ax = plt.subplots(figsize=(max(6, len(labels) * 0.4), 3.5))
    ax.bar(labels, obs, color=["#22c55e" if o else "#cbd5e1" for o in obs],
           edgecolor="white", linewidth=0.6)
    ax.set_ylim(0, 1.2)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["Not observable", "Observable"])
    ax.set_title(f"TESS sector coverage — {sum(obs)}/{len(obs)} observable")
    ax.tick_params(axis="x", labelrotation=90, labelsize=8)
    fig.tight_layout()
    return fig
