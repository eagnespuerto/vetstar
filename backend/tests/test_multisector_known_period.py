"""Multi-sector reconciliation with a known period."""
import numpy as np
import pytest

from backend.app.pipeline import (
    BLS_KNOWN_PERIOD_TOL_FRAC,
    run_full_vetting,
    run_multisector_analysis,
    StarInfo,
)
from backend.tests.test_pipeline_known_period import _inject_box_transit


def _vet_sector(seed, period_d, known):
    rng = np.random.default_rng(seed)
    t, f, fe = _inject_box_transit(rng, period_d=period_d)
    star = StarInfo(tic_id=0, ra=0.0, dec=0.0, tmag=10.0,
                    teff=5800.0, radius=1.0, mass=1.0)
    return run_full_vetting(
        t, f, fe, quality=None, mom_x=None, mom_y=None,
        star=star, known_period_days=known,
    )


def test_consensus_uses_user_known_period_label():
    sector_results = [(s, _vet_sector(s, 3.1, 3.1)) for s in (1, 2, 3)]
    out = run_multisector_analysis(sector_results, period_d=3.1,
                                   known_period_days=3.1)
    pc = out["period_consensus"]
    assert pc["source"] == "user known period (constrained BLS)"
    assert pc["value_d"] == pytest.approx(3.1)
    assert pc.get("harmonic_disagreement") is False
    assert "refined_median_d" in pc
    assert abs(pc["refined_median_d"] - 3.1) / 3.1 <= BLS_KNOWN_PERIOD_TOL_FRAC


def test_harmonic_disagreement_flagged():
    sr1 = _vet_sector(10, 3.1, 3.1)
    sr2 = _vet_sector(11, 3.1, 3.1)
    sr3 = _vet_sector(12, 3.1, 3.1)
    sr3.bls["matched_harmonic"] = "2P"
    out = run_multisector_analysis(
        [(1, sr1), (2, sr2), (3, sr3)],
        period_d=3.1, known_period_days=3.1,
    )
    pc = out["period_consensus"]
    assert pc["harmonic_disagreement"] is True
    assert "per_sector_matches" in pc


def test_periods_consistent_uses_tightened_tolerance_when_constrained():
    sr1 = _vet_sector(20, 3.1, 3.1)
    sr2 = _vet_sector(21, 3.1, 3.1)
    sr2.bls["period"] = 3.1 * 1.03
    out = run_multisector_analysis(
        [(1, sr1), (2, sr2)],
        period_d=3.1, known_period_days=3.1,
    )
    objs = out.get("objects", [])
    assert len(objs) >= 1, "expected at least one clustered object"
    assert objs[0]["periods_consistent"] is False
