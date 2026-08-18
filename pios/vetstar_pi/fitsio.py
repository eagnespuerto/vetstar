"""FITS / CSV / JSON light-curve readers for the Pi port.

Kept dependency-light on purpose: only astropy.io.fits for FITS, numpy + csv
for the flat-file formats. No astroquery / MAST fetching — the Pi port is
strictly offline; the user supplies the file.
"""
from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class StarInfo:
    tic_id: Optional[int] = None
    tmag: Optional[float] = None
    teff: Optional[float] = None
    radius: Optional[float] = None
    logg: Optional[float] = None
    mass: Optional[float] = None
    ra: Optional[float] = None
    dec: Optional[float] = None
    sector: Optional[int] = None
    camera: Optional[int] = None
    ccd: Optional[int] = None
    crowdsap: Optional[float] = None
    source: str = "unknown"


@dataclass
class LightCurve:
    t: np.ndarray
    flux: np.ndarray
    flux_err: np.ndarray
    quality: Optional[np.ndarray] = None
    mom_x: Optional[np.ndarray] = None
    mom_y: Optional[np.ndarray] = None
    star: StarInfo = field(default_factory=StarInfo)


# ----------------------------------------------------------------------
# FITS reader — TESS SPOC / Kepler / QLP / CDIPS, tries columns in order
# ----------------------------------------------------------------------
_FLUX_COLS = [
    ("PDCSAP_FLUX", "PDCSAP_FLUX_ERR"),
    ("SAP_FLUX", "SAP_FLUX_ERR"),
    ("KSPSAP_FLUX", "KSPSAP_FLUX_ERR"),
    ("DET_FLUX", "DET_FLUX_ERR"),
    ("FLUX", "FLUX_ERR"),
]


def read_fits(path: str) -> LightCurve:
    """Return a :class:`LightCurve` from a TESS/Kepler SPOC-style FITS file."""
    from astropy.io import fits

    with fits.open(path, memmap=True) as hdul:
        h0 = hdul[0].header
        h1 = hdul[1].header
        d = hdul[1].data
        cols = set(d.columns.names)

        flux_col = err_col = None
        for fc, ec in _FLUX_COLS:
            if fc in cols:
                flux_col, err_col = fc, ec
                break
        if flux_col is None:
            raise ValueError(
                f"No recognised flux column in {os.path.basename(path)}. "
                f"Looked for: {[c for c, _ in _FLUX_COLS]}. Found: {sorted(cols)}"
            )

        t = np.asarray(d["TIME"], dtype=float)
        flux = np.asarray(d[flux_col], dtype=float)
        if err_col in cols:
            flux_err = np.asarray(d[err_col], dtype=float)
        else:
            flux_err = np.full_like(flux, np.nan)
        quality = np.asarray(d["QUALITY"], dtype=int) if "QUALITY" in cols else None
        mom_x = np.asarray(d["MOM_CENTR1"], dtype=float) if "MOM_CENTR1" in cols else None
        mom_y = np.asarray(d["MOM_CENTR2"], dtype=float) if "MOM_CENTR2" in cols else None

        def g(key):
            for h in (h0, h1):
                if key in h and h[key] not in ("", None):
                    return h[key]
            return None

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

    return LightCurve(
        t=t, flux=flux, flux_err=flux_err, quality=quality,
        mom_x=mom_x, mom_y=mom_y, star=star,
    )


# ----------------------------------------------------------------------
# Flat-file readers (microlensing pipeline mainly consumes these)
# ----------------------------------------------------------------------
def read_csv(path: str) -> LightCurve:
    """Read a CSV with headers ``time,flux,flux_err`` (case-insensitive).

    Extra columns are ignored. Missing ``flux_err`` -> np.nan (a scatter
    estimate is filled in when the pipeline cleans the arrays).
    """
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"{path}: empty file")
        lower = {n.lower(): n for n in reader.fieldnames}
        if "time" not in lower or "flux" not in lower:
            raise ValueError(
                f"{path}: need at least 'time' and 'flux' columns; "
                f"got {reader.fieldnames}"
            )
        tk_, fk = lower["time"], lower["flux"]
        ek = lower.get("flux_err")
        ts, fs, es = [], [], []
        for row in reader:
            try:
                ts.append(float(row[tk_]))
                fs.append(float(row[fk]))
                es.append(float(row[ek]) if ek and row[ek] not in ("", None) else np.nan)
            except (TypeError, ValueError):
                continue
    t = np.asarray(ts, dtype=float)
    flux = np.asarray(fs, dtype=float)
    flux_err = np.asarray(es, dtype=float)
    return LightCurve(t=t, flux=flux, flux_err=flux_err, star=StarInfo(source="csv"))


def read_json(path: str) -> LightCurve:
    """Read a JSON blob with keys ``t``, ``flux``, and optionally ``flux_err``,
    ``quality``, ``mom_x``, ``mom_y``, ``star``.
    """
    with open(path, "rb") as fh:
        data = json.loads(fh.read().decode("utf-8", errors="replace"))
    if "t" not in data or "flux" not in data:
        raise ValueError(f"{path}: JSON must contain 't' and 'flux' arrays.")
    t = np.asarray(data["t"], dtype=float)
    flux = np.asarray(data["flux"], dtype=float)
    flux_err = np.asarray(
        data.get("flux_err", [np.nan] * len(t)), dtype=float
    )
    quality = np.asarray(data["quality"], dtype=int) if "quality" in data else None
    mom_x = np.asarray(data["mom_x"], dtype=float) if "mom_x" in data else None
    mom_y = np.asarray(data["mom_y"], dtype=float) if "mom_y" in data else None
    s = data.get("star", {}) or {}
    star = StarInfo(
        tic_id=s.get("tic_id"), tmag=s.get("tmag"), teff=s.get("teff"),
        radius=s.get("radius"), logg=s.get("logg"), mass=s.get("mass"),
        ra=s.get("ra"), dec=s.get("dec"), sector=s.get("sector"),
        crowdsap=s.get("crowdsap"), source="json",
    )
    return LightCurve(
        t=t, flux=flux, flux_err=flux_err, quality=quality,
        mom_x=mom_x, mom_y=mom_y, star=star,
    )


def read_any(path: str) -> LightCurve:
    """Dispatch by extension: .fits/.fits.gz → :func:`read_fits`,
    .csv → :func:`read_csv`, .json → :func:`read_json`.
    """
    name = path.lower()
    if name.endswith(".fits") or name.endswith(".fits.gz"):
        return read_fits(path)
    if name.endswith(".csv"):
        return read_csv(path)
    if name.endswith(".json"):
        return read_json(path)
    raise ValueError(
        f"Unsupported extension for {path}. Accepted: .fits, .fits.gz, .csv, .json"
    )
