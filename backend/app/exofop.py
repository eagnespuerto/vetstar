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

    # Stellar parameters.
    #
    # ExoFOP returns "stellar_parameters" as a list whose first element is a
    # "provenance" header ({prov, prov_title, prov_num}) and whose subsequent
    # elements are the actual data rows from each contributing source. Picking
    # sp[0] blindly grabs the header and yields no stellar values, so we walk
    # the list and merge across rows, taking the first non-empty value for
    # each field. (Field names per ExoFOP: teff, srad, mass, logg, dist; the
    # st_* aliases are kept for forward compatibility with TIC v8 JSON.)
    raw_sp = data.get("stellar_parameters") or {}
    if isinstance(raw_sp, dict):
        sp_rows = [raw_sp]
    elif isinstance(raw_sp, list):
        sp_rows = [r for r in raw_sp if isinstance(r, dict) and "prov_num" not in r]
    else:
        sp_rows = []

    def _pick(*keys):
        for row in sp_rows:
            for k in keys:
                v = _f(row.get(k))
                if v is not None:
                    return v
        return None

    star = {
        "teff":     _pick("teff", "st_teff"),
        "radius":   _pick("srad", "st_rad", "rad"),
        "mass":     _pick("mass", "smass", "st_mass"),
        "logg":     _pick("logg", "slogg", "st_logg"),
        "tmag":     _pick("tmag"),
        "ra":       _pick("ra"),
        "dec":      _pick("dec"),
        "distance": _pick("dist", "distance", "st_dist"),
    }

    tois = _parse_tois(data, star)

    return {"star": star, "tois": tois, "source": "exofop"}


def _toi_key(v) -> str:
    """Normalise "TOI 324.01" / "TOI-324.01" / "324.01" / 324.01 to "324.01"."""
    s = str(v or "").strip().upper()
    for prefix in ("TOI-", "TOI "):
        if s.startswith(prefix):
            s = s[len(prefix):]
    return s.strip().strip(".")


def _first(row: dict, *keys):
    for k in keys:
        v = _f(row.get(k))
        if v is not None:
            return v
    return None


def _parse_tois(data: dict, star: dict) -> list:
    """Build the TOI list from an ExoFOP target JSON.

    ``planet_parameters`` is a flat list in which provenance header rows
    ({prov, prov_title, prov_num}) introduce the data rows that follow. Only
    rows under the ``prov == "toi"`` header (the TESS project's own TOI
    values) are used: user-uploaded rows (``prov == "ctoi"``) mix in
    community fits and vetstar's own uploads, which would make the catalogue
    cross-check circular. Data rows use ``per`` (d), ``dep_p`` (ppm),
    ``dur`` (h), ``rad`` (R_E) and ``sma`` (AU); legacy ``period`` /
    ``depth`` / ``prad`` / ``pl_*`` names are kept as fallbacks.

    Dispositions are not in those rows: they come from the top-level ``tois``
    list as ``disp_tfop`` (TFOP working group, preferred) and ``disp_tess``
    (TESS project), keyed by the same "TOI nnn.nn" string.
    """
    raw_pp = data.get("planet_parameters") or []
    if isinstance(raw_pp, dict):
        raw_pp = list(raw_pp.values())
    raw_tois = data.get("tois") or []
    if isinstance(raw_tois, dict):
        raw_tois = list(raw_tois.values())

    params: dict = {}
    prov = "toi"    # header-less (legacy) lists are treated as TESS-project rows
    for row in (raw_pp if isinstance(raw_pp, list) else []):
        if not isinstance(row, dict):
            continue
        if "prov_num" in row:
            prov = str(row.get("prov") or "").lower()
            continue
        key = _toi_key(row.get("toi") or row.get("toi_id"))
        if prov != "toi" or not key:
            continue
        params.setdefault(key, row)

    meta: dict = {}
    for row in (raw_tois if isinstance(raw_tois, list) else []):
        if isinstance(row, dict):
            key = _toi_key(row.get("toi") or row.get("toi_id"))
            if key:
                meta.setdefault(key, row)

    tois = []
    for key in list(dict.fromkeys([*params, *meta])):
        p = params.get(key, {})
        m = meta.get(key, {})
        period = _first(p, "per", "period", "pl_orbper")
        depth = _first(p, "dep_p")
        if depth is None:
            depth = _first(p, "depth", "dep_ppm", "pl_trandep")
            if depth and depth < 100:
                depth = depth * 1e4      # legacy fields were sometimes percent
        a_au = _first(p, "sma", "a", "pl_orbsmax")
        a_source = "exofop" if a_au is not None else None
        # Derive a_au via Kepler's 3rd law if absent
        if a_au is None and period and star.get("mass"):
            try:
                a_au = round((star["mass"] * (period / 365.25) ** 2) ** (1 / 3), 5)
                a_source = "Kepler III (ExoFOP P + M*)"
            except Exception:
                pass
        disp_tfop = str(m.get("disp_tfop") or "").strip().upper()
        disp_tess = str(m.get("disp_tess") or "").strip().upper()
        legacy = str(p.get("disposition") or p.get("tfopwg_disp") or p.get("disp") or "").strip().upper()
        raw_sec = str(m.get("sector") or p.get("sectors") or p.get("sectors_observed") or "")
        sectors = [s.strip() for s in raw_sec.replace(";", ",").split(",")
                   if s.strip().lstrip("-").isdigit()]
        tois.append({
            "toi_number":        key,
            "period_d":          period,
            "depth_ppm":         depth,
            "duration_hr":       _first(p, "dur", "duration", "pl_trandur"),
            "radius_earth":      _first(p, "rad", "prad", "pl_rade"),
            "semi_major_axis_au": a_au,
            "semi_major_axis_source": a_source,
            "disposition":       disp_tfop or disp_tess or legacy or None,
            "disposition_tess":  disp_tess or None,
            "notes":             (m.get("notes") or None),
            "sectors":           sectors,
        })
    return tois


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
