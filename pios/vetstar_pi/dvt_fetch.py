"""
TESS SPOC DVT (Data Validation Time Series) fetcher and parser.

DVT FITS files are produced by the SPOC pipeline alongside regular light
curves and contain one binary-table extension per TCE (Threshold Crossing
Event).  Key header keywords per TCE extension:

    TPERIOD  – orbital period (days)
    TDUR     – full transit duration (hours)
    TEPOCH   – transit epoch (BTJD)
    TDEPTH   – transit depth (ppm)
    IMPACT   – fitted impact parameter b
    ARAT     – fitted a/R★ (scaled semi-major axis)   ← key for semi-major axis
    INCLIN   – fitted inclination (degrees)
    RPRS     – fitted Rp/Rs radius ratio
    PRAD     – fitted planet radius (Earth radii, when present)
    SNRATIO  – detection SNR

Columns per TCE binary table:
    TIME, PHASE, LC_INIT, MODEL_INIT [, LC_DETREND, MODEL_DETREND]

Why DVT improves the semi-major axis
-------------------------------------
The standard BLS-only path assumes a central transit (impact parameter b = 0)
when solving:

    a/Rs = (P / (π T₁₄)) √((1+k)² − b²)

That overestimates a/Rs (and therefore a in AU) whenever the real transit is
even slightly off-centre.  The SPOC DV pipeline fits a full Mandel-Agol model
and stores the fitted IMPACT and ARAT directly.  Using these:

    a (AU) = ARAT × R★    (R★ converted from R_sun to AU)

is independent of the b=0 assumption and removes a systematic bias.

The SPOC period is also more precise than a single-sector BLS peak because the
DV pipeline phase-folds across every available sector.

Returns None when no DVT product is available (sector too recent, FFI-only
observation, or MAST timeout) so the rest of the pipeline degrades gracefully.
"""
from __future__ import annotations

import base64
import io
import logging
from typing import Optional

log = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# MAST search for DVT products
# -----------------------------------------------------------------------

def _get_dvt_products(tic_id: int, sector: Optional[int] = None):
    """Return MAST product rows for the DVT file associated with (TIC, sector).

    DVT files have productSubGroupDescription == "DVT".  Only SPOC produces
    them.  Returns None when none are found.

    SPOC DV runs are often filed under a multi-sector observation row whose
    ``sequence_number`` is the *first* sector of the run, not the requested
    one.  A strict sector filter therefore frequently misses the DVT product.
    We try the sector-filtered search first (cheapest, most specific) and fall
    back to a TIC-wide search whenever it yields no DVT.
    """
    from astroquery.mast import Observations
    from .mast_fetch import find_observations, safe_get, retry

    def _query(sec):
        try:
            return find_observations(tic_id, sector=sec)
        except Exception as e:
            log.warning("DVT: observation lookup failed for TIC %s sector %s: %s",
                        tic_id, sec, e)
            return None

    def _filter_dvt(obs):
        if obs is None or len(obs) == 0:
            return None
        try:
            spoc_mask = [str(safe_get(o, "provenance_name", "")) == "SPOC" for o in obs]
            spoc_obs = obs[[i for i, m in enumerate(spoc_mask) if m]]
            if len(spoc_obs) == 0:
                spoc_obs = obs
        except Exception:
            spoc_obs = obs

        def get_prods():
            return Observations.get_product_list(spoc_obs)

        try:
            products = retry(get_prods, name=f"DVT product list TIC {tic_id}")
        except RuntimeError as e:
            log.warning("DVT: product list failed for TIC %s: %s", tic_id, e)
            return None

        dvt = Observations.filter_products(
            products,
            productSubGroupDescription="DVT",
            extension="fits",
        )
        return dvt if len(dvt) > 0 else None

    # First: strict (sector-filtered) search.
    dvt = _filter_dvt(_query(sector))
    if dvt is not None:
        return dvt
    # Fallback: TIC-wide search picks up multi-sector DV rows whose
    # sequence_number doesn't match the requested sector.
    if sector is not None:
        log.info("DVT: no DVT in sector %s for TIC %s, retrying TIC-wide", sector, tic_id)
        dvt = _filter_dvt(_query(None))
        if dvt is not None:
            return dvt
    log.info("DVT: no DVT product for TIC %s sector %s", tic_id, sector)
    return None


def fetch_dvt(tic_id: int, sector: Optional[int] = None) -> Optional[dict]:
    """Download and parse the DVT FITS file for (tic_id, sector) from MAST.

    Returns a parsed dict (see ``parse_dvt_fits``) or None when unavailable.
    Never raises — logs warnings and returns None on any failure so the caller
    can degrade gracefully.
    """
    from astroquery.mast import Observations
    from .mast_fetch import retry

    dvt_products = _get_dvt_products(tic_id, sector)
    if dvt_products is None:
        return None

    def download():
        return Observations.download_products(dvt_products[:1])

    try:
        manifest = retry(download, name=f"DVT download TIC {tic_id}")
    except RuntimeError as e:
        log.warning("DVT: download failed for TIC %s: %s", tic_id, e)
        return None

    if len(manifest) == 0 or "Local Path" not in manifest.colnames:
        log.warning("DVT: empty download manifest for TIC %s", tic_id)
        return None

    path = str(manifest["Local Path"][0])
    log.info("DVT downloaded: %s", path)
    try:
        return parse_dvt_fits(path)
    except Exception as e:
        log.warning("DVT: parse failed for %s: %s", path, e)
        return None


# -----------------------------------------------------------------------
# DVT FITS parser
# -----------------------------------------------------------------------

# Header keywords → output field names (numeric only)
_TCE_FLOAT_KEYS: list[tuple[str, str]] = [
    ("TPERIOD",  "period_d"),
    ("TDUR",     "duration_h"),
    ("TEPOCH",   "epoch_btjd"),
    ("TDEPTH",   "depth_ppm"),
    ("IMPACT",   "impact_b"),
    # a/R* — SPOC stores it as ARAT; handle legacy aliases
    ("ARAT",     "a_over_rs"),
    ("AR",       "a_over_rs"),          # older SPOC format fallback
    ("AROS",     "a_over_rs"),          # alternative name observed in some files
    ("INCLIN",   "inclination_deg"),
    ("RPRS",     "rprs"),
    ("PRAD",     "planet_radius_earth"),
    ("SNRATIO",  "snr"),
    ("NUMTRANS", "n_transits"),
]

_STAR_FLOAT_KEYS: list[tuple[str, str]] = [
    ("TEFF",    "teff"),
    ("LOGG",    "logg"),
    ("TESSMAG", "tmag"),
    ("RADIUS",  "radius_sun"),
    ("MASS",    "mass_sun"),
]


def _hdr_float(hdr, key) -> Optional[float]:
    try:
        v = hdr.get(key)
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def parse_dvt_fits(path: str) -> dict:
    """Parse a TESS SPOC DVT FITS file.

    Returns::

        {
            "star": {
                "teff": float | None,
                "logg": float | None,
                "tmag": float | None,
                "radius_sun": float | None,
                "mass_sun": float | None,
            },
            "tces": [
                {
                    "tce_index": int,
                    "period_d": float | None,
                    "duration_h": float | None,
                    "epoch_btjd": float | None,
                    "depth_ppm": float | None,
                    "depth_frac": float | None,   # depth_ppm / 1e6
                    "impact_b": float | None,     # fitted impact parameter
                    "a_over_rs": float | None,    # fitted a/R★ from ARAT keyword
                    "inclination_deg": float | None,
                    "rprs": float | None,
                    "planet_radius_earth": float | None,
                    "snr": float | None,
                    "n_transits": float | None,
                    "phase_fold_plot": str,       # base64 PNG (empty on failure)
                    "columns_available": list[str],
                },
                ...
            ],
        }

    Only the first (best-SNR or only) TCE is typically used downstream;
    the caller can access ``result["tces"][0]`` safely when ``tces`` is
    non-empty.
    """
    from astropy.io import fits

    result: dict = {"star": {}, "tces": []}

    with fits.open(path, mode="readonly", memmap=False) as hdulist:
        # Primary header: stellar parameters
        phdr = hdulist[0].header
        for fits_key, field in _STAR_FLOAT_KEYS:
            v = _hdr_float(phdr, fits_key)
            if v is not None:
                result["star"][field] = v

        # One binary-table extension per TCE (extensions 1, 2, …)
        for i, hdu in enumerate(hdulist[1:], start=1):
            try:
                if not hasattr(hdu, "data") or hdu.data is None:
                    continue
                tce: dict = {"tce_index": i}
                hdr = hdu.header

                for fits_key, field in _TCE_FLOAT_KEYS:
                    # Don't overwrite once a_over_rs is populated from the
                    # primary keyword (ARAT); aliases are checked in order.
                    if field in tce:
                        continue
                    v = _hdr_float(hdr, fits_key)
                    if v is not None:
                        tce[field] = v

                # Convenience: depth as a fraction for downstream functions
                if tce.get("depth_ppm") is not None:
                    tce["depth_frac"] = tce["depth_ppm"] / 1.0e6

                # Phase-folded time series columns
                data = hdu.data
                cols = [c.name.upper() for c in hdu.columns]
                tce["columns_available"] = cols

                phases = _col(data, cols, "PHASE")
                lc_init = _col(data, cols, "LC_INIT")
                model_init = _col(data, cols, "MODEL_INIT")

                tce["phase_fold_plot"] = (
                    _plot_phase_fold(phases, lc_init, model_init, tce)
                    if phases is not None and lc_init is not None
                    else ""
                )

                result["tces"].append(tce)
            except Exception as exc:
                log.warning("DVT: TCE %d parse error in %s: %s", i, path, exc)

    return result


def _col(data, cols: list[str], name: str):
    """Safely extract a float array from a binary table by column name."""
    if name not in cols:
        return None
    try:
        return data[name].astype(float)
    except Exception:
        return None


# -----------------------------------------------------------------------
# Phase-fold diagnostic plot  (mirrors MAST beginner DVT notebook)
# -----------------------------------------------------------------------

def _plot_phase_fold(phases, lc_init, model_init, tce: dict) -> str:
    """Render phase-folded data + SPOC transit model. Returns base64 PNG."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        sort_idx = np.argsort(phases)
        ph = phases[sort_idx]
        lc = lc_init[sort_idx]

        period = tce.get("period_d")
        depth_ppm = tce.get("depth_ppm")
        duration_h = tce.get("duration_h")
        a_over_rs = tce.get("a_over_rs")
        impact = tce.get("impact_b")

        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(ph, lc, "ko", ms=1.5, alpha=0.35, label="Data (LC_INIT)")
        if model_init is not None:
            mod = model_init[sort_idx]
            ax.plot(ph, mod, "-r", lw=1.8, label="SPOC transit model")

        # Annotation block in lower-left corner
        parts: list[str] = []
        if period is not None:
            parts.append(f"P = {period:.6f} d")
        if duration_h is not None:
            parts.append(f"T₁₄ = {duration_h:.4f} h")
        if depth_ppm is not None:
            parts.append(f"depth = {depth_ppm:.0f} ppm")
        if a_over_rs is not None:
            parts.append(f"a/R★ = {a_over_rs:.3f}  (SPOC DV)")
        if impact is not None:
            parts.append(f"b = {impact:.3f}  (SPOC DV)")

        if parts:
            ax.text(
                0.02, 0.04,
                "   ".join(parts),
                transform=ax.transAxes,
                fontsize=8,
                color="#374151",
                va="bottom",
            )

        ax.set_xlabel("Orbital phase")
        ax.set_ylabel("Relative flux")
        ax.set_title(
            f"SPOC DV phase-fold — TCE {tce['tce_index']}"
            + (f"  (P = {period:.4f} d)" if period else ""),
        )
        ax.legend(fontsize=8, loc="upper right")
        ax.ticklabel_format(axis="y", useOffset=False, style="plain")
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
        plt.close(fig)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:
        log.warning("DVT: phase-fold plot failed: %s", exc)
        return ""


# -----------------------------------------------------------------------
# Convenience: extract the best (first / highest-SNR) TCE parameters
# -----------------------------------------------------------------------

def best_tce(dvt: Optional[dict]) -> Optional[dict]:
    """Return the first (highest-SNR or only) TCE from a parsed DVT result.

    Returns None when dvt is None or contains no TCEs.  The caller should
    treat every field as Optional — not all SPOC runs produce every keyword.
    """
    if not dvt or not dvt.get("tces"):
        return None
    tces = dvt["tces"]
    # Prefer highest SNR if multiple TCEs are present
    return max(tces, key=lambda t: t.get("snr") or 0.0)
