"""Clean a TESS light curve before the OGLE event search.

The OGLE selection (microlensing_ogle.py, Mróz et al. 2019) is kept exactly
as published. It assumes a flat baseline, which OGLE's targets have but many
TESS stars do not: spots and pulsations can move the flux by many times the
noise, inflate the baseline scatter and hide short bumps. This step removes
that slow variability first, and only for stars that clearly vary.

Method: a running median of width ``window_d``, done twice. The second pass
ignores points that stand above the first trend (and their neighbours), so a
bump does not pull the trend up and get flattened with it.
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

# A star counts as variable when its smoothed light curve has a dip (a stretch
# lower than the light curve on both sides) deeper than this many times its
# median photometric error. A single microlensing event rises and falls once
# and leaves no such dip, however long or bright it is; spots and pulsations
# do. A plain scatter test would call a quiet star with a big event variable.
VARIABLE_FACTOR = 5.0
WINDOW_D = 0.5  # follows spot/pulsation slopes; bumps are masked so events survive
BUMP_SIGMA = 3.0


def _mad_sigma(x: np.ndarray) -> float:
    x = x[np.isfinite(x)]
    return float(1.4826 * np.median(np.abs(x - np.median(x)))) if x.size else 0.0


def dip_depth(trend: np.ndarray) -> float:
    """Deepest point below the lower of the highest points on its left and
    right ("water trapped" in the curve). 0 for a single rise-and-fall."""
    left = np.maximum.accumulate(trend)
    right = np.maximum.accumulate(trend[::-1])[::-1]
    return float(np.max(np.minimum(left, right) - trend))


def running_median(t: np.ndarray, f: np.ndarray, width_d: float,
                   use: Optional[np.ndarray] = None, slope: bool = True,
                   step_d: float = 0.02) -> np.ndarray:
    """Median of f within +/- width_d/2 of each time (t sorted). Only points
    with ``use`` True feed the median; gaps are filled by interpolation."""
    tu, fu = (t, f) if use is None else (t[use], f[use])
    # The trend is smooth on the window scale, so evaluate it on a grid of
    # step_d and interpolate: same result, a fraction of the work.
    t_all = t
    if step_d and t.size > 2 and (t[-1] - t[0]) / step_d < t.size:
        keep = np.unique(np.searchsorted(t, np.arange(t[0], t[-1] + step_d, step_d)).clip(0, t.size - 1))
        t = t[keep]
    # Enough points for a trustworthy window: half of what a full window
    # holds at this cadence (at least 5).
    dt = float(np.median(np.diff(tu))) if tu.size > 1 else width_d
    need = max(5, int(0.5 * width_d / max(dt, 1e-6)))
    lo = np.searchsorted(tu, t - width_d / 2)
    hi = np.searchsorted(tu, t + width_d / 2, side="right")
    # Median about the local slope, so the trend does not lag on a steep rise
    # or fall (most visible at the ends of the data, where the window is one-sided).
    out = np.full(t.size, np.nan)
    for k, (a, b) in enumerate(zip(lo, hi)):
        x, y = tu[a:b] - t[k], fu[a:b]
        # Too little left (mostly masked): leave it to the gap fill below. A
        # slope from a few clustered points would extrapolate wildly.
        if b - a < need or x.max() - x.min() < width_d / 3:
            continue
        if not slope:
            out[k] = np.median(y)
            continue
        xm = x.mean()
        den = float(np.sum((x - xm) ** 2))
        m = float(np.sum((x - xm) * (y - y.mean())) / den) if den > 0 else 0.0
        out[k] = np.median(y - m * x)
    bad = ~np.isfinite(out)
    if bad.any() and (~bad).sum() >= 2:
        # Fill each masked stretch with a quadratic through the trend on both
        # sides: a straight line misses the curve of a spot or pulsation.
        idx = np.flatnonzero(bad)
        for run in np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1):
            a, b = t[run[0]], t[run[-1]]
            # Fit the unmasked *data* on each side (the trend values next to a
            # masked stretch are biased by half-masked windows).
            near = (tu >= a - width_d) & (tu <= b + width_d)
            sides = (tu[near] < a).any() and (tu[near] > b).any()
            if near.sum() >= need:
                # Both sides: quadratic. Edge of the data: a line from one side.
                c = np.polyfit(tu[near] - a, fu[near], 2 if sides else 1)
                fill = np.polyval(c, t[run] - a)
                # A quadratic can shoot off across a long gap; keep it within
                # the data range on either side.
                # (Interior gaps only: at the data edge the line has to go
                # beyond the data it was fitted to.)
                if sides:
                    lo_, hi_ = np.percentile(fu[near], [1, 99])
                    fill = np.clip(fill, lo_, hi_)
                out[run] = fill
            else:
                out[run] = np.interp(t[run], t[~bad], out[~bad])
    return out if t.size == t_all.size else np.interp(t_all, t, out)


def clean_for_event_search(time, flux, flux_err, window_d: float = WINDOW_D
                           ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, Optional[dict]]:
    """Return (t, f, e, info), time-sorted. ``info`` is None when the star is
    quiet enough that nothing was changed."""
    t = np.asarray(time, float)
    f = np.asarray(flux, float)
    e = np.asarray(flux_err, float)
    ok = np.isfinite(t) & np.isfinite(f) & np.isfinite(e) & (e > 0)
    t, f, e = t[ok], f[ok], e[ok]
    order = np.argsort(t)
    t, f, e = t[order], f[order], e[order]
    if t.size < 30:
        return t, f, e, None

    med = float(np.median(f))
    if med <= 0:
        return t, f, e, None
    fn, en = f / med, e / med
    scatter, noise = _mad_sigma(fn), float(np.median(en))
    dip = dip_depth(running_median(t, fn, window_d, slope=False))
    trend = running_median(t, fn, window_d)
    if noise <= 0 or dip <= VARIABLE_FACTOR * noise:
        return t, f, e, None

    # Mask bumps and refit, a few times: the first trend partly follows a
    # bump, so its wings only show up once the core is masked.
    near = np.zeros(t.size, dtype=bool)
    for _ in range(4):
        resid = fn / trend - 1
        high = resid > BUMP_SIGMA * max(_mad_sigma(resid[~near]), noise)
        new = near.copy()
        idx = np.flatnonzero(high)
        # Only runs of >= 3 high points count as a bump (single points are
        # noise); pad each run by half a window so its wings are skipped too.
        for run in (np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1) if idx.size else []):
            if run.size >= 3:
                new |= (t >= t[run[0]] - window_d / 2) & (t <= t[run[-1]] + window_d / 2)
        # A sliver of data between a mask and the start or end is too short to
        # fit; mask it too, so the edge is filled from the side with data.
        if new.any():
            first, last = np.flatnonzero(new)[[0, -1]]
            if t[first] - t[0] < window_d:
                new[:first] = True
            if t[-1] - t[last] < window_d:
                new[last:] = True
        if new.sum() == near.sum() or new.mean() > 0.5:
            break
        near = new
        trend = running_median(t, fn, window_d, use=~near)
    good = np.isfinite(trend) & (trend > 0)
    info = {
        "method": f"{window_d:g}-day running median, bumps masked on the second pass",
        "window_d": window_d,
        "dip_depth": dip,
        "scatter_before": scatter,
        "scatter_after": _mad_sigma(fn[good] / trend[good]),
        "median_error": noise,
        "note": (f"This star varies slowly (dips of {dip:.2%}, about {dip / noise:.0f}x its noise), so its slow "
                 f"variability was removed with a {window_d:g}-day running median before the OGLE search. "
                 "The OGLE selection itself is unchanged."),
    }
    return t[good], fn[good] / trend[good], en[good] / trend[good], info
