"""Odd/even EB test must not run on a single transit event.

Regression for TIC 443666343 (TOI-3500.02) S101: one discrete dip in the
sector, but BLS folded it on a spurious short period, so the "odd" set held
only out-of-transit noise and the "even" set held the one real transit. The
depth difference came out at 22.57σ, raised ``odd_even_mismatch`` and turned
the verdict into "Eclipsing binary candidate 85%".
"""
from __future__ import annotations

import numpy as np

from backend.app.exominer import run_exominer
from backend.app.pipeline import detect_events, make_verdict, odd_even_check

from backend.tests.conftest import inject_box_transit

CADENCE_D = 2.0 / 1440.0   # 2-min cadence
SPAN_D = 27.0
DUR_D = 0.25               # 6 h
DEPTH = 0.006              # ~0.6 %, as in the S101 report


def _single_event_lc(rng):
    t = np.arange(0.0, SPAN_D, CADENCE_D)
    f = 1.0 + rng.normal(0.0, 3e-4, t.size)
    # One transit only: a period longer than the sector.
    f = inject_box_transit(t, f, period=350.31, t0=12.0, depth=DEPTH, duration=DUR_D)
    return t, f


def _eb_lc(rng, period=4.0, t0=2.0):
    """Alternating primary/secondary depths: a real odd/even mismatch."""
    t = np.arange(0.0, SPAN_D, CADENCE_D)
    f = 1.0 + rng.normal(0.0, 3e-4, t.size)
    f = inject_box_transit(t, f, period=2 * period, t0=t0, depth=0.010, duration=DUR_D)
    f = inject_box_transit(t, f, period=2 * period, t0=t0 + period, depth=0.004, duration=DUR_D)
    return t, f


def test_single_event_odd_even_not_available(rng):
    t, f = _single_event_lc(rng)
    events = detect_events(t, f)
    assert len(events) == 1

    # Spurious BLS period anchored on the one real event: cycle 0 is the
    # only even window with data, cycles -1 and +1 (odd) are pure noise.
    oe = odd_even_check(t, f, period=8.0, t0=12.0, duration=DUR_D, events=events)
    assert oe["available"] is False
    assert oe["reason"] == "insufficient odd or even transits"
    assert "flag_eb" not in oe

    verdict = make_verdict(
        n_events=len(events), physics={"available": False},
        centroid={"available": False}, odd_even=oe,
        secondary={"available": False}, bls_sde=12.0,
    )
    assert "odd_even_mismatch" not in verdict["flags"]
    assert verdict["category"] != "eclipsing_binary_candidate"


def test_single_event_without_event_gate_still_misfires(rng):
    """Documents the bug: the in-window point count alone lets noise in."""
    t, f = _single_event_lc(rng)
    oe = odd_even_check(t, f, period=8.0, t0=12.0, duration=DUR_D)
    assert oe["available"] is True and oe["flag_eb"] is True


def test_real_eb_still_flagged(rng):
    t, f = _eb_lc(rng)
    events = detect_events(t, f)
    assert len(events) >= 4
    oe = odd_even_check(t, f, period=4.0, t0=2.0, duration=DUR_D, events=events)
    assert oe["available"] is True
    assert oe["n_odd_events"] >= 1 and oe["n_even_events"] >= 1
    assert oe["flag_eb"] is True


def test_exominer_single_event_no_odd_even_contribution(rng):
    t, f = _single_event_lc(rng)
    res = run_exominer(t=t, f=f, mom_x=None, mom_y=None,
                       period=8.0, t0=12.0, duration=DUR_D)
    sc = res["scalars"]
    assert sc["odd_even_available"] is False
    assert sc["odd_even_sigma"] == 0.0


def test_exominer_real_eb_keeps_odd_even(rng):
    t, f = _eb_lc(rng)
    res = run_exominer(t=t, f=f, mom_x=None, mom_y=None,
                       period=4.0, t0=2.0, duration=DUR_D)
    sc = res["scalars"]
    assert sc["odd_even_available"] is True
    assert sc["odd_even_sigma"] > 0.0
