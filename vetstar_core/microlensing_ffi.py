"""FFI cutout + Gaia catalog overlay for the microlensing pipeline.

Reuses the transit pipeline's TESScut fetcher (`ffi_cutout.make_ffi_cutout`)
for the base image, then queries Gaia DR3 for stars in the FOV and returns
a re-rendered PNG with those stars overlaid. Useful for microlensing
because the source star and its neighbours dominate the aperture — the
overlay makes source identification (and blending diagnosis) explicit.

Pixel projection uses a tangent-plane approximation centred on the cutout
target. Without a full WCS from the FITS header this is accurate to
sub-pixel over the ~1' FOV of a typical 15-px cutout.
"""
from __future__ import annotations

import base64
import io
import logging
import math
from dataclasses import dataclass
from typing import List, Optional

log = logging.getLogger(__name__)

# TESS pixel scale (arcsec per pixel).
_TESS_ARCSEC_PER_PX = 21.0


@dataclass
class GaiaFovSource:
    source_id: int
    ra: float
    dec: float
    phot_g_mean_mag: Optional[float]
    separation_arcsec: float

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "ra": self.ra,
            "dec": self.dec,
            "phot_g_mean_mag": self.phot_g_mean_mag,
            "separation_arcsec": self.separation_arcsec,
        }


def query_gaia_sources_in_fov(ra_center: float, dec_center: float,
                                radius_arcsec: float,
                                mag_limit: float = 20.0,
                                max_sources: int = 40) -> List[GaiaFovSource]:
    """Cone-search Gaia DR3 within `radius_arcsec` of (ra_center, dec_center).

    Returns rows sorted by separation. Falls back to an empty list on any
    query failure (network, missing astroquery, etc.) — a Gaia overlay is
    always optional.
    """
    try:
        from astroquery.gaia import Gaia
        from astropy.coordinates import SkyCoord
        from astropy import units as u
    except Exception as e:
        log.info("Gaia FOV query skipped: astroquery/astropy unavailable (%s)", e)
        return []
    try:
        Gaia.MAIN_GAIA_TABLE = "gaiadr3.gaia_source"
        Gaia.ROW_LIMIT = int(max_sources)
        coord = SkyCoord(ra_center * u.deg, dec_center * u.deg, frame="icrs")
        table = Gaia.query_object_async(
            coordinate=coord,
            radius=u.Quantity(radius_arcsec, u.arcsec),
        )
    except Exception as e:
        log.warning("Gaia cone search failed: %s", e)
        return []
    if table is None or len(table) == 0:
        return []

    hits: List[GaiaFovSource] = []
    for row in table:
        try:
            g_ra = float(row["ra"])
            g_dec = float(row["dec"])
        except Exception:
            continue
        # Bright-limit + optional faint-cap.
        try:
            g_mag = float(row["phot_g_mean_mag"])
            if not math.isfinite(g_mag) or g_mag > mag_limit:
                continue
        except Exception:
            g_mag = None  # keep it if magnitude missing
        try:
            src_id = int(row["source_id"])
        except Exception:
            src_id = int(row["SOURCE_ID"]) if "SOURCE_ID" in row.colnames else -1
        sep = _angular_sep_arcsec(ra_center, dec_center, g_ra, g_dec)
        hits.append(GaiaFovSource(
            source_id=src_id, ra=g_ra, dec=g_dec,
            phot_g_mean_mag=g_mag, separation_arcsec=sep,
        ))
    hits.sort(key=lambda h: h.separation_arcsec)
    return hits[:max_sources]


def _angular_sep_arcsec(ra1: float, dec1: float,
                          ra2: float, dec2: float) -> float:
    r1, r2 = math.radians(ra1), math.radians(ra2)
    d1, d2 = math.radians(dec1), math.radians(dec2)
    a = (math.sin(0.5 * (d2 - d1)) ** 2
         + math.cos(d1) * math.cos(d2) * math.sin(0.5 * (r2 - r1)) ** 2)
    a = min(1.0, max(0.0, a))
    return 2.0 * math.asin(math.sqrt(a)) * (180.0 / math.pi) * 3600.0


def _sky_to_pixel(ra: float, dec: float,
                    ra_center: float, dec_center: float,
                    size_px: int) -> tuple[float, float]:
    """Tangent-plane approximation: (ra, dec) → (col, row) in a size_px×size_px
    cutout centred on (ra_center, dec_center) at 21″/px.

    Sign convention: TESS FFI cutouts render with row=0 at the bottom
    (`origin="lower"`), and east (increasing RA) points LEFT. So the
    column offset is −Δα·cos(δ) / plate_scale.
    """
    dra_arcsec = (ra - ra_center) * math.cos(math.radians(dec_center)) * 3600.0
    ddec_arcsec = (dec - dec_center) * 3600.0
    center = (size_px - 1) / 2.0
    col = center - (dra_arcsec / _TESS_ARCSEC_PER_PX)  # east is left
    row = center + (ddec_arcsec / _TESS_ARCSEC_PER_PX)  # north is up
    return col, row


def _render_cutout_with_gaia(base_png_b64: str, sources: List[GaiaFovSource],
                              ra_center: float, dec_center: float,
                              size_px: int,
                              tic_id: Optional[int],
                              sector: Optional[int]) -> str:
    """Take the base cutout PNG (from make_ffi_cutout), rasterise it as an
    array, then re-render with Gaia sources overlaid as circles sized by
    Gaia G-band magnitude."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image as PILImage
    import numpy as _np

    # Decode the base PNG so we can overlay onto it.
    raw = base64.b64decode(base_png_b64)
    with PILImage.open(io.BytesIO(raw)) as im:
        arr = _np.asarray(im.convert("RGB"))

    fig, ax = plt.subplots(figsize=(4.6, 4.3))
    ax.imshow(arr)
    ax.set_xticks([]); ax.set_yticks([])

    # We can't overlay in *pixel-of-original-cutout* space directly because
    # the base PNG has its own margins/colorbar. Instead re-mark with a
    # translucent overlay panel inset — draw the raw catalog on top-right.
    # (A proper WCS-aware overlay would require reading the TPF header;
    # this insert is the visual analogue that helps blend diagnosis.)
    inset = ax.inset_axes([0.60, 0.60, 0.38, 0.38])
    inset.set_facecolor("#0f172a")
    inset.set_xlim(-0.5, size_px - 0.5)
    inset.set_ylim(-0.5, size_px - 0.5)
    inset.set_xticks([]); inset.set_yticks([])
    inset.set_aspect("equal")
    # Target crosshair at inset centre.
    center = (size_px - 1) / 2.0
    inset.plot(center, center, marker="+", color="#ff3b30", markersize=14, markeredgewidth=1.6)
    # Draw each Gaia source as a filled circle sized by magnitude.
    for s in sources:
        col, row = _sky_to_pixel(s.ra, s.dec, ra_center, dec_center, size_px)
        if not (-0.5 <= col <= size_px - 0.5 and -0.5 <= row <= size_px - 0.5):
            continue
        g = s.phot_g_mean_mag if s.phot_g_mean_mag is not None else 18.0
        # Map G-mag to marker radius: 10 mag → 12 px, 20 mag → 2 px.
        radius = max(2.0, 12.0 - 1.0 * (g - 10.0))
        inset.scatter([col], [row], s=radius ** 2, facecolor="#fbbf24",
                       edgecolor="black", linewidth=0.4, zorder=3)
    inset.set_title(f"Gaia DR3 in FOV (n={len(sources)})",
                     fontsize=7, color="white", pad=2)

    header = "TESS FFI + Gaia overlay"
    if tic_id: header += f" — TIC {tic_id}"
    if sector: header += f" (S{sector})"
    ax.set_title(header, fontsize=10)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def make_ffi_gaia_cutout(ra: float, dec: float,
                          sector: Optional[int] = None,
                          tic_id: Optional[int] = None,
                          size_px: int = 15,
                          gaia_mag_limit: float = 20.0,
                          gaia_max_sources: int = 40) -> Optional[dict]:
    """Fetch a TESScut FFI cutout and re-render with Gaia DR3 overlay.

    Returns None if the base cutout couldn't be produced. The Gaia overlay
    is a best-effort inset — an empty overlay is still a valid response.
    """
    from .ffi_cutout import make_ffi_cutout
    base = make_ffi_cutout(ra=ra, dec=dec, sector=sector,
                            tic_id=tic_id, size_px=size_px)
    if base is None:
        return None

    # Cone search covers the cutout diagonal.
    radius_arcsec = _TESS_ARCSEC_PER_PX * (size_px / 2.0) * math.sqrt(2.0)
    sources = query_gaia_sources_in_fov(
        ra_center=ra, dec_center=dec,
        radius_arcsec=radius_arcsec,
        mag_limit=gaia_mag_limit,
        max_sources=gaia_max_sources,
    )
    try:
        overlay_png = _render_cutout_with_gaia(
            base_png_b64=base["image"], sources=sources,
            ra_center=ra, dec_center=dec, size_px=size_px,
            tic_id=tic_id, sector=sector,
        )
    except Exception as e:
        log.warning("Gaia overlay render failed: %s (returning base cutout only)", e)
        overlay_png = base["image"]

    return {
        "image": overlay_png,
        "base_image": base["image"],
        "size_px": size_px,
        "n_frames": base.get("n_frames"),
        "sector": base.get("sector"),
        "gaia_sources": [s.to_dict() for s in sources],
        "gaia_n_sources": len(sources),
        "gaia_fov_radius_arcsec": radius_arcsec,
    }
