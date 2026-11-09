"""Single-sector driver: run_full_vetting ties the pipeline together.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from .models import StarInfo, VettingResult
from .catalogs import apply_known_object_override, crossmatch_known_object
from .verdict import centroid_check, make_verdict, measure_shape, odd_even_check, physics_interpretation, secondary_eclipse_search
from .detection import clean_lightcurve, detect_events, run_bls, run_bls_constrained, run_lomb_scargle
from .plots import make_plots


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
    known_object=None,
) -> VettingResult:
    """``known_object`` is an optional zero-arg callable returning a
    ``crossmatch_known_object`` result that was started earlier (see
    ``prefetch.Prefetcher``), so the remote catalogue queries overlap the
    BLS search instead of running after it."""
    from ..detrend import apply_variability_detrend
    if reporter is None:
        from ..progress import make_noop_reporter
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
        events=events,
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
    known = known_object() if known_object is not None else None
    if not known:
        known = crossmatch_known_object(star.ra, star.dec)
    apply_known_object_override(verdict, known)
    reporter("crossmatch", 1.0)

    # Plots — Task 7 will extend make_plots to accept detrend_meta + f_raw.
    # For now keep the existing call signature; Task 7 adds the new kwargs.
    reporter("plots", 0.0)
    from ..locks import PLOT_LOCK
    with PLOT_LOCK:
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
