"""Cross-match a target against external catalogs (Gaia DR3, SIMBAD,
NASA Exoplanet Archive, exoplanets.org) and apply known-object overrides.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ..timeouts import collect_within, submit


# ----------------------------------------------------------------------
# External catalog cross-match (Gaia DR3 + SIMBAD + exoplanets.org)
# ----------------------------------------------------------------------
# Cone-search radius for matching a TIC position to a Gaia DR3 / SIMBAD /
# exoplanets.org entry. TESS pixels are ~21", so a few arcsec is plenty for
# an unambiguous match on the target star itself without dragging in nearby
# blends.
CROSSMATCH_RADIUS_ARCSEC = 5.0

# Hard wall-clock budget for the remote catalog queries. On expiry the
# slow source is reported as an error and the verdict stands unchanged.
CROSSMATCH_TIMEOUT_S = 30.0

# exoplanets.org full-catalog CSV. The site has been static since ~2018 but
# remains the canonical machine-readable mirror of the original Exoplanet
# Orbit Database. We pull the whole table once per process and cone-search
# in memory — it's only ~5k rows.
_EXOPLANETS_ORG_CSV_URL = "http://exoplanets.org/csv-files/exoplanets.csv"
_EXOPLANETS_ORG_CACHE: Optional[object] = None  # cached column dict, or False on failure

# NASA Exoplanet Archive TAP service. Unlike exoplanets.org this is actively
# maintained, so we use it as the authoritative confirmed-planet source and
# fall back to exoplanets.org only when the live query fails.
_NEA_TAP_SYNC = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"

# SIMBAD object-type prefixes that we treat as "known" classifications. The
# value is the verdict headline we substitute in when one is matched.
# Order matters: planet host beats binary beats variable, because a confirmed
# exoplanet host is the more specific (and more useful) label. Matching is by
# prefix, so a longer otype must precede any shorter one it starts with
# ("Pl?" before "Pl"), or a SIMBAD candidate is read as a confirmed planet.
_SIMBAD_OTYPE_MAP = [
    # Exoplanet-related
    ("Pl?", "Known Planet Candidate"),
    ("Pl",  "Known Planet"),         # confirmed exoplanet
    # Eclipsing binaries
    ("EB*", "Known Eclipsing Binary"),
    ("Al*", "Known Eclipsing Binary (Algol)"),
    ("bL*", "Known Eclipsing Binary (β Lyr)"),
    ("WU*", "Known Eclipsing Binary (W UMa)"),
    # Other binaries
    ("SB*", "Known Spectroscopic Binary"),
    ("**",  "Known Multiple Star"),
    # Variables
    ("RR*", "Known RR Lyrae Variable"),
    ("Ce*", "Known Cepheid Variable"),
    ("dS*", "Known δ Scuti Variable"),
    ("Mi*", "Known Mira Variable"),
    ("V*",  "Known Variable Star"),
    ("CV*", "Known Cataclysmic Variable"),
]

# Catalogue labels that are themselves unconfirmed. They are reported as a
# reason but never replace the pipeline verdict: a SIMBAD "Pl?" entry is just
# a TOI, and TOIs are regularly retired as false positives / EBs.
_UNCONFIRMED_HEADLINES = {"Known Planet Candidate"}


def _gaia_nss_description(nss_flag) -> Optional[str]:
    """Translate a Gaia DR3 `non_single_star` integer into a human label.

    The DR3 field is a bitmask: 1=astrometric, 2=spectroscopic, 4=eclipsing.
    Combinations (e.g. 3 = astrometric+spectroscopic) are common. Returns
    None when the flag is 0 / missing (Gaia has no NSS solution).
    """
    try:
        flag = int(nss_flag)
    except (TypeError, ValueError):
        return None
    if flag <= 0:
        return None
    parts = []
    if flag & 1:
        parts.append("astrometric")
    if flag & 2:
        parts.append("spectroscopic")
    if flag & 4:
        parts.append("eclipsing")
    return ("Gaia DR3 NSS solution: " + " + ".join(parts)) if parts else None


def _row_get(row, *names):
    """Return the first column present in a row, case-insensitively.

    astroquery flipped SIMBAD column casing (``MAIN_ID`` → ``main_id``) in
    0.4.8; we accept either so the cross-match keeps working across versions.
    """
    try:
        cols = row.colnames
    except AttributeError:
        return None
    lower_map = {c.lower(): c for c in cols}
    for n in names:
        if n in cols:
            val = row[n]
        elif n.lower() in lower_map:
            val = row[lower_map[n.lower()]]
        else:
            continue
        if val is None:
            return None
        s = str(val).strip()
        return s if s and s.lower() != "nan" else None
    return None


def _parse_exoplanets_org_csv(lines) -> Optional[dict]:
    """Keep only what the cone search needs from the exoplanets.org CSV.

    The full table has ~5k rows and dozens of columns (~11 MB of text);
    reading it all with astropy's pure-Python CSV reader built hundreds of
    thousands of string objects (well over 100 MB transient, ~80 MB cached)
    — enough to push a 512 MB instance over the edge mid-vet. Streaming it
    through ``csv`` and keeping five columns costs well under 1 MB.

    Returns ``{"ra", "dec": float arrays, "name", "host", "period": lists of
    str|None}`` with only rows that have finite coordinates, or ``None`` if
    the header lacks RA/DEC.
    """
    import csv

    def _clean(v):
        s = (v or "").strip()
        return s if s and s.lower() != "nan" else None

    reader = csv.reader(lines)
    header = next(reader, None)
    if not header:
        return None
    idx = {h.strip().upper(): i for i, h in enumerate(header)}
    if "RA" not in idx or "DEC" not in idx:
        return None
    i_ra, i_dec = idx["RA"], idx["DEC"]
    i_name = idx.get("NAME")
    i_host = idx.get("STAR", idx.get("HOSTNAME"))
    i_per = idx.get("PER")

    ra, dec, name, host, period = [], [], [], [], []
    for row in reader:
        try:
            r, d = float(row[i_ra]), float(row[i_dec])
        except (IndexError, ValueError):
            continue
        if not (math.isfinite(r) and math.isfinite(d)):
            continue
        get = lambda i: _clean(row[i]) if i is not None and i < len(row) else None  # noqa: E731
        ra.append(r)
        dec.append(d)
        name.append(get(i_name))
        host.append(get(i_host))
        period.append(get(i_per))
    return {
        "ra": np.array(ra, dtype=float),
        "dec": np.array(dec, dtype=float),
        "name": name,
        "host": host,
        "period": period,
    }


def _load_exoplanets_org_table() -> Optional[dict]:
    """Download the exoplanets.org catalog once and cache the columns the
    cone search needs (see ``_parse_exoplanets_org_csv``).

    Returns ``None`` on failure (offline, HTTP error, parse error). Cached
    for the process lifetime.
    """
    global _EXOPLANETS_ORG_CACHE
    if _EXOPLANETS_ORG_CACHE is not None:
        return _EXOPLANETS_ORG_CACHE if _EXOPLANETS_ORG_CACHE is not False else None
    try:
        import io
        from urllib.request import Request, urlopen
        req = Request(
            _EXOPLANETS_ORG_CSV_URL,
            headers={"User-Agent": "vetstar/known-obj-fix"},
        )
        with urlopen(req, timeout=20) as resp:
            # Stream line by line instead of holding the whole body.
            tbl = _parse_exoplanets_org_csv(
                io.TextIOWrapper(resp, encoding="utf-8", errors="replace", newline="")
            )
        _EXOPLANETS_ORG_CACHE = tbl if tbl is not None else False
        return tbl
    except Exception:
        _EXOPLANETS_ORG_CACHE = False
        return None


def _query_exoplanets_org(ra: float, dec: float, radius_arcsec: float) -> Optional[dict]:
    """Cone-search exoplanets.org for a confirmed planet host at (ra, dec).

    Returns ``None`` if the catalog can't be loaded. Returns ``{"matched":
    False, ...}`` if no row falls within the radius, otherwise the closest
    match with its planet name, host star, period, and separation.
    """
    tbl = _load_exoplanets_org_table()
    if tbl is None:
        return {"error": "exoplanets.org catalog unavailable"}

    ra_col, dec_col = tbl["ra"], tbl["dec"]
    if ra_col.size == 0:
        return {"matched": False, "n_rows": 0}

    # Small-angle haversine for cone search; fine for arcsec-scale radii.
    cos_dec = math.cos(math.radians(dec))
    dra = (ra_col - ra) * cos_dec
    ddec = dec_col - dec
    sep_arcsec = np.sqrt(dra * dra + ddec * ddec) * 3600.0

    idx = int(np.argmin(sep_arcsec))
    best = float(sep_arcsec[idx])
    if best > radius_arcsec:
        return {"matched": False, "closest_arcsec": best}

    return {
        "matched": True,
        "name": tbl["name"][idx] or "exoplanets.org candidate",
        "host": tbl["host"][idx],
        "period_days": tbl["period"][idx],
        "dist_arcsec": best,
    }


def _query_nasa_exoplanet_archive(ra: float, dec: float, radius_arcsec: float) -> Optional[dict]:
    """Cone-search the NASA Exoplanet Archive ``ps`` (planetary systems) table.

    Uses the public TAP sync endpoint with an ADQL CONTAINS query so we only
    pull rows actually inside the radius. Returns ``{"matched": False}`` when
    no rows come back, or an error dict on network/parse failure.
    """
    try:
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen
        from astropy.io import ascii as ascii_io
        radius_deg = radius_arcsec / 3600.0
        adql = (
            "SELECT TOP 1 pl_name, hostname, ra, dec, pl_orbper, "
            "DISTANCE(POINT('ICRS', ra, dec), "
            f"POINT('ICRS', {ra}, {dec})) * 3600 AS dist_arcsec "
            "FROM ps "
            f"WHERE CONTAINS(POINT('ICRS', ra, dec), "
            f"CIRCLE('ICRS', {ra}, {dec}, {radius_deg})) = 1 "
            "ORDER BY dist_arcsec ASC"
        )
        url = _NEA_TAP_SYNC + "?" + urlencode({
            "query": adql,
            "format": "csv",
        })
        req = Request(url, headers={"User-Agent": "vetstar/known-obj-fix"})
        with urlopen(req, timeout=20) as resp:
            data = resp.read().decode("utf-8", errors="replace")
        tbl = ascii_io.read(data, format="csv", fast_reader=False)
        if len(tbl) == 0:
            return {"matched": False}
        row = tbl[0]
        return {
            "matched": True,
            "name":   _row_get(row, "pl_name"),
            "host":   _row_get(row, "hostname"),
            "period_days": _row_get(row, "pl_orbper"),
            "dist_arcsec": float(row["dist_arcsec"]) if "dist_arcsec" in row.colnames else None,
        }
    except Exception as exc:
        return {"error": str(exc)}


def _query_gaia_dr3(ra: float, dec: float, radius_arcsec: float) -> Optional[dict]:
    """Nearest Gaia DR3 source with its non-single-star flag, or None."""
    from astroquery.gaia import Gaia
    Gaia.ROW_LIMIT = 5
    adql = (
        "SELECT TOP 1 source_id, ra, dec, phot_g_mean_mag, "
        "non_single_star, "
        "DISTANCE(POINT('ICRS', ra, dec), "
        f"POINT('ICRS', {ra}, {dec})) * 3600 AS dist_arcsec "
        "FROM gaiadr3.gaia_source "
        f"WHERE 1=CONTAINS(POINT('ICRS', ra, dec), "
        f"CIRCLE('ICRS', {ra}, {dec}, {radius_arcsec / 3600.0})) "
        "ORDER BY dist_arcsec ASC"
    )
    # Synchronous job: a TOP 1 cone search is tiny, and the async variant
    # polls the archive's job queue with no upper bound.
    tbl = Gaia.launch_job(adql).get_results()
    if len(tbl) == 0:
        return None
    row = tbl[0]
    return {
        "source_id": int(row["source_id"]),
        "dist_arcsec": float(row["dist_arcsec"]),
        "g_mag": float(row["phot_g_mean_mag"]) if row["phot_g_mean_mag"] is not None else None,
        "nss_flag": int(row["non_single_star"]) if row["non_single_star"] is not None else 0,
        "nss_description": _gaia_nss_description(row["non_single_star"]),
    }


def _query_simbad(coord, radius) -> Optional[dict]:
    """Nearest SIMBAD object with its otype/ids, or None."""
    from astroquery.simbad import Simbad
    sb = Simbad()
    sb.TIMEOUT = 15
    # otype is included by default in newer astroquery; ids is opt-in.
    # Wrap each in try/except so version drift doesn't crash the query.
    for field_name in ("otype", "ids"):
        try:
            sb.add_votable_fields(field_name)
        except Exception:
            pass
    tbl = sb.query_region(coord, radius=radius)
    if tbl is None or len(tbl) == 0:
        return None
    row = tbl[0]
    return {
        "main_id": _row_get(row, "MAIN_ID", "main_id"),
        "otype":   _row_get(row, "OTYPE", "otype") or "",
        "ids":     _row_get(row, "IDS", "ids") or "",
    }


def crossmatch_known_object(
    ra: Optional[float],
    dec: Optional[float],
    radius_arcsec: float = CROSSMATCH_RADIUS_ARCSEC,
) -> dict:
    """Cone-search Gaia DR3 and SIMBAD for a known object at (ra, dec).

    Returns ``{"available": False, ...}`` when coordinates are missing or
    every external query fails (offline, timeout, service down) — the
    caller should leave the pipeline verdict untouched in that case.

    A successful match returns:
        {
          "available": True,
          "matched":   True/False,
          "headline":  "Known Eclipsing Binary" | ...   (only if matched)
          "name":      "TIC 12345 / HD 6789"            (only if matched)
          "description": "Gaia DR3 NSS: eclipsing; SIMBAD otype EB*"
          "sources":   ["Gaia DR3", "SIMBAD"],
          "distance_arcsec": 0.7,
        }
    """
    if ra is None or dec is None or not np.isfinite(ra) or not np.isfinite(dec):
        return {"available": False, "reason": "no coordinates"}

    # Lazy imports so a missing/offline astroquery doesn't break unit tests
    # of the rest of the pipeline.
    try:
        from astropy import units as u
        from astropy.coordinates import SkyCoord
    except Exception as exc:  # pragma: no cover
        return {"available": False, "reason": f"astropy import failed: {exc}"}

    coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")
    radius = radius_arcsec * u.arcsec

    sources_tried = ["Gaia DR3", "SIMBAD", "NASA Exoplanet Archive"]

    # Run the remote queries concurrently under one wall-clock budget.
    # Neither astroquery's Gaia async TAP job nor its pyvo-backed SIMBAD
    # query honours a timeout, so a slow/queued service used to block the
    # job indefinitely (and the SSE stream then gave up after 10 min).
    futs = {
        "gaia": submit(_query_gaia_dr3, ra, dec, radius_arcsec),
        "simbad": submit(_query_simbad, coord, radius),
        "nea": submit(_query_nasa_exoplanet_archive, ra, dec, radius_arcsec),
    }
    results = collect_within(futs, CROSSMATCH_TIMEOUT_S)
    gaia_match = results["gaia"]
    simbad_match = results["simbad"]
    nea_match = results["nea"]
    exoplanets_org_match = None

    # ----- exoplanets.org (fallback / corroborating) ------------------
    # Only hit exoplanets.org when NEA didn't give us a confirmed match —
    # NEA is the authoritative live source, the .org mirror is frozen at 2018.
    if not (nea_match and nea_match.get("matched")):
        sources_tried.append("exoplanets.org")
        fut = submit(_query_exoplanets_org, ra, dec, radius_arcsec)
        exoplanets_org_match = collect_within(
            {"org": fut}, CROSSMATCH_TIMEOUT_S
        )["org"]

    # ----- Decide whether this counts as a "known" classification -----
    headline = None
    description_bits = []
    name_bits = []

    # Confirmed-planet sources are the most specific (and outrank SIMBAD's
    # generic otype). NEA is authoritative; exoplanets.org is the legacy
    # mirror used only when NEA didn't return a hit.
    for src_label, m in (
        ("NASA Exoplanet Archive", nea_match),
        ("exoplanets.org",         exoplanets_org_match),
    ):
        if m and m.get("matched"):
            headline = "Known Planet"
            bits = [f"{src_label}: {m.get('name')}"]
            if m.get("period_days"):
                bits.append(f"P={m['period_days']} d")
            description_bits.append("; ".join(bits))
            if m.get("host"):
                name_bits.append(m["host"])
            elif m.get("name"):
                name_bits.append(m["name"])
            break

    if simbad_match and "otype" in simbad_match:
        otype = simbad_match["otype"]
        for prefix, label in _SIMBAD_OTYPE_MAP:
            if otype.startswith(prefix):
                # Don't overwrite the more-specific exoplanets.org label.
                if headline is None:
                    headline = label
                description_bits.append(f"SIMBAD object type '{otype}'")
                break
        if simbad_match.get("main_id"):
            name_bits.append(simbad_match["main_id"])

    if gaia_match and gaia_match.get("nss_description"):
        # Gaia NSS solution always implies binarity. Only upgrade the
        # headline if SIMBAD didn't already give us something more
        # specific (e.g. confirmed planet host).
        if headline is None or headline.startswith("Known Variable"):
            headline = "Known Binary"
        description_bits.append(gaia_match["nss_description"])
        name_bits.append(f"Gaia DR3 {gaia_match['source_id']}")

    matched = headline is not None
    out = {
        "available": True,
        "matched": matched,
        "sources_tried": sources_tried,
        "gaia": gaia_match,
        "simbad": simbad_match,
        "exoplanets_org": exoplanets_org_match,
        "nasa_exoplanet_archive": nea_match,
    }
    if matched:
        out["headline"] = headline
        # De-dupe while preserving order.
        seen = set()
        unique_names = [n for n in name_bits if n and not (n in seen or seen.add(n))]
        out["name"] = " / ".join(unique_names) if unique_names else None
        out["description"] = "; ".join(description_bits)
        # Closest-match distance: prefer Gaia (sub-arcsec), then NEA, then
        # the legacy exoplanets.org cone-search separation.
        if gaia_match and "dist_arcsec" in gaia_match:
            out["distance_arcsec"] = gaia_match["dist_arcsec"]
        elif nea_match and nea_match.get("dist_arcsec") is not None:
            out["distance_arcsec"] = nea_match["dist_arcsec"]
        elif exoplanets_org_match and exoplanets_org_match.get("dist_arcsec") is not None:
            out["distance_arcsec"] = exoplanets_org_match["dist_arcsec"]
    return out


def apply_known_object_override(verdict: dict, known: dict) -> dict:
    """Fold a ``crossmatch_known_object`` result into ``verdict`` in place.

    A confirmed catalogue classification replaces the headline at 0.99
    confidence. An unconfirmed one (SIMBAD "Pl?") only adds a reason: the
    pipeline's own verdict stands.
    """
    if not known.get("matched"):
        return verdict
    headline = known["headline"]
    where = (
        (f" — {known['name']}" if known.get("name") else "")
        + (f" ({known['description']})" if known.get("description") else "")
        + (
            f" at {known['distance_arcsec']:.2f}\" from target"
            if known.get("distance_arcsec") is not None else ""
        )
    )
    if headline in _UNCONFIRMED_HEADLINES:
        verdict.setdefault("reasons", []).insert(
            0, f"Catalog note: {headline}{where}; unconfirmed, verdict not overridden."
        )
        verdict.setdefault("flags", []).append("known_object_candidate")
        return verdict
    verdict["original_headline"] = verdict.get("headline")
    verdict["original_category"] = verdict.get("category")
    verdict["headline"] = headline
    verdict["category"] = "known_object"
    verdict["confidence"] = 0.99
    verdict.setdefault("reasons", []).insert(0, f"Catalog override: {headline}{where}.")
    verdict.setdefault("flags", []).append("known_object_override")
    return verdict
