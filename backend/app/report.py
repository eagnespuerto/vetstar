"""
PDF report generator — turns a VettingResult into a clean, consistent
multi-page PDF.

Layout principles (so pages don't look patchy):
  * A single page template draws the same header band + footer on every
    page, so page 1 and page 5 share an identity.
  * One table style everywhere (zebra rows, one grid colour, one header
    style). Key/value tables and data tables differ only in column count.
  * Section headings are left-aligned with a thin accent rule, and each
    heading is kept together with the block that follows it, so a heading
    never strands itself at the bottom of a page.
  * Images are scaled to their true aspect ratio (never stretched) and
    capped in height so one figure can't overflow a page.
"""
from __future__ import annotations

import base64
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import HRFlowable

from .pipeline import VettingResult

# ----------------------------------------------------------------------
# Palette — one place, used everywhere
# ----------------------------------------------------------------------
INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#6b7785")
ACCENT = colors.HexColor("#2563eb")
BAND = colors.HexColor("#1f3a5f")
GRID = colors.HexColor("#d3dae3")
HEADER_BG = colors.HexColor("#e8eef6")
STRIPE = colors.HexColor("#f6f8fb")

PAGE_W, PAGE_H = letter
LMARGIN = RMARGIN = 0.7 * inch
TMARGIN = 0.95 * inch          # room for the header band
BMARGIN = 0.75 * inch
CONTENT_W = PAGE_W - LMARGIN - RMARGIN


# ----------------------------------------------------------------------
# Formatting helpers
# ----------------------------------------------------------------------
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


def _img_dims(b64: str):
    """Return (width_px, height_px) of a base64 PNG, or None on failure."""
    try:
        from PIL import Image as PILImage
        with PILImage.open(io.BytesIO(base64.b64decode(b64))) as im:
            return im.size  # (w, h)
    except Exception:
        return None


def _b64_image(b64: str, max_width=CONTENT_W, max_height=4.3 * inch, fallback_aspect=0.4):
    """Image flowable scaled to its true aspect ratio, capped by width and
    height so nothing stretches or overflows. Centred."""
    buf = io.BytesIO(base64.b64decode(b64))
    dims = _img_dims(b64)
    if dims and dims[0] > 0:
        w_px, h_px = dims
        width = max_width
        height = width * (h_px / w_px)
        if height > max_height:
            height = max_height
            width = height * (w_px / h_px)
    else:
        width = max_width
        height = width * fallback_aspect
    img = Image(buf, width=width, height=height)
    img.hAlign = "CENTER"
    return img


# ----------------------------------------------------------------------
# Tables — one unified style
# ----------------------------------------------------------------------
def _table_style(has_header=False, label_col=True):
    cmds = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if has_header:
        cmds += [
            ("BACKGROUND", (0, 0), (-1, 0), BAND),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, STRIPE]),
        ]
    else:
        cmds += [("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, STRIPE])]
        if label_col:
            cmds += [("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold")]
    return TableStyle(cmds)


def _kv_table(rows, label_w=2.4 * inch):
    """Two-column key/value table spanning the content width."""
    t = Table(rows, colWidths=[label_w, CONTENT_W - label_w])
    t.setStyle(_table_style(has_header=False))
    t.hAlign = "CENTER"
    return t


def _data_table(rows, colWidths):
    t = Table(rows, colWidths=colWidths)
    t.setStyle(_table_style(has_header=True))
    t.hAlign = "CENTER"
    return t


# ----------------------------------------------------------------------
# Section heading kept with its first block
# ----------------------------------------------------------------------
def _section(title, *blocks, styles=None):
    """Return a KeepTogether of [heading, accent rule, first block...] so the
    heading never orphans. Extra blocks after the first are returned loose."""
    head = [
        Paragraph(title, styles["h2"]),
        HRFlowable(width="100%", thickness=1.1, color=ACCENT,
                   spaceBefore=1, spaceAfter=6, lineCap="round"),
    ]
    first = list(blocks[:1])
    rest = list(blocks[1:])
    return [KeepTogether(head + first)] + rest


# ----------------------------------------------------------------------
# Page decoration (same header band + footer on every page)
# ----------------------------------------------------------------------
def _make_decorator(title_line, sub_line):
    def _decorate(canvas, doc):
        canvas.saveState()
        # Header band
        band_h = 0.5 * inch
        y = PAGE_H - band_h
        canvas.setFillColor(BAND)
        canvas.rect(0, y, PAGE_W, band_h, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawString(LMARGIN, y + 0.18 * inch, title_line)
        if sub_line:
            canvas.setFont("Helvetica", 9)
            canvas.drawRightString(PAGE_W - RMARGIN, y + 0.19 * inch, sub_line)
        # Footer
        canvas.setStrokeColor(GRID)
        canvas.setLineWidth(0.5)
        canvas.line(LMARGIN, BMARGIN - 0.18 * inch, PAGE_W - RMARGIN, BMARGIN - 0.18 * inch)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(LMARGIN, BMARGIN - 0.32 * inch, "Vetstar · TESS Vetting Studio")
        canvas.drawRightString(
            PAGE_W - RMARGIN, BMARGIN - 0.32 * inch, f"Page {doc.page}"
        )
        canvas.restoreState()
    return _decorate


# ----------------------------------------------------------------------
# Build
# ----------------------------------------------------------------------
def build_pdf(
    result: VettingResult,
    hci_bundle: dict = None,
    exominer: dict = None,
    ffi_cutout: dict = None,
) -> bytes:
    buf = io.BytesIO()

    star = result.star
    v = result.verdict
    title_line = "TESS Vetting Report"
    sub_line = f"TIC {star.tic_id}" if star.tic_id else ""

    doc = BaseDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=LMARGIN, rightMargin=RMARGIN,
        topMargin=TMARGIN, bottomMargin=BMARGIN,
        title=f"Vetting report TIC {star.tic_id}",
    )
    frame = Frame(
        LMARGIN, BMARGIN, CONTENT_W, PAGE_H - TMARGIN - BMARGIN,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    decorate = _make_decorator(title_line, sub_line)
    doc.addPageTemplates([
        PageTemplate(id="main", frames=[frame], onPage=decorate)
    ])

    base = getSampleStyleSheet()
    styles = {
        "h1": ParagraphStyle("h1", parent=base["Heading1"], alignment=TA_CENTER,
                             textColor=INK, fontSize=18, spaceAfter=2),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], alignment=TA_LEFT,
                             textColor=BAND, fontSize=12.5, spaceBefore=10, spaceAfter=0),
        "h3": ParagraphStyle("h3", parent=base["Heading3"], alignment=TA_LEFT,
                             textColor=INK, fontSize=10.5, spaceBefore=6, spaceAfter=2),
        "body": ParagraphStyle("body", parent=base["BodyText"], textColor=INK,
                               fontSize=9.5, leading=13, spaceAfter=3),
        "center": ParagraphStyle("center", parent=base["BodyText"], alignment=TA_CENTER,
                                 textColor=MUTED, fontSize=9, spaceAfter=2),
        "verdict": ParagraphStyle("verdict", parent=base["Heading1"], alignment=TA_CENTER,
                                  fontSize=14, textColor=ACCENT, spaceBefore=4, spaceAfter=2),
        "caption": ParagraphStyle("caption", parent=base["BodyText"], alignment=TA_CENTER,
                                  textColor=MUTED, fontSize=8, spaceBefore=2, spaceAfter=8),
    }
    body = styles["body"]
    story = []

    # ---------------- Cover block ----------------
    story.append(Paragraph(title_line, styles["h1"]))
    if star.tic_id:
        story.append(Paragraph(f"TIC {star.tic_id}", styles["center"]))
    story.append(Paragraph(
        f"Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}", styles["center"]))
    story.append(Spacer(1, 0.1 * inch))

    # Verdict
    story.append(Paragraph(f"Verdict: {v.get('headline', '—')}", styles["verdict"]))
    story.append(Paragraph(
        f"Category: <b>{v.get('category', '—')}</b> &nbsp;&nbsp; "
        f"Confidence: <b>{v.get('confidence', 0)*100:.0f}%</b>", styles["center"]))
    if v.get("reasons"):
        story.append(Spacer(1, 0.04 * inch))
        story.append(Paragraph("<b>Reasoning</b>", body))
        for r in v["reasons"]:
            story.append(Paragraph(f"• {r}", body))
    if v.get("flags"):
        story.append(Paragraph(f"<b>Flags raised:</b> {', '.join(v['flags'])}", body))
    story.append(Spacer(1, 0.12 * inch))

    # ---------------- Stellar parameters ----------------
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
    story += _section("Stellar parameters", _kv_table(star_rows), styles=styles)
    story.append(Spacer(1, 0.12 * inch))

    # ---------------- Light curve summary ----------------
    s = result.summary
    summary_rows = [
        ["N points (cleaned)", _fmt(s.get("n_points"), nd=0)],
        ["Time span (d)", _fmt(s.get("time_span_d"), nd=2)],
        ["Median cadence (min)", _fmt(s.get("median_cadence_min"), nd=2)],
        ["Photometric scatter (MAD)", _fmt(s.get("scatter_mad"), nd=5)],
        ["Discrete dip events", _fmt(s.get("n_events_detected"), nd=0)],
    ]
    story += _section("Light curve summary", _kv_table(summary_rows), styles=styles)
    story.append(Spacer(1, 0.12 * inch))

    # ---------------- FFI cutout (target on the sky) ----------------
    if ffi_cutout and ffi_cutout.get("image"):
        cap = "TESScut FFI cutout centred on the target (red +). "
        if ffi_cutout.get("size_px"):
            cap += f"{ffi_cutout['size_px']}×{ffi_cutout['size_px']} px (~21\"/px). "
        cap += "Yellow outline shows the photometric aperture where available."
        story += _section(
            "Target field — FFI cutout",
            _b64_image(ffi_cutout["image"], max_width=3.3 * inch, max_height=3.3 * inch),
            Paragraph(cap, styles["caption"]),
            styles=styles,
        )
        story.append(Spacer(1, 0.1 * inch))

    # ---------------- Detrended light curve ----------------
    if "lightcurve" in result.plots:
        story += _section(
            "Detrended light curve",
            _b64_image(result.plots["lightcurve"]),
            styles=styles,
        )

    story.append(PageBreak())

    # ---------------- Period searches ----------------
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
    story += _section("Period searches", _kv_table(rows, label_w=2.7 * inch), styles=styles)
    if "bls" in result.plots:
        story.append(Spacer(1, 0.1 * inch))
        story.append(_b64_image(result.plots["bls"]))
    if "lomb_scargle" in result.plots:
        story.append(Spacer(1, 0.1 * inch))
        story.append(_b64_image(result.plots["lomb_scargle"]))
    story.append(Spacer(1, 0.14 * inch))

    # ---------------- Discrete dip events ----------------
    if result.events:
        ev_rows = [["#", "t_start", "t_end", "Dur (h)", "Depth (%)"]]
        for i, e in enumerate(result.events[:10], 1):
            ev_rows.append([
                str(i), _fmt(e["t_start"], nd=3), _fmt(e["t_end"], nd=3),
                _fmt(e["duration_d"] * 24, nd=2), _fmt(e["depth"] * 100, nd=2),
            ])
        ev_block = _data_table(
            ev_rows, [0.4 * inch, 1.6 * inch, 1.6 * inch, 1.0 * inch, 1.2 * inch])
    else:
        ev_block = Paragraph("No discrete dip events detected.", body)
    story += _section("Discrete dip events", ev_block, styles=styles)
    if "event_zoom" in result.plots:
        story.append(Spacer(1, 0.1 * inch))
        story.append(_b64_image(result.plots["event_zoom"]))

    story.append(PageBreak())

    # ---------------- Vetting tests ----------------
    story.append(Paragraph("Vetting tests", styles["h2"]))
    story.append(HRFlowable(width="100%", thickness=1.1, color=ACCENT,
                            spaceBefore=1, spaceAfter=8, lineCap="round"))

    # Centroid
    c = result.centroid
    if c.get("available"):
        rows = [
            ["Column shift (px)", _fmt(c["shift_col_px"], nd=5)],
            ["Column shift (σ)", _fmt(c["shift_col_sigma"], nd=2)],
            ["Row shift (px)", _fmt(c["shift_row_px"], nd=5)],
            ["Row shift (σ)", _fmt(c["shift_row_sigma"], nd=2)],
            ["On target?", _fmt(c["on_target"])],
        ]
        c_block = _kv_table(rows)
    else:
        c_block = Paragraph("Centroid data not available.", body)
    story.append(KeepTogether([
        Paragraph("Centroid offset (background-blend test)", styles["h3"]), c_block]))
    if "centroid" in result.plots:
        story.append(Spacer(1, 0.06 * inch))
        story.append(_b64_image(result.plots["centroid"]))
    story.append(Spacer(1, 0.1 * inch))

    # Odd/even
    oe = result.odd_even
    if oe.get("available"):
        rows = [
            ["Depth — odd transits", _fmt(oe["depth_odd"], nd=5)],
            ["Depth — even transits", _fmt(oe["depth_even"], nd=5)],
            ["Difference σ", _fmt(oe["sigma"], nd=2)],
            ["EB flag?", _fmt(oe["flag_eb"])],
        ]
        oe_block = _kv_table(rows)
    else:
        oe_block = Paragraph(f"Not available: {oe.get('reason', '—')}", body)
    story.append(KeepTogether([
        Paragraph("Odd vs even transit depths (EB test)", styles["h3"]), oe_block]))
    story.append(Spacer(1, 0.1 * inch))

    # Secondary eclipse
    se = result.secondary
    if se.get("available"):
        rows = [
            ["Depth at phase 0.5", _fmt(se["depth"], nd=5)],
            ["Significance σ", _fmt(se["sigma"], nd=2)],
            ["Detected?", _fmt(se["detected"])],
        ]
        se_block = _kv_table(rows)
    else:
        se_block = Paragraph(f"Not available: {se.get('reason', '—')}", body)
    story.append(KeepTogether([
        Paragraph("Secondary eclipse search (EB test)", styles["h3"]), se_block]))
    story.append(Spacer(1, 0.1 * inch))

    # Shape
    sh = result.shape
    if sh.get("available"):
        rows = [
            ["T14 (h)", _fmt(sh["t14_hours"], nd=2)],
            ["T23 (h, flat bottom)", _fmt(sh["t23_hours"], nd=2)],
            ["T23 / T14", _fmt(sh["t23_over_t14"], nd=2)],
            ["Shape class", sh["shape_class"]],
        ]
        sh_block = _kv_table(rows)
    else:
        sh_block = Paragraph("Shape not available.", body)
    story.append(KeepTogether([
        Paragraph("Transit shape (U vs V)", styles["h3"]), sh_block]))
    story.append(Spacer(1, 0.1 * inch))

    # Physics
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
        ph_block = _kv_table(rows, label_w=2.7 * inch)
    else:
        ph_block = Paragraph("Physics not available (missing stellar parameters).", body)
    story.append(KeepTogether([
        Paragraph("Physical interpretation", styles["h3"]), ph_block]))

    # ---------------- HCI ----------------
    if hci_bundle and hci_bundle.get("hci"):
        story.append(PageBreak())
        _append_hci_section(story, hci_bundle, styles)

    # ---------------- ExoMiner ----------------
    if exominer:
        story.append(PageBreak())
        _append_exominer_section(story, exominer, styles)

    doc.build(story)
    pdf = buf.getvalue()
    buf.close()
    return pdf


# ----------------------------------------------------------------------
# HCI + ExoMiner sections
# ----------------------------------------------------------------------
def _append_hci_section(story, bundle: dict, styles):
    body = styles["body"]
    hci = bundle.get("hci") or {}
    story.append(Paragraph("Habitability Chance Index (HCI)", styles["h2"]))
    story.append(HRFlowable(width="100%", thickness=1.1, color=ACCENT,
                            spaceBefore=1, spaceAfter=6, lineCap="round"))
    story.append(Paragraph(
        f"<b>Score:</b> {_fmt(hci.get('hci'), nd=1)} / 100 &nbsp;&nbsp; "
        f"<b>Tier:</b> {hci.get('tier', '—')}", body))

    if bundle.get("hci_image"):
        story.append(Spacer(1, 0.08 * inch))
        story.append(_b64_image(bundle["hci_image"], max_height=6.5 * inch))
        story.append(Spacer(1, 0.1 * inch))

    subs = hci.get("sub_scores") or []
    if subs:
        rows = [["Component", "Weight", "Sub-score", "Assessment"]]
        for s in subs:
            rows.append([
                s.get("name", "—"),
                f"{(s.get('weight', 0) or 0) * 100:.0f}%",
                f"{(s.get('score', 0) or 0) * 100:.0f}/100",
                s.get("label", "—"),
            ])
        story.append(_data_table(
            rows, [1.9 * inch, 0.9 * inch, 1.0 * inch, CONTENT_W - 3.8 * inch]))

    caveats = hci.get("caveats") or []
    if caveats:
        story.append(Spacer(1, 0.1 * inch))
        story.append(Paragraph("<b>Caveats</b>", body))
        for cv in caveats:
            story.append(Paragraph(f"• {cv}", body))
    if hci.get("paper_ref"):
        story.append(Paragraph(f"<i>Reference: {hci['paper_ref']}</i>", styles["caption"]))


def _append_exominer_section(story, exominer: dict, styles):
    body = styles["body"]
    story.append(Paragraph("ExoMiner feature extraction", styles["h2"]))
    story.append(HRFlowable(width="100%", thickness=1.1, color=ACCENT,
                            spaceBefore=1, spaceAfter=6, lineCap="round"))
    scalars = exominer.get("scalars") or {}
    if scalars:
        rows = [[k, _fmt(v, nd=4)] for k, v in scalars.items()]
        story.append(_kv_table(rows, label_w=2.7 * inch))
        story.append(Spacer(1, 0.12 * inch))

    plots = exominer.get("plots") or {}
    order = [
        ("global_view", "Global view"),
        ("local_view", "Local view"),
        ("secondary_view", "Secondary view"),
        ("odd_even_view", "Odd / even views"),
        ("centroid_global_view", "Centroid (global)"),
        ("centroid_local_view", "Centroid (local)"),
        ("diagnostic_sigmas", "Diagnostic significances"),
    ]
    for key, label in order:
        if plots.get(key):
            story.append(KeepTogether([
                Paragraph(label, styles["h3"]),
                _b64_image(plots[key], max_height=3.0 * inch),
            ]))
            story.append(Spacer(1, 0.08 * inch))
