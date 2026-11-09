"""Gaia Alerts light-curve fetcher and cone-search helper.

Powers the "fetch a Gaia baseline" side of the joint TESS + Gaia
microlensing pipeline (Harris et al. 2026, ApJL 1005 L33). The Gaia
Alerts feed at https://gsaweb.ast.cam.ac.uk/alerts publishes per-event
CSV light curves in the Gaia G band with typical baselines of 5+ years,
which is exactly what breaks the θ_E ↔ M_L degeneracy TESS's short
single-sector baseline can't resolve on its own.

Read-only HTTP GETs to the public Alerts endpoints. No auth, no
rate-limit signalling beyond standard HTTP status.
"""
from __future__ import annotations

import csv
import io
import logging
import math
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

_ALERTS_HOST = "https://gsaweb.ast.cam.ac.uk"
_ALERTS_INDEX_CSV = f"{_ALERTS_HOST}/alerts/alerts.csv"


def _lightcurve_url(alert_id: str) -> str:
    """Per-alert LC CSV URL. `alert_id` is e.g. 'Gaia23bra' — no path chars."""
    if not re.match(r"^Gaia[0-9A-Za-z]+$", alert_id):
        raise ValueError(f"alert_id must match /^Gaia[0-9A-Za-z]+$/; got {alert_id!r}")
    return f"{_ALERTS_HOST}/alerts/alert/{urllib.parse.quote(alert_id)}/lightcurve.csv/"


_HTTP_TIMEOUT_S = 30.0
_USER_AGENT = "Vetstar-Microlensing/1.0 (+https://github.com/eagnespuerto/vetstar)"


def _http_get(url: str) -> str:
    """Fetch a URL and return the body as text. Raises RuntimeError on
    HTTP failure with a clear message; leaves URL / status visible."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from {url}: {e.reason}")
    except Exception as e:
        raise RuntimeError(f"Failed to fetch {url}: {e}")
    if status != 200:
        raise RuntimeError(f"HTTP {status} from {url}")
    return body


# ---------------------------------------------------------------------------
# CSV parsers — the Gaia Alerts format has drifted over time; be permissive.
# ---------------------------------------------------------------------------

# Recognised alias sets for the columns we need. First match wins.
_TIME_ALIASES = ("jd(tcb)", "jd", "time", "date_tcb", "obs_time")
_MAG_ALIASES = ("averagemag", "average_mag", "g_mag", "mag", "gmag")
_ERR_ALIASES = ("averagemag_err", "average_mag_err", "mag_err", "g_mag_err", "sigma_mag")


def _first_matching(header: List[str], aliases: Tuple[str, ...]) -> Optional[str]:
    """Return the original header column that matches any alias (case-insensitive,
    leading '#' and whitespace stripped)."""
    normalized = {re.sub(r"^\s*#?\s*", "", h).strip().lower(): h for h in header}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    return None


@dataclass
class GaiaLightcurve:
    alert_id: str
    time_jd: List[float]           # JD (TCB, Gaia native)
    mag: List[float]               # G-band magnitude
    mag_err: List[float]           # Inflated per Kruszynska+2022 approximation
    mag_err_reported: List[float]  # Raw, pre-inflation, for reference
    n_points: int
    source_url: str

    def to_dict(self) -> dict:
        return {
            "alert_id": self.alert_id,
            "time_jd": self.time_jd,
            "mag": self.mag,
            "mag_err": self.mag_err,
            "mag_err_reported": self.mag_err_reported,
            "n_points": self.n_points,
            "source_url": self.source_url,
        }


def parse_lightcurve_csv(text: str, alert_id: str, source_url: str) -> GaiaLightcurve:
    """Parse a Gaia Alerts per-event lightcurve CSV.

    Format varies over time; the header can carry a leading `#` and column
    names have drifted. We pick the columns by alias.
    """
    # Some Gaia CSVs start with a preamble line before the real header — drop
    # everything before the first line that looks like it starts with `#` or
    # contains "JD" plus "mag".
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("Empty CSV body")
    # Find the header line
    header_ix = None
    for i, ln in enumerate(lines[:12]):  # scan first 12 lines
        low = ln.lower()
        if any(a in low for a in _TIME_ALIASES) and any(a in low for a in _MAG_ALIASES):
            header_ix = i
            break
    if header_ix is None:
        raise ValueError(
            "Could not locate a Gaia lightcurve header line "
            "(need columns matching time + magnitude aliases). "
            f"First lines: {lines[:3]!r}"
        )
    reader = csv.reader(lines[header_ix:])
    header = next(reader)
    t_col = _first_matching(header, _TIME_ALIASES)
    m_col = _first_matching(header, _MAG_ALIASES)
    e_col = _first_matching(header, _ERR_ALIASES)  # may be None
    if t_col is None or m_col is None:
        raise ValueError(
            f"Missing required time/mag column. Header was: {header!r}. "
            f"Detected t_col={t_col!r}, m_col={m_col!r}"
        )
    # Build column-index map on the ORIGINAL header positions (csv preserves order).
    idx = {name: i for i, name in enumerate(header)}
    t_idx, m_idx = idx[t_col], idx[m_col]
    e_idx = idx[e_col] if e_col else -1

    time_jd, mag, mag_err_reported = [], [], []
    for row in reader:
        if not row or all(not c.strip() for c in row):
            continue
        try:
            t = float(row[t_idx])
            m = float(row[m_idx])
        except (ValueError, IndexError):
            continue
        # Sentinel value 99.999 marks "no data" in some Gaia CSVs.
        if not math.isfinite(t) or not math.isfinite(m) or m > 90.0:
            continue
        e_val = float("nan")
        if e_idx >= 0 and e_idx < len(row) and row[e_idx].strip():
            try:
                e_val = float(row[e_idx])
                if not math.isfinite(e_val) or e_val <= 0:
                    e_val = float("nan")
            except ValueError:
                e_val = float("nan")
        time_jd.append(t)
        mag.append(m)
        mag_err_reported.append(e_val)

    if len(time_jd) < 5:
        raise ValueError(
            f"Only {len(time_jd)} usable rows after parsing — Gaia CSV may be malformed "
            f"or the alert has no photometry"
        )

    mag_err_inflated = [
        _kruszynska_inflated_error(m, e) for m, e in zip(mag, mag_err_reported)
    ]

    return GaiaLightcurve(
        alert_id=alert_id,
        time_jd=time_jd,
        mag=mag,
        mag_err=mag_err_inflated,
        mag_err_reported=mag_err_reported,
        n_points=len(time_jd),
        source_url=source_url,
    )


# ---------------------------------------------------------------------------
# Error inflation — Kruszynska+2022 approximation
# ---------------------------------------------------------------------------

# Kruszynska et al. (2022, A&A 662, A59) calibrate Gaia G-band photometric
# uncertainties (see their Sec. 3.2). Their published prescription rescales
# the reported per-transit uncertainty by a magnitude-dependent multiplier
# and adds a systematic floor. Their per-magnitude table is not compact, so
# we apply a conservative single-coefficient approximation:
#
#     σ_calibrated² = (α · σ_reported)² + σ_sys²
#
# with α = 1.5 and σ_sys = 3 mmag. This over-estimates the uncertainty
# slightly at G < 15 mag and under-estimates it slightly at G > 19 mag,
# but matches the reported floor for bulge-brightness sources within a
# factor of 2. When accuracy matters, override at analysis time with the
# per-magnitude table from Kruszynska et al. 2022 Table 2.
_ALPHA_KRUSZYNSKA = 1.5
_SIGMA_SYS_MAG = 0.003


def _kruszynska_inflated_error(mag: float, reported_err: float) -> float:
    """Apply the Kruszynska+2022 approximate error inflation.

    If `reported_err` is not finite (missing / unreported), fall back to a
    magnitude-tiered default (bright sources get ~5 mmag, faint get ~50 mmag).
    """
    if not math.isfinite(reported_err) or reported_err <= 0:
        # Rough magnitude-dependent placeholder — Gaia per-transit is worse
        # for faint sources; scale roughly as 10^(0.4·(G-14))·5 mmag.
        base = 0.005 * (10 ** (0.4 * max(mag - 14.0, 0.0)))
        reported_err = min(base, 0.5)
    return math.sqrt((_ALPHA_KRUSZYNSKA * reported_err) ** 2 + _SIGMA_SYS_MAG ** 2)


# ---------------------------------------------------------------------------
# Public API — fetchers
# ---------------------------------------------------------------------------

def fetch_alert_lightcurve(alert_id: str) -> GaiaLightcurve:
    """Fetch and parse the Gaia Alerts CSV for a given alert (e.g. 'Gaia23bra')."""
    url = _lightcurve_url(alert_id)
    body = _http_get(url)
    return parse_lightcurve_csv(body, alert_id=alert_id, source_url=url)


# ---------------------------------------------------------------------------
# Cone search — find published Gaia microlensing alerts near (RA, Dec)
# ---------------------------------------------------------------------------

# Columns published in the Gaia Alerts index CSV (as of 2025). The file may
# gain columns over time; we key by alias again.
_INDEX_NAME_ALIASES = ("#name", "name", "alert", "alertname")
_INDEX_RA_ALIASES = ("radeg", "ra", "ra_deg", "ra(j2000)")
_INDEX_DEC_ALIASES = ("decdeg", "dec", "dec_deg", "dec(j2000)")
_INDEX_CLASS_ALIASES = ("class", "type", "classification")
_INDEX_DATE_ALIASES = ("date", "alertdate", "alert_date", "publisheddate")


@dataclass
class GaiaAlertEntry:
    alert_id: str
    ra: float
    dec: float
    classification: str
    date: str
    separation_arcsec: float

    def to_dict(self) -> dict:
        return {
            "alert_id": self.alert_id,
            "ra": self.ra,
            "dec": self.dec,
            "classification": self.classification,
            "date": self.date,
            "separation_arcsec": self.separation_arcsec,
        }


def _angular_separation_arcsec(ra1: float, dec1: float,
                                 ra2: float, dec2: float) -> float:
    """Haversine on the celestial sphere → arcseconds."""
    r1, r2 = math.radians(ra1), math.radians(ra2)
    d1, d2 = math.radians(dec1), math.radians(dec2)
    sin_dd = math.sin(0.5 * (d2 - d1))
    sin_dra = math.sin(0.5 * (r2 - r1))
    a = sin_dd * sin_dd + math.cos(d1) * math.cos(d2) * sin_dra * sin_dra
    a = min(1.0, max(0.0, a))
    return 2.0 * math.asin(math.sqrt(a)) * (180.0 / math.pi) * 3600.0


def parse_alerts_index_csv(text: str) -> List[dict]:
    """Parse the Gaia Alerts master index CSV. Returns a list of dict rows
    with the columns we care about (name, ra, dec, class, date)."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        raise ValueError("Empty alerts index CSV")
    name_col = _first_matching(header, _INDEX_NAME_ALIASES)
    ra_col = _first_matching(header, _INDEX_RA_ALIASES)
    dec_col = _first_matching(header, _INDEX_DEC_ALIASES)
    class_col = _first_matching(header, _INDEX_CLASS_ALIASES)
    date_col = _first_matching(header, _INDEX_DATE_ALIASES)
    if not all([name_col, ra_col, dec_col]):
        raise ValueError(
            f"Alerts index missing required columns. Header was: {header!r}"
        )
    idx = {name: i for i, name in enumerate(header)}
    out: List[dict] = []
    for row in reader:
        if not row or len(row) <= max(idx[name_col], idx[ra_col], idx[dec_col]):
            continue
        try:
            ra = float(row[idx[ra_col]])
            dec = float(row[idx[dec_col]])
        except ValueError:
            continue
        out.append({
            "name": row[idx[name_col]].strip(),
            "ra": ra,
            "dec": dec,
            "class": row[idx[class_col]].strip() if class_col else "",
            "date": row[idx[date_col]].strip() if date_col else "",
        })
    return out


# Simple in-process cache for the alerts index (small, changes slowly).
_INDEX_CACHE: dict = {"body": None}


def _load_alerts_index(force_refetch: bool = False) -> List[dict]:
    if _INDEX_CACHE["body"] is None or force_refetch:
        body = _http_get(_ALERTS_INDEX_CSV)
        _INDEX_CACHE["body"] = parse_alerts_index_csv(body)
    return _INDEX_CACHE["body"]


def search_alerts_near(ra: float, dec: float,
                        radius_arcsec: float = 60.0,
                        microlensing_only: bool = True,
                        max_results: int = 20) -> List[GaiaAlertEntry]:
    """Return published Gaia alerts within `radius_arcsec` of (ra, dec).

    Filtered to microlensing candidates by default (their `class` column
    contains 'microlensing' or 'ML'). Pass microlensing_only=False to
    include every published alert type at these coords.
    """
    index = _load_alerts_index()
    hits: List[GaiaAlertEntry] = []
    for row in index:
        cls = (row["class"] or "").lower()
        if microlensing_only and not (
            "microlensing" in cls or cls == "ml" or "lens" in cls
        ):
            continue
        sep = _angular_separation_arcsec(ra, dec, row["ra"], row["dec"])
        if sep <= radius_arcsec:
            hits.append(GaiaAlertEntry(
                alert_id=row["name"],
                ra=row["ra"],
                dec=row["dec"],
                classification=row["class"],
                date=row["date"],
                separation_arcsec=sep,
            ))
    hits.sort(key=lambda e: e.separation_arcsec)
    return hits[:max_results]
