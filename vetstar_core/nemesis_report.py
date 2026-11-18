"""Vetstar PDF report for a NEMESIS run.

Same look as the other Vetstar reports (running header band, striped tables,
DejaVu Sans). Contents: verdict, target, the NEMESIS search parameters (BLS
and TLS), the LEO-Vetter tests, the four NEMESIS II paper-style figures with
their "how to read" notes, the v1 FFI cutout, the optional HCI v2 summary,
and the citations a user must give.

Input is the worker's result JSON (``nemesis_worker/worker/summarize.py`` plus
``figures``, ``star`` and ``meta``).
"""
from __future__ import annotations

import base64
import io
from typing import Any, Dict, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from .microlensing_report import (
    ACCENT, CONTENT_W, FONT_BOLD, FONT_NORMAL, INK, _data_table, _fmt, _kv_table,
)
from reportlab.platypus.flowables import HRFlowable

VERDICTS = {
    "PC": ("Planet candidate", "#00864F",
           "The signal passed every LEO-Vetter test. It is worth a closer look, but it is not a "
           "confirmed planet: that needs follow-up observations."),
    "FP": ("Likely false positive", "#C9263E",
           "The dip looks real, but its shape or behaviour points to something other than a planet, "
           "most often an eclipsing binary or a nearby star."),
    "FA": ("Needs a closer look", "#976236",
           "At least one false-alarm test flagged the signal. It may be noise or stellar activity. "
           "LEO-Vetter is strict for shallow dips, so check the folded light curve yourself."),
}

# Same notes as the wizard (frontend/src/nemesis/NemesisWizard.tsx, HOW_TO_READ).
FIGURES = [
    ("light_curve", "The light curve",
     "The star's brightness over one TESS sector. Grey dots are single measurements, black dots "
     "one-hour averages, in ppt (parts per thousand). Red lines mark the predicted transits; a real "
     "planet makes a small dip at every one. Gaps are normal: TESS pauses to send data to Earth."),
    ("periodogram", "The period search",
     "How well a repeating dip fits at each trial period (SDE). The solid red line is the best period; "
     "a good signal is one tall, narrow peak. Smaller peaks at half and double the period (dashed) are "
     "normal echoes. Many peaks of similar height suggest noise."),
    ("fold", "All transits stacked",
     "Every transit lined up on the best period. Grey: single measurements; black: averages with "
     "uncertainty bars; red: the transit model. Depth gives the object's size, width the transit "
     "duration. The lower panel (data minus model) should be random scatter around zero."),
    ("odd_even", "Odd and even transits",
     "Transits 1, 3, 5... on the left and 2, 4, 6... on the right. For a planet both look the same. "
     "A clearly deeper side suggests two stars eclipsing each other at twice the period."),
]

CITATIONS = [
    "Feliz, D. L. et al. 2021, AJ, 161, 247 (NEMESIS). doi:10.3847/1538-3881/abedb3",
    "Feliz, D. L. et al. 2026, arXiv:2611.21758 (NEMESIS II; submitted to AJ)",
    "Kunimoto, M. et al. 2025, AJ, 170, 280 (LEO-Vetter). arXiv:2509.10619",
    "Hippke, M. & Heller, R. 2019, A&A, 623, A39 (Transit Least Squares)",
]


def _int(v) -> str:
    try:
        return str(int(round(float(v))))
    except (TypeError, ValueError):
        return "—"


def _styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle("nm_title", parent=s["Title"], fontName=FONT_BOLD, fontSize=18,
                         textColor=INK, spaceAfter=4))
    s.add(ParagraphStyle("nm_h2", parent=s["Heading2"], fontName=FONT_BOLD, fontSize=12.5,
                         textColor=INK, spaceBefore=10, spaceAfter=2))
    s.add(ParagraphStyle("nm_body", parent=s["BodyText"], fontName=FONT_NORMAL, fontSize=9.5,
                         leading=13, textColor=INK))
    s.add(ParagraphStyle("nm_small", parent=s["BodyText"], fontName=FONT_NORMAL, fontSize=8,
                         leading=10.5, textColor=colors.HexColor("#59585F")))
    s.add(ParagraphStyle("nm_cell", parent=s["BodyText"], fontName=FONT_NORMAL, fontSize=8,
                         leading=10, textColor=INK))
    return s


def _heading(text, st):
    return KeepTogether([Paragraph(text, st["nm_h2"]),
                         HRFlowable(width="100%", thickness=0.7, color=ACCENT, spaceAfter=6)])


def _img(b64: str, max_w: float, max_h: float) -> Optional[Image]:
    try:
        raw = base64.b64decode(b64)
        im = Image(io.BytesIO(raw))
        scale = min(max_w / im.imageWidth, max_h / im.imageHeight)
        im.drawWidth, im.drawHeight = im.imageWidth * scale, im.imageHeight * scale
        im.hAlign = "CENTER"
        return im
    except Exception:
        return None


def _header_footer(canvas, doc, title: str):
    from datetime import datetime, timezone
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#453C67"))
    canvas.rect(0, letter[1] - 0.55 * inch, letter[0], 0.55 * inch, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont(FONT_BOLD, 12)
    canvas.drawString(0.6 * inch, letter[1] - 0.35 * inch, title)
    canvas.setFont(FONT_NORMAL, 8)
    canvas.drawRightString(letter[0] - 0.6 * inch, letter[1] - 0.35 * inch,
                           "Vetstar NEMESIS vetting report")
    canvas.setFillColor(colors.HexColor("#706F76"))
    canvas.drawString(0.6 * inch, 0.4 * inch,
                      datetime.now(timezone.utc).strftime("Generated %Y-%m-%d %H:%M UTC"))
    canvas.drawRightString(letter[0] - 0.6 * inch, 0.4 * inch, f"Page {doc.page}")
    canvas.restoreState()


def build_nemesis_pdf(result: Dict[str, Any], hci_image_b64: Optional[str] = None,
                      ffi_image_b64: Optional[str] = None) -> bytes:
    st = _styles()
    meta = result.get("meta") or {}
    star = result.get("star") or {}
    tic, sector = meta.get("tic"), meta.get("sector")
    primary = result.get("primary")
    searches = result.get("search") or {}
    prim = searches.get(primary) if primary else None
    title = f"TIC {tic} · Sector {sector}"

    story = [Paragraph(f"NEMESIS search: TIC {tic}, Sector {sector}", st["nm_title"])]
    story.append(Paragraph(
        f"NEMESIS {meta.get('nemesis_version', '?')} · {meta.get('cadence', '')} cadence · "
        f"{'light mode (coarser TLS grid)' if meta.get('lite') else 'full NEMESIS II settings'} · "
        f"run time {_int(meta.get('runtime_s'))} s", st["nm_small"]))
    story.append(Spacer(1, 8))

    # Verdict
    leo = (prim or {}).get("leo") or {}
    disp = leo.get("disposition") if leo.get("available") else None
    if disp in VERDICTS:
        name, hexcol, text = VERDICTS[disp]
    else:
        name, hexcol, text = ("LEO-Vetter did not run", "#59585F", leo.get("reason") or
                              "No transit search result for this star and sector.")
    box = Table([[Paragraph(f'<font color="{hexcol}"><b>{name}</b></font>', st["nm_h2"])],
                 [Paragraph(text, st["nm_body"])]], colWidths=[CONTENT_W])
    box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1.4, colors.HexColor(hexcol)),
                             ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [box, Spacer(1, 8)]

    # Target
    story.append(_heading("Target", st))
    story.append(_kv_table([
        ["TIC", str(tic)], ["Sector", str(sector)],
        ["RA, Dec (deg)", f"{_fmt(star.get('ra'), 7)}, {_fmt(star.get('dec'), 7)}"],
        ["TESS magnitude", _fmt(star.get("tmag"), 4)],
        ["Teff (K)", _int(star.get("teff"))],
        ["Radius (R☉) / mass (M☉)", f"{_fmt(star.get('radius'), 3)} / {_fmt(star.get('mass'), 3)}"],
    ]))

    # Search parameters
    if searches:
        story.append(_heading("Transit search", st))
        rows = [["", *searches.keys()]]
        for label, key, nd in (("Period (d)", "period_d", 6), ("Mid-transit T0 (BTJD)", "t0_btjd", 7),
                               ("Duration (h)", "duration_hr", 4), ("Depth (ppt)", "depth_ppt", 4),
                               ("Planet radius (R⊕)", "radius_earth", 4), ("SDE", "sde", 4),
                               ("Transits", "n_transits", 3)):
            fmt = _int if key == "n_transits" else (lambda v, nd=nd: _fmt(v, nd))
            rows.append([label, *[fmt(s["params"].get(key)) for s in searches.values()]])
        n = len(searches)
        story.append(_data_table(rows, [2.3 * inch] + [(CONTENT_W - 2.3 * inch) / n] * n))
        if primary:
            story.append(Paragraph(
                f"{primary} is the headline result. BJD = BTJD + 2457000 (ExoFOP expects BJD).",
                st["nm_small"]))

    # LEO-Vetter
    tests = leo.get("tests") or []
    if tests:
        story.append(_heading("LEO-Vetter tests", st))
        rows = [["Test", "Group", "Result", "A flag would mean"]]
        for t in tests:
            rows.append([Paragraph(t["title"], st["nm_cell"]), t["group"],
                         "pass" if t["passed"] else "FLAG", Paragraph(t["meaning"], st["nm_cell"])])
        tbl = _data_table(rows, [1.6 * inch, 0.55 * inch, 0.6 * inch, CONTENT_W - 2.75 * inch])
        for i, t in enumerate(tests, start=1):
            if not t["passed"]:
                tbl.setStyle(TableStyle([("TEXTCOLOR", (2, i), (2, i), colors.HexColor("#C9263E")),
                                         ("FONTNAME", (2, i), (2, i), FONT_BOLD)]))
        story.append(tbl)
        story.append(Paragraph("FA = false-alarm tests (is the dip real?); FP = false-positive tests "
                               "(is it a planet?).", st["nm_small"]))

    # Figures
    figs = result.get("figures") or {}
    if any(figs.get(k) for k, _, _ in FIGURES):
        story.append(PageBreak())
        story.append(_heading("The plots, step by step", st))
        for i, (key, name, note) in enumerate(FIGURES, start=1):
            if not figs.get(key):
                continue
            im = _img(figs[key], CONTENT_W, 3.6 * inch)
            if im:
                story.append(KeepTogether([Paragraph(f"<b>{i}. {name}</b>", st["nm_body"]), im,
                                           Paragraph(note, st["nm_small"]), Spacer(1, 10)]))

    # FFI cutout + HCI
    extras = [(ffi_image_b64, "Target field: TESS FFI cutout",
               "TESScut full-frame-image cutout around the target (red +). A bright neighbour inside the "
               "aperture could be the real source of the dip."),
              (hci_image_b64, "Habitability Chance Index (HCI v2)",
               "A rough score of how Earth-like this candidate could be. For teaching and fun, "
               "not a discovery claim.")]
    for b64, name, note in extras:
        if b64:
            im = _img(b64, CONTENT_W, 4.2 * inch)
            if im:
                story.append(KeepTogether([_heading(name, st), im, Paragraph(note, st["nm_small"])]))

    story.append(_heading("Please cite", st))
    for c in CITATIONS:
        story.append(Paragraph(c, st["nm_small"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph("A NEMESIS detection is a planet candidate, not a confirmed planet.",
                           st["nm_small"]))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=inch, rightMargin=inch,
                            topMargin=0.85 * inch, bottomMargin=0.7 * inch,
                            title=f"Vetstar NEMESIS report TIC {tic} S{sector}", author="Vetstar")
    doc.build(story, onFirstPage=lambda c, d: _header_footer(c, d, title),
              onLaterPages=lambda c, d: _header_footer(c, d, title))
    return buf.getvalue()
