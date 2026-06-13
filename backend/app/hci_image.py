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
from matplotlib.patches import Circle, Wedge


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
# planet-system diagram (mirrors the ExoWorld tuner SVG stage)
# ----------------------------------------------------------------------
def _star_color(teff: Optional[float]) -> str:
    if teff is None:
        return "#ffd76b"
    if teff >= 7500:
        return "#cad7ff"
    if teff >= 6000:
        return "#fff4e0"
    if teff >= 5200:
        return "#ffd76b"
    if teff >= 3700:
        return "#ff9a4d"
    return "#ef5a3a"


def _spectral_type(teff: Optional[float]) -> str:
    if teff is None:
        return "★"
    for thresh, sp in (
        (30000, "O"), (10000, "B"), (7500, "A"),
        (6000, "F"), (5200, "G"), (3700, "K"), (2400, "M"),
    ):
        if teff >= thresh:
            return sp
    return "L"


def _draw_planet_diagram(ax, observables: dict, planet: Optional[dict]) -> None:
    """Render the star/HZ/orbit/planet stage diagram (ExoWorld style)."""
    obs = observables or {}
    inputs = obs.get("inputs") or {}
    hz = obs.get("habitable_zone") or {}
    orbit = obs.get("orbit") or {}
    pl = obs.get("planet") or {}

    teff = inputs.get("teff_k")
    rstar = inputs.get("rstar_sun") or 1.0
    au = orbit.get("semi_major_axis_au")
    rp = pl.get("rp_earth") or (planet or {}).get("rp_earth")
    hz_in = hz.get("inner_au")
    hz_out = hz.get("outer_au")

    ax.set_facecolor("#0c1322")
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_edgecolor("#1f2a3d")
        sp.set_linewidth(0.8)

    if au is None or hz_in is None or hz_out is None:
        ax.text(0.5, 0.5, "System diagram\nunavailable",
                ha="center", va="center", color="#64748b", fontsize=8,
                transform=ax.transAxes)
        return

    # The ExoWorld tuner SVG uses preserveAspectRatio="none" — the stage
    # stretches to fill its container so the HZ band, orbit and planet
    # are rendered as ellipses that fan out across the full panel. Match
    # that here by NOT forcing aspect=equal; the patches end up filling
    # the entire diagram column instead of being letterboxed.
    max_au = max(hz_out * 1.20, au * 1.15, 0.02)
    ax.set_xlim(-0.10 * max_au, max_au * 1.10)
    ax.set_ylim(-max_au * 1.0, max_au * 1.0)

    band = Wedge((0, 0), hz_out, 0, 360, width=(hz_out - hz_in),
                 facecolor="#34e3a4", alpha=0.18, edgecolor="none", zorder=1)
    ax.add_patch(band)
    for r in (hz_in, hz_out):
        ax.add_patch(Circle((0, 0), r, fill=False, edgecolor="#34e3a4",
                            linestyle=(0, (3, 4)), linewidth=0.8,
                            alpha=0.55, zorder=2))

    ax.add_patch(Circle((0, 0), au, fill=False, edgecolor="#ffffff",
                        linewidth=0.6, alpha=0.20, zorder=2))

    star_col = _star_color(teff)
    star_r = max_au * 0.050 * min(2.5, max(0.6, rstar))
    for alpha, mult in ((0.40, 2.3), (0.20, 1.7), (0.12, 1.3)):
        ax.add_patch(Circle((0, 0), star_r * mult, color=star_col,
                            alpha=alpha, zorder=3, linewidth=0))
    ax.add_patch(Circle((0, 0), star_r, color=star_col, zorder=4, linewidth=0))
    ax.add_patch(Circle((0, 0), star_r * 0.55, color="#ffffff",
                        alpha=0.85, zorder=5, linewidth=0))

    planet_r = max_au * 0.028 * min(3.0, max(0.5, (rp or 1.0) / 1.5))
    min_sep = star_r + planet_r + max_au * 0.012
    px = max(au, min_sep)
    if hz_in <= au <= hz_out:
        planet_col = "#34e3a4"
    elif au < hz_in:
        planet_col = "#ef5a3a"
    else:
        planet_col = "#a0b4d6"
    ax.add_patch(Circle((px, 0), planet_r, color=planet_col, zorder=6,
                        linewidth=0))
    ax.add_patch(Wedge((px, 0), planet_r, -90, 90,
                       facecolor="#0a0e1a", alpha=0.55, zorder=7,
                       edgecolor="none"))

    hz_center = (hz_in + hz_out) / 2
    ax.text(hz_center, -max_au * 0.52, "HZ", color="#34e3a4", alpha=0.85,
            fontsize=7, ha="center", va="center",
            family="monospace", fontweight="bold")

    sp_letter = _spectral_type(teff)
    teff_str = f"{int(round(teff))} K" if teff else "— K"
    au_str = f"{au:.3f}" if au < 0.1 else f"{au:.2f}"
    rp_str = f"{rp:.1f}" if rp else "—"
    ax.text(0.985, 0.95, f"{sp_letter}-type · {teff_str}",
            fontsize=7.5, color="#ffffff", ha="right", va="top",
            transform=ax.transAxes, fontweight="bold")
    ax.text(0.985, 0.83, f"{au_str} AU · {rp_str} R⊕",
            fontsize=7.5, color="#ffffff", ha="right", va="top",
            transform=ax.transAxes, fontweight="bold")


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
        height_ratios=[2.2, 1.9, 2.4],
        hspace=0.45, wspace=0.18,
        left=0.06, right=0.96, top=0.93, bottom=0.06,
    )

    # Split the header row: text on the left, planet-system diagram on the
    # right. Sub-gridspec keeps the diagram independent of the table columns
    # below so the observables/TLCM tables remain equal-width.
    # Diagram column doubled in width vs. the prior layout (it now takes
    # ~69% of the row instead of ~35%). The heading text fontsizes are
    # tightened accordingly so the title and score still fit in the
    # narrower left column.
    gs_head = gs[0, :].subgridspec(1, 2, width_ratios=[0.45, 1.0], wspace=0.06)

    # --- header band ---------------------------------------------------
    ax_head = fig.add_subplot(gs_head[0, 0])
    ax_diag = fig.add_subplot(gs_head[0, 1])
    _draw_planet_diagram(ax_diag, observables or {}, planet)
    ax_head.axis("off")
    heading = title or "Habitability Chance Index"
    # Title row
    # Title is clipped to a "HCI — ..." short form when long so it fits
    # the narrower text column without spilling under the diagram, which
    # has an opaque dark facecolor and would otherwise mask the overflow.
    short_heading = heading
    if heading and len(heading) > 22:
        short_heading = heading.replace("Habitability Chance Index", "HCI")
    ax_head.text(0.0, 0.95, short_heading, fontsize=11, fontweight="bold",
                 color="#0f172a", va="top", transform=ax_head.transAxes)
    ax_head.text(0.0, 0.72, hci.get("paper_ref", ""), fontsize=6.5,
                 style="italic", color="#64748b", va="top",
                 transform=ax_head.transAxes)
    # Score on its own line, below the title
    if score is not None:
        rng = ""
        if hci.get("hci_low") is not None and hci.get("hci_high") is not None \
                and (hci["hci_high"] - hci["hci_low"]) > 0.1:
            rng = f"  ({hci['hci_low']}\u2013{hci['hci_high']})"
        ax_head.text(0.0, 0.45, f"{_fmt(score, 1)} / 100{rng}",
                     fontsize=15, fontweight="bold", color=_tier_color(score),
                     va="top", ha="left", transform=ax_head.transAxes)
        ax_head.text(0.0, 0.18, f"Tier: {tier}", fontsize=9,
                     color=_tier_color(score), va="top", ha="left",
                     transform=ax_head.transAxes)
        dmod = hci.get("density_modifier") or 0
        dlabel = hci.get("density_modifier_label")
        if dmod and dlabel:
            sign = "+" if dmod > 0 else "−"
            mod_color = "#047857" if dmod > 0 else "#b91c1c"
            ax_head.text(
                0.0, 0.02,
                f"Density modifier: {sign}{abs(dmod)} pts ({dlabel})",
                fontsize=7.5, color=mod_color, va="top", ha="left",
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
