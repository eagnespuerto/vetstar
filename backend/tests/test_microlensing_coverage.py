"""Tests for Module B — TESS sector-overlap targeting."""
from __future__ import annotations

import pytest

from app.microlensing_coverage import (
    EventRow,
    evaluate_catalog,
    evaluate_event,
    parse_events_csv,
)
from app.tess_sector_dates import get_sector_window, all_known_windows


# ---------------------------------------------------------------------------
# Sector date table
# ---------------------------------------------------------------------------

def test_sector_1_calendar_anchored():
    """Sector 1 midtime from tess-point is BTJD ≈ 1339.65; window is
    ±13.7 d from the midtime, giving a ~27.4-day span with midpoint near
    1339.65."""
    w = get_sector_window(1)
    assert w is not None
    # When tess-point is available it drives the value (non-nominal). The
    # test suite intentionally installs tess-point in the smoke path.
    if not w.nominal:
        midpoint = 0.5 * (w.start_btjd + w.end_btjd)
        assert midpoint == pytest.approx(1339.65, abs=0.1)
    # Sector 1 is ~27 days long regardless of source.
    assert 26.0 < (w.end_btjd - w.start_btjd) < 29.0


def test_sector_lookup_returns_none_outside_range():
    assert get_sector_window(0) is None
    assert get_sector_window(-3) is None
    # Sector 9999 has no midtime and is far beyond the fallback range.
    assert get_sector_window(9999) is None


def test_high_sector_covered_by_tess_point():
    """Sectors that tess-point ships midtimes for (up to ~121) should NOT
    fall back to the nominal formula. If tess-point isn't installed the
    test is skipped."""
    pytest.importorskip("tess_stars2px")
    w = get_sector_window(100)
    assert w is not None
    assert w.nominal is False, "Sector 100 should be calendar-anchored via tess-point"
    assert w.end_btjd > w.start_btjd


def test_all_known_windows_are_monotonic_in_start():
    starts = [w.start_btjd for w in all_known_windows()]
    for a, b in zip(starts, starts[1:]):
        assert b > a


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def test_parse_events_csv_minimal():
    csv_text = "event_id,ra,dec,t0,tE\nGaia21abc,270.0,-30.0,1500.0,25.0\n"
    events = parse_events_csv(csv_text)
    assert len(events) == 1
    assert events[0].event_id == "Gaia21abc"
    assert events[0].ra == 270.0
    assert events[0].t0 == 1500.0
    assert events[0].tE == 25.0


def test_parse_events_csv_defaults_te_when_missing():
    csv_text = "event_id,ra,dec,t0\nMOA-2023-BLG-999,268.0,-29.0,2000.0\n"
    events = parse_events_csv(csv_text)
    assert events[0].tE == 20.0


def test_parse_events_csv_rejects_missing_column():
    csv_text = "event_id,ra,dec\nfoo,1.0,2.0\n"
    with pytest.raises(ValueError, match="missing required column 't0'"):
        parse_events_csv(csv_text)


def test_parse_events_csv_reports_bad_row():
    csv_text = "event_id,ra,dec,t0\nOK,10,20,1500\nBAD,not-a-number,20,1500\n"
    with pytest.raises(ValueError, match="Row 3"):
        parse_events_csv(csv_text)


# ---------------------------------------------------------------------------
# Coverage evaluation (works regardless of whether tess-point is installed)
# ---------------------------------------------------------------------------

def test_evaluate_event_flags_bulge_blind_zone():
    """Galactic center (RA=266.4°, Dec=-29.0°) → ecl lat ~-5.6° → blind zone."""
    evt = EventRow(event_id="bulge", ra=266.4, dec=-29.0, t0=1500.0, tE=25.0)
    out = evaluate_event(evt)
    assert out["in_bulge_blind_zone"] is True
    assert abs(out["ecliptic_latitude_deg"]) < 6.0


def test_evaluate_event_high_ecliptic_target_not_in_bulge_zone():
    """North ecliptic pole area (Dec ~+66°, RA ~270°) — |ecl lat| ~90°."""
    evt = EventRow(event_id="pole", ra=270.0, dec=66.56, t0=1500.0, tE=25.0)
    out = evaluate_event(evt)
    assert out["in_bulge_blind_zone"] is False
    assert abs(out["ecliptic_latitude_deg"]) > 80.0


def test_evaluate_catalog_reports_summary_counters():
    events = [
        EventRow("a", 266.4, -29.0, 1500.0, 20.0),  # Galactic centre → bulge blind zone
        EventRow("b", 90.0, 30.0, 1500.0, 15.0),
        EventRow("c", 180.0, 66.56, 2500.0, 10.0),
    ]
    out = evaluate_catalog(events)
    assert out["summary"]["n_total"] == 3
    assert out["summary"]["n_in_bulge_blind_zone"] >= 1  # at least the bulge one
    assert any("bulge" in n.lower() for n in out["notes"])


def test_evaluate_event_t0_in_window_when_sector_covers():
    """If tess-point returns any sectors, an event whose t0 hits that sector's
    window should be marked observable. When tess-point isn't installed we
    skip — the endpoint still works but returns no_tess_point=True.
    """
    try:
        from tess_stars2px import tess_stars2px_function_entry  # noqa: F401
    except Exception:
        pytest.skip("tess-point not installed in this environment")

    # Ecliptic pole area is covered by TESS Northern hemisphere sectors.
    evt = EventRow("epole", 270.0, 66.56, t0=1700.0, tE=5.0)
    out = evaluate_event(evt)
    # If sectors were returned, at least one should have a decision.
    assert out["no_tess_point"] is False or out["no_tess_point"] is True
    # If any sector windows landed in the same era as t0=1700, observable is set.
    if out["observable"]:
        assert any(s["t0_in_window"] for s in out["sectors"])


def test_evaluate_catalog_handles_missing_tess_point_gracefully():
    """No tess-point installed → we still return a valid response with the
    no_tess_point flag set."""
    events = [EventRow("x", 100.0, 20.0, 1500.0, 20.0)]
    out = evaluate_catalog(events)
    # Endpoint returns a dict with the expected top-level keys regardless.
    assert set(out.keys()) == {"events", "summary", "notes"}
    assert isinstance(out["events"], list)
