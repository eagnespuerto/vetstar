"""
Radial-velocity lookup from the NASA Exoplanet Archive.

Queries the Planetary Systems Composite Parameters table (``pscomppars``) via
the archive's TAP service for the radial-velocity semi-amplitude K and the
orbital/stellar parameters needed to turn it into an absolute companion mass.

  TAP sync endpoint:
    https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=<ADQL>&format=json

NOTE: this performs a live HTTP request to exoplanetarchive.ipac.caltech.edu.
The deploy environment must allow that host. The exact column coverage of
``pscomppars`` (especially non-null ``pl_rvamp`` and ``tic_id`` matching)
should be verified against the live service; the parser below is tolerant of
missing fields and the caller falls back to a user-supplied RV time series
when no K is returned.
"""
from __future__ import annotations

import json
import logging
import ssl
import urllib.parse
import urllib.request
from typing import Optional

log = logging.getLogger("vetting")

TAP_URL = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
_COLS = "pl_name,hostname,tic_id,pl_orbper,pl_rvamp,pl_orbeccen,pl_orbincl,st_mass"


def _f(v) -> Optional[float]:
    try:
        if v in (None, ""):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_rv_rows(rows: list) -> dict:
    """
    Pick the best RV solution from archive rows (testable, no network).

    "Best" = the row with a non-null ``pl_rvamp`` (RV semi-amplitude). Among
    those, prefer the one that also has eccentricity and stellar mass.
    """
    if not rows:
        return {"available": False, "reason": "no rows returned"}
    with_k = [r for r in rows if _f(r.get("pl_rvamp")) is not None]
    if not with_k:
        return {"available": False, "reason": "no pl_rvamp in catalog rows"}

    def completeness(r):
        return sum(
            _f(r.get(c)) is not None
            for c in ("pl_orbeccen", "pl_orbincl", "st_mass", "pl_orbper")
        )

    best = max(with_k, key=completeness)
    return {
        "available": True,
        "pl_name": best.get("pl_name"),
        "hostname": best.get("hostname"),
        "k_ms": _f(best.get("pl_rvamp")),
        "orbital_period_d": _f(best.get("pl_orbper")),
        "eccentricity": _f(best.get("pl_orbeccen")),
        "inclination_deg": _f(best.get("pl_orbincl")),
        "stellar_mass_sun": _f(best.get("st_mass")),
        "source": "nasa_exoplanet_archive_pscomppars",
    }


def fetch_rv_from_archive(tic_id: int, timeout: int = 12) -> dict:
    """Query the archive TAP service for RV parameters of a TIC target."""
    adql = f"select {_COLS} from pscomppars where tic_id={int(tic_id)}"
    url = f"{TAP_URL}?{urllib.parse.urlencode({'query': adql, 'format': 'json'})}"
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(url, timeout=timeout, context=ctx) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # network, TLS, JSON, or HTTP error
        log.warning("Exoplanet Archive RV query failed for TIC %s: %s", tic_id, e)
        return {"available": False, "reason": f"archive query failed: {e}"}
    if isinstance(rows, dict):  # some error payloads come back as a dict
        return {"available": False, "reason": "unexpected archive response"}
    return parse_rv_rows(rows)
