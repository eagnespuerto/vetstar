"""
HCI summary image generator.

Renders a single self-contained PNG that combines, for one target:

  * the Habitability Chance Index headline score + tier,
  * the six STEHM sub-score **metrics** with their **weightings**,
  * the POE predicted **observables**, and
  * the **TLCM** model-independent geometry values.

Returned as a base64-encoded PNG so it can be shipped straight to the
frontend (collapsible / shareable image) and embedded in the PDF report.

The renderer is defensive: every field is optional and missing values are
shown as "—" rather than raising.
"""
from __future__ import annotations

import base64
import io
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ----------------------------------------------------------------------
# formatting helpers
# ----------------------------------------------------------------------
def _fmt(v, nd=3, suffix=""):
    if v is None:
        return "—"
    if isinstance(v, bool):
        return ("yes" if v else "no") + suffix
    if isinstance(v, (int,)):
        return f"{v}{suffix}"
    if isinstance(v, float):
        if v != 0 and (abs(v) < 1e-3 or abs(v) >= 1e6):
            return f"{v:.2e}{suffix}"
        return f"{v:.{nd}f}{suffix}"
    return f"{v}{suffix}"


def _bar_color(score01: float) -> str:
    if score01 >= 0.7:
        return "#10b981"
    if score01 >= 0.4:
        return "#f59e0b"
    if score01 >= 0.2:
        return "#ef4444"
    return "#94a3b8"


def _tier_color(score: float) -> str:
    if score >= 70:
        return "#047857"
    if score >= 45:
        return "#b45309"
    if score >= 20:
        return "#b91c1c"
    return "#475569"


def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _table(ax, title, rows):
    """Render a borderless 2-column key/value table onto a hidden axis."""
    ax.axis("off")
    ax.set_title(title, fontsize=11, fontweight="bold", loc="left", color="#1e293b", pad=6)
    if not rows:
        ax.text(0.0, 0.95, "Not available.", fontsize=9, color="#64748b",
                va="top", transform=ax.transAxes)
        return
    n = len(rows)
    y0, dy = 0.96, min(0.085, 0.96 / max(n, 1))
    for i, (k, v) in enumerate(rows):
        y = y0 - i * dy
        if y < 0:
            break
        ax.text(0.0, y, k, fontsize=8.5, color="#475569", va="top",
                transform=ax.transAxes)
        ax.text(1.0, y, v, fontsize=8.5, color="#0f172a", va="top", ha="right",
                family="monospace", transform=ax.transAxes)


# ----------------------------------------------------------------------
# row builders
# ----------------------------------------------------------------------
def _observable_rows(obs: dict) -> list:
    if not obs:
        return []
    hz = obs.get("habitable_zone") or {}
    orbit = obs.get("orbit") or {}
    planet = obs.get("planet") or {}
    rv = obs.get("radial_velocity") or {}
    transit = obs.get("transit") or {}
    astro = obs.get("astrometric") or {}
    rows = [
        ("Stellar luminosity (L\u2299)", _fmt(obs.get("luminosity_lsun"), 4)),
        ("Insolation (S\u2295)", _fmt(obs.get("insolation_searth"), 3)),
        ("HZ centre (AU)", _fmt(hz.get("center_au"), 3)),
        ("HZ inner / outer (AU)",
         f"{_fmt(hz.get('inner_au'), 3)} / {_fmt(hz.get('outer_au'), 3)}"),
        ("Semi-major axis a (AU)", _fmt(orbit.get("semi_major_axis_au"), 4)),
        ("Orbital period (d)", _fmt(orbit.get("orbital_period_d"), 4)),
        ("Planet radius (R\u2295)", _fmt(planet.get("rp_earth"), 3)),
        ("Planet mass (M\u2295)", _fmt(planet.get("mp_earth"), 3)),
        ("RV semi-amplitude K (m/s)", _fmt(rv.get("K_ms"), 3)),
        ("Predicted transit depth (%)", _fmt(transit.get("depth_pct"), 4)),
    ]
    if astro.get("theta_uas") is not None:
        rows.append(("Astrometric \u0394\u03b8 (\u03bcas)", _fmt(astro.get("theta_uas"), 3)))
    if obs.get("max_projected_separation_arcsec") is not None:
        rows.append(("Max separation (arcsec)",
                     _fmt(obs.get("max_projected_separation_arcsec"), 4)))
    return rows


def _tlcm_rows(tlcm: dict) -> list:
    if not tlcm:
        return []
    rv = tlcm.get("radial_velocity") or {}
    rows = [
        ("Radius ratio k = Rp/Rs", _fmt(tlcm.get("radius_ratio_k"), 4)),
        ("Scaled axis a/Rs (duration)", _fmt(tlcm.get("a_over_rs"), 3)),
        ("Scaled axis a/Rs (dynamical)", _fmt(tlcm.get("a_over_rs_dynamical"), 3)),
        ("a/Rs agreement (%)", _fmt(tlcm.get("a_over_rs_agreement_pct"), 1)),
        ("Stellar density (\u03c1\u2299)", _fmt(tlcm.get("stellar_density_rho_sun"), 3)),
        ("Stellar density (g/cm\u00b3)", _fmt(tlcm.get("stellar_density_gcc"), 3)),
        ("Photometric a (AU)", _fmt(tlcm.get("a_au_photometric"), 4)),
        ("M\u2605 from density (M\u2299)", _fmt(tlcm.get("mstar_from_density_sun"), 3)),
        ("Inclination (deg)", _fmt(tlcm.get("inclination_deg"), 3)),
    ]
    if rv.get("mp_earth") is not None:
        rows.append(("Companion mass (M\u2295, RV)", _fmt(rv.get("mp_earth"), 3)))
    elif rv.get("mp_mjup") is not None:
        rows.append(("Companion mass (M_Jup, RV)", _fmt(rv.get("mp_mjup"), 4)))
    return rows


# ----------------------------------------------------------------------
# main entry point
# ----------------------------------------------------------------------
def make_hci_summary_image(
    hci: Optional[dict],
    observables: Optional[dict] = None,
    tlcm: Optional[dict] = None,
    planet: Optional[dict] = None,
    title: Optional[str] = None,
) -> Optional[str]:
    """
    Build the combined HCI summary figure.

    Returns a base64 PNG string, or None if no HCI data was supplied.
    """
    if not hci:
        return None

    sub_scores = hci.get("sub_scores") or []
    score = hci.get("hci")
    tier = hci.get("tier", "")

    fig = plt.figure(figsize=(9.0, 7.4))
    gs = fig.add_gridspec(
        nrows=3, ncols=2,
        height_ratios=[1.0, 2.4, 2.6],
        hspace=0.45, wspace=0.18,
        left=0.06, right=0.96, top=0.93, bottom=0.06,
    )

    # --- header band ---------------------------------------------------
    ax_head = fig.add_subplot(gs[0, :])
    ax_head.axis("off")
    heading = title or "Habitability Chance Index"
    # Title row
    ax_head.text(0.0, 0.95, heading, fontsize=15, fontweight="bold",
                 color="#0f172a", va="top", transform=ax_head.transAxes)
    ax_head.text(0.0, 0.62, hci.get("paper_ref", ""), fontsize=8,
                 style="italic", color="#64748b", va="top",
                 transform=ax_head.transAxes)
    # Score on its own line, below the title
    if score is not None:
        rng = ""
        if hci.get("hci_low") is not None and hci.get("hci_high") is not None \
                and (hci["hci_high"] - hci["hci_low"]) > 0.1:
            rng = f"  ({hci['hci_low']}\u2013{hci['hci_high']})"
        ax_head.text(0.0, 0.30, f"{_fmt(score, 1)} / 100{rng}",
                     fontsize=20, fontweight="bold", color=_tier_color(score),
                     va="top", ha="left", transform=ax_head.transAxes)
        ax_head.text(1.0, 0.32, f"Tier: {tier}", fontsize=11,
                     color=_tier_color(score), va="top", ha="right",
                     transform=ax_head.transAxes)
        dmod = hci.get("density_modifier") or 0
        dlabel = hci.get("density_modifier_label")
        if dmod and dlabel:
            sign = "+" if dmod > 0 else "−"
            mod_color = "#047857" if dmod > 0 else "#b91c1c"
            ax_head.text(
                1.0, 0.62,
                f"Density modifier: {sign}{abs(dmod)} pts ({dlabel})",
                fontsize=8.5, color=mod_color, va="top", ha="right",
                transform=ax_head.transAxes,
            )

    # --- sub-score metrics + weightings (horizontal bars) --------------
    ax_bar = fig.add_subplot(gs[1, :])
    if sub_scores:
        labels, scores01, weights, value_labels = [], [], [], []
        for s in sub_scores:
            w = s.get("weight", 0) or 0
            labels.append(f"{s.get('name','?')}  ({w*100:.0f}%)")
            scores01.append((s.get("score") or 0))
            weights.append(w)
            value_labels.append(s.get("label", ""))
        ypos = range(len(labels))
        colors = [_bar_color(v) for v in scores01]
        ax_bar.barh(list(ypos), [v * 100 for v in scores01],
                    color=colors, edgecolor="#e2e8f0", height=0.62)
        ax_bar.set_yticks(list(ypos))
        ax_bar.set_yticklabels(labels, fontsize=8.5)
        ax_bar.invert_yaxis()
        ax_bar.set_xlim(0, 100)
        ax_bar.set_xlabel("Sub-score (0\u2013100)", fontsize=9)
        ax_bar.set_title("Score components, weightings & sub-scores",
                         fontsize=11, fontweight="bold", loc="left",
                         color="#1e293b", pad=6)
        ax_bar.grid(axis="x", color="#f1f5f9", lw=0.8)
        ax_bar.set_axisbelow(True)
        for sp in ("top", "right"):
            ax_bar.spines[sp].set_visible(False)
        for i, (v, lab) in enumerate(zip(scores01, value_labels)):
            ax_bar.text(min(v * 100 + 1.5, 99), i, f"{v*100:.0f}",
                        va="center", ha="left", fontsize=8, color="#334155")
            if lab:
                ax_bar.text(1.5, i + 0.34, lab, va="center", ha="left",
                            fontsize=6.6, color="#475569", alpha=0.85)
    else:
        ax_bar.axis("off")
        ax_bar.text(0.0, 0.9, "No sub-scores available.", fontsize=9,
                    color="#64748b", va="top", transform=ax_bar.transAxes)

    # --- observables + TLCM tables -------------------------------------
    ax_obs = fig.add_subplot(gs[2, 0])
    _table(ax_obs, "Predicted observables (POE)", _observable_rows(observables or {}))
    obs_planet = (observables or {}).get("planet") or {}
    if obs_planet.get("mass_fallback_powerlaw"):
        ax_obs.text(
            0.0, -0.02,
            "Mass: power-law fallback (Chen & Kipping radius-degenerate\n"
            "for R$_p$ > ~14 R$_\\oplus$; pins to ~131.6 M$_\\oplus$).",
            fontsize=6.8, style="italic", color="#64748b",
            va="top", ha="left", transform=ax_obs.transAxes,
        )

    ax_tlcm = fig.add_subplot(gs[2, 1])
    _table(ax_tlcm, "Transit geometry & masses (TLCM)", _tlcm_rows(tlcm or {}))

    return _fig_to_b64(fig)
