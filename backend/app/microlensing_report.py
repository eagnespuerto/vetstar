"""PDF vetting-report builder for the microlensing classifier.

Mirrors the transit `report.py` style (running header, unified table style,
Helvetica typography) but is scoped to what Module A produces: verdict,
PSPL fit params + errors, observable parameters, model comparison via BIC,
diagnostic notes, and optionally the embedded plot.
"""
from __future__ import annotations

import base64
import io
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import HRFlowable


INK = colors.HexColor("#0f172a")
BAND = colors.HexColor("#1e293b")
STRIPE = colors.HexColor("#f8fafc")
GRID = colors.HexColor("#cbd5e1")
ACCENT = colors.HexColor("#2563eb")
CONTENT_W = 6.5 * inch


def _fmt(v: Any, nd: int = 4, suffix: str = "") -> str:
    """Format a number with a nice default precision; return "—" for None/nan."""
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f:  # NaN
        return "—"
    if not (f == f and f != float("inf") and f != float("-inf")):
        return "—"
    abs_f = abs(f)
    if abs_f != 0 and (abs_f < 1e-3 or abs_f >= 1e6):
        return f"{f:.{nd}e}{suffix}"
    return f"{f:.{nd}f}{suffix}".rstrip("0").rstrip(".") + suffix if False else f"{f:.{nd}f}{suffix}"


def _fmt_with_err(val: Any, err: Any, nd: int = 5) -> str:
    v = _fmt(val, nd)
    if err is None:
        return v
    try:
        e = float(err)
        if e != e:
            return v
        return f"{v} ± {_fmt(e, min(nd, 4))}"
    except (TypeError, ValueError):
        return v


def _table_style(has_header: bool = False, label_col: bool = True) -> TableStyle:
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
    t = Table(rows, colWidths=[label_w, CONTENT_W - label_w])
    t.setStyle(_table_style(has_header=False))
    t.hAlign = "CENTER"
    return t


def _data_table(rows, colWidths):
    t = Table(rows, colWidths=colWidths)
    t.setStyle(_table_style(has_header=True))
    t.hAlign = "CENTER"
    return t


def _header_footer(canvas, doc, title: str):
    canvas.saveState()
    # Header band.
    canvas.setFillColor(BAND)
    canvas.rect(0, letter[1] - 0.55 * inch, letter[0], 0.55 * inch, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 12)
    canvas.drawString(0.6 * inch, letter[1] - 0.35 * inch, title)
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(letter[0] - 0.6 * inch, letter[1] - 0.35 * inch,
                           "Vetstar microlensing vetting report")
    # Footer band.
    canvas.setFillColor(colors.HexColor("#64748b"))
    canvas.setFont("Helvetica", 8)
    canvas.drawString(0.6 * inch, 0.4 * inch,
                      datetime.now(timezone.utc).strftime("Generated %Y-%m-%d %H:%M UTC"))
    canvas.drawRightString(letter[0] - 0.6 * inch, 0.4 * inch,
                           f"Page {doc.page}")
    canvas.restoreState()


_VERDICT_COLORS = {
    "microlensing": colors.HexColor("#10b981"),
    "flare":        colors.HexColor("#f59e0b"),
    "null":         colors.HexColor("#64748b"),
    "ambiguous":    colors.HexColor("#3b82f6"),
}


def build_microlensing_pdf(result: Dict[str, Any],
                            metadata: Optional[Dict[str, Any]] = None,
                            plot_png_b64: Optional[str] = None,
                            ffi_png_b64: Optional[str] = None) -> bytes:
    """Assemble the PDF and return the raw bytes.

    `plot_png_b64` is the classifier light-curve plot; `ffi_png_b64` is the
    optional TESScut FFI + Gaia overlay from /api/microlensing/ffi_cutout.
    Both are base64 without the data-URL prefix.
    """
    metadata = metadata or {}
    styles = getSampleStyleSheet()
    # getSampleStyleSheet already registers "h1"–"h5" as aliases, so use
    # ml_-prefixed names to avoid the alias-collision KeyError.
    styles.add(ParagraphStyle("ml_h2", parent=styles["Heading2"],
                              textColor=INK, fontSize=13, spaceAfter=6,
                              fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle("ml_small", parent=styles["Normal"],
                              textColor=INK, fontSize=9, leading=12))
    styles.add(ParagraphStyle("ml_verdict", parent=styles["Normal"],
                              textColor=colors.white, fontSize=14,
                              alignment=1, fontName="Helvetica-Bold",
                              spaceBefore=4, spaceAfter=4))

    buf = io.BytesIO()
    title_id = metadata.get("event_id") or (
        f"TIC {metadata['tic_id']}" if metadata.get("tic_id") else "microlensing event"
    )
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        title=f"Vetstar microlensing — {title_id}",
        author="Vetstar",
        topMargin=0.85 * inch, bottomMargin=0.7 * inch,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
    )

    story: list = []

    # ---- Target block ----
    tgt_rows = [["Event ID", metadata.get("event_id") or "—"]]
    if metadata.get("tic_id"):
        tgt_rows.append(["TIC", str(metadata["tic_id"])])
    if metadata.get("ra") is not None and metadata.get("dec") is not None:
        tgt_rows.append([
            "RA, Dec (J2000)",
            f"{float(metadata['ra']):.5f}°, {float(metadata['dec']):+.5f}°"
        ])
    if metadata.get("sector") is not None:
        s = int(metadata["sector"])
        prov = metadata.get("provider") or ""
        tgt_rows.append(["Sector",
                         f"S{s:03d}" + (f" ({prov})" if prov else "")])
    tgt_rows.append(["Window",
                     f"BTJD {result['window']['t_start']:.3f} → "
                     f"{result['window']['t_end']:.3f} ({result['window']['n_points']} points)"])
    story.append(_kv_table(tgt_rows))
    story.append(Spacer(1, 0.15 * inch))

    # ---- Verdict banner ----
    verdict = str(result.get("verdict", "ambiguous"))
    conf = float(result.get("confidence") or 0.0)
    banner_color = _VERDICT_COLORS.get(verdict, _VERDICT_COLORS["ambiguous"])
    banner = Table(
        [[Paragraph(f"VERDICT: {verdict.upper()} — {conf * 100:.0f}% confidence",
                    styles["ml_verdict"])]],
        colWidths=[CONTENT_W],
    )
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), banner_color),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("BOX", (0, 0), (-1, -1), 0.5, banner_color),
    ]))
    banner.hAlign = "CENTER"
    story.append(banner)
    story.append(Spacer(1, 0.14 * inch))

    # ---- Observables table ----
    obs = result.get("observables") or {}
    if obs:
        obs_rows = [["Observable parameter", "Value"]]
        obs_rows.append(["t₀ (BTJD)", _fmt_with_err(obs.get("t0_btjd"),
                                                     obs.get("t0_btjd_err"), 5)])
        obs_rows.append(["t₀ (BJD)", _fmt(obs.get("t0_bjd"), 5)])
        obs_rows.append(["Einstein timescale tE (d)",
                         _fmt_with_err(obs.get("einstein_timescale_d"),
                                       obs.get("einstein_timescale_err_d"), 3)])
        obs_rows.append(["Impact parameter u₀",
                         _fmt_with_err(obs.get("impact_parameter_u0"),
                                       obs.get("impact_parameter_err"), 4)])
        obs_rows.append(["Peak magnification A_max",
                         _fmt(obs.get("peak_magnification"), 3)])
        obs_rows.append(["Blended peak magnification (observed)",
                         _fmt(obs.get("peak_magnification_observed"), 3)])
        obs_rows.append(["Peak brightening (mag)",
                         _fmt(obs.get("peak_brightening_mag"), 3)])
        obs_rows.append(["Einstein-crossing duration (d, u < 1)",
                         _fmt(obs.get("einstein_crossing_duration_d"), 3)])
        obs_rows.append(["Magnification FWHM (d)",
                         _fmt(obs.get("magnification_fwhm_d"), 3)])
        obs_rows.append(["Source flux fraction (f_s / (f_s + f_b))",
                         _fmt(obs.get("source_flux_fraction"), 3)])
        obs_rows.append(["Blend flux fraction",
                         _fmt(obs.get("blend_flux_fraction"), 3)])
        obs_rows.append(["μ_rel (fiducial θ_E = 0.5 mas) [mas/yr]",
                         _fmt(obs.get("mu_rel_mas_per_yr_fiducial"), 3)])
        story.append(_section_heading("Observable parameters", styles))
        story.append(_data_table(obs_rows,
                                  [3.6 * inch, CONTENT_W - 3.6 * inch]))
        story.append(Paragraph(
            "<i>Angular Einstein radius θ_E and lens mass M_L are degenerate "
            "from single-band photometry alone; μ_rel above uses a fiducial "
            "θ_E ≈ 0.5 mas (typical bulge value) and should be treated as an "
            "order-of-magnitude estimate.</i>",
            styles["ml_small"]))
        story.append(Spacer(1, 0.14 * inch))

    # ---- ExoFOP-style planet parameter rows ----
    exofop_rows = result.get("exofop_rows") or []
    if exofop_rows:
        er_table = [["Parameter", "Value", "Unit"]]
        for r in exofop_rows:
            marker = "*" if r.get("required") else ""
            label = f"{marker}{r['label']}" if marker else r["label"]
            er_table.append([label, _fmt(r.get("value"), 4), r.get("unit") or ""])
        story.append(_section_heading(
            "ExoFOP planet parameters (microlensing-derivable subset)", styles))
        story.append(_data_table(er_table,
                                  [3.4 * inch, 1.8 * inch, CONTENT_W - 5.2 * inch]))
        story.append(Paragraph(
            "<i>Fields marked * are required for an ExoFOP-TESS TOI submission. "
            "Rows shown as '—' are not derivable from a single-lens microlensing "
            "fit (need radial-velocity follow-up or a binary-lens caustic-crossing "
            "detection). Host/planet properties assume the fiducial bulge-lens "
            "priors described under 'Predicted planet parameters' below.</i>",
            styles["ml_small"]))
        story.append(Spacer(1, 0.14 * inch))

    # ---- Planet-detection predictions ----
    pp = result.get("planet_predictions") or {}
    if pp:
        pp_rows = [["Predicted quantity", "Value (fiducial priors)"]]
        pp_rows.append(["Fiducial lens mass M_L (M☉)",
                        _fmt(pp.get("fiducial_lens_mass_solar"), 2)])
        pp_rows.append(["Fiducial lens distance D_L (kpc)",
                        _fmt(pp.get("fiducial_lens_distance_kpc"), 2)])
        pp_rows.append(["Fiducial source distance D_S (kpc)",
                        _fmt(pp.get("fiducial_source_distance_kpc"), 2)])
        pp_rows.append(["θ_E (mas)",
                        _fmt(pp.get("theta_E_mas_fiducial"), 4)])
        pp_rows.append(["Physical Einstein radius r_E (AU)",
                        _fmt(pp.get("einstein_radius_au_fiducial"), 3)])
        pp_rows.append(["v_rel (km/s)",
                        _fmt(pp.get("v_rel_km_s_fiducial"), 1)])
        pp_rows.append(["Closest approach u₀·r_E (AU)",
                        _fmt(pp.get("closest_approach_au_fiducial"), 3)])
        pp_rows.append(["Planet-detection floor q_min = M_p/M_L",
                        _fmt(pp.get("planet_q_min_detectable"), 6)])
        pp_rows.append(["Planet mass floor (M⊕ at fiducial M_L)",
                        _fmt(pp.get("planet_mass_floor_m_earth_fiducial"), 3)])
        pp_rows.append(["Planet mass floor (M♃ at fiducial M_L)",
                        _fmt(pp.get("planet_mass_floor_m_jupiter_fiducial"), 4)])
        story.append(_section_heading("Predicted planet parameters (under fiducial bulge lens)", styles))
        story.append(_data_table(pp_rows, [3.6 * inch, CONTENT_W - 3.6 * inch]))
        if pp.get("assumption"):
            story.append(Paragraph(f"<i>{pp['assumption']}</i>", styles["ml_small"]))
        if pp.get("planet_sensitivity_note"):
            story.append(Paragraph(f"<i>{pp['planet_sensitivity_note']}</i>",
                                    styles["ml_small"]))
        story.append(Spacer(1, 0.14 * inch))

    # ---- PSPL params table ----
    pspl = result["models"]["pspl"]
    pparams = pspl.get("params", {})
    perrs = pspl.get("param_err", {})
    pspl_rows = [["Parameter", "Best fit ± 1σ"]]
    for k in ("t0", "tE", "u0", "f_s", "f_b"):
        pspl_rows.append([k, _fmt_with_err(pparams.get(k), perrs.get(k), 5)])
    story.append(_section_heading("PSPL best-fit parameters", styles))
    story.append(_data_table(pspl_rows, [2.0 * inch, CONTENT_W - 2.0 * inch]))
    story.append(Spacer(1, 0.14 * inch))

    # ---- Model comparison table ----
    bic_p = pspl.get("bic")
    bic_f = result["models"]["flare"].get("bic")
    bic_n = result["models"]["null"].get("bic")
    best = min((v for v in (bic_p, bic_f, bic_n) if v is not None and _isfinite(v)),
               default=None)
    dnp = result.get("delta_bic", {}).get("null_minus_pspl")
    dfp = result.get("delta_bic", {}).get("flare_minus_pspl")

    def _row(label: str, bic):
        d = "" if best is None or bic is None or not _isfinite(bic) else \
            _fmt(bic - best, 2)
        return [label, _fmt(bic, 2), d]
    bic_rows = [["Model", "BIC", "ΔBIC vs best"]]
    bic_rows.append(_row("PSPL", bic_p))
    bic_rows.append(_row("Flare (Davenport 2014)", bic_f))
    bic_rows.append(_row("Null (constant baseline)", bic_n))
    story.append(_section_heading("Model comparison — lower BIC wins", styles))
    story.append(_data_table(bic_rows, [2.8 * inch, 1.9 * inch, CONTENT_W - 4.7 * inch]))

    interpret_lines = []
    if dnp is not None and _isfinite(dnp):
        interpret_lines.append(
            f"ΔBIC(null − PSPL) = <b>{_fmt(dnp, 1)}</b>"
            f" ({_verdict_gloss('null_minus_pspl', dnp)})")
    if dfp is not None and _isfinite(dfp):
        interpret_lines.append(
            f"ΔBIC(flare − PSPL) = <b>{_fmt(dfp, 1)}</b>"
            f" ({_verdict_gloss('flare_minus_pspl', dfp)})")
    sym = result.get("symmetry_score")
    if sym is not None and _isfinite(sym):
        interpret_lines.append(
            f"Residual symmetry score (PSPL residuals folded about t₀): <b>{_fmt(sym, 3)}</b> "
            f"(+1 symmetric, ~0 uncorrelated, &lt;0 anti-symmetric)")
    if interpret_lines:
        story.append(Paragraph("<br/>".join(interpret_lines), styles["ml_small"]))
    story.append(Spacer(1, 0.14 * inch))

    # ---- FFI cutout with Gaia overlay ----
    if ffi_png_b64:
        try:
            ffi_bytes = base64.b64decode(ffi_png_b64)
            img = Image(io.BytesIO(ffi_bytes),
                        width=CONTENT_W * 0.7, height=CONTENT_W * 0.7)
            story.append(_section_heading("TESScut FFI cutout + Gaia DR3 overlay", styles))
            story.append(KeepTogether([img]))
            story.append(Paragraph(
                "<i>Median-stacked TESScut frames with target crosshair (red) "
                "and Gaia DR3 sources within the FOV plotted as yellow circles "
                "sized by G-band magnitude. Use to diagnose source blending "
                "in the 21″ pixels.</i>", styles["ml_small"]))
            story.append(Spacer(1, 0.14 * inch))
        except Exception:
            pass

    # ---- Plot ----
    if plot_png_b64:
        try:
            png_bytes = base64.b64decode(plot_png_b64)
            img = Image(io.BytesIO(png_bytes),
                        width=CONTENT_W, height=CONTENT_W * 260 / 780)
            story.append(_section_heading("Light-curve + best-fit overlay", styles))
            story.append(KeepTogether([img]))
            story.append(Spacer(1, 0.1 * inch))
        except Exception:
            pass  # non-fatal

    # ---- Notes ----
    notes = result.get("notes") or []
    if notes:
        story.append(_section_heading("Diagnostic notes", styles))
        for n in notes:
            story.append(Paragraph("• " + n, styles["ml_small"]))
        story.append(Spacer(1, 0.1 * inch))

    # ---- Build ----
    header_title = f"Vetstar microlensing — {title_id}"
    doc.build(
        story,
        onFirstPage=lambda c, d: _header_footer(c, d, header_title),
        onLaterPages=lambda c, d: _header_footer(c, d, header_title),
    )
    return buf.getvalue()


def _section_heading(text: str, styles) -> KeepTogether:
    return KeepTogether([
        Paragraph(text, styles["ml_h2"]),
        HRFlowable(width="100%", thickness=0.7, color=ACCENT, spaceAfter=6),
    ])


def _isfinite(v: Any) -> bool:
    try:
        f = float(v)
        return f == f and f not in (float("inf"), float("-inf"))
    except (TypeError, ValueError):
        return False


def _verdict_gloss(kind: str, delta: float) -> str:
    """Short interpretation of a ΔBIC value."""
    if kind == "null_minus_pspl":
        if delta > 10: return "PSPL strongly preferred over null"
        if delta > 2:  return "PSPL preferred over null"
        if delta < -2: return "null preferred over PSPL"
        return "PSPL and null indistinguishable"
    if kind == "flare_minus_pspl":
        a = abs(delta)
        if a < 6: return "PSPL and flare indistinguishable — ambiguous"
        if delta > 10: return "PSPL strongly preferred over flare"
        if delta < -10: return "flare strongly preferred over PSPL"
        return "leaning " + ("PSPL" if delta > 0 else "flare")
    return ""
