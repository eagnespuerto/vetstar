"""Matplotlib figure for the OGLE event search, in the transit plots' style
(vetstar_core/pipeline/plots.py): black cadence dots, crimson time bins,
red-shaded event window, plain y-axis without offset."""
from __future__ import annotations

import base64
import io
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .locks import PLOT_LOCK
from .pipeline.plots import _time_bin


def _pspl(t, t0, tE, u0, fs, fb):
    u = np.sqrt(u0 ** 2 + ((t - t0) / tE) ** 2)
    a = (u ** 2 + 2) / (u * np.sqrt(u ** 2 + 4))
    return fs * a + fb


def _plain_y(ax):
    ax.ticklabel_format(axis="y", useOffset=False, style="plain")
    ax.yaxis.set_major_formatter(plt.ScalarFormatter(useOffset=False))


def make_ogle_figure(time, flux, report: dict, title: Optional[str] = None,
                     bin_minutes: int = 30) -> str:
    """PNG (base64) of the analysed light curve, strong bumps, event window,
    PSPL fit and residuals."""
    t = np.asarray(time, float)
    f = np.asarray(flux, float)
    fit = report.get("fit")
    model = _pspl(t, fit["t0"], fit["tE"], fit["u0"], fit["F_s"], fit["F_b"]) if fit else None
    win = report.get("event_window")
    strong = [b for b in report.get("bumps", []) if b.get("chi2_3plus", 0) >= 32]

    with PLOT_LOCK:
        if model is not None:
            fig, (ax, axr) = plt.subplots(2, 1, figsize=(10, 4.6), sharex=True,
                                          gridspec_kw={"height_ratios": [3, 1.2], "hspace": 0.06})
        else:
            fig, ax = plt.subplots(figsize=(10, 3))
            axr = None

        ax.plot(t, f, "k.", ms=1, alpha=0.35, label="cadences")
        t_b, f_b, e_b = _time_bin(t, f, bin_minutes)
        if len(t_b):
            ax.errorbar(t_b, f_b, yerr=e_b, fmt="o", ms=2.4, color="crimson", ecolor="crimson",
                        elinewidth=0.6, capsize=0, alpha=0.8, label=f"{bin_minutes}-min bins")
        if win:
            ax.axvspan(max(win["t_start"], t.min()), min(win["t_end"], t.max()),
                       color="red", alpha=0.12, label="event window")
        if model is not None:
            order = np.argsort(t)
            tm, mm = t[order], model[order].copy()
            mm[1:][np.diff(tm) > 0.5] = np.nan  # break the line across data gaps
            ax.plot(tm, mm, "C1-", lw=1.2, label="PSPL fit")

        ymax = float(np.nanmax(f))
        ymin = float(np.nanpercentile(f, 0.3))
        pad = 0.06 * (ymax - ymin or 1e-3)
        ax.set_ylim(ymin - pad, ymax + 3 * pad)
        for b in strong:
            ax.annotate("▼", (b["t_peak"], ymax + 1.6 * pad), ha="center", va="center",
                        color="darkorange", fontsize=9)
        ax.set_ylabel("Normalised flux")
        _plain_y(ax)
        # Legend in a row above the plot so it never hides the data or the ▼ marks.
        ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), fontsize=8, ncol=4, frameon=False)
        cleaned = " (slow variability removed)" if report.get("cleaning") else ""
        fig.suptitle(title or f"OGLE event search{cleaned}: {len(strong)} bump(s) with χ²₃₊ ≥ 32",
                     fontsize=10, y=1.0)

        if axr is not None:
            axr.plot(t, f - model, "k.", ms=1, alpha=0.35)
            axr.axhline(0.0, color="gray", ls=":", alpha=0.6)
            axr.set_ylabel("Data − fit")
            _plain_y(axr)
            axr.set_xlabel("Time (BTJD)")
        else:
            ax.set_xlabel("Time (BTJD)")

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
        plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")
