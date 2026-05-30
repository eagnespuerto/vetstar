"""
ffi_cutout.py — TESS Full-Frame-Image (FFI) cutout of the target.

For light-curve vetting it is invaluable to *see* the patch of sky the light
curve came from: a nearby bright neighbour, a background eclipsing binary, or
a scattered-light gradient can all masquerade as a transit. This module pulls
a small FFI cutout centred on the target and renders it as a PNG that drops
straight into the on-screen vetting results and the PDF report.

The cutout is fetched from MAST's TESScut service (``astroquery.mast.Tesscut``),
which is the hosted form of the ``astrocut`` package. The image normalisation
(asinh stretch + asymmetric percentile clip) follows ``astrocut.cutouts``'
``normalize_img`` / ``img_cut`` defaults, reproduced here with
``astropy.visualization`` so no extra dependency is needed.

Because TESScut centres the cutout on the requested coordinates, the target
sits at the centre of the array by construction — so we can mark it without
needing the per-column TPF WCS.

Network failures, missing sectors, and empty cutouts all fail soft: the
caller gets ``None`` and the rest of the report is unaffected.
"""
from __future__ import annotations

import base64
import io
import logging
from typing import Optional

import numpy as np

log = logging.getLogger("vetstar.ffi_cutout")

# astrocut's img_cut defaults.
_STRETCH = "asinh"
_MINMAX_PERCENT = (0.5, 99.5)
_DEFAULT_SIZE_PX = 15  # ~5 arcmin at 21"/px — enough to show close neighbours.


# ----------------------------------------------------------------------
# Normalisation (astrocut.cutouts.normalize_img, reproduced)
# ----------------------------------------------------------------------
def _normalize(img: np.ndarray) -> np.ndarray:
    """Asinh stretch + asymmetric percentile clip → float array in [0, 1].

    Mirrors astrocut's default image scaling. NaNs are mapped to 0 so dead
    pixels render as background rather than blowing out the interval.
    """
    from astropy.visualization import AsinhStretch, AsymmetricPercentileInterval

    finite = np.isfinite(img)
    if not finite.any():
        return np.zeros_like(img, dtype=float)

    transform = AsinhStretch() + AsymmetricPercentileInterval(*_MINMAX_PERCENT)
    out = np.array(img, dtype=float)
    out[~finite] = np.nanmin(img[finite]) if finite.any() else 0.0
    norm = transform(out)
    return np.clip(np.nan_to_num(norm, nan=0.0), 0.0, 1.0)


# ----------------------------------------------------------------------
# Render (pure: no network — unit-testable on a synthetic cube)
# ----------------------------------------------------------------------
def render_cutout_png(
    flux_cube: np.ndarray,
    *,
    aperture: Optional[np.ndarray] = None,
    quality: Optional[np.ndarray] = None,
    tic_id: Optional[int] = None,
    sector: Optional[int] = None,
    ra: Optional[float] = None,
    dec: Optional[float] = None,
) -> str:
    """Collapse a (n_time, ny, nx) flux cube to a median image and render a
    base64 PNG with the target marked at the centre and the optional pipeline
    aperture outlined. Returns the base64 string (no data-URI prefix)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cube = np.asarray(flux_cube, dtype=float)
    if cube.ndim == 2:
        cube = cube[None, ...]
    if quality is not None:
        good = np.asarray(quality) == 0
        if good.any():
            cube = cube[good]
    # Median over time is robust to cosmic rays and momentum dumps.
    img = np.nanmedian(cube, axis=0)
    ny, nx = img.shape
    norm = _normalize(img)

    fig, ax = plt.subplots(figsize=(4.2, 4.0))
    im = ax.imshow(norm, origin="lower", cmap="viridis", interpolation="nearest")

    # Target marker — TESScut centres the cutout on the requested position.
    cy, cx = (ny - 1) / 2.0, (nx - 1) / 2.0
    ax.plot(
        cx, cy, marker="+", markersize=16, markeredgewidth=2.0,
        color="#ff3b30", zorder=5,
    )

    # Optional aperture outline (pipeline / TPF aperture mask).
    if aperture is not None:
        ap = np.asarray(aperture)
        if ap.shape == img.shape:
            ax.contour(
                (ap > 0).astype(float), levels=[0.5],
                colors="#ffd60a", linewidths=1.2, zorder=4,
            )

    title = "TESS FFI cutout"
    if tic_id:
        title += f" — TIC {tic_id}"
    if sector:
        title += f" (S{sector})"
    ax.set_title(title, fontsize=10)
    ax.set_xlabel("column (px)", fontsize=8)
    ax.set_ylabel("row (px)", fontsize=8)
    ax.tick_params(labelsize=7)
    if ra is not None and dec is not None:
        ax.text(
            0.02, 0.02, f"center  α={ra:.4f}°  δ={dec:.4f}°",
            transform=ax.transAxes, fontsize=7, color="white",
            ha="left", va="bottom",
            bbox=dict(boxstyle="round,pad=0.2", fc="black", ec="none", alpha=0.45),
        )
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("asinh-scaled flux", fontsize=8)
    cbar.ax.tick_params(labelsize=7)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ----------------------------------------------------------------------
# Fetch + render (network)
# ----------------------------------------------------------------------
def make_ffi_cutout(
    ra: float,
    dec: float,
    sector: Optional[int] = None,
    tic_id: Optional[int] = None,
    size_px: int = _DEFAULT_SIZE_PX,
) -> Optional[dict]:
    """Fetch a TESScut FFI cutout at (ra, dec) for ``sector`` and render it.

    Returns ``{"image": <base64 png>, "size_px": int, "n_frames": int,
    "sector": int|None}`` or ``None`` if the cutout could not be produced.
    """
    if ra is None or dec is None:
        log.info("FFI cutout skipped: no coordinates")
        return None
    try:
        from astropy.coordinates import SkyCoord
        from astropy import units as u
        from astroquery.mast import Tesscut

        coord = SkyCoord(float(ra), float(dec), unit="deg")
        kwargs = {"coordinates": coord, "size": int(size_px)}
        if sector is not None:
            kwargs["sector"] = int(sector)
        hdulists = Tesscut.get_cutouts(**kwargs)
        if not hdulists:
            log.info("TESScut returned no cutouts for (%.4f, %.4f) S%s", ra, dec, sector)
            return None

        hdul = hdulists[0]
        data = hdul[1].data
        flux = np.asarray(data["FLUX"])          # (n_time, ny, nx)
        quality = None
        for q in ("QUALITY", "DQUALITY"):
            if q in data.columns.names:
                quality = np.asarray(data[q])
                break
        aperture = None
        try:
            aperture = np.asarray(hdul[2].data)
        except Exception:
            pass

        image = render_cutout_png(
            flux, aperture=aperture, quality=quality,
            tic_id=tic_id, sector=sector, ra=ra, dec=dec,
        )
        return {
            "image": image,
            "size_px": int(size_px),
            "n_frames": int(flux.shape[0]) if flux.ndim == 3 else 1,
            "sector": int(sector) if sector is not None else None,
        }
    except Exception as e:  # network, missing sector, parse error — all soft.
        log.warning("FFI cutout failed for (%.4f, %.4f) S%s: %s", ra, dec, sector, e)
        return None
