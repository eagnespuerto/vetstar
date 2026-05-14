"""
PDF report generator — turns a VettingResult into a multi-page PDF.
"""
from __future__ import annotations

import base64
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .pipeline import VettingResult


def _fmt(v, suffix="", nd=4):
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        if abs(v) < 1e-4 or abs(v) >= 1e6:
            return f"{v:.3e}{suffix}"
        return f"{v:.{nd}f}{suffix}"
    return f"{v}{suffix}"


def _b64_to_image(b64: str, width=6.5 * inch, aspect=0.35):
    """Return a centered Image flowable."""
    buf = io.BytesIO(base64.b64decode(b64))
    img = Image(buf, width=width, height=width * aspect)
    img.hAlign = "CENTER"
    return img


def _centered(flowable):
    """Force-center any flowable (esp. tables) on the page."""
    if hasattr(flowable, "hAlign"):
        flowable.hAlign = "CENTER"
    return flowable


def build_pdf(result: VettingResult) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title=f"Vetting report TIC {result.star.tic_id}",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1c", parent=styles["Heading1"], alignment=1)   # 1 = TA_CENTER
    h2 = ParagraphStyle("h2c", parent=styles["Heading2"], alignment=1)
    body = styles["BodyText"]
    body_center = ParagraphStyle("bodyc", parent=body, alignment=1)
    verdict_style = ParagraphStyle(
        "verdict", parent=h1, fontSize=14, textColor=colors.darkblue, alignment=1
    )

    story = []
    star = result.star
    v = result.verdict

    # Header
    story.append(Paragraph(f"TESS / Kepler Vetting Report", h1))
    if star.tic_id:
        story.append(Paragraph(f"TIC {star.tic_id}", h2))
    story.append(
        Paragraph(
            f"Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            body_center,
        )
    )
    story.append(Spacer(1, 0.15 * inch))

    # Verdict box
    story.append(Paragraph(f"<b>Verdict:</b> {v.get('headline', '—')}", verdict_style))
    story.append(
        Paragraph(
            f"Category: <b>{v.get('category', '—')}</b> &nbsp;&nbsp; "
            f"Confidence: <b>{v.get('confidence', 0)*100:.0f}%</b>",
            body_center,
        )
    )
    if v.get("reasons"):
        story.append(Paragraph("<b>Reasoning:</b>", body))
        for r in v["reasons"]:
            story.append(Paragraph(f"• {r}", body))
    if v.get("flags"):
        story.append(Paragraph(f"<b>Flags raised:</b> {', '.join(v['flags'])}", body))
    story.append(Spacer(1, 0.15 * inch))

    # Star table
    story.append(Paragraph("Stellar parameters", h2))
    star_rows = [
        ["TIC ID", _fmt(star.tic_id, nd=0)],
        ["Sector / Camera / CCD", f"{star.sector} / {star.camera} / {star.ccd}"],
        ["RA, Dec (deg)", f"{_fmt(star.ra, nd=4)}, {_fmt(star.dec, nd=4)}"],
        ["Tmag", _fmt(star.tmag, nd=2)],
        ["Teff (K)", _fmt(star.teff, nd=0)],
        ["Radius (R_sun)", _fmt(star.radius, nd=2)],
        ["log g (cgs)", _fmt(star.logg, nd=2)],
        ["CROWDSAP", _fmt(star.crowdsap, nd=3)],
        ["Source", star.source],
    ]
    t = Table(star_rows, colWidths=[2.3 * inch, 4.0 * inch])
    t.setStyle(_table_style())
    story.append(_centered(t))
    story.append(Spacer(1, 0.15 * inch))

    # Summary table
    story.append(Paragraph("Light curve summary", h2))
    s = result.summary
    summary_rows = [
        ["N points (cleaned)", _fmt(s.get("n_points"), nd=0)],
        ["Time span (d)", _fmt(s.get("time_span_d"), nd=2)],
        ["Median cadence (min)", _fmt(s.get("median_cadence_min"), nd=2)],
        ["Photometric scatter (MAD)", _fmt(s.get("scatter_mad"), nd=5)],
        ["Discrete dip events", _fmt(s.get("n_events_detected"), nd=0)],
    ]
    t = Table(summary_rows, colWidths=[2.3 * inch, 4.0 * inch])
    t.setStyle(_table_style())
    story.append(_centered(t))
    story.append(Spacer(1, 0.15 * inch))

    # Light curve image
    if "lightcurve" in result.plots:
        story.append(Paragraph("Detrended light curve", h2))
        story.append(_b64_to_image(result.plots["lightcurve"]))
        story.append(Spacer(1, 0.1 * inch))

    story.append(PageBreak())

    # BLS + LS
    story.append(Paragraph("Period searches", h2))
    bls = result.bls
    ls = result.lomb_scargle
    rows = [
        ["BLS period (d)", _fmt(bls.get("period"), nd=5)],
        ["BLS t0 (BTJD)", _fmt(bls.get("t0"), nd=4)],
        ["BLS duration (d)", _fmt(bls.get("duration"), nd=3)],
        ["BLS depth", _fmt(bls.get("depth"), nd=5)],
        ["BLS SDE", _fmt(bls.get("sde"), nd=2)],
        ["BLS transits in window", _fmt(bls.get("n_transits_in_window"), nd=0)],
        ["Lomb-Scargle top period (d)", _fmt(ls.get("top_period"), nd=4)],
        ["Lomb-Scargle top power", _fmt(ls.get("top_power"), nd=3)],
        ["LS false-alarm probability", _fmt(ls.get("false_alarm_prob"))],
    ]
    t = Table(rows, colWidths=[2.6 * inch, 3.7 * inch])
    t.setStyle(_table_style())
    story.append(_centered(t))
    if "bls" in result.plots:
        story.append(Spacer(1, 0.1 * inch))
        story.append(_b64_to_image(result.plots["bls"]))
    if "lomb_scargle" in result.plots:
        story.append(Spacer(1, 0.1 * inch))
        story.append(_b64_to_image(result.plots["lomb_scargle"]))

    story.append(PageBreak())

    # Events
    story.append(Paragraph("Discrete dip events", h2))
    if result.events:
        ev_rows = [["#", "t_start", "t_end", "Dur (h)", "Depth (%)"]]
        for i, e in enumerate(result.events[:10], 1):
            ev_rows.append(
                [
                    str(i),
                    _fmt(e["t_start"], nd=3),
                    _fmt(e["t_end"], nd=3),
                    _fmt(e["duration_d"] * 24, nd=2),
                    _fmt(e["depth"] * 100, nd=2),
                ]
            )
        t = Table(ev_rows, colWidths=[0.4 * inch, 1.5 * inch, 1.5 * inch, 1.0 * inch, 1.2 * inch])
        t.setStyle(_table_style(header=True))
        story.append(_centered(t))
    else:
        story.append(Paragraph("No discrete dip events detected.", body))

    story.append(Spacer(1, 0.15 * inch))
    if "event_zoom" in result.plots:
        story.append(_b64_to_image(result.plots["event_zoom"]))

    story.append(PageBreak())

    # Centroid + shape
    story.append(Paragraph("Vetting tests", h2))

    # Centroid
    story.append(Paragraph("<b>Centroid offset (background-blend test)</b>", body))
    c = result.centroid
    if c.get("available"):
        rows = [
            ["Column shift (px)", _fmt(c["shift_col_px"], nd=5)],
            ["Column shift (σ)", _fmt(c["shift_col_sigma"], nd=2)],
            ["Row shift (px)", _fmt(c["shift_row_px"], nd=5)],
            ["Row shift (σ)", _fmt(c["shift_row_sigma"], nd=2)],
            ["On target?", _fmt(c["on_target"])],
        ]
        t = Table(rows, colWidths=[2.3 * inch, 4.0 * inch])
        t.setStyle(_table_style())
        story.append(_centered(t))
    else:
        story.append(Paragraph("Centroid data not available.", body))
    if "centroid" in result.plots:
        story.append(_b64_to_image(result.plots["centroid"]))
    story.append(Spacer(1, 0.1 * inch))

    # Odd/even
    story.append(Paragraph("<b>Odd vs even transit depths (EB test)</b>", body))
    oe = result.odd_even
    if oe.get("available"):
        rows = [
            ["Depth — odd transits", _fmt(oe["depth_odd"], nd=5)],
            ["Depth — even transits", _fmt(oe["depth_even"], nd=5)],
            ["Difference σ", _fmt(oe["sigma"], nd=2)],
            ["EB flag?", _fmt(oe["flag_eb"])],
        ]
        t = Table(rows, colWidths=[2.3 * inch, 4.0 * inch])
        t.setStyle(_table_style())
        story.append(_centered(t))
    else:
        story.append(Paragraph(f"Not available: {oe.get('reason', '—')}", body))
    story.append(Spacer(1, 0.1 * inch))

    # Secondary eclipse
    story.append(Paragraph("<b>Secondary eclipse search (EB test)</b>", body))
    se = result.secondary
    if se.get("available"):
        rows = [
            ["Depth at phase 0.5", _fmt(se["depth"], nd=5)],
            ["Significance σ", _fmt(se["sigma"], nd=2)],
            ["Detected?", _fmt(se["detected"])],
        ]
        t = Table(rows, colWidths=[2.3 * inch, 4.0 * inch])
        t.setStyle(_table_style())
        story.append(_centered(t))
    else:
        story.append(Paragraph(f"Not available: {se.get('reason', '—')}", body))
    story.append(Spacer(1, 0.1 * inch))

    # Shape
    story.append(Paragraph("<b>Transit shape (U vs V)</b>", body))
    sh = result.shape
    if sh.get("available"):
        rows = [
            ["T14 (h)", _fmt(sh["t14_hours"], nd=2)],
            ["T23 (h, flat bottom)", _fmt(sh["t23_hours"], nd=2)],
            ["T23 / T14", _fmt(sh["t23_over_t14"], nd=2)],
            ["Shape class", sh["shape_class"]],
        ]
        t = Table(rows, colWidths=[2.3 * inch, 4.0 * inch])
        t.setStyle(_table_style())
        story.append(_centered(t))
    else:
        story.append(Paragraph("Shape not available.", body))
    story.append(Spacer(1, 0.15 * inch))

    # Physics
    story.append(Paragraph("<b>Physical interpretation</b>", body))
    p = result.physics
    if p.get("available"):
        rows = [
            ["Observed depth", _fmt(p["observed_depth"], nd=5)],
            ["Dilution-corrected depth", _fmt(p["dilution_corrected_depth"], nd=5)],
            ["Companion radius (R_sun)", _fmt(p["R_companion_Rsun"], nd=3)],
            ["Companion radius (R_Jup)", _fmt(p["R_companion_Rjup"], nd=2)],
            ["Category", p["category"]],
            ["Planet candidate?", _fmt(p["is_planet_candidate"])],
            ["Estimated star mass (M_sun)", _fmt(p.get("M_star_estimated_Msun"), nd=2)],
            ["Central-transit P implied (d)", _fmt(p.get("P_central_implied_d"), nd=2)],
        ]
        t = Table(rows, colWidths=[2.6 * inch, 3.7 * inch])
        t.setStyle(_table_style())
        story.append(_centered(t))
    else:
        story.append(Paragraph("Physics not available (missing stellar parameters).", body))

    doc.build(story)
    pdf = buf.getvalue()
    buf.close()
    return pdf


def _table_style(header=False):
    cmds = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f0f0")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    if header:
        cmds += [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#cce0f0")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ]
    return TableStyle(cmds)
