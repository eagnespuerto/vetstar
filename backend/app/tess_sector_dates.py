"""TESS sector observation windows in BTJD.

Sourced from `tess-point`'s bundled `TESS_Spacecraft_Pointing_Data.midtimes`
when the package is importable — this is the authoritative mission
calendar the tess-point maintainers keep updated. From each per-sector
midtime we form a fixed window ±13.7 days (the half-length of TESS's
nominal two-orbit sector: two ~13.7-day orbits with a ~1-day perigee
downlink gap in the middle). Windows are flagged `nominal=False` when
sourced from tess-point.

When tess-point is unavailable we fall back to an anchor-based
approximation (Sector 1 midtime + N * 27.4-day cadence) — flagged
`nominal=True`. If you need finer-than-day precision for a specific
sector, override the returned window from the actual per-orbit
`t_min`/`t_max` in the SPOC/QLP data products for that sector.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional

# BJD - BTJD offset (TESS convention).
_BTJD_OFFSET = 2_457_000.0

# Half the nominal two-orbit sector length in days. Sectors run ~13.7 d + ~1 d
# gap + ~13.7 d = ~27.4 d total, but the perigee downlink gap sits inside the
# window so from an observability standpoint the whole ~27.4-day span counts.
_HALF_SECTOR_D = 13.7

# Anchor for the fallback nominal-cadence formula (used only when tess-point
# is unavailable). Sector 1 opened on 2018-07-25 = BTJD 1325.29.
_SECTOR1_START_BTJD = 1325.29
_NOMINAL_SECTOR_LENGTH_D = 27.4
_NOMINAL_INTER_SECTOR_GAP_D = 1.0

# Highest sector to synthesise a fallback window for when tess-point is missing.
_FALLBACK_MAX_SECTOR = 121


@dataclass(frozen=True)
class SectorWindow:
    sector: int
    start_btjd: float
    end_btjd: float
    nominal: bool     # True only when tess-point wasn't available and the
                      # window came from the anchor-based fallback formula.

    def to_dict(self) -> dict:
        return {
            "sector": self.sector,
            "start_btjd": self.start_btjd,
            "end_btjd": self.end_btjd,
            "nominal": self.nominal,
        }


def _load_tess_point_midtimes() -> Dict[int, float]:
    """Try to load per-sector midtimes (BTJD) from tess-point. Returns an
    empty dict if the package isn't installed — callers should then fall
    back to the anchor formula."""
    try:
        from tess_stars2px import TESS_Spacecraft_Pointing_Data
    except Exception:
        return {}
    try:
        sci = TESS_Spacecraft_Pointing_Data()
        midtimes_jd = sci.midtimes
    except Exception:
        return {}
    # midtimes[i] corresponds to sector (i + 1) — the array is 0-indexed.
    return {i + 1: float(mt) - _BTJD_OFFSET for i, mt in enumerate(midtimes_jd)}


# Loaded once at import time; harmless if empty.
_TESS_POINT_MIDTIMES_BTJD: Dict[int, float] = _load_tess_point_midtimes()


def _nominal_fallback(sector: int) -> tuple[float, float]:
    """Anchor-based nominal window when tess-point isn't available."""
    if sector < 1:
        raise ValueError(f"sector must be >= 1; got {sector}")
    offset = (sector - 1) * (_NOMINAL_SECTOR_LENGTH_D + _NOMINAL_INTER_SECTOR_GAP_D)
    start = _SECTOR1_START_BTJD + offset
    return (start, start + _NOMINAL_SECTOR_LENGTH_D)


def get_sector_window(sector: int) -> Optional[SectorWindow]:
    """Look up a sector's observation window.

    Returns a calendar-anchored window (from tess-point midtimes) when
    available, otherwise a nominal-cadence approximation. Returns None
    for sectors below 1 or beyond the fallback range when tess-point is
    unavailable.
    """
    if sector < 1:
        return None

    if sector in _TESS_POINT_MIDTIMES_BTJD:
        mid = _TESS_POINT_MIDTIMES_BTJD[sector]
        return SectorWindow(
            sector=sector,
            start_btjd=mid - _HALF_SECTOR_D,
            end_btjd=mid + _HALF_SECTOR_D,
            nominal=False,
        )

    if sector > _FALLBACK_MAX_SECTOR:
        return None
    s, e = _nominal_fallback(sector)
    return SectorWindow(sector=sector, start_btjd=s, end_btjd=e, nominal=True)


def all_known_windows() -> Iterable[SectorWindow]:
    """Iterate every window we can currently return."""
    top = max(
        max(_TESS_POINT_MIDTIMES_BTJD.keys(), default=0),
        _FALLBACK_MAX_SECTOR,
    )
    for s in range(1, top + 1):
        w = get_sector_window(s)
        if w is not None:
            yield w


def calendar_source() -> str:
    """Report which source powered the loaded calendar (for the response notes)."""
    if _TESS_POINT_MIDTIMES_BTJD:
        return (
            f"tess-point midtimes ({len(_TESS_POINT_MIDTIMES_BTJD)} sectors, "
            f"±{_HALF_SECTOR_D:g}-day nominal half-length)"
        )
    return (
        f"anchor-based fallback (Sector 1 = BTJD {_SECTOR1_START_BTJD} + "
        f"N·{_NOMINAL_SECTOR_LENGTH_D + _NOMINAL_INTER_SECTOR_GAP_D:g} d)"
    )
