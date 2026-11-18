"""Plain-language guide to the LEO-Vetter tests that NEMESIS runs.

NEMESIS (Feliz et al. 2021; NEMESIS II, Feliz et al. 2026) runs LEO-Vetter
(Kunimoto et al. 2025) on every detection and appends the metrics to the
bestfit CSV as ``LEOVetter_*`` columns. NEMESIS's own
``check_ALL_thresholds`` runs the tests in a fixed order, adding the
centroid-offset test only when an ``offset_qual`` metric exists. This module
keeps that order and adds one-line explanations for new users.

The order must match ``check_ALL_thresholds`` in nemesis_tess/pipeline.py
(pinned commit in requirements.txt).
"""
from __future__ import annotations

# (key, short title, group, plain-language "what a flag means")
# group "FA" = false alarm (noise or systematics, not a real astrophysical dip)
# group "FP" = false positive (a real dip, but probably not a planet)
LEO_TESTS: list[tuple[str, str, str, str]] = [
    ("weak", "Signal strength", "FA",
     "The dip is too weak compared with the noise to trust."),
    ("invalid_transits", "Enough good transits", "FA",
     "Too few individual transits have usable data."),
    ("bad_shape", "Transit shape", "FA",
     "The folded dip does not look like a transit."),
    ("non_unique", "Uniqueness", "FA",
     "Other dips of similar size appear at other phases, so this one may be noise."),
    ("chases", "Single-event check (CHASES)", "FA",
     "Individual transits do not stand out from nearby bumps in the light curve."),
    ("dmm", "Mean vs median depth", "FA",
     "The average and the median depth disagree, so one or two events dominate."),
    ("single_event", "Single dominant event", "FA",
     "Most of the signal comes from one event, not a repeating transit."),
    ("bad_fit", "Model fit", "FA",
     "A transit model does not fit the data well."),
    ("sinusoidal", "Sinusoid check", "FA",
     "The signal looks like smooth stellar variability, not a transit."),
    ("unphysical_duration", "Duration", "FA",
     "The transit lasts too long or too short for a planet around this star."),
    ("asymmetric", "Symmetry", "FA",
     "Ingress and egress differ, which real transits rarely do."),
    ("chi", "Consistency (chi-square)", "FA",
     "Transit depths vary between events more than noise allows."),
    ("data_gapped", "Data gaps", "FA",
     "Transits fall mostly in or next to data gaps."),
    ("odd_even", "Odd/even depths", "FP",
     "Odd and even transits differ in depth: likely an eclipsing binary at twice the period."),
    ("vshaped", "V-shape", "FP",
     "The dip is V-shaped (grazing), common for eclipsing binaries."),
    ("large", "Size", "FP",
     "The implied companion is too big to be a planet."),
    ("secondary", "Secondary eclipse", "FP",
     "A second dip near phase 0.5 points to a self-luminous companion (a star)."),
    ("offset", "Centroid offset", "FP",
     "The light shifts during transit, so the dip may come from a nearby star."),
]

LEO_PREFIX = "LEOVetter_"


def leo_metrics_from_bestfit(row: dict) -> dict:
    """Strip the ``LEOVetter_`` prefix from a bestfit-CSV row."""
    return {k[len(LEO_PREFIX):]: v for k, v in row.items() if k.startswith(LEO_PREFIX)}


def label_leo_results(metrics: dict, flags: list[bool], strings: list[str]) -> list[dict]:
    """Pair NEMESIS's ordered flag/string output with titles and explanations.

    ``flags`` and ``strings`` come from
    ``check_ALL_thresholds(metrics, collect_output_strings=True)``.
    """
    has_offset = ("offset_qual" in metrics) or ("offset" in metrics)
    tests = LEO_TESTS if has_offset else LEO_TESTS[:-1]
    if len(flags) != len(tests):
        raise ValueError(
            f"LEO-Vetter returned {len(flags)} flags but {len(tests)} tests were expected; "
            "has the NEMESIS test order changed?"
        )
    out = []
    for (key, title, group, meaning), flag, detail in zip(tests, flags, strings):
        out.append({
            "key": key,
            "title": title,
            "group": group,
            "passed": not bool(flag),
            "meaning": meaning,
            "detail": str(detail),
        })
    return out


def leo_disposition(results: list[dict]) -> str:
    """Overall LEO-Vetter label used by NEMESIS II: FA, FP or PC."""
    if any(r["group"] == "FA" and not r["passed"] for r in results):
        return "FA"
    if any(r["group"] == "FP" and not r["passed"] for r in results):
        return "FP"
    return "PC"
