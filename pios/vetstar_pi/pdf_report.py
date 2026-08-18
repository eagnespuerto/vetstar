"""ReportLab PDF builder for both pipelines.

The full server report has ~20 sections (HCI, POE, TLCM, ExoMiner, DVT, etc.);
the Pi port keeps the vetting essentials — verdict, target, tables of the
key measurements, and embedded diagnostic plots — which is what a Pi user
needs offline.
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from . import __version__
from .plots import build_microlens_fit, build_transit_overview, build_transit_zoom, fig_to_png_bytes


_STYLES = getSampleStyleSheet()
_H1 = ParagraphStyle("h1", parent=_STYLES["Heading1"], fontSize=14, spaceAfter=6)
_H2 = ParagraphStyle("h2", parent=_STYLES["Heading2"], fontSize=11, spaceAfter=4)
_BODY = ParagraphStyle("body", parent=_STYLES["BodyText"], fontSize=9, leading=12)
_SMALL = ParagraphStyle("small", parent=_STYLES["BodyText"], fontSize=8, textColor=colors.grey)


def _table(rows, col_widths=None):
    t = Table(rows, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.grey)
    canvas.drawString(
        0.7 * inch, 0.4 * inch,
        f"VetStar Pi v{__version__} — generated {datetime.utcnow().strftime('%Y-%m-%d %H:%MZ')}",
    )
    canvas.drawRightString(
        LETTER[0] - 0.7 * inch, 0.4 * inch, f"Page {canvas.getPageNumber()}",
    )
    canvas.restoreState()


def _fmt(v, fmt="{:g}", none="—"):
    if v is None:
        return none
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f:  # NaN
        return none
    return fmt.format(f)


# ----------------------------------------------------------------------
# Transit report
# ----------------------------------------------------------------------
def build_transit_pdf(result, t, f, out_path: str) -> str:
    """Write a transit vetting PDF to ``out_path`` and return the path."""
    doc = SimpleDocTemplate(
        out_path, pagesize=LETTER,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.6 * inch, bottomMargin=0.7 * inch,
        title="VetStar Pi — Transit vetting report",
    )
    story = []

    tic = result.star.tic_id
    title = f"Transit vetting report — TIC {tic}" if tic else "Transit vetting report"
    story.append(Paragraph(title, _H1))
    story.append(Paragraph(
        f"<b>Verdict:</b> {result.verdict.get('headline', '—')} "
        f"(category <i>{result.verdict.get('category', '—')}</i>, "
        f"confidence {result.verdict.get('confidence', 0):.2f})",
        _BODY,
    ))
    story.append(Spacer(1, 6))

    # ---- Target ----
    story.append(Paragraph("Target", _H2))
    s = result.star
    story.append(_table([
        ["Field", "Value"],
        ["TIC ID", str(s.tic_id) if s.tic_id else "—"],
        ["Tmag", _fmt(s.tmag, "{:.3f}")],
        ["Teff (K)", _fmt(s.teff, "{:.0f}")],
        ["Radius (R☉)", _fmt(s.radius, "{:.3f}")],
        ["log g", _fmt(s.logg, "{:.3f}")],
        ["RA / Dec (deg)", f"{_fmt(s.ra, '{:.5f}')} / {_fmt(s.dec, '{:.5f}')}"],
        ["Sector / Camera / CCD",
         f"{_fmt(s.sector, '{:.0f}')} / {_fmt(s.camera, '{:.0f}')} / {_fmt(s.ccd, '{:.0f}')}"],
        ["CROWDSAP", _fmt(s.crowdsap, "{:.3f}")],
    ], col_widths=[1.7 * inch, 4.6 * inch]))
    story.append(Spacer(1, 8))

    # ---- Periodograms + events ----
    story.append(Paragraph("Signal search", _H2))
    bls = result.bls
    story.append(_table([
        ["Metric", "Value"],
        ["BLS period (d)", _fmt(bls.get("period"), "{:.6f}")],
        ["BLS t0", _fmt(bls.get("t0"), "{:.5f}")],
        ["BLS duration (d)", _fmt(bls.get("duration"), "{:.5f}")],
        ["BLS depth (frac)", _fmt(bls.get("depth"), "{:.5f}")],
        ["BLS SDE", _fmt(bls.get("sde"), "{:.2f}")],
        ["BLS transits in window", str(bls.get("n_transits_in_window", "—"))],
        ["LS top period (d)", _fmt(result.lomb_scargle.get("top_period"), "{:.6f}")],
        ["LS FAP", _fmt(result.lomb_scargle.get("false_alarm_prob"), "{:.3e}")],
        ["Events detected", str(len(result.events))],
    ], col_widths=[1.7 * inch, 4.6 * inch]))
    story.append(Spacer(1, 8))

    if result.events:
        ev_rows = [["#", "t_start", "t_end", "dur (d)", "depth", "SNR"]]
        for i, ev in enumerate(result.events[:12], start=1):
            ev_rows.append([
                str(i),
                _fmt(ev["t_start"], "{:.4f}"),
                _fmt(ev["t_end"], "{:.4f}"),
                _fmt(ev["duration_d"], "{:.4f}"),
                _fmt(ev["depth"], "{:.5f}"),
                _fmt(ev["depth_snr"], "{:.1f}"),
            ])
        story.append(_table(ev_rows))
        story.append(Spacer(1, 8))

    # ---- Diagnostics ----
    story.append(Paragraph("Diagnostics", _H2))
    oe = result.odd_even
    sec = result.secondary
    cen = result.centroid
    sh = result.shape
    story.append(_table([
        ["Test", "Result"],
        ["Odd/even",
         (f"Δ={_fmt(oe.get('difference'), '{:.5f}')}  σ={_fmt(oe.get('sigma'), '{:.2f}')}  "
          f"flag_eb={oe.get('flag_eb')}") if oe.get("available") else oe.get("reason", "n/a")],
        ["Secondary eclipse",
         (f"σ={_fmt(sec.get('sigma'), '{:.2f}')}  detected={sec.get('detected')}")
         if sec.get("available") else sec.get("reason", "n/a")],
        ["Centroid",
         (f"Δcol={_fmt(cen.get('shift_col_sigma'), '{:.2f}')}σ  "
          f"Δrow={_fmt(cen.get('shift_row_sigma'), '{:.2f}')}σ  "
          f"on_target={cen.get('on_target')}")
         if cen.get("available") else cen.get("reason", "n/a")],
        ["Shape",
         (f"t14={_fmt(sh.get('t14_hours'), '{:.2f}')} h  "
          f"t23/t14={_fmt(sh.get('t23_over_t14'), '{:.2f}')}  "
          f"class={sh.get('shape_class')}")
         if sh.get("available") else "n/a"],
    ], col_widths=[1.7 * inch, 4.6 * inch]))
    story.append(Spacer(1, 8))

    # ---- Physics ----
    ph = result.physics
    if ph.get("available"):
        story.append(Paragraph("Physical interpretation", _H2))
        story.append(_table([
            ["Metric", "Value"],
            ["Observed depth", _fmt(ph.get("observed_depth"), "{:.5f}")],
            ["Depth (dilution-corrected)", _fmt(ph.get("dilution_corrected_depth"), "{:.5f}")],
            ["Rp/R★", _fmt(ph.get("ratio_companion_over_star"), "{:.4f}")],
            ["R_companion (R☉)", _fmt(ph.get("R_companion_Rsun"), "{:.4f}")],
            ["R_companion (R_Jup)", _fmt(ph.get("R_companion_Rjup"), "{:.3f}")],
            ["Category", ph.get("category", "—")],
            ["M★ estimated (M☉)", _fmt(ph.get("M_star_estimated_Msun"), "{:.3f}")],
            ["P_central implied (d)", _fmt(ph.get("P_central_implied_d"), "{:.3f}")],
        ], col_widths=[1.7 * inch, 4.6 * inch]))
        story.append(Spacer(1, 8))

    # ---- Verdict reasons ----
    story.append(Paragraph("Verdict reasoning", _H2))
    for r in result.verdict.get("reasons", []) or ["(no notes)"]:
        story.append(Paragraph("• " + r, _BODY))
    story.append(Spacer(1, 8))

    # ---- Embedded plots ----
    story.append(PageBreak())
    story.append(Paragraph("Diagnostic plots", _H2))
    overview = build_transit_overview(result, t, f)
    story.append(_image_from_fig(overview, width=6.5 * inch))
    zoom = build_transit_zoom(result, t, f)
    if zoom is not None:
        story.append(Spacer(1, 6))
        story.append(_image_from_fig(zoom, width=6.5 * inch))

    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "This report was generated locally on a Raspberry Pi by VetStar Pi. "
        "The full Vetstar studio adds Gaia / SIMBAD / NASA Exoplanet Archive "
        "cross-match, HCI habitability scoring, ExoMiner, DVT, multi-sector "
        "analysis, and FFI cutouts — see vetstar.onrender.com.",
        _SMALL,
    ))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return out_path


# ----------------------------------------------------------------------
# Microlensing report
# ----------------------------------------------------------------------
def build_microlens_pdf(result, out_path: str, target_label: Optional[str] = None) -> str:
    doc = SimpleDocTemplate(
        out_path, pagesize=LETTER,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.6 * inch, bottomMargin=0.7 * inch,
        title="VetStar Pi — Microlensing vetting report",
    )
    story = []

    label = target_label or "unnamed event"
    story.append(Paragraph(f"Microlensing vetting report — {label}", _H1))
    story.append(Paragraph(
        f"<b>Verdict:</b> {result.verdict.upper()}  (confidence {result.confidence:.2f})",
        _BODY,
    ))
    story.append(Spacer(1, 6))

    # Window
    w = result.window
    story.append(Paragraph("Fit window", _H2))
    story.append(_table([
        ["Field", "Value"],
        ["t_start", _fmt(w["t_start"], "{:.5f}")],
        ["t_end", _fmt(w["t_end"], "{:.5f}")],
        ["Baseline flux", _fmt(w["baseline_flux"], "{:g}")],
        ["N points in window", str(w["n_points"])],
    ], col_widths=[1.7 * inch, 4.6 * inch]))
    story.append(Spacer(1, 8))

    # Model comparison
    story.append(Paragraph("Model comparison (BIC)", _H2))
    story.append(_table([
        ["Model", "χ²", "χ²/dof", "BIC", "ΔBIC vs PSPL", "success"],
        ["PSPL",
         _fmt(result.pspl.chi2, "{:.2f}"),
         _fmt(result.pspl.chi2_red, "{:.2f}"),
         _fmt(result.pspl.bic, "{:.2f}"),
         "0.00", str(result.pspl.success)],
        ["Flare",
         _fmt(result.flare.chi2, "{:.2f}"),
         _fmt(result.flare.chi2_red, "{:.2f}"),
         _fmt(result.flare.bic, "{:.2f}"),
         _fmt(result.delta_bic["flare_minus_pspl"], "{:+.2f}"),
         str(result.flare.success)],
        ["Null",
         _fmt(result.null.chi2, "{:.2f}"),
         _fmt(result.null.chi2_red, "{:.2f}"),
         _fmt(result.null.bic, "{:.2f}"),
         _fmt(result.delta_bic["null_minus_pspl"], "{:+.2f}"),
         "True"],
    ]))
    story.append(Spacer(1, 8))

    # PSPL params
    story.append(Paragraph("PSPL best fit", _H2))
    pr = [["Param", "Value", "1σ error"]]
    for name in ("t0", "tE", "u0", "f_s", "f_b"):
        pr.append([
            name,
            _fmt(result.pspl.params.get(name), "{:g}"),
            _fmt(result.pspl.param_err.get(name), "{:g}"),
        ])
    story.append(_table(pr, col_widths=[1.2 * inch, 2.5 * inch, 2.5 * inch]))
    story.append(Paragraph(
        f"Symmetry score: {_fmt(result.symmetry_score, '{:.3f}')} "
        "(+1 = symmetric = PSPL-like; ≈0 = noise/flare)",
        _SMALL,
    ))
    story.append(Spacer(1, 8))

    # Observables
    if result.observables:
        story.append(Paragraph("Observables (from PSPL fit)", _H2))
        o = result.observables
        story.append(_table([
            ["Quantity", "Value"],
            ["t0 (BTJD / BJD)",
             f"{_fmt(o.get('t0_btjd'), '{:.5f}')} / {_fmt(o.get('t0_bjd'), '{:.5f}')}"],
            ["tE (d)", _fmt(o.get("einstein_timescale_d"), "{:.3f}")],
            ["u0", _fmt(o.get("impact_parameter_u0"), "{:.4f}")],
            ["Peak magnification A_max", _fmt(o.get("peak_magnification"), "{:.3f}")],
            ["A_obs (blended)", _fmt(o.get("peak_magnification_observed"), "{:.3f}")],
            ["Δm (mag)", _fmt(o.get("peak_brightening_mag"), "{:.3f}")],
            ["Einstein crossing (d)", _fmt(o.get("einstein_crossing_duration_d"), "{:.3f}")],
            ["FWHM (d)", _fmt(o.get("magnification_fwhm_d"), "{:.3f}")],
            ["Source flux fraction", _fmt(o.get("source_flux_fraction"), "{:.3f}")],
            ["Blend flux fraction", _fmt(o.get("blend_flux_fraction"), "{:.3f}")],
            ["μ_rel (fiducial θ_E, mas/yr)",
             _fmt(o.get("mu_rel_mas_per_yr_fiducial"), "{:.2f}")],
        ], col_widths=[2.4 * inch, 3.9 * inch]))
        story.append(Spacer(1, 8))

    if result.planet_predictions:
        story.append(Paragraph("Planet-detection predictions (fiducial bulge lens)", _H2))
        pp = result.planet_predictions
        story.append(Paragraph(pp.get("assumption", ""), _SMALL))
        story.append(_table([
            ["Quantity", "Value"],
            ["θ_E (mas)", _fmt(pp.get("theta_E_mas_fiducial"), "{:.3f}")],
            ["r_E (AU)", _fmt(pp.get("einstein_radius_au_fiducial"), "{:.3f}")],
            ["v_rel (km/s)", _fmt(pp.get("v_rel_km_s_fiducial"), "{:.1f}")],
            ["Closest approach (AU)", _fmt(pp.get("closest_approach_au_fiducial"), "{:.4f}")],
            ["q_min detectable", _fmt(pp.get("planet_q_min_detectable"), "{:.3e}")],
            ["Planet mass floor (M⊕)", _fmt(pp.get("planet_mass_floor_m_earth_fiducial"), "{:.3f}")],
            ["Planet mass floor (M_Jup)", _fmt(pp.get("planet_mass_floor_m_jupiter_fiducial"), "{:.5f}")],
        ], col_widths=[2.4 * inch, 3.9 * inch]))
        story.append(Spacer(1, 8))

    # Notes
    if result.notes:
        story.append(Paragraph("Notes", _H2))
        for n in result.notes:
            story.append(Paragraph("• " + n, _BODY))

    # Embedded fit plot
    story.append(PageBreak())
    story.append(Paragraph("Fit plot", _H2))
    fig = build_microlens_fit(result)
    story.append(_image_from_fig(fig, width=6.5 * inch))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return out_path


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _image_from_fig(fig, width) -> Image:
    """Convert a matplotlib Figure to a ReportLab Image at the given target width."""
    png = fig_to_png_bytes(fig, dpi=140)
    # Preserve aspect ratio: reportlab.Image needs height, so compute from figure.
    w_in, h_in = fig.get_size_inches()
    ratio = h_in / w_in if w_in > 0 else 0.75
    img = Image(io.BytesIO(png), width=width, height=width * ratio)
    return img
