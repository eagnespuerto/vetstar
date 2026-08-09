"""Module B — TESS sector-overlap targeting.

Given an event catalog (event_id, ra, dec, t0, tE), determine which events
are actually observable in TESS: coordinates covered AND peak time falling
within the sector's observation window (± a margin of a few * tE so the
wings are in-frame too).

Critical caveat surfaced in the response: the Galactic bulge — where
microlensing rates are highest — lies at ecliptic latitude ~-5.5°, in TESS's
*thinnest* coverage zone (cameras start ~6° off the ecliptic). Most classic
bulge events will therefore come back not observable. This is expected.
"""
from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .tess_sector_dates import calendar_source, get_sector_window, SectorWindow


# ---------------------------------------------------------------------------
# RA/Dec → TIC resolution (for the Module B → Module A LC autoload handoff)
# ---------------------------------------------------------------------------

def resolve_ra_dec_to_tic(ra: float, dec: float,
                          radius_arcsec: float = 30.0) -> dict:
    """Query the MAST TIC catalog and return the closest TIC to (ra, dec).

    Returns a dict {tic_id, resolved_ra, resolved_dec, separation_arcsec,
    tmag} or raises RuntimeError if nothing is found within `radius_arcsec`.

    Requires astroquery (already a Vetstar dep). Lazily imported so
    unit-tests can run without hitting the network.
    """
    from astropy import units as u
    from astropy.coordinates import SkyCoord
    from astroquery.mast import Catalogs

    coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")
    result = Catalogs.query_region(
        coordinates=coord,
        radius=radius_arcsec * u.arcsec,
        catalog="TIC",
    )
    if result is None or len(result) == 0:
        raise RuntimeError(
            f"No TIC entry found within {radius_arcsec:g}\" of "
            f"RA={ra:.5f}, Dec={dec:+.5f}. Try a wider search radius."
        )
    # Sort by angular separation column (`dstArcSec`) if present, else recompute.
    row = None
    if "dstArcSec" in result.colnames:
        result.sort("dstArcSec")
        row = result[0]
        sep = float(row["dstArcSec"])
    else:
        # Compute separations ourselves.
        cand = SkyCoord(ra=result["ra"] * u.deg, dec=result["dec"] * u.deg)
        seps = coord.separation(cand).to(u.arcsec).value
        idx = int(seps.argmin())
        row = result[idx]
        sep = float(seps[idx])
    return {
        "tic_id": int(row["ID"]),
        "resolved_ra": float(row["ra"]),
        "resolved_dec": float(row["dec"]),
        "separation_arcsec": sep,
        "tmag": float(row["Tmag"]) if "Tmag" in row.colnames and row["Tmag"] is not None else None,
    }


# Default: require the peak plus at least 1x tE on either side to fall
# inside the sector window (so wings are visible for shape characterization).
DEFAULT_WING_MARGIN_TE = 1.0


# ---------------------------------------------------------------------------
# tess-point wrapper
# ---------------------------------------------------------------------------

def _tess_point_lookup(ra: float, dec: float) -> List[Tuple[int, int, int]]:
    """Return (sector, camera, ccd) triples for a coordinate.

    Uses the `tess-point` package if installed; returns an empty list if not
    (the endpoint will still work but flag every event as `no_tess_point`).
    """
    try:
        # Import lazily so the module still loads on machines without tess-point.
        from tess_stars2px import tess_stars2px_function_entry
    except Exception:
        return []
    try:
        # ticid input is required by tess-stars2px but is only used to key the
        # output; use 0 for our single-target lookups.
        (outID, outEclipLong, outEclipLat, outSec, outCam,
         outCcd, outColPix, outRowPix, scinfo) = tess_stars2px_function_entry(
            0, ra, dec,
        )
    except Exception:
        return []
    triples: List[Tuple[int, int, int]] = []
    for i in range(len(outSec)):
        sec = int(outSec[i]); cam = int(outCam[i]); ccd = int(outCcd[i])
        if sec > 0:
            triples.append((sec, cam, ccd))
    return triples


def _ecliptic_latitude(ra_deg: float, dec_deg: float) -> float:
    """Return the ecliptic latitude in degrees for a J2000 (ra, dec).

    Simple rotation with the mean obliquity ε = 23.4392911°. No precession
    correction — good enough for the "you're in the bulge blind spot" note.
    """
    eps = math.radians(23.4392911)
    ra = math.radians(ra_deg)
    dec = math.radians(dec_deg)
    sin_beta = (math.sin(dec) * math.cos(eps)
                - math.cos(dec) * math.sin(eps) * math.sin(ra))
    sin_beta = max(-1.0, min(1.0, sin_beta))
    return math.degrees(math.asin(sin_beta))


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = ("event_id", "ra", "dec", "t0", "tE")


@dataclass
class EventRow:
    event_id: str
    ra: float
    dec: float
    t0: float
    tE: float


def parse_events_csv(text: str) -> List[EventRow]:
    """Parse a CSV blob into EventRows. Header row is required.

    Recognized columns: event_id, ra, dec, t0, tE. Extra columns are ignored.
    `tE` is optional — defaults to 20 days if missing (typical bulge event).
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("CSV appears to be empty (no header row).")
    normalized = {name.strip().lower(): name for name in reader.fieldnames}
    for col in ("event_id", "ra", "dec", "t0"):
        if col not in normalized:
            raise ValueError(f"CSV is missing required column '{col}'. "
                             f"Got columns: {reader.fieldnames}")
    te_col = normalized.get("te")

    out: List[EventRow] = []
    for i, row in enumerate(reader, start=2):
        try:
            evt = EventRow(
                event_id=str(row[normalized["event_id"]]).strip(),
                ra=float(row[normalized["ra"]]),
                dec=float(row[normalized["dec"]]),
                t0=float(row[normalized["t0"]]),
                tE=float(row[te_col]) if te_col and row.get(te_col) else 20.0,
            )
        except (KeyError, ValueError, TypeError) as e:
            raise ValueError(f"Row {i}: could not parse ({e}). Row was: {row}")
        out.append(evt)
    return out


# ---------------------------------------------------------------------------
# Coverage logic
# ---------------------------------------------------------------------------

@dataclass
class SectorCoverage:
    sector: int
    camera: int
    ccd: int
    window: Optional[SectorWindow]
    t0_in_window: bool     # is t0 alone inside [start, end]?
    wings_in_window: bool  # is [t0 - margin*tE, t0 + margin*tE] inside [start, end]?

    def to_dict(self) -> dict:
        return {
            "sector": self.sector,
            "camera": self.camera,
            "ccd": self.ccd,
            "window": self.window.to_dict() if self.window else None,
            "t0_in_window": self.t0_in_window,
            "wings_in_window": self.wings_in_window,
        }


def evaluate_event(evt: EventRow, margin_te: float = DEFAULT_WING_MARGIN_TE) -> dict:
    """Return the coverage assessment for a single event."""
    triples = _tess_point_lookup(evt.ra, evt.dec)

    per_sector: List[SectorCoverage] = []
    for sec, cam, ccd in triples:
        w = get_sector_window(sec)
        if w is None:
            per_sector.append(SectorCoverage(sec, cam, ccd, None, False, False))
            continue
        t0_in = w.start_btjd <= evt.t0 <= w.end_btjd
        wing_lo = evt.t0 - margin_te * evt.tE
        wing_hi = evt.t0 + margin_te * evt.tE
        wings_in = wing_lo >= w.start_btjd and wing_hi <= w.end_btjd
        per_sector.append(SectorCoverage(sec, cam, ccd, w, t0_in, wings_in))

    observable = any(c.t0_in_window for c in per_sector)
    observable_with_wings = any(c.wings_in_window for c in per_sector)
    ecl_lat = _ecliptic_latitude(evt.ra, evt.dec)
    in_bulge_blind_zone = abs(ecl_lat) < 6.0

    return {
        "event_id": evt.event_id,
        "ra": evt.ra,
        "dec": evt.dec,
        "t0": evt.t0,
        "tE": evt.tE,
        "ecliptic_latitude_deg": ecl_lat,
        "in_bulge_blind_zone": in_bulge_blind_zone,
        "sectors": [c.to_dict() for c in per_sector],
        "observable": observable,
        "observable_with_wings": observable_with_wings,
        "no_tess_point": not triples,
    }


def evaluate_catalog(events: List[EventRow],
                     margin_te: float = DEFAULT_WING_MARGIN_TE) -> dict:
    """Evaluate a whole catalog and add summary counters."""
    rows = [evaluate_event(evt, margin_te=margin_te) for evt in events]
    n_observable = sum(1 for r in rows if r["observable"])
    n_with_wings = sum(1 for r in rows if r["observable_with_wings"])
    n_bulge_blind = sum(1 for r in rows if r["in_bulge_blind_zone"])
    n_no_tp = sum(1 for r in rows if r["no_tess_point"])

    notes: List[str] = []
    if n_no_tp:
        notes.append(
            f"{n_no_tp} events could not be resolved by tess-point (package "
            f"missing or coordinates off-sky). Install `tess-point` to enable."
        )
    notes.append(
        f"Sector date windows: {calendar_source()}. Rows flagged "
        f"`nominal: true` are anchor-based approximations (±3 days); "
        f"tess-point-sourced rows are calendar-anchored to per-sector midtimes."
    )
    if n_bulge_blind:
        notes.append(
            f"{n_bulge_blind} events sit at |ecliptic latitude| < 6° (the "
            f"Galactic-bulge blind zone TESS cameras skip). Most classic "
            f"bulge microlensing events end up here — expected, not a bug."
        )

    return {
        "events": rows,
        "summary": {
            "n_total": len(rows),
            "n_observable": n_observable,
            "n_observable_with_wings": n_with_wings,
            "n_in_bulge_blind_zone": n_bulge_blind,
            "n_no_tess_point": n_no_tp,
            "margin_te_used": margin_te,
        },
        "notes": notes,
    }
