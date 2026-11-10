"""Light-curve cleaning and signal search: Lomb-Scargle, BLS (blind and
known-period) and direct event detection.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from astropy.timeseries import BoxLeastSquares
from astropy.timeseries import LombScargle
from scipy.ndimage import median_filter


# ----------------------------------------------------------------------
# Core pipeline
# ----------------------------------------------------------------------
def clean_lightcurve(
    t: np.ndarray,
    flux: np.ndarray,
    flux_err: np.ndarray,
    quality: Optional[np.ndarray] = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Standard quality-flag mask + finite filtering + normalisation."""
    mask = np.isfinite(t) & np.isfinite(flux) & (flux > 0)
    if quality is not None:
        mask &= quality == 0
    if flux_err is not None:
        mask &= np.isfinite(flux_err)
    t, flux = t[mask], flux[mask]
    fe = flux_err[mask] if flux_err is not None else np.full_like(flux, np.nanstd(flux))
    med = np.nanmedian(flux)
    return t, flux / med, fe / med


def run_lomb_scargle(t, f, fe, p_min=0.1, p_max=20.0) -> dict:
    ls = LombScargle(t, f, fe)
    freq, power = ls.autopower(
        minimum_frequency=1 / p_max,
        maximum_frequency=1 / p_min,
        samples_per_peak=15,
    )
    periods = 1 / freq
    # FAP for the top peak
    ip = int(np.argmax(power))
    try:
        fap = float(ls.false_alarm_probability(power[ip]))
    except Exception:
        fap = None
    # 5 well-separated peaks
    order = np.argsort(power)[::-1]
    tops = []
    for idx in order:
        p = float(periods[idx])
        if all(abs(p - tp["period"]) / tp["period"] > 0.05 for tp in tops):
            tops.append({"period": p, "power": float(power[idx])})
        if len(tops) >= 5:
            break
    return {
        "top_period": float(periods[ip]),
        "top_power": float(power[ip]),
        "false_alarm_prob": fap,
        "top_peaks": tops,
    }


# Default fractional half-width of the BLS search window around a known
# period, e.g. 0.02 means scan periods in [P*0.98, P*1.02].
BLS_KNOWN_PERIOD_TOL_FRAC = 0.02

# Box widths (days) tried at every trial period: 1.2 h to 7.2 h.
BLS_DURATIONS_D = (0.05, 0.1, 0.15, 0.2, 0.3)

# Longer boxes (9.6 h to 18 h) for long-period planets and evolved hosts.
# Each one is only tried where it is at most BLS_LONG_DURATION_MAX_FRAC of
# the period, so a short-period search never fits a box that covers a large
# part of the orbit (that mostly picks up stellar variability).
# With 5%: 0.4 d from P = 8 d, 0.5 d from 10 d, 0.75 d from 15 d.
BLS_LONG_DURATIONS_D = (0.4, 0.5, 0.75)
BLS_LONG_DURATION_MAX_FRAC = 0.05

# Points per bin when the periodogram is thinned for the plot.
_PERIODOGRAM_THIN = 20


def bls_durations_for(period_d: float) -> np.ndarray:
    """Trial box widths (days) used at ``period_d``."""
    durs = [d for d in BLS_DURATIONS_D if d < period_d]
    durs += [
        d for d in BLS_LONG_DURATIONS_D
        if d <= BLS_LONG_DURATION_MAX_FRAC * period_d
    ]
    return np.array(durs)


def bls_frequency_grid(
    p_min: float, p_max: float, span: float, max_points: int,
    min_duration: float = BLS_DURATIONS_D[0],
) -> np.ndarray:
    """Trial periods (ascending) on a grid that is uniform in frequency.

    A BLS peak has a fixed width in frequency, about ``duration / (P * span)``,
    so even steps in frequency sample every peak equally well. Even steps in
    period (the old grid) put far too few points at short periods and far
    too many at long ones.

    The step is ``min_duration / span**2``, the rule that astropy's
    ``BoxLeastSquares.autoperiod`` uses: between two neighbouring trial
    periods the model transit drifts by a small part of the shortest box
    over the whole light curve. ``max_points`` caps the grid (it is the old
    ``n_periods``), so a long multi-sector baseline costs no more than before.
    """
    f_lo, f_hi = 1.0 / p_max, 1.0 / p_min
    df = min_duration / max(span, p_max) ** 2
    n = int(np.ceil((f_hi - f_lo) / df)) + 1
    n = max(2, min(n, int(max_points)))
    return 1.0 / np.linspace(f_hi, f_lo, n)


def _bls_search(t, f, fe, periods: np.ndarray) -> dict:
    """Run BLS over ``periods`` (ascending) and return the stacked arrays.

    The grid is cut where a longer box becomes allowed, and each piece is
    searched with its own set of widths (see :func:`bls_durations_for`).
    """
    bls = BoxLeastSquares(t, f, fe)
    edges = [d / BLS_LONG_DURATION_MAX_FRAC for d in BLS_LONG_DURATIONS_D]
    cuts = np.unique(np.searchsorted(periods, edges, side="left"))
    cuts = [0] + [int(c) for c in cuts if 0 < c < len(periods)] + [len(periods)]
    parts = []
    for lo, hi in zip(cuts[:-1], cuts[1:]):
        seg = periods[lo:hi]
        parts.append(bls.power(seg, bls_durations_for(float(seg[0]))))
    return {
        key: np.concatenate([np.asarray(getattr(r, key)) for r in parts])
        for key in ("period", "power", "transit_time", "duration", "depth")
    }


def _thin_periodogram(periods: np.ndarray, power: np.ndarray) -> dict:
    """Thin the periodogram for transport, keeping the peak of every bin.

    Taking every 20th point (the old way) can step right over a narrow
    peak, so the plot showed a lower peak than the reported one, or none.
    """
    out_p, out_w = [], []
    for lo in range(0, len(periods), _PERIODOGRAM_THIN):
        seg = power[lo:lo + _PERIODOGRAM_THIN]
        k = int(np.argmax(seg))
        out_p.append(float(periods[lo + k]))
        out_w.append(float(seg[k]))
    return {"periods": out_p, "power": out_w}


def _bls_summary(t, res: dict) -> dict:
    """The result dict shared by the blind and the known-period search."""
    power = res["power"]
    ib = int(np.argmax(power))
    _std = float(np.std(power))
    sde = float((power[ib] - np.median(power)) / _std) if _std > 0 else 0.0
    period = float(res["period"][ib])
    t0 = float(res["transit_time"][ib])
    return {
        "period": period,
        "t0": t0,
        "duration": float(res["duration"][ib]),
        "depth": float(res["depth"][ib]),
        "power": float(power[ib]),
        "sde": sde,
        "n_transits_in_window": int(
            np.floor((t.max() - t0) / period)
            - np.ceil((t.min() - t0) / period)
            + 1
        ),
        "_periodogram": _thin_periodogram(res["period"], power),
    }


def run_bls_constrained(
    t, f, fe,
    known_period_days: float,
    tol_frac: float = BLS_KNOWN_PERIOD_TOL_FRAC,
    n_periods: int = 4000,
    search_harmonics: bool = True,
) -> dict:
    """BLS over a narrow window around a known period plus P/2 and 2P harmonics.

    Returns the same dict shape as :func:`run_bls` plus three extra keys:
    ``constrained``, ``known_period_input_days``, and ``matched_harmonic``
    (one of ``"P"``, ``"P/2"``, ``"2P"``).

    Each window is sampled evenly in frequency, and the windows are searched
    in order of period.
    """
    span = float(t.max() - t.min())
    p_max_blind = max(0.5, span * 0.7)

    sub_grids = [("P", known_period_days)]
    if search_harmonics:
        if known_period_days / 2.0 >= 0.5:
            sub_grids.append(("P/2", known_period_days / 2.0))
        if 2.0 * known_period_days <= p_max_blind:
            sub_grids.append(("2P", 2.0 * known_period_days))
    sub_grids.sort(key=lambda g: g[1])

    per_grid = max(200, n_periods // len(sub_grids))
    windows = []
    for label, p in sub_grids:
        lo = p * (1.0 - tol_frac)
        hi = p * (1.0 + tol_frac)
        # A fixed count per window, as before: the windows are narrow, and
        # the SDE needs enough off-peak samples to mean anything.
        windows.append((label, 1.0 / np.linspace(1.0 / lo, 1.0 / hi, per_grid)))

    res = _bls_search(t, f, fe, np.concatenate([ps for _, ps in windows]))
    out = _bls_summary(t, res)

    matched = "P"
    for label, ps in windows:
        if ps[0] <= out["period"] <= ps[-1]:
            matched = label
            break

    out["constrained"] = True
    out["known_period_input_days"] = float(known_period_days)
    out["matched_harmonic"] = matched
    return out


def run_bls(
    t, f, fe, p_min=0.5, p_max=None, n_periods=20000
) -> dict:
    """Blind BLS between ``p_min`` and ``p_max`` days.

    ``n_periods`` is the largest number of trial periods. The grid is even
    in frequency (:func:`bls_frequency_grid`) and uses fewer points when the
    baseline is short enough not to need them all.
    """
    span = float(t.max() - t.min())
    if p_max is None:
        p_max = max(p_min * 2, span * 0.7)
    periods = bls_frequency_grid(p_min, p_max, span, n_periods)
    return _bls_summary(t, _bls_search(t, f, fe, periods))


def detect_events(t, f, threshold=0.997, min_pts=10, min_snr=4.0, max_gap=5) -> list:
    """Direct event detection — finds discrete dips regardless of period.

    Uses a two-pass approach:
      Pass 1 — measure the star's photometric scatter from out-of-dip data.
      Pass 2 — flag contiguous stretches where smoothed flux drops below
               **the greater of** the user's absolute threshold AND an
               adaptive threshold = baseline − min_snr × scatter.

    The adaptive threshold is the key change: for a quiet star with
    scatter = 0.0002, a 3σ dip is only 0.06% deep — well above the old
    fixed 0.997 threshold. The adaptive approach catches it. For a noisy
    star the fixed threshold still acts as a floor.

    Each candidate event must also pass a per-event SNR check. That check
    uses the *integrated* significance of the dip, not a single point:

        snr = mean_in_transit_depth × sqrt(n_points) / scatter

    The sqrt(n) factor is essential. A real but shallow transit on a noisy
    star can have a per-point depth ≈ the scatter (point SNR ≈ 1) yet still
    be hugely significant once you average over the dozens of in-transit
    points (integrated SNR ≈ 10). The old check divided a single point's
    depth by the scatter, so it threw away every shallow-on-noisy transit —
    e.g. an 0.8%-deep, ~5 h transit on a star with 0.53% point scatter
    scored ~1.5 and was rejected despite being a clear, repeating signal.

    Contiguous in-dip stretches separated by only a few points (median-filter
    noise can briefly lift the smoothed flux back over the threshold mid-dip)
    are bridged via ``max_gap`` so one transit yields one event, not two.
    """
    fs = median_filter(f, size=21)
    baseline = float(np.nanmedian(fs))

    # Identify real time gaps so we don't (a) flag points whose smoothed flux
    # was dragged down by the median filter reaching across the gap, or
    # (b) merge two separate in-dip runs across the gap into one fake event.
    # A "gap" is any sample-to-sample dt much larger than the typical cadence.
    t_arr = np.asarray(t)
    dt = np.diff(t_arr)
    pos_dt = dt[dt > 0]
    if pos_dt.size:
        cadence = float(np.median(pos_dt))
        gap_threshold = max(5 * cadence, 0.2)  # days; ~5x cadence or 4.8 h
        # gap_after[i] is True if there is a large gap between sample i and i+1
        gap_after = np.concatenate([dt > gap_threshold, [False]])
    else:
        gap_after = np.zeros(len(t_arr), dtype=bool)
        cadence = 0.0

    # --- Pass 1: measure local scatter from non-dip data ---
    # Use a rough cut: anything within 2× the raw MAD of median is "baseline"
    raw_mad = float(1.4826 * np.nanmedian(np.abs(f - baseline)))
    rough_mask = np.abs(fs - baseline) < 3 * max(raw_mad, 1e-6)
    if rough_mask.sum() > 100:
        scatter = float(1.4826 * np.nanmedian(np.abs(f[rough_mask] - np.nanmedian(f[rough_mask]))))
    else:
        scatter = raw_mad
    if scatter <= 0:
        scatter = 1e-5

    # --- Pass 2: adaptive threshold ---
    # The threshold is the HIGHER (less strict) of:
    #   (a) the user-supplied absolute threshold (e.g. 0.997 = 0.3% below baseline)
    #   (b) baseline − min_snr × scatter (adapts to the star's actual noise)
    # This way quiet stars get a sensitive threshold automatically, while the
    # absolute threshold still caps things for noisy stars.
    adaptive_threshold = baseline - min_snr * scatter
    effective_threshold = max(threshold, adaptive_threshold)

    in_dip = fs < effective_threshold

    # Suppress samples adjacent to a large time gap: the size-21 median filter
    # can pull edge points well below baseline by mixing in unrelated flux from
    # the other side of the gap (or the gap edge itself), producing a spurious
    # "dip" that just traces the discontinuity.
    edge_pad = 10  # half the median filter window
    n = len(in_dip)
    gap_idx = np.where(gap_after)[0]
    for gi in gap_idx:
        lo = max(0, gi - edge_pad + 1)
        hi = min(n, gi + edge_pad + 1)
        in_dip[lo:hi] = False

    # Bridge short gaps: fill runs of <= max_gap "out" points that sit between
    # two "in" stretches, so median-filter noise doesn't split one transit.
    if max_gap > 0:
        i = 0
        while i < n:
            if not in_dip[i]:
                start = i
                while i < n and not in_dip[i]:
                    i += 1
                # gap is [start, i); bridge only if flanked on both sides and
                # the bridged region does not straddle a real time gap.
                if (
                    0 < start
                    and i < n
                    and (i - start) <= max_gap
                    and not gap_after[start - 1 : i].any()
                ):
                    in_dip[start:i] = True
            else:
                i += 1

    events = []
    i = 0
    while i < len(t):
        if in_dip[i]:
            start = i
            while i < len(t) and in_dip[i]:
                # Stop a run at a real time gap — a single event cannot span
                # a multi-hour data outage.
                if gap_after[i]:
                    i += 1
                    break
                i += 1
            end = i
            if end - start >= min_pts:
                seg = f[start:end]
                n_pts = end - start
                seg_min = float(fs[start:end].min())
                depth = float(baseline - seg_min)              # max depth (reporting)
                mean_depth = float(baseline - np.nanmean(seg))  # avg depth (significance)
                # Integrated SNR: average dip depth grows in significance with
                # sqrt(number of in-transit points). This is what lets a shallow
                # transit on a noisy star clear the bar.
                snr = mean_depth * np.sqrt(n_pts) / scatter if scatter > 0 else 0.0
                if snr >= min_snr and mean_depth > 0:
                    events.append(
                        {
                            "t_start": float(t[start]),
                            "t_end": float(t[end - 1]),
                            "duration_d": float(t[end - 1] - t[start]),
                            "min_flux": seg_min,
                            "depth": depth,
                            "depth_snr": float(snr),
                            "n_points": int(n_pts),
                        }
                    )
        else:
            i += 1

    return events
