"""Shared DejaVu Sans registration for reportlab-built PDFs.

Reportlab's built-in Helvetica only ships the WinAnsi subset — every
astronomy symbol we care about (θ, ε, μ, α, δ, ×, °, ², ³, ⊙, ⊕, ♃, Δ, …)
renders as a black square. DejaVu Sans covers the full Unicode range for
sub-mathematical operators, Greek, and superscripts, so both the transit
and microlensing PDFs use it.

DejaVu ships with matplotlib (an existing Vetstar dep) — we grab the
TTFs from the mpl-data path rather than depending on a system install
that may or may not be present in a Fly.io / Render container.
"""
from __future__ import annotations

import logging
import os
from typing import Tuple

log = logging.getLogger(__name__)

# What callers actually import — swap-in replacements for
# "Helvetica" / "Helvetica-Bold" in FONTNAME / setFont / fontName calls.
FONT_NORMAL: str = "Helvetica"
FONT_BOLD: str = "Helvetica-Bold"
_REGISTERED: bool = False


def _matplotlib_dejavu_paths() -> Tuple[str, str] | None:
    """Return (regular_ttf, bold_ttf) paths from matplotlib's bundled fonts,
    or None if either file is missing."""
    try:
        import matplotlib
    except Exception as e:
        log.info("matplotlib not importable — falling back to Helvetica (%s)", e)
        return None
    d = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
    regular = os.path.join(d, "DejaVuSans.ttf")
    bold = os.path.join(d, "DejaVuSans-Bold.ttf")
    if not (os.path.isfile(regular) and os.path.isfile(bold)):
        return None
    return regular, bold


def ensure_dejavu_registered() -> None:
    """Idempotent — register DejaVu Sans + DejaVu Sans Bold with reportlab
    on first call, then flip FONT_NORMAL/FONT_BOLD so downstream code
    picks up the new names. Silent no-op if the fonts can't be found."""
    global FONT_NORMAL, FONT_BOLD, _REGISTERED
    if _REGISTERED:
        return
    paths = _matplotlib_dejavu_paths()
    if paths is None:
        log.warning("DejaVu Sans TTFs not found — PDFs stay on Helvetica; "
                    "non-ASCII glyphs (Greek, ×, °, superscripts) will render "
                    "as black squares.")
        _REGISTERED = True  # don't retry every PDF build
        return
    regular, bold = paths
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.pdfmetrics import registerFontFamily
        from reportlab.pdfbase.ttfonts import TTFont
        pdfmetrics.registerFont(TTFont("DejaVuSans", regular))
        pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", bold))
        # Bind bold as the <b>...</b> variant so Paragraph inline markup
        # picks up the right face automatically.
        registerFontFamily(
            "DejaVuSans",
            normal="DejaVuSans",
            bold="DejaVuSans-Bold",
            italic="DejaVuSans",
            boldItalic="DejaVuSans-Bold",
        )
    except Exception as e:
        log.warning("DejaVu registration failed (%s) — staying on Helvetica.", e)
        _REGISTERED = True
        return
    FONT_NORMAL = "DejaVuSans"
    FONT_BOLD = "DejaVuSans-Bold"
    _REGISTERED = True
    log.info("Registered DejaVu Sans with reportlab: normal=%s, bold=%s",
              FONT_NORMAL, FONT_BOLD)


def font_normal() -> str:
    """Preferred sans-serif font name for reportlab — call after
    ensure_dejavu_registered()."""
    return FONT_NORMAL


def font_bold() -> str:
    """Bold variant of font_normal()."""
    return FONT_BOLD
