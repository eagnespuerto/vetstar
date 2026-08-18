"""
Vetting pipeline: BLS, Lomb-Scargle, centroid check, odd/even, secondary
eclipse search, shape analysis, verdict.

All functions are pure-ish — accept arrays + metadata, return dicts of
results.  Plotting helpers return base64-encoded PNGs so the API can ship
them straight to the frontend.
"""
from __future__ import annotations

import base64
import io
import math
from dataclasses import asdict, dataclass, field
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from astropy.timeseries import BoxLeastSquares, LombScargle
from scipy.ndimage import median_filter


# ----------------------------------------------------------------------
# External catalog cross-match (Gaia DR3 + SIMBAD + exoplanets.org)
# ----------------------------------------------------------------------
# Cone-search radius for matching a TIC position to a Gaia DR3 / SIMBAD /
# exoplanets.org entry. TESS pixels are ~21", so a few arcsec is plenty for
# an unambiguous match on the target star itself without dragging in nearby
# blends.
CROSSMATCH_RADIUS_ARCSEC = 5.0

# exoplanets.org full-catalog CSV. The site has been static since ~2018 but
# remains the canonical machine-readable mirror of the original Exoplanet
# Orbit Database. We pull the whole table once per process and cone-search
# in memory — it's only ~5k rows.
_EXOPLANETS_ORG_CSV_URL = "http://exoplanets.org/csv-files/exoplanets.csv"
_EXOPLANETS_ORG_CACHE: Optional[object] = None  # cached astropy Table or False on failure

# NASA Exoplanet Archive TAP service. Unlike exoplanets.org this is actively
# maintained, so we use it as the authoritative confirmed-planet source and
# fall back to exoplanets.org only when the live query fails.
_NEA_TAP_SYNC = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"

# SIMBAD object-type prefixes that we treat as "known" classifications. The
# value is the verdict headline we substitute in when one is matched.
# Order matters: planet host beats binary beats variable, because a confirmed
# exoplanet host is the more specific (and more useful) label.
_SIMBAD_OTYPE_MAP = [
    # Exoplanet-related
    ("Pl",  "Known Planet"),         # confirmed exoplanet
    ("Pl?", "Known Planet Candidate"),
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


def _load_exoplanets_org_table():
    """Download the exoplanets.org catalog once and cache it.

    Returns an astropy Table on success, or ``None`` on failure (offline,
    HTTP error, parse error). Cached for the process lifetime.
    """
    global _EXOPLANETS_ORG_CACHE
    if _EXOPLANETS_ORG_CACHE is not None:
        return _EXOPLANETS_ORG_CACHE if _EXOPLANETS_ORG_CACHE is not False else None
    try:
        from urllib.request import Request, urlopen
        from astropy.io import ascii as ascii_io
        req = Request(
            _EXOPLANETS_ORG_CSV_URL,
            headers={"User-Agent": "vetstar/known-obj-fix"},
        )
        with urlopen(req, timeout=20) as resp:
            data = resp.read().decode("utf-8", errors="replace")
        tbl = ascii_io.read(data, format="csv", fast_reader=False)
        _EXOPLANETS_ORG_CACHE = tbl
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

    # exoplanets.org uses RA/DEC in degrees (columns "RA" and "DEC"); some
    # rows are blank. Build a numeric mask once.
    try:
        ra_col = np.array(tbl["RA"], dtype=float)
        dec_col = np.array(tbl["DEC"], dtype=float)
    except Exception:
        return {"error": "exoplanets.org missing RA/DEC columns"}

    finite = np.isfinite(ra_col) & np.isfinite(dec_col)
    if not finite.any():
        return {"matched": False, "n_rows": 0}

    # Small-angle haversine for cone search; fine for arcsec-scale radii.
    cos_dec = math.cos(math.radians(dec))
    dra = (ra_col - ra) * cos_dec
    ddec = dec_col - dec
    sep_arcsec = np.sqrt(dra * dra + ddec * ddec) * 3600.0
    sep_arcsec[~finite] = np.inf

    idx = int(np.argmin(sep_arcsec))
    best = float(sep_arcsec[idx])
    if best > radius_arcsec:
        return {"matched": False, "closest_arcsec": best}

    row = tbl[idx]
    name = _row_get(row, "NAME") or "exoplanets.org candidate"
    host = _row_get(row, "STAR", "HOSTNAME")
    period = _row_get(row, "PER")
    return {
        "matched": True,
        "name": name,
        "host": host,
        "period_days": period,
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

    sources_tried = []
    gaia_match = None
    simbad_match = None
    exoplanets_org_match = None
    nea_match = None

    # ----- Gaia DR3 ----------------------------------------------------
    try:
        from astroquery.gaia import Gaia
        sources_tried.append("Gaia DR3")
        Gaia.ROW_LIMIT = 5
        # gaiadr3.gaia_source contains non_single_star; we also pull the
        # in_qso_candidates / in_galaxy_candidates flags so we can flag
        # non-stellar matches.
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
        job = Gaia.launch_job_async(adql)
        tbl = job.get_results()
        if len(tbl) > 0:
            row = tbl[0]
            nss_desc = _gaia_nss_description(row["non_single_star"])
            gaia_match = {
                "source_id": int(row["source_id"]),
                "dist_arcsec": float(row["dist_arcsec"]),
                "g_mag": float(row["phot_g_mean_mag"]) if row["phot_g_mean_mag"] is not None else None,
                "nss_flag": int(row["non_single_star"]) if row["non_single_star"] is not None else 0,
                "nss_description": nss_desc,
            }
    except Exception as exc:  # network/service issue: keep going
        gaia_match = {"error": str(exc)}

    # ----- SIMBAD ------------------------------------------------------
    try:
        from astroquery.simbad import Simbad
        sources_tried.append("SIMBAD")
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
        if tbl is not None and len(tbl) > 0:
            row = tbl[0]
            simbad_match = {
                "main_id": _row_get(row, "MAIN_ID", "main_id"),
                "otype":   _row_get(row, "OTYPE", "otype") or "",
                "ids":     _row_get(row, "IDS", "ids") or "",
            }
    except Exception as exc:
        simbad_match = {"error": str(exc)}

    # ----- NASA Exoplanet Archive (live TAP) --------------------------
    try:
        sources_tried.append("NASA Exoplanet Archive")
        nea_match = _query_nasa_exoplanet_archive(ra, dec, radius_arcsec)
    except Exception as exc:
        nea_match = {"error": str(exc)}

    # ----- exoplanets.org (fallback / corroborating) ------------------
    # Only hit exoplanets.org when NEA didn't give us a confirmed match —
    # NEA is the authoritative live source, the .org mirror is frozen at 2018.
    if not (nea_match and nea_match.get("matched")):
        try:
            sources_tried.append("exoplanets.org")
            exoplanets_org_match = _query_exoplanets_org(ra, dec, radius_arcsec)
        except Exception as exc:
            exoplanets_org_match = {"error": str(exc)}

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


# ----------------------------------------------------------------------
# Tolerances
# ----------------------------------------------------------------------
# How far two transit/eclipse durations may differ (in HOURS) and still be
# treated as "the same" event — across cycles within a sector, or across
# sectors in a multi-sector run. Mature vetting pipelines allow a small
# slop here so that noise-driven width differences are NOT mistaken for the
# unequal primary/secondary durations that betray an eclipsing binary.
# Tune this if real signals are being over-flagged as EBs.
DURATION_MATCH_TOL_H = 0.05  # hours

# Fractional tolerance for calling two periods "the same" across sectors.
PERIOD_MATCH_TOL_FRAC = 0.02  # 2%

# Companion-size thresholds used by the verdict. A radius just above the
# planetary cap is BORDERLINE — mature pipelines do not call an eclipsing
# binary on size alone; they need an eclipse signature (secondary / odd-even)
# or an RV mass. Only a radius well above the cap is treated as unambiguously
# stellar/brown-dwarf sized.
COMPANION_PLANET_CAP_RJUP = 2.2   # largest plausible (inflated) giant planet
COMPANION_EB_HARD_RJUP = 4.0      # above this: unambiguously not a planet


def durations_consistent(durations_h, tol_h: float = DURATION_MATCH_TOL_H) -> bool:
    """True if all finite durations (hours) lie within ``tol_h`` of their median.

    A single value (or none) is trivially consistent. This is the absolute
    test used when the durations being compared are already robust medians
    (e.g. odd-vs-even transit durations, or one representative event per
    sector) rather than raw, noise-broadened single-event widths.
    """
    arr = np.asarray(
        [d for d in durations_h if d is not None and np.isfinite(d)], dtype=float
    )
    if arr.size < 2:
        return True
    return bool(np.all(np.abs(arr - np.median(arr)) <= tol_h))


def periods_consistent(periods_d, tol_frac: float = PERIOD_MATCH_TOL_FRAC) -> bool:
    """True if all finite periods agree to within ``tol_frac`` of their median."""
    arr = np.asarray(
        [p for p in periods_d if p is not None and np.isfinite(p) and p > 0],
        dtype=float,
    )
    if arr.size < 2:
        return True
    med = np.median(arr)
    return bool(med > 0 and np.all(np.abs(arr - med) / med <= tol_frac))


# ----------------------------------------------------------------------
# Data containers
# ----------------------------------------------------------------------
@dataclass
class StarInfo:
    tic_id: Optional[int] = None
    tmag: Optional[float] = None
    teff: Optional[float] = None
    radius: Optional[float] = None        # R_sun
    logg: Optional[float] = None
    mass: Optional[float] = None          # M_sun (often derived)
    ra: Optional[float] = None
    dec: Optional[float] = None
    sector: Optional[int] = None
    camera: Optional[int] = None
    ccd: Optional[int] = None
    crowdsap: Optional[float] = None
    source: str = "unknown"               # "fits" or "exofop_json"


@dataclass
class VettingResult:
    star: StarInfo
    summary: dict = field(default_factory=dict)
    bls: dict = field(default_factory=dict)
    lomb_scargle: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    centroid: dict = field(default_factory=dict)
    odd_even: dict = field(default_factory=dict)
    secondary: dict = field(default_factory=dict)
    shape: dict = field(default_factory=dict)
    physics: dict = field(default_factory=dict)
    verdict: dict = field(default_factory=dict)
    known_object: dict = field(default_factory=dict)
    detrend: dict = field(default_factory=dict)        # NEW
    sensitivity: dict = field(default_factory=dict)    # NEW (echo applied thresholds)
    plots: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["star"] = asdict(self.star)
        return d


# ----------------------------------------------------------------------
# Plot helpers
# ----------------------------------------------------------------------
def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ----------------------------------------------------------------------
# Core pipeline
# ----------------------------------------------------------------------
def clean_lightcurve(
    t: np.ndarray,
    flux: np.ndarray,
    flux_err: np.ndarray,
    quality: Optional[np.ndarray] = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Standard quality-flag mask + finite filtering + normalisation."""
    mask = np.isfinite(t) & np.isfinite(flux) & (flux > 0)
    if quality is not None:
        mask &= quality == 0
    if flux_err is not None:
        mask &= np.isfinite(flux_err)
    t, flux = t[mask], flux[mask]
    fe = flux_err[mask] if flux_err is not None else np.full_like(flux, np.nanstd(flux))
    med = np.nanmedian(flux)
    return t, flux / med, fe / med


def run_lomb_scargle(t, f, fe, p_min=0.1, p_max=20.0) -> dict:
    ls = LombScargle(t, f, fe)
    freq, power = ls.autopower(
        minimum_frequency=1 / p_max,
        maximum_frequency=1 / p_min,
        samples_per_peak=15,
    )
    periods = 1 / freq
    # FAP for the top peak
    ip = int(np.argmax(power))
    try:
        fap = float(ls.false_alarm_probability(power[ip]))
    except Exception:
        fap = None
    # 5 well-separated peaks
    order = np.argsort(power)[::-1]
    tops = []
    for idx in order:
        p = float(periods[idx])
        if all(abs(p - tp["period"]) / tp["period"] > 0.05 for tp in tops):
            tops.append({"period": p, "power": float(power[idx])})
        if len(tops) >= 5:
            break
    return {
        "top_period": float(periods[ip]),
        "top_power": float(power[ip]),
        "false_alarm_prob": fap,
        "top_peaks": tops,
    }


# Default fractional half-width of the BLS search window around a known
# period, e.g. 0.02 means scan periods in [P*0.98, P*1.02].
BLS_KNOWN_PERIOD_TOL_FRAC = 0.02


def run_bls_constrained(
    t, f, fe,
    known_period_days: float,
    tol_frac: float = BLS_KNOWN_PERIOD_TOL_FRAC,
    n_periods: int = 4000,
    search_harmonics: bool = True,
) -> dict:
    """BLS over a narrow window around a known period plus P/2 and 2P harmonics.

    Returns the same dict shape as :func:`run_bls` plus three extra keys:
    ``constrained``, ``known_period_input_days``, and ``matched_harmonic``
    (one of ``"P"``, ``"P/2"``, ``"2P"``).
    """
    span = float(t.max() - t.min())
    p_max_blind = max(0.5, span * 0.7)

    sub_grids = [("P", known_period_days)]
    if search_harmonics:
        if known_period_days / 2.0 >= 0.5:
            sub_grids.append(("P/2", known_period_days / 2.0))
        if 2.0 * known_period_days <= p_max_blind:
            sub_grids.append(("2P", 2.0 * known_period_days))

    per_grid = max(200, n_periods // len(sub_grids))
    grid_periods = []
    grid_labels = []
    for label, p in sub_grids:
        lo = p * (1.0 - tol_frac)
        hi = p * (1.0 + tol_frac)
        ps = np.linspace(lo, hi, per_grid)
        grid_periods.append(ps)
        grid_labels.append((label, ps))

    periods = np.concatenate(grid_periods)
    durations = np.array([0.05, 0.1, 0.15, 0.2, 0.3])
    bls = BoxLeastSquares(t, f, fe)
    res = bls.power(periods, durations)
    ib = int(np.argmax(res.power))

    best_p = float(res.period[ib])
    matched = "P"
    for label, ps in grid_labels:
        if ps[0] <= best_p <= ps[-1]:
            matched = label
            break

    _std = float(np.std(res.power))
    sde = float((res.power[ib] - np.median(res.power)) / _std) if _std > 0 else 0.0
    return {
        "period": best_p,
        "t0": float(res.transit_time[ib]),
        "duration": float(res.duration[ib]),
        "depth": float(res.depth[ib]),
        "power": float(res.power[ib]),
        "sde": sde,
        "n_transits_in_window": int(
            np.floor((t.max() - res.transit_time[ib]) / res.period[ib])
            - np.ceil((t.min() - res.transit_time[ib]) / res.period[ib])
            + 1
        ),
        "_periodogram": {
            "periods": periods.tolist()[::20],
            "power": res.power.tolist()[::20],
        },
        "constrained": True,
        "known_period_input_days": float(known_period_days),
        "matched_harmonic": matched,
    }


def run_bls(
    t, f, fe, p_min=0.5, p_max=None, n_periods=20000
) -> dict:
    span = float(t.max() - t.min())
    if p_max is None:
        p_max = max(p_min * 2, span * 0.7)
    durations = np.array([0.05, 0.1, 0.15, 0.2, 0.3])
    bls = BoxLeastSquares(t, f, fe)
    periods = np.linspace(p_min, p_max, n_periods)
    res = bls.power(periods, durations)
    ib = int(np.argmax(res.power))
    _std = float(np.std(res.power))
    sde = float((res.power[ib] - np.median(res.power)) / _std) if _std > 0 else 0.0
    return {
        "period": float(res.period[ib]),
        "t0": float(res.transit_time[ib]),
        "duration": float(res.duration[ib]),
        "depth": float(res.depth[ib]),
        "power": float(res.power[ib]),
        "sde": sde,
        "n_transits_in_window": int(
            np.floor((t.max() - res.transit_time[ib]) / res.period[ib])
            - np.ceil((t.min() - res.transit_time[ib]) / res.period[ib])
            + 1
        ),
        "_periodogram": {
            "periods": periods.tolist()[::20],     # sub-sample for transport
            "power": res.power.tolist()[::20],
        },
    }


def detect_events(t, f, threshold=0.997, min_pts=10, min_snr=4.0, max_gap=5) -> list:
    """Direct event detection — finds discrete dips regardless of period.

    Uses a two-pass approach:
      Pass 1 — measure the star's photometric scatter from out-of-dip data.
      Pass 2 — flag contiguous stretches where smoothed flux drops below
               **the greater of** the user's absolute threshold AND an
               adaptive threshold = baseline − min_snr × scatter.

    The adaptive threshold is the key change: for a quiet star with
    scatter = 0.0002, a 3σ dip is only 0.06% deep — well above the old
    fixed 0.997 threshold. The adaptive approach catches it. For a noisy
    star the fixed threshold still acts as a floor.

    Each candidate event must also pass a per-event SNR check. That check
    uses the *integrated* significance of the dip, not a single point:

        snr = mean_in_transit_depth × sqrt(n_points) / scatter

    The sqrt(n) factor is essential. A real but shallow transit on a noisy
    star can have a per-point depth ≈ the scatter (point SNR ≈ 1) yet still
    be hugely significant once you average over the dozens of in-transit
    points (integrated SNR ≈ 10). The old check divided a single point's
    depth by the scatter, so it threw away every shallow-on-noisy transit —
    e.g. an 0.8%-deep, ~5 h transit on a star with 0.53% point scatter
    scored ~1.5 and was rejected despite being a clear, repeating signal.

    Contiguous in-dip stretches separated by only a few points (median-filter
    noise can briefly lift the smoothed flux back over the threshold mid-dip)
    are bridged via ``max_gap`` so one transit yields one event, not two.
    """
    fs = median_filter(f, size=21)
    baseline = float(np.nanmedian(fs))

    # Identify real time gaps so we don't (a) flag points whose smoothed flux
    # was dragged down by the median filter reaching across the gap, or
    # (b) merge two separate in-dip runs across the gap into one fake event.
    # A "gap" is any sample-to-sample dt much larger than the typical cadence.
    t_arr = np.asarray(t)
    dt = np.diff(t_arr)
    pos_dt = dt[dt > 0]
    if pos_dt.size:
        cadence = float(np.median(pos_dt))
        gap_threshold = max(5 * cadence, 0.2)  # days; ~5x cadence or 4.8 h
        # gap_after[i] is True if there is a large gap between sample i and i+1
        gap_after = np.concatenate([dt > gap_threshold, [False]])
    else:
        gap_after = np.zeros(len(t_arr), dtype=bool)
        cadence = 0.0

    # --- Pass 1: measure local scatter from non-dip data ---
    # Use a rough cut: anything within 2× the raw MAD of median is "baseline"
    raw_mad = float(1.4826 * np.nanmedian(np.abs(f - baseline)))
    rough_mask = np.abs(fs - baseline) < 3 * max(raw_mad, 1e-6)
    if rough_mask.sum() > 100:
        scatter = float(1.4826 * np.nanmedian(np.abs(f[rough_mask] - np.nanmedian(f[rough_mask]))))
    else:
        scatter = raw_mad
    if scatter <= 0:
        scatter = 1e-5

    # --- Pass 2: adaptive threshold ---
    # The threshold is the HIGHER (less strict) of:
    #   (a) the user-supplied absolute threshold (e.g. 0.997 = 0.3% below baseline)
    #   (b) baseline − min_snr × scatter (adapts to the star's actual noise)
    # This way quiet stars get a sensitive threshold automatically, while the
    # absolute threshold still caps things for noisy stars.
    adaptive_threshold = baseline - min_snr * scatter
    effective_threshold = max(threshold, adaptive_threshold)

    in_dip = fs < effective_threshold

    # Suppress samples adjacent to a large time gap: the size-21 median filter
    # can pull edge points well below baseline by mixing in unrelated flux from
    # the other side of the gap (or the gap edge itself), producing a spurious
    # "dip" that just traces the discontinuity.
    edge_pad = 10  # half the median filter window
    n = len(in_dip)
    gap_idx = np.where(gap_after)[0]
    for gi in gap_idx:
        lo = max(0, gi - edge_pad + 1)
        hi = min(n, gi + edge_pad + 1)
        in_dip[lo:hi] = False

    # Bridge short gaps: fill runs of <= max_gap "out" points that sit between
    # two "in" stretches, so median-filter noise doesn't split one transit.
    if max_gap > 0:
        i = 0
        while i < n:
            if not in_dip[i]:
                start = i
                while i < n and not in_dip[i]:
                    i += 1
                # gap is [start, i); bridge only if flanked on both sides and
                # the bridged region does not straddle a real time gap.
                if (
                    0 < start
                    and i < n
                    and (i - start) <= max_gap
                    and not gap_after[start - 1 : i].any()
                ):
                    in_dip[start:i] = True
            else:
                i += 1

    events = []
    i = 0
    while i < len(t):
        if in_dip[i]:
            start = i
            while i < len(t) and in_dip[i]:
                # Stop a run at a real time gap — a single event cannot span
                # a multi-hour data outage.
                if gap_after[i]:
                    i += 1
                    break
                i += 1
            end = i
            if end - start >= min_pts:
                seg = f[start:end]
                n_pts = end - start
                seg_min = float(fs[start:end].min())
                depth = float(baseline - seg_min)              # max depth (reporting)
                mean_depth = float(baseline - np.nanmean(seg))  # avg depth (significance)
                # Integrated SNR: average dip depth grows in significance with
                # sqrt(number of in-transit points). This is what lets a shallow
                # transit on a noisy star clear the bar.
                snr = mean_depth * np.sqrt(n_pts) / scatter if scatter > 0 else 0.0
                if snr >= min_snr and mean_depth > 0:
                    events.append(
                        {
                            "t_start": float(t[start]),
                            "t_end": float(t[end - 1]),
                            "duration_d": float(t[end - 1] - t[start]),
                            "min_flux": seg_min,
                            "depth": depth,
                            "depth_snr": float(snr),
                            "n_points": int(n_pts),
                        }
                    )
        else:
            i += 1

    return events


def centroid_check(
    t, mom_x, mom_y, t_start, t_end, pad=0.5
) -> dict:
    """Compare in-event vs out-of-event centroids."""
    in_mask = (t >= t_start) & (t <= t_end)
    oot_mask = ((t >= t_start - pad) & (t < t_start)) | (
        (t > t_end) & (t <= t_end + pad)
    )
    if in_mask.sum() < 5 or oot_mask.sum() < 5:
        return {"available": False}
    mx_oot, my_oot = np.median(mom_x[oot_mask]), np.median(mom_y[oot_mask])
    mx_in, my_in = np.median(mom_x[in_mask]), np.median(mom_y[in_mask])
    mx_std = max(np.std(mom_x[oot_mask]), 1e-6)
    my_std = max(np.std(mom_y[oot_mask]), 1e-6)
    return {
        "available": True,
        "shift_col_px": float(mx_in - mx_oot),
        "shift_row_px": float(my_in - my_oot),
        "shift_col_sigma": float((mx_in - mx_oot) / mx_std),
        "shift_row_sigma": float((my_in - my_oot) / my_std),
        "on_target": bool(
            abs((mx_in - mx_oot) / mx_std) < 3
            and abs((my_in - my_oot) / my_std) < 3
        ),
    }


def odd_even_check(t, f, period, t0, duration, odd_even_sigma: float = 3.0) -> dict:
    """Compare even- vs odd-numbered transits.  Big depth difference -> EB.

    ``odd_even_sigma`` is the user-tunable EB-flag threshold (default 3σ).
    """
    if period is None or period <= 0:
        return {"available": False, "reason": "no period"}
    # phase folded; cycle number
    cycle = np.round((t - t0) / period).astype(int)
    half = duration * 0.7
    in_tr = np.abs((t - t0 - cycle * period)) < half
    if in_tr.sum() < 5:
        return {"available": False, "reason": "no in-transit points"}
    odd = in_tr & (cycle % 2 == 1)
    even = in_tr & (cycle % 2 == 0)
    if odd.sum() < 3 or even.sum() < 3:
        return {
            "available": False,
            "reason": "insufficient odd or even transits",
            "n_odd": int(odd.sum()),
            "n_even": int(even.sum()),
        }
    d_odd = 1.0 - np.median(f[odd])
    d_even = 1.0 - np.median(f[even])
    err_odd = np.std(f[odd]) / np.sqrt(odd.sum())
    err_even = np.std(f[even]) / np.sqrt(even.sum())
    diff = d_odd - d_even
    diff_err = math.sqrt(err_odd**2 + err_even**2)
    sigma = abs(diff) / diff_err if diff_err > 0 else 0
    return {
        "available": True,
        "depth_odd": float(d_odd),
        "depth_even": float(d_even),
        "difference": float(diff),
        "sigma": float(sigma),
        "n_odd": int(odd.sum()),
        "n_even": int(even.sum()),
        "flag_eb": bool(sigma > odd_even_sigma),
        "threshold_sigma": float(odd_even_sigma),
    }


def secondary_eclipse_search(t, f, period, t0, duration, secondary_sigma: float = 3.0) -> dict:
    """Look at phase 0.5 for a secondary dip.

    ``secondary_sigma`` is the user-tunable detection threshold (default 3σ).
    """
    if period is None or period <= 0:
        return {"available": False, "reason": "no period"}
    phase = ((t - t0) / period) % 1.0
    in_sec = np.abs(phase - 0.5) < (duration / period / 1.5)
    if in_sec.sum() < 5:
        return {"available": False, "reason": "no phase-0.5 coverage"}
    f_sec = np.median(f[in_sec])
    oot = (phase > 0.2) & (phase < 0.4) | (phase > 0.6) & (phase < 0.8)
    if oot.sum() < 10:
        return {"available": False, "reason": "no oot baseline"}
    baseline = np.median(f[oot])
    depth = baseline - f_sec
    noise = np.std(f[oot]) / np.sqrt(in_sec.sum())
    sigma = depth / noise if noise > 0 else 0
    return {
        "available": True,
        "depth": float(depth),
        "sigma": float(sigma),
        "detected": bool(sigma > secondary_sigma),
        "threshold_sigma": float(secondary_sigma),
    }


def measure_shape(t, f, t_start, t_end) -> dict:
    """Estimate ingress/egress and flat-bottom durations from a single event."""
    pad = 0.3 * (t_end - t_start)
    m = (t > t_start - pad) & (t < t_end + pad)
    if m.sum() < 30:
        return {"available": False}
    tt, ff = t[m], f[m]
    fs = median_filter(ff, size=11)
    baseline = np.median(fs[(tt < t_start) | (tt > t_end)])
    minf = float(fs.min())
    half_depth = baseline - 0.5 * (baseline - minf)
    # Find first/last point that crosses half-depth
    cross = fs < half_depth
    if cross.sum() < 5:
        return {"available": False}
    idx = np.where(cross)[0]
    t14 = float(tt[idx[-1]] - tt[idx[0]])
    # Find flat-bottom (within 10% of min)
    flat = fs < (minf + 0.10 * (baseline - minf))
    if flat.sum() >= 3:
        idx2 = np.where(flat)[0]
        t23 = float(tt[idx2[-1]] - tt[idx2[0]])
    else:
        t23 = 0.0
    return {
        "available": True,
        "t14_d": t14,
        "t23_d": t23,
        "t14_hours": t14 * 24,
        "t23_hours": t23 * 24,
        "t23_over_t14": float(t23 / t14) if t14 > 0 else 0,
        "ingress_d": float((t14 - t23) / 2) if t14 > t23 else 0.0,
        "shape_class": (
            "U (flat-bottomed)"
            if t14 > 0 and t23 / t14 > 0.4
            else "V (grazing/pointed)"
            if t14 > 0
            else "unknown"
        ),
    }


def physics_interpretation(star: StarInfo, depth: float, t14_d: float) -> dict:
    """
    Compute companion radius and a sanity-check period for a CENTRAL transit.
    Apply CROWDSAP dilution correction if available.
    """
    if star.radius is None or depth is None:
        return {"available": False}

    obs_depth = depth
    true_depth = obs_depth / star.crowdsap if star.crowdsap else obs_depth
    # Cap to physical range
    true_depth = min(max(true_depth, 0.0), 0.99)
    ratio = math.sqrt(true_depth)
    R_sun_in_R_jup = 9.73
    R_comp_Rsun = ratio * star.radius
    R_comp_Rjup = R_comp_Rsun * R_sun_in_R_jup

    # Estimate mass if missing (rough): use radius+logg if available
    M_sun_est = None
    if star.logg is not None and star.radius is not None:
        # g = GM/R^2 -> M = g R^2 / G
        G = 6.674e-11
        Rsun_m = 6.96e8
        Msun_kg = 1.989e30
        g_cgs = 10 ** star.logg          # cm/s^2
        g_si = g_cgs / 100.0
        M = g_si * (star.radius * Rsun_m) ** 2 / G
        M_sun_est = M / Msun_kg

    # Central-transit period from T14 (rough)
    P_central_d = None
    if t14_d and t14_d > 0 and (star.mass or M_sun_est):
        Mstar_Msun = star.mass or M_sun_est
        G = 6.674e-11
        Msun = 1.989e30
        Rsun_m = 6.96e8
        day = 86400
        Mstar = Mstar_Msun * Msun
        Rstar = star.radius * Rsun_m
        T14_s = t14_d * day
        P_central_s = G * Mstar * math.pi * T14_s**3 / (4 * Rstar**3)
        P_central_d = float(P_central_s / day)

    # Categorize companion
    if R_comp_Rjup < 2.2:
        category = "Planet-sized"
    elif R_comp_Rjup < 7:
        category = "Brown dwarf / very-low-mass star sized"
    elif R_comp_Rjup < 20:
        category = "M-dwarf sized"
    else:
        category = "Stellar (G-K or earlier)"

    return {
        "available": True,
        "observed_depth": obs_depth,
        "dilution_corrected_depth": true_depth,
        "ratio_companion_over_star": ratio,
        "R_companion_Rsun": R_comp_Rsun,
        "R_companion_Rjup": R_comp_Rjup,
        "category": category,
        "is_planet_candidate": R_comp_Rjup < 2.2,
        "M_star_estimated_Msun": M_sun_est,
        "P_central_implied_d": P_central_d,
    }


def make_verdict(
    n_events: int,
    physics: dict,
    centroid: dict,
    odd_even: dict,
    secondary: dict,
    bls_sde: float,
    duration_consistent: Optional[bool] = None,
    duration_tol_h: float = DURATION_MATCH_TOL_H,
) -> dict:
    flags = []
    reasons = []
    confidence = 0.5

    # No detection at all
    if n_events == 0 and bls_sde < 7:
        return {
            "headline": "No significant transit/eclipse signal",
            "category": "no_signal",
            "confidence": 0.8,
            "flags": [],
            "reasons": ["No discrete dip events found and BLS SDE < 7."],
        }

    # Physics-based companion size. A radius just over the planetary cap is
    # BORDERLINE, not an automatic EB — only a radius well above the cap is a
    # hard EB indicator on size alone.
    companion_borderline = False
    if physics.get("available"):
        R = physics["R_companion_Rjup"]
        if R > COMPANION_EB_HARD_RJUP:
            flags.append("companion_too_large_for_planet")
            reasons.append(
                f"Implied companion radius {R:.1f} R_Jup ({physics['category']}) — "
                f"far above the planetary cap (~{COMPANION_PLANET_CAP_RJUP} R_Jup); "
                "stellar / brown-dwarf sized."
            )
        elif R > COMPANION_PLANET_CAP_RJUP:
            companion_borderline = True
            flags.append("companion_borderline_large")
            reasons.append(
                f"Implied companion radius {R:.1f} R_Jup is just above the "
                f"~{COMPANION_PLANET_CAP_RJUP} R_Jup planetary cap — borderline. "
                "Size alone is not treated as proof of an eclipsing binary; an "
                "eclipse signature (secondary or odd/even) or an RV mass is "
                "needed to confirm."
            )

    # Centroid offset = blend
    if centroid.get("available") and centroid.get("on_target") is False:
        flags.append("centroid_offset")
        reasons.append("In-transit centroid shifts >3σ — possible background blend.")
    elif centroid.get("available") and centroid.get("on_target"):
        reasons.append("Centroid is on-target — not a background blend.")

    # Odd/even
    if odd_even.get("available") and odd_even.get("flag_eb"):
        flags.append("odd_even_mismatch")
        reasons.append(
            f"Odd vs even transits differ at {odd_even['sigma']:.1f}σ — eclipsing-binary indicator."
        )

    # Secondary eclipse
    if secondary.get("available") and secondary.get("detected"):
        flags.append("secondary_eclipse_detected")
        reasons.append(
            f"Secondary eclipse detected at phase 0.5 ({secondary['sigma']:.1f}σ) — eclipsing-binary indicator."
        )

    # Transit-duration corroboration (±duration_tol_h). Consistent durations
    # across events support a single real transiting body; large variation is a
    # caution flag (blends / multiple signals) but is not, by itself, an EB call.
    if duration_consistent is True:
        reasons.append(
            f"Transit durations agree across events to within ±{duration_tol_h:.2f} h "
            "— consistent with a single repeating transit."
        )
    elif duration_consistent is False:
        flags.append("duration_inconsistent")
        reasons.append(
            f"Transit durations vary by more than ±{duration_tol_h:.2f} h between "
            "events — check for blends or more than one signal."
        )

    # Single-transit case
    if n_events == 1:
        reasons.append(
            "Only one in-sector event — period unconstrained; need follow-up or future sectors."
        )

    # Decide
    has_eclipse_indicator = (
        "secondary_eclipse_detected" in flags or "odd_even_mismatch" in flags
    )
    if "companion_too_large_for_planet" in flags or has_eclipse_indicator:
        category = "eclipsing_binary_candidate"
        headline = "Eclipsing binary candidate"
        confidence = 0.85
    elif "centroid_offset" in flags:
        category = "false_positive_blend"
        headline = "Likely background blend (false positive)"
        confidence = 0.75
    elif companion_borderline:
        # Borderline size, no corroborating eclipse signature -> large planet
        # candidate pending RV (matches mature-pipeline behaviour).
        category = "planet_candidate"
        headline = "Large planet candidate (RV needed to exclude brown dwarf)"
        confidence = 0.55
    elif physics.get("available") and physics.get("is_planet_candidate"):
        category = "planet_candidate"
        headline = "Planet candidate (further vetting required)"
        confidence = 0.60
    else:
        category = "ambiguous"
        headline = "Ambiguous signal — manual review needed"
        confidence = 0.40

    return {
        "headline": headline,
        "category": category,
        "confidence": confidence,
        "flags": flags,
        "reasons": reasons,
    }


# ----------------------------------------------------------------------
# Plot generation
# ----------------------------------------------------------------------
def _rolling_median_detrend(t: np.ndarray, f: np.ndarray, window_days: float = 1.0) -> np.ndarray:
    """Lightweight per-cadence detrend for plotting only.

    Subtracts a rolling-median trend in a ``window_days``-wide window and
    re-normalises to median=1. Robust to single-cadence outliers (no
    Gaussian smoothing). This is *plot-only* — BLS still sees the
    unfiltered f_c from run_full_vetting.
    """
    if len(t) < 8:
        return f
    n = len(t)
    out = np.empty(n, dtype=float)
    half = window_days / 2.0
    # Pointers for the sliding window — O(n) over a sorted t.
    lo = 0
    hi = 0
    for i in range(n):
        t_lo = t[i] - half
        t_hi = t[i] + half
        while lo < n and t[lo] < t_lo:
            lo += 1
        while hi < n and t[hi] <= t_hi:
            hi += 1
        seg = f[lo:hi]
        if seg.size == 0:
            out[i] = f[i]
        else:
            out[i] = float(np.nanmedian(seg))
    trend = out
    # Avoid divide-by-zero on a pathological all-zero trend
    safe = np.where(np.isfinite(trend) & (trend != 0), trend, np.nanmedian(f) or 1.0)
    flat = f / safe
    med = np.nanmedian(flat)
    if not np.isfinite(med) or med == 0:
        return flat
    return flat / med


def _time_bin(t: np.ndarray, f: np.ndarray, bin_minutes: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Bin (t, f) by ``bin_minutes`` in time. Returns (t_bin, f_bin, err_bin)
    where err_bin is the standard error of the mean in each bin (empty bins
    are dropped).
    """
    if bin_minutes is None or bin_minutes <= 0 or len(t) == 0:
        return t, f, np.zeros_like(f)
    bin_days = bin_minutes / 1440.0
    t_min = float(np.nanmin(t))
    idx = np.floor((t - t_min) / bin_days).astype(int)
    n_bins = int(idx.max()) + 1
    sums_t = np.zeros(n_bins)
    sums_f = np.zeros(n_bins)
    sums_ff = np.zeros(n_bins)
    counts = np.zeros(n_bins, dtype=int)
    for i in range(len(t)):
        b = idx[i]
        if not (np.isfinite(t[i]) and np.isfinite(f[i])):
            continue
        sums_t[b] += t[i]
        sums_f[b] += f[i]
        sums_ff[b] += f[i] * f[i]
        counts[b] += 1
    mask = counts > 0
    t_b = sums_t[mask] / counts[mask]
    f_b = sums_f[mask] / counts[mask]
    # Standard error of the mean per bin; use ddof=1 where possible.
    mean_sq = sums_ff[mask] / counts[mask]
    var = np.maximum(0.0, mean_sq - f_b * f_b)
    n = counts[mask]
    sem = np.sqrt(var / np.maximum(1, n))
    return t_b, f_b, sem


def make_plots(
    t, f, fe, mom_x, mom_y, events, primary_event,
    bls_periodogram, ls_periodogram,
    detrend_meta: Optional[dict] = None,
    f_raw: Optional[np.ndarray] = None,
    plot_detrend: bool = True,
    bin_minutes: Optional[int] = 30,
) -> dict:
    """Generate diagnostic plots.

    ``events`` is the full list from detect_events (may be empty). ``primary_event``
    is the one chosen for centroid/shape analysis (typically the deepest).
    The full-LC plot shades EVERY event; the zoom plot shows up to 6 events
    in a grid, with the primary one highlighted.

    Plot-presentation kwargs (don't affect detection):
      plot_detrend  — apply a 1-day rolling-median flatten to the LC plot
                      so long-term variability doesn't drown shallow dips.
      bin_minutes   — when set, overplot time-binned points in red. Default
                      30 min beats per-cadence noise down ~3-4× for 2-min LCs.
    """
    plots = {}
    events = events or []

    # 0. Stellar variability detrend (only when actually applied).
    if (
        detrend_meta and detrend_meta.get("applied")
        and f_raw is not None and "fit" in detrend_meta
    ):
        from .detrend import _design_matrix  # internal but stable for plot use
        fit = detrend_meta["fit"]
        X = _design_matrix(t, fit["period_days"])
        coeffs = np.array([fit["C"], fit["A1"], fit["B1"], fit["A2"], fit["B2"]])
        model = X @ coeffs
        residual = f_raw - model + 1.0

        fig, axes = plt.subplots(3, 1, figsize=(10, 6.5), sharex=True)
        axes[0].plot(t, f_raw, "k.", ms=1, alpha=0.4)
        axes[0].plot(t, model, "C1-", lw=1.0, alpha=0.9, label="sin + 1st harmonic")
        axes[0].set_ylabel("Raw flux")
        axes[0].set_title(
            f"Stellar variability detrend — P = {fit['period_days']:.3f} d, "
            f"amplitude = {detrend_meta['amplitude_ppm']:.0f} ppm, "
            f"RMS reduced {detrend_meta['rms_reduction_pct']:.1f}%"
        )
        axes[0].legend(fontsize=8, loc="upper right")

        axes[1].plot(t, model, "C1-", lw=0.8)
        axes[1].set_ylabel("Fitted model")

        axes[2].plot(t, residual, "k.", ms=1, alpha=0.4)
        axes[2].axhline(1.0, color="gray", ls=":", alpha=0.5)
        axes[2].set_ylabel("Residual (BLS in)")
        axes[2].set_xlabel("Time (BTJD or similar)")

        for ax in axes:
            ax.ticklabel_format(axis="y", useOffset=False, style="plain")
            ax.yaxis.set_major_formatter(plt.ScalarFormatter(useOffset=False))

        fig.tight_layout()
        plots["detrend"] = _fig_to_b64(fig)

    # 1. Full LC with all events shaded.
    # When plot_detrend is on, flatten with a 1-day rolling median so
    # long-period variability doesn't drown shallow dips. Detection
    # (BLS/events) is unaffected — they already ran on f.
    f_plot = _rolling_median_detrend(t, f, window_days=1.0) if plot_detrend else f
    fig, ax = plt.subplots(figsize=(10, 3))
    ax.plot(t, f_plot, "k.", ms=1, alpha=0.35, label="cadences")
    if bin_minutes and len(t) > 0:
        t_b, f_b, e_b = _time_bin(t, f_plot, bin_minutes)
        if len(t_b) > 0:
            ax.errorbar(
                t_b, f_b, yerr=e_b,
                fmt="o", ms=3.2, color="crimson", ecolor="crimson",
                elinewidth=0.6, capsize=0, alpha=0.85,
                label=f"{bin_minutes}-min bins",
            )
        ax.legend(loc="upper right", fontsize=8, framealpha=0.85)
    # Set y-limits explicitly so we can place labels just inside the top edge.
    # Use the 99.7 percentile on top to suppress upward outliers, but the
    # bottom must accommodate the deepest detected dip — a percentile-based
    # lower bound clips event troughs when the in-transit fraction exceeds
    # the percentile (e.g. many dips on a short baseline).
    ymax = float(np.nanpercentile(f_plot, 99.7))
    if events:
        event_mins = []
        for ev in events:
            in_ev = (t >= ev["t_start"]) & (t <= ev["t_end"])
            if np.any(in_ev):
                event_mins.append(float(np.nanmin(f_plot[in_ev])))
        ymin = min(event_mins) if event_mins else float(np.nanpercentile(f_plot, 0.3))
    else:
        ymin = float(np.nanpercentile(f_plot, 0.3))
    y_pad = 0.05 * (ymax - ymin)
    ax.set_ylim(ymin - y_pad, ymax + y_pad)
    # IMPORTANT: disable the "+1" offset notation — it's confusing for shallow dips
    ax.ticklabel_format(axis="y", useOffset=False, style="plain")
    ax.yaxis.set_major_formatter(plt.ScalarFormatter(useOffset=False))
    label_y = ymax + 0.3 * y_pad  # just below the top
    for i, ev in enumerate(events):
        is_primary = primary_event is not None and ev["t_start"] == primary_event["t_start"]
        ax.axvspan(
            ev["t_start"], ev["t_end"],
            color="red" if is_primary else "orange",
            alpha=0.30 if is_primary else 0.18,
        )
        mid = 0.5 * (ev["t_start"] + ev["t_end"])
        ax.text(
            mid, label_y,
            f"#{i+1}",
            ha="center", va="bottom", fontsize=8,
            color="darkred" if is_primary else "saddlebrown",
            fontweight="bold" if is_primary else "normal",
        )
    ax.set_xlabel("Time (BTJD or similar)")
    ax.set_ylabel("Normalised flux")
    title = "Flattened light curve" if plot_detrend else "Light curve"
    if bin_minutes:
        title += f" — {bin_minutes}-min binned overlay"
    if events:
        title += f" — {len(events)} dip event{'s' if len(events) != 1 else ''} detected"
    ax.set_title(title)
    plots["lightcurve"] = _fig_to_b64(fig)

    # 2. Zoom: grid of up to 6 events. Primary highlighted.
    if events:
        n_show = min(len(events), 6)
        ncols = 1 if n_show == 1 else (2 if n_show <= 4 else 3)
        nrows = int(np.ceil(n_show / ncols))
        fig, axes = plt.subplots(
            nrows, ncols,
            figsize=(10 if ncols > 1 else 10, 3.0 * nrows),
            squeeze=False,
        )
        # Show DEEPEST events first.
        shown = sorted(events, key=lambda e: -e["depth"])[:n_show]
        for idx, ev in enumerate(shown):
            r, c = divmod(idx, ncols)
            ax = axes[r][c]
            pad = (ev["t_end"] - ev["t_start"]) * 1.5
            m = (t > ev["t_start"] - pad) & (t < ev["t_end"] + pad)
            if m.sum() <= 10:
                ax.text(0.5, 0.5, "(too few points)", ha="center", va="center",
                        transform=ax.transAxes)
                continue
            ax.errorbar(t[m], f[m], yerr=fe[m] if fe is not None else None,
                        fmt="k.", ms=2, alpha=0.5)
            ax.axhline(1.0, color="gray", ls=":", alpha=0.5)
            ax.axhline(ev["min_flux"], color="red", ls=":", alpha=0.6)
            is_primary = primary_event is not None and ev["t_start"] == primary_event["t_start"]
            border = "red" if is_primary else "gray"
            for spine in ax.spines.values():
                spine.set_edgecolor(border)
                spine.set_linewidth(1.5 if is_primary else 0.8)
            tag = " (primary)" if is_primary else ""
            snr_tag = f"  SNR={ev.get('depth_snr', 0):.1f}σ" if ev.get('depth_snr') else ""
            ax.set_title(
                f"Event at t≈{0.5*(ev['t_start']+ev['t_end']):.3f}{tag}\n"
                f"depth={ev['depth']*100:.2f}%, dur={ev['duration_d']*24:.1f}h{snr_tag}",
                fontsize=9,
            )
            ax.tick_params(labelsize=8)
        # Hide unused panels.
        for idx in range(n_show, nrows * ncols):
            r, c = divmod(idx, ncols)
            axes[r][c].set_visible(False)
        fig.supxlabel("Time", fontsize=9)
        fig.supylabel("Flux", fontsize=9)
        fig.tight_layout()
        plots["event_zoom"] = _fig_to_b64(fig)

    # 3. Centroid (anchored on the primary event only — that's where the
    # blend test is most meaningful)
    if primary_event is not None and mom_x is not None and mom_y is not None:
        pad = (primary_event["t_end"] - primary_event["t_start"]) * 2
        m = (t > primary_event["t_start"] - pad) & (t < primary_event["t_end"] + pad)
        if m.sum() > 20:
            fig, ax = plt.subplots(figsize=(10, 3))
            ax.plot(t[m], mom_x[m] - np.median(mom_x[m]), "b.", ms=2, alpha=0.5, label="col (x)")
            ax.plot(t[m], mom_y[m] - np.median(mom_y[m]), "g.", ms=2, alpha=0.5, label="row (y)")
            ax.axvspan(primary_event["t_start"], primary_event["t_end"], color="red", alpha=0.15)
            ax.set_xlabel("Time")
            ax.set_ylabel("Centroid offset (px)")
            ax.set_title("Centroid behaviour during primary event")
            ax.legend()
            plots["centroid"] = _fig_to_b64(fig)

    # 4. BLS periodogram
    if bls_periodogram:
        fig, ax = plt.subplots(figsize=(10, 3))
        ax.plot(bls_periodogram["periods"], bls_periodogram["power"], "k-", lw=0.6)
        ax.set_xlabel("Period (d)")
        ax.set_ylabel("BLS power")
        ax.set_title("Box Least Squares periodogram")
        plots["bls"] = _fig_to_b64(fig)

    # 5. LS
    if ls_periodogram and ls_periodogram.get("top_period"):
        fig, ax = plt.subplots(figsize=(6, 3))
        peaks = ls_periodogram.get("top_peaks", [])
        if peaks:
            ax.bar(
                [f"{p['period']:.2f} d" for p in peaks],
                [p["power"] for p in peaks],
                color="steelblue",
            )
            ax.set_ylabel("LS power")
            ax.set_title("Lomb-Scargle top peaks")
            ax.tick_params(axis="x", rotation=20)
            plots["lomb_scargle"] = _fig_to_b64(fig)

    return plots


# ----------------------------------------------------------------------
# Top-level driver
# ----------------------------------------------------------------------
def run_full_vetting(
    t: np.ndarray,
    flux: np.ndarray,
    flux_err: np.ndarray,
    quality: Optional[np.ndarray],
    mom_x: Optional[np.ndarray],
    mom_y: Optional[np.ndarray],
    star: StarInfo,
    detect_threshold: float = 0.997,
    detect_min_snr: float = 4.0,
    high_variability: bool = False,
    rotation_period_days: Optional[float] = None,
    known_period_days: Optional[float] = None,
    secondary_sigma: float = 3.0,
    odd_even_sigma: float = 3.0,
    reporter=None,
    plot_detrend: bool = True,
    plot_bin_minutes: Optional[int] = 30,
) -> VettingResult:
    from .detrend import apply_variability_detrend
    if reporter is None:
        from .progress import make_noop_reporter
        reporter = make_noop_reporter()

    # Clean
    reporter("clean", 0.0)
    t_c, f_c, fe_c = clean_lightcurve(t, flux, flux_err, quality)
    if mom_x is not None and quality is not None:
        m = np.isfinite(t) & np.isfinite(flux) & (flux > 0) & (quality == 0)
        mom_x = mom_x[m]
        mom_y = mom_y[m]
    reporter("clean", 1.0)

    # Stats
    span = float(t_c.max() - t_c.min())

    # Lomb-Scargle (cap at half the baseline)
    reporter("lomb_scargle", 0.0)
    ls = run_lomb_scargle(t_c, f_c, fe_c, p_min=0.1, p_max=min(20.0, span / 2))
    reporter("lomb_scargle", 1.0)

    # --- Optional sinusoidal detrend before BLS ----------------------------
    reporter("detrend", 0.0)
    detrend_meta: dict
    f_raw_for_plot = f_c.copy() if high_variability else None
    if high_variability:
        period_for_fit = rotation_period_days or ls.get("top_period")
        source = "user_period" if rotation_period_days else "ls_peak"
        # Per-cadence noise floor in ppm — anything below this is just scatter.
        # Use point-to-point differences so the floor reflects high-frequency
        # noise rather than the very variability we're trying to fit.
        df = np.diff(f_c)
        noise_floor_ppm = float(1.4826 * np.nanmedian(np.abs(df)) / np.sqrt(2.0)) * 1e6
        f_c, detrend_meta = apply_variability_detrend(
            t_c, f_c, period_days=period_for_fit,
            noise_floor_ppm=noise_floor_ppm, source=source,
        )
    else:
        detrend_meta = {
            "applied": False, "reason": "disabled",
            "period_days": None, "amplitude_ppm": None,
            "harmonic_amplitude_ppm": None, "rms_reduction_pct": None,
        }
    reporter("detrend", 1.0)

    # BLS (runs on detrended residual when high_variability was enabled)
    reporter("bls", 0.0)
    if (known_period_days is not None
            and np.isfinite(known_period_days)
            and 0 < known_period_days <= 0.7 * span):
        bls = run_bls_constrained(t_c, f_c, fe_c, known_period_days=known_period_days)
    else:
        bls = run_bls(t_c, f_c, fe_c, p_min=0.5, p_max=span * 0.7)
        if known_period_days is not None:
            bls["constrained_fallback_reason"] = (
                "known_period_days outside valid range"
            )
    reporter("bls", 1.0)

    # Direct event detection (user-tunable sensitivity).
    reporter("events", 0.0)
    events = detect_events(
        t_c, f_c,
        threshold=detect_threshold,
        min_pts=10,
        min_snr=detect_min_snr,
    )
    reporter("events", 1.0)

    # If exactly one in-sector event, anchor centroid/shape on it.
    primary_event = events[0] if len(events) == 1 else None
    if len(events) > 1:
        primary_event = max(events, key=lambda e: e["depth"])

    # Centroid
    reporter("centroid", 0.0)
    centroid = {"available": False}
    if primary_event and mom_x is not None and mom_y is not None and len(mom_x) == len(t_c):
        centroid = centroid_check(
            t_c, mom_x, mom_y, primary_event["t_start"], primary_event["t_end"]
        )
    reporter("centroid", 1.0)

    # Shape
    reporter("shape", 0.0)
    shape = {"available": False}
    if primary_event:
        shape = measure_shape(t_c, f_c, primary_event["t_start"], primary_event["t_end"])
    reporter("shape", 1.0)

    # Odd/even and secondary (both threshold-tunable)
    reporter("odd_even", 0.0)
    odd_even = odd_even_check(
        t_c, f_c, bls["period"], bls["t0"], bls["duration"],
        odd_even_sigma=odd_even_sigma,
    )
    reporter("odd_even", 1.0)
    reporter("secondary", 0.0)
    secondary = secondary_eclipse_search(
        t_c, f_c, bls["period"], bls["t0"], bls["duration"],
        secondary_sigma=secondary_sigma,
    )
    reporter("secondary", 1.0)

    # Physics
    reporter("physics", 0.0)
    depth_for_physics = primary_event["depth"] if primary_event else bls.get("depth")
    t14_for_physics = shape.get("t14_d") if shape.get("available") else bls.get("duration")
    physics = physics_interpretation(star, depth_for_physics, t14_for_physics)
    reporter("physics", 1.0)

    # Verdict
    reporter("verdict", 0.0)
    verdict = make_verdict(
        n_events=len(events),
        physics=physics,
        centroid=centroid,
        odd_even=odd_even,
        secondary=secondary,
        bls_sde=bls["sde"],
    )
    reporter("verdict", 1.0)

    # External catalog cross-match (unchanged).
    reporter("crossmatch", 0.0)
    known = crossmatch_known_object(star.ra, star.dec)
    if known.get("matched"):
        verdict["original_headline"] = verdict.get("headline")
        verdict["original_category"] = verdict.get("category")
        verdict["headline"] = known["headline"]
        verdict["category"] = "known_object"
        verdict["confidence"] = 0.99
        match_reason = (
            f"Catalog override: {known['headline']}"
            + (f" — {known['name']}" if known.get("name") else "")
            + (f" ({known['description']})" if known.get("description") else "")
            + (
                f" at {known['distance_arcsec']:.2f}\" from target"
                if known.get("distance_arcsec") is not None else ""
            )
            + "."
        )
        verdict.setdefault("reasons", []).insert(0, match_reason)
        verdict.setdefault("flags", []).append("known_object_override")
    reporter("crossmatch", 1.0)

    # Plots — Task 7 will extend make_plots to accept detrend_meta + f_raw.
    # For now keep the existing call signature; Task 7 adds the new kwargs.
    reporter("plots", 0.0)
    plots = make_plots(
        t_c, f_c, fe_c, mom_x, mom_y, events, primary_event,
        bls.get("_periodogram"), ls,
        detrend_meta=detrend_meta,
        f_raw=f_raw_for_plot,
        plot_detrend=plot_detrend,
        bin_minutes=plot_bin_minutes,
    )
    reporter("plots", 1.0)

    summary = {
        "n_points": int(len(t_c)),
        "time_span_d": span,
        "median_cadence_min": float(np.median(np.diff(t_c)) * 1440),
        "n_events_detected": len(events),
        "scatter_mad": float(1.4826 * np.nanmedian(np.abs(f_c - 1))),
    }

    bls.pop("_periodogram", None)

    return VettingResult(
        star=star,
        summary=summary,
        bls=bls,
        lomb_scargle=ls,
        events=events,
        centroid=centroid,
        odd_even=odd_even,
        secondary=secondary,
        shape=shape,
        physics=physics,
        verdict=verdict,
        known_object=known,
        detrend=detrend_meta,
        sensitivity={
            "threshold": float(detect_threshold),
            "min_snr": float(detect_min_snr),
            "secondary_sigma": float(secondary_sigma),
            "odd_even_sigma": float(odd_even_sigma),
        },
        plots=plots,
    )



