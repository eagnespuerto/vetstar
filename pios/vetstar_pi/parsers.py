"""
Parsers: turn an uploaded file (TESS/Kepler FITS lightcurve OR an ExoFOP JSON
target dump) into the standard inputs the pipeline expects.
"""
from __future__ import annotations

import json
from typing import Optional

import numpy as np
from astropy.io import fits

from .pipeline import StarInfo


# ----------------------------------------------------------------------
# FITS parser — supports TESS SPOC and Kepler/K2 light curve products
# ----------------------------------------------------------------------
def parse_lightcurve_fits(path: str) -> dict:
    """
    Returns a dict with the arrays + StarInfo extracted from a TESS/Kepler
    SPOC light-curve FITS file.
    """
    hdul = fits.open(path)
    h0 = hdul[0].header
    h1 = hdul[1].header
    d = hdul[1].data
    cols = set(d.columns.names)

    # Flux column: try PDCSAP (SPOC), then SAP, then KSPSAP (QLP), then DET_FLUX
    # (CDIPS), then plain FLUX (Kepler). First match wins.
    flux_col = err_col = None
    for fcand, ecand in [
        ("PDCSAP_FLUX", "PDCSAP_FLUX_ERR"),
        ("SAP_FLUX", "SAP_FLUX_ERR"),
        ("KSPSAP_FLUX", "KSPSAP_FLUX_ERR"),    # QLP
        ("DET_FLUX", "DET_FLUX_ERR"),          # CDIPS detrended
        ("FLUX", "FLUX_ERR"),                  # Kepler / generic
    ]:
        if fcand in cols:
            flux_col = fcand
            err_col = ecand
            break
    if flux_col is None:
        raise ValueError(f"No recognised flux column in {path}. Found: {cols}")

    t = np.asarray(d["TIME"], dtype=float)
    flux = np.asarray(d[flux_col], dtype=float)
    flux_err = np.asarray(d[err_col], dtype=float) if err_col in cols else np.full_like(flux, np.nan)
    quality = np.asarray(d["QUALITY"], dtype=int) if "QUALITY" in cols else None

    mom_x = np.asarray(d["MOM_CENTR1"], dtype=float) if "MOM_CENTR1" in cols else None
    mom_y = np.asarray(d["MOM_CENTR2"], dtype=float) if "MOM_CENTR2" in cols else None

    # Star info — header keys vary by mission
    def g(key, default=None):
        for hdr in (h0, h1):
            if key in hdr and hdr[key] not in ("", None):
                return hdr[key]
        return default

    star = StarInfo(
        tic_id=int(g("TICID")) if g("TICID") else None,
        tmag=float(g("TESSMAG")) if g("TESSMAG") is not None else (
            float(g("KEPMAG")) if g("KEPMAG") is not None else None
        ),
        teff=float(g("TEFF")) if g("TEFF") is not None else None,
        radius=float(g("RADIUS")) if g("RADIUS") is not None else None,
        logg=float(g("LOGG")) if g("LOGG") is not None else None,
        ra=float(g("RA_OBJ")) if g("RA_OBJ") is not None else None,
        dec=float(g("DEC_OBJ")) if g("DEC_OBJ") is not None else None,
        sector=int(g("SECTOR")) if g("SECTOR") is not None else None,
        camera=int(g("CAMERA")) if g("CAMERA") is not None else None,
        ccd=int(g("CCD")) if g("CCD") is not None else None,
        crowdsap=float(g("CROWDSAP")) if g("CROWDSAP") is not None else None,
        source="fits",
    )

    hdul.close()
    return {
        "t": t,
        "flux": flux,
        "flux_err": flux_err,
        "quality": quality,
        "mom_x": mom_x,
        "mom_y": mom_y,
        "star": star,
    }


# ----------------------------------------------------------------------
# ExoFOP JSON parser
# ----------------------------------------------------------------------
def parse_exofop_json(path: str) -> dict:
    """
    Accepts:
      (a) an ExoFOP-TESS .customization JSON download (target metadata only,
          no light curve);
      (b) any custom JSON the user assembles, with keys 't', 'flux',
          'flux_err' (and optional 'quality', 'mom_x', 'mom_y', 'star').

    For (a), star info is extracted but the caller is responsible for noting
    that no time series is present and either pairing it with a FITS upload
    or running in metadata-only mode.
    """
    with open(path, "rb") as fh:
        raw = fh.read()

    # Try utf-8 then latin-1
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")

    data = json.loads(text)

    # Case (b): explicit arrays present
    if isinstance(data, dict) and "t" in data and "flux" in data:
        s = data.get("star", {}) or {}
        star = StarInfo(
            tic_id=s.get("tic_id"),
            tmag=s.get("tmag"),
            teff=s.get("teff"),
            radius=s.get("radius"),
            logg=s.get("logg"),
            mass=s.get("mass"),
            ra=s.get("ra"),
            dec=s.get("dec"),
            sector=s.get("sector"),
            crowdsap=s.get("crowdsap"),
            source="exofop_json",
        )
        return {
            "t": np.asarray(data["t"], dtype=float),
            "flux": np.asarray(data["flux"], dtype=float),
            "flux_err": np.asarray(
                data.get("flux_err", [np.nan] * len(data["t"])), dtype=float
            ),
            "quality": np.asarray(data["quality"], dtype=int) if "quality" in data else None,
            "mom_x": np.asarray(data["mom_x"], dtype=float) if "mom_x" in data else None,
            "mom_y": np.asarray(data["mom_y"], dtype=float) if "mom_y" in data else None,
            "star": star,
        }

    # Case (a): ExoFOP customization — just metadata.
    # The schema varies, so we just try common field names.
    def dig(*keys, default=None):
        cur = data
        for k in keys:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                return default
        return cur

    star = StarInfo(
        tic_id=dig("target", "tic_id") or dig("tic_id"),
        tmag=dig("target", "tmag") or dig("Tmag"),
        teff=dig("target", "teff") or dig("Teff"),
        radius=dig("target", "radius") or dig("Radius"),
        logg=dig("target", "logg") or dig("logg"),
        ra=dig("target", "ra") or dig("RA"),
        dec=dig("target", "dec") or dig("Dec"),
        source="exofop_json_metadata_only",
    )
    return {
        "t": None,
        "flux": None,
        "flux_err": None,
        "quality": None,
        "mom_x": None,
        "mom_y": None,
        "star": star,
        "metadata_only": True,
    }


def parse_upload(path: str, filename: str) -> dict:
    """Dispatch by extension."""
    name = filename.lower()
    if name.endswith(".fits") or name.endswith(".fits.gz"):
        return parse_lightcurve_fits(path)
    if name.endswith(".json") or name.endswith(".customization"):
        return parse_exofop_json(path)
    raise ValueError(
        f"Unsupported file type: {filename}. Accepted: .fits, .fits.gz, .json, .customization"
    )
