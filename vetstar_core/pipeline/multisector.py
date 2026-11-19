"""Multi-sector analysis: vet up to MAX_SECTORS sectors and group their
events into objects.
"""
from __future__ import annotations

import numpy as np

from .detection import BLS_KNOWN_PERIOD_TOL_FRAC
from .models import DURATION_MATCH_TOL_H, PERIOD_MATCH_TOL_FRAC, durations_consistent, periods_consistent
from .plots import _make_timeline_plot


# ----------------------------------------------------------------------
# Multi-sector analysis
# ----------------------------------------------------------------------

# Multi-sector runs are intentionally capped: from each of up to MAX_SECTORS
# sectors we take up to EVENTS_PER_SECTOR (2) of the deepest events, then group
# them into up to MAX_OBJECTS distinct objects by transit duration. Each
# object is confirmed by checking its members share the same duration and
# period across sectors. MAX_OBJECTS is the *default*; the caller can request
# more (up to MAX_OBJECTS_HARD_CAP) when the deepest dips are blends or false
# positives that mask the real TOI of interest.
MAX_SECTORS = 3
EVENTS_PER_SECTOR = 2
MAX_OBJECTS = 2
MAX_OBJECTS_HARD_CAP = MAX_SECTORS * EVENTS_PER_SECTOR  # 10 — every event its own bucket


def _cluster_events_into_objects(reps: list, tol_h: float, max_objects: int = MAX_OBJECTS) -> list:
    """Group representative events into <=max_objects clusters by duration.

    Events whose durations sit within ``tol_h`` of each other join the same
    object; a gap larger than ``tol_h`` starts a new object. If that yields
    more than ``max_objects`` clusters, the two closest are merged until the
    cap is met. Returns a list of lists of rep dicts.
    """
    if not reps:
        return []
    s = sorted(reps, key=lambda e: e["duration_h"])
    clusters = [[s[0]]]
    for prev, cur in zip(s, s[1:]):
        if cur["duration_h"] - prev["duration_h"] <= tol_h:
            clusters[-1].append(cur)
        else:
            clusters.append([cur])
    while len(clusters) > max_objects:
        gaps = [
            (clusters[i + 1][0]["duration_h"] - clusters[i][-1]["duration_h"], i)
            for i in range(len(clusters) - 1)
        ]
        _, i = min(gaps, key=lambda g: g[0])
        clusters[i].extend(clusters[i + 1])
        del clusters[i + 1]
    return clusters


def run_multisector_analysis(
    sector_results: list,
    period_d: float | None = None,
    t0: float | None = None,
    detect_threshold: float = 0.997,
    detect_min_snr: float = 4.0,
    high_variability: bool = False,
    secondary_sigma: float = 3.0,
    odd_even_sigma: float = 3.0,
    duration_tol_h: float = DURATION_MATCH_TOL_H,
    max_objects: int = MAX_OBJECTS,
    known_period_days: float | None = None,
) -> dict:
    """
    Given vetting results from multiple sectors, build:
      - A detection timeline: which sectors showed events
      - Up to EVENTS_PER_SECTOR representative (deepest) events per sector,
        across up to MAX_SECTORS sectors
      - Grouping of those events into up to ``max_objects`` distinct objects by
        transit duration, each confirmed to share the SAME duration (within
        ``duration_tol_h`` hours) and the SAME period across sectors
      - A consensus ephemeris (period refinement)

    Returns a plain dict — JSON-serialisable, ships to frontend.
    """
    if not sector_results:
        return {"error": "No sector results provided."}

    # Clamp max_objects to [1, MAX_OBJECTS_HARD_CAP]. Anything beyond the hard
    # cap (one bucket per representative event) is meaningless.
    try:
        max_objects = int(max_objects)
    except (TypeError, ValueError):
        max_objects = MAX_OBJECTS
    max_objects = max(1, min(max_objects, MAX_OBJECTS_HARD_CAP))

    # Cap to MAX_SECTORS, preferring sectors that show a dip and, among those,
    # the deepest detection (most diagnostic).
    ranked = sorted(
        sector_results,
        key=lambda sr: (
            len(sr[1].events) > 0,
            max((e["depth"] for e in sr[1].events), default=0.0),
        ),
        reverse=True,
    )
    sector_results = ranked[:MAX_SECTORS]

    timeline = []
    representative_events = []  # up to EVENTS_PER_SECTOR per sector with a dip

    for sec_num, res in sector_results:
        has_dip = len(res.events) > 0
        # Up to EVENTS_PER_SECTOR deepest events from this sector.
        top_evs = sorted(res.events, key=lambda e: e["depth"], reverse=True)[:EVENTS_PER_SECTOR]
        deepest_depth = top_evs[0]["depth"] if top_evs else 0.0
        timeline.append({
            "sector": sec_num,
            "n_events": len(res.events),
            "has_dip": has_dip,
            "deepest_depth_pct": round(deepest_depth * 100, 3),
            "bls_period_d": res.bls.get("period"),
            "bls_sde": res.bls.get("sde"),
            "verdict": res.verdict.get("category"),
        })
        for rep in top_evs:
            representative_events.append({
                "sector": sec_num,
                "t_center": round(0.5 * (rep["t_start"] + rep["t_end"]), 5),
                "duration_h": round(rep["duration_d"] * 24.0, 4),
                "depth_pct": round(rep["depth"] * 100, 4),
                "bls_period_d": res.bls.get("period"),
            })

    n_with_dip = sum(1 for x in timeline if x["has_dip"])
    n_total = len(timeline)

    # Period analysis: collect BLS peaks from all sectors
    period_estimates = [
        x["bls_period_d"] for x in timeline
        if x["bls_period_d"] and x["bls_sde"] and x["bls_sde"] > 6
    ]
    # When the user supplied a known period, every per-sector BLS was anchored
    # to it: include every finite peak so the refined median is meaningful even
    # when blind-search SDE would have been low.
    if known_period_days is not None and np.isfinite(known_period_days):
        constrained_estimates = [
            sr[1].bls.get("period")
            for sr in sector_results
            if sr[1].bls.get("constrained") and sr[1].bls.get("period")
        ]
        if len(constrained_estimates) >= len(period_estimates):
            period_estimates = constrained_estimates
    period_consensus = None
    refined_median_d = None
    refined_std_d = None
    if len(period_estimates) >= 2:
        p_arr = np.array(period_estimates)
        refined_median_d = float(np.median(p_arr))
        refined_std_d = float(np.std(p_arr))

    if known_period_days is not None and np.isfinite(known_period_days):
        per_sector_matches = [
            (x["sector"], (sr[1].bls.get("matched_harmonic")))
            for x, sr in zip(timeline, sector_results)
            if sr[1].bls.get("constrained")
        ]
        harmonics = {m for _, m in per_sector_matches if m}
        period_consensus = {
            "value_d": float(known_period_days),
            "source": "user known period (constrained BLS)",
            "harmonic_disagreement": len(harmonics) > 1,
            "per_sector_matches": per_sector_matches,
            "no_constrained_sectors": len(per_sector_matches) == 0,
        }
        if refined_median_d is not None:
            period_consensus["refined_median_d"] = refined_median_d
            period_consensus["refined_std_d"] = refined_std_d
    elif period_d:
        period_consensus = {"value_d": period_d, "source": "external (ExoFOP/user)"}
        if refined_median_d is not None:
            period_consensus["refined_median_d"] = refined_median_d
            period_consensus["refined_std_d"] = refined_std_d
    elif refined_median_d is not None:
        period_consensus = {
            "value_d": refined_median_d,
            "std_d": refined_std_d,
            "source": f"median of {len(period_estimates)} sector BLS peaks",
        }

    # --- Group events into up to ``max_objects`` distinct objects by duration -
    clusters = _cluster_events_into_objects(
        representative_events, duration_tol_h, max_objects=max_objects,
    )
    objects = []
    for oid, members in enumerate(clusters, start=1):
        durs = [m["duration_h"] for m in members]
        pers = [m["bls_period_d"] for m in members]
        sectors = sorted({m["sector"] for m in members})
        dur_ok = durations_consistent(durs, tol_h=duration_tol_h)
        if known_period_days is not None and np.isfinite(known_period_days):
            # With a user-supplied known period, every sector's BLS peak must
            # agree with that anchor (not just with each other) to within the
            # constrained-BLS tolerance.
            per_tol = BLS_KNOWN_PERIOD_TOL_FRAC
            finite_pers = [p for p in pers if p is not None and np.isfinite(p) and p > 0]
            per_ok = bool(finite_pers) and all(
                abs(p - known_period_days) / known_period_days <= per_tol
                for p in finite_pers
            )
        else:
            per_ok = periods_consistent(pers, tol_frac=PERIOD_MATCH_TOL_FRAC)
        spread = round(max(durs) - min(durs), 4) if durs else None
        # "Confirmed" requires the same object seen in >=2 sectors with matching
        # duration and period.
        confirmed = bool(len(sectors) >= 2 and dur_ok and per_ok)
        if len(sectors) < 2:
            note = (
                f"Seen in only {len(sectors)} sector — needs a second sector to "
                "cross-confirm duration and period."
            )
        elif confirmed:
            note = (
                f"Confirmed across {len(sectors)} sectors: matching duration "
                f"(spread {spread:.3f} h ≤ {duration_tol_h:.3f} h) and period."
            )
        else:
            bits = []
            if not dur_ok:
                bits.append(f"duration spread {spread:.3f} h > {duration_tol_h:.3f} h")
            if not per_ok:
                bits.append("periods disagree")
            note = "Inconsistent across sectors: " + "; ".join(bits) + "."
        objects.append({
            "object_id": oid,
            "n_events": len(members),
            "sectors": sectors,
            "duration_h_median": round(float(np.median(durs)), 4) if durs else None,
            "duration_spread_h": spread,
            "period_d_median": (
                round(float(np.median([p for p in pers if p])), 5)
                if any(pers) else None
            ),
            "depth_pct_median": round(float(np.median([m["depth_pct"] for m in members])), 4),
            "durations_consistent": dur_ok,
            "periods_consistent": per_ok,
            "confirmed_multisector": confirmed,
            "members": members,
            "note": note,
        })

    n_objects = len(objects)
    if n_objects == 0:
        objects_summary = "No repeating events to group into objects."
    elif n_objects == 1:
        objects_summary = "One object identified. " + objects[0]["note"]
    else:
        dur_list = ", ".join(
            f"{o['duration_h_median']:.2f} h" for o in objects if o["duration_h_median"]
        )
        objects_summary = (
            f"{n_objects} distinct objects identified by differing transit "
            f"durations ({dur_list})."
        )

    # Anchor t0 for any downstream folding.
    if t0 is None:
        for _, res in sector_results:
            if res.bls.get("t0"):
                t0 = res.bls["t0"]
                break

    # Detection timeline plot
    timeline_plot = _make_timeline_plot(timeline)

    analysis_settings = {
        "detect_threshold": float(detect_threshold),
        "detect_min_snr": float(detect_min_snr),
        "high_variability": bool(high_variability),
        "secondary_sigma": float(secondary_sigma),
        "odd_even_sigma": float(odd_even_sigma),
    }

    return {
        "n_sectors_observed": n_total,
        "n_sectors_with_detections": n_with_dip,
        "detection_rate": round(n_with_dip / n_total, 3) if n_total else 0,
        "max_sectors": MAX_SECTORS,
        "events_per_sector": EVENTS_PER_SECTOR,
        "max_objects": max_objects,
        "max_objects_default": MAX_OBJECTS,
        "max_objects_hard_cap": MAX_OBJECTS_HARD_CAP,
        "duration_tol_h": duration_tol_h,
        "timeline": timeline,
        "period_consensus": period_consensus,
        "n_objects_detected": n_objects,
        "objects": objects,
        "timeline_plot": timeline_plot,
        "settings": analysis_settings,
        "summary": (
            f"{n_with_dip}/{n_total} sectors show a dip event. "
            + (f"Consensus period ≈ {period_consensus['value_d']:.4f} d. "
               if period_consensus else "Period not well-constrained. ")
            + objects_summary
        ),
    }
