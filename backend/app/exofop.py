"""
ExoFOP-TESS querier.

ExoFOP does not publish a formal public API, but exposes a JSON endpoint
(used by its own front-end) that returns TOI and stellar data per TIC:

  https://exofop.ipac.caltech.edu/tess/target.php?id={TIC}&json

This endpoint is not officially documented but has been stable since 2019
and is widely used by the exoplanet community.  It returns an HTML error
page for unknown TICs, which we detect and handle gracefully.

We fall back to the TIC v8 catalog via astroquery when ExoFOP is
unreachable or returns no useful data.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

log = logging.getLogger(__name__)


def _f(v, default=None) -> Optional[float]:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def query_exofop(tic_id: int) -> dict:
    """
    Returns:
      {
        "star":   {teff, radius, mass, logg, ra, dec, tmag, distance},
        "tois":   [{toi_number, period_d, depth_ppm, duration_hr,
                    radius_earth, semi_major_axis_au, disposition, sectors}, ...],
        "source": "exofop" | "tic_catalog" | "unavailable",
      }
    """
    result = _from_exofop(tic_id)
    if result is not None:
        return result
    return _from_tic_catalog(tic_id)


def _from_exofop(tic_id: int) -> Optional[dict]:
    try:
        import json
        import ssl
        import urllib.request

        url = f"https://exofop.ipac.caltech.edu/tess/target.php?id={tic_id}&json"
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(url, timeout=12, context=ctx) as resp:
            raw = resp.read()
        if raw[:10].strip().startswith((b"<!", b"<h", b"<H")):
            return None   # HTML error page — TIC not in ExoFOP
        data = json.loads(raw)
    except Exception as e:
        log.info("ExoFOP fetch failed for TIC %s: %s", tic_id, e)
        return None

    # Stellar parameters
    sp = data.get("stellar_parameters") or {}
    if isinstance(sp, list) and sp:
        sp = sp[0]
    star = {}
    if isinstance(sp, dict):
        star = {
            "teff":     _f(sp.get("teff") or sp.get("st_teff")),
            "radius":   _f(sp.get("srad") or sp.get("st_rad")),
            "mass":     _f(sp.get("smass") or sp.get("st_mass")),
            "logg":     _f(sp.get("slogg") or sp.get("st_logg")),
            "tmag":     _f(sp.get("tmag")),
            "ra":       _f(sp.get("ra")),
            "dec":      _f(sp.get("dec")),
            "distance": _f(sp.get("distance") or sp.get("st_dist")),
        }

    # TOI entries
    tois = []
    raw_tois = data.get("planet_parameters") or data.get("tois") or []
    if isinstance(raw_tois, dict):
        raw_tois = list(raw_tois.values())
    for t in (raw_tois if isinstance(raw_tois, list) else []):
        if not isinstance(t, dict):
            continue
        toi_num = str(t.get("toi") or t.get("toi_id") or "").strip(".")
        if not toi_num:
            continue
        period = _f(t.get("period") or t.get("pl_orbper"))
        depth  = _f(t.get("depth") or t.get("dep_ppm") or t.get("pl_trandep"))
        if depth and depth < 100:
            depth = depth * 1e4          # ExoFOP sometimes returns percent
        dur  = _f(t.get("duration") or t.get("pl_trandur"))
        rp   = _f(t.get("prad") or t.get("pl_rade"))
        a_au = _f(t.get("a") or t.get("pl_orbsmax"))
        # Derive a_au via Kepler's 3rd law if absent
        if a_au is None and period and star.get("mass"):
            try:
                a_au = round((star["mass"] * (period / 365.25) ** 2) ** (1 / 3), 5)
            except Exception:
                pass
        disp = str(t.get("disposition") or t.get("tfopwg_disp") or "").strip().upper()
        raw_sec = str(t.get("sectors") or t.get("sectors_observed") or "")
        sectors = [s.strip() for s in raw_sec.replace(";", ",").split(",")
                   if s.strip().lstrip("-").isdigit()]
        tois.append({
            "toi_number":        toi_num,
            "period_d":          period,
            "depth_ppm":         depth,
            "duration_hr":       dur,
            "radius_earth":      rp,
            "semi_major_axis_au": a_au,
            "disposition":       disp or None,
            "sectors":           sectors,
        })

    return {"star": star, "tois": tois, "source": "exofop"}


def _from_tic_catalog(tic_id: int) -> dict:
    try:
        import numpy as np
        from astroquery.mast import Catalogs
        cat = Catalogs.query_criteria(catalog="TIC", ID=int(tic_id))
        if len(cat) == 0:
            return {"star": {}, "tois": [], "source": "unavailable"}

        def g(k):
            try:
                v = cat[0][k]
                return None if np.ma.is_masked(v) else float(v)
            except Exception:
                return None

        star = {
            "teff":   g("Teff"),
            "radius": g("rad"),
            "mass":   g("mass"),
            "logg":   g("logg"),
            "tmag":   g("Tmag"),
            "ra":     g("ra"),
            "dec":    g("dec"),
        }
        return {"star": star, "tois": [], "source": "tic_catalog"}
    except Exception as e:
        log.info("TIC catalog fallback failed for TIC %s: %s", tic_id, e)
        return {"star": {}, "tois": [], "source": "unavailable"}
