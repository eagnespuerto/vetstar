"""Turn NEMESIS ``full_pipeline`` outputs into a compact JSON summary.

Pure functions on pandas/numpy objects, so they are testable without
nemesis-tess installed. The LEO-Vetter threshold check is passed in as a
callable (NEMESIS's ``check_ALL_thresholds``) for the same reason.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Optional

import numpy as np

from .leo_tests import label_leo_results, leo_disposition, leo_metrics_from_bestfit

MAX_LC_POINTS = 3000
MAX_PERIODOGRAM_POINTS = 2000
MAX_FOLD_BINS = 200


def _finite_or_none(x: Any) -> Any:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return x if isinstance(x, (str, bool)) or x is None else str(x)
    return v if math.isfinite(v) else None


def _thin(n: int, cap: int) -> np.ndarray:
    """Evenly spaced indices so at most ``cap`` points are kept."""
    if n <= cap:
        return np.arange(n)
    return np.linspace(0, n - 1, cap).round().astype(int)


def _col(df, name) -> Optional[np.ndarray]:
    if df is None or name not in getattr(df, "columns", []):
        return None
    return np.asarray(df[name], dtype=float)


def light_curve_payload(lc_df) -> dict:
    t = _col(lc_df, "Time")
    if t is None:
        return {}
    idx = _thin(len(t), MAX_LC_POINTS)
    out = {"time": t[idx]}
    for key, col in (("sap", "SAP Flux"), ("pld", "PLD Flux"),
                     ("flux", "Detrended Flux"), ("trend", "Fitted Trend")):
        v = _col(lc_df, col)
        if v is not None:
            out[key] = v[idx]
    return {k: [_finite_or_none(x) for x in v] for k, v in out.items()}


def periodogram_payload(ps_df, method: str) -> dict:
    p = _col(ps_df, f"{method} Periods")
    s = _col(ps_df, f"{method} SDE")
    if p is None or s is None:
        return {}
    # Keep every local max-ish point by thinning on a sorted period grid.
    order = np.argsort(p)
    p, s = p[order], s[order]
    idx = _thin(len(p), MAX_PERIODOGRAM_POINTS)
    return {"period": [_finite_or_none(x) for x in p[idx]],
            "sde": [_finite_or_none(x) for x in s[idx]]}


def bestfit_params(bestfit_df, method: str) -> dict:
    """Pick the headline numbers from a NEMESIS bestfit row."""
    if bestfit_df is None or len(bestfit_df) == 0:
        return {}
    row = bestfit_df.iloc[0].to_dict()

    def g(*names):
        for n in names:
            if n in row:
                return _finite_or_none(row[n])
        return None

    return {
        "method": method,
        "period_d": g(f"{method} Period [d]", "Period [d]"),
        "t0_btjd": g(f"{method} TC [BTJD]", "TC [BTJD]"),
        "duration_hr": g(f"{method} Dur [hrs]", "Dur [hrs]"),
        "depth_ppt": g(f"{method} depth [ppt]", "depth [ppt]"),
        "sde": g(f"{method} SDE", "SDE"),
        "radius_earth": g("Planet Radius [RE]"),
        "n_transits": g("Distinct Transit Count", f"{method} Distinct Transit Count"),
    }


def fold_payload(lc_df, period_d, t0_btjd) -> dict:
    """Phase-folded, binned detrended flux around the best period."""
    t = _col(lc_df, "Time")
    f = _col(lc_df, "Detrended Flux")
    if t is None or f is None or not period_d or t0_btjd is None:
        return {}
    ok = np.isfinite(t) & np.isfinite(f)
    t, f = t[ok], f[ok]
    if len(t) < 10:
        return {}
    phase = ((t - t0_btjd) / period_d + 0.5) % 1.0 - 0.5
    edges = np.linspace(-0.5, 0.5, MAX_FOLD_BINS + 1)
    which = np.digitize(phase, edges) - 1
    centers, med = [], []
    for b in range(MAX_FOLD_BINS):
        sel = which == b
        if sel.any():
            centers.append(0.5 * (edges[b] + edges[b + 1]))
            med.append(float(np.median(f[sel])))
    idx = _thin(len(phase), MAX_LC_POINTS)
    return {
        "phase": [_finite_or_none(x) for x in phase[idx]],
        "flux": [_finite_or_none(x) for x in f[idx]],
        "bin_phase": centers,
        "bin_flux": med,
    }


def leo_payload(bestfit_df, check_all: Callable) -> dict:
    """Run NEMESIS's LEO-Vetter threshold check on the bestfit metrics."""
    if bestfit_df is None or len(bestfit_df) == 0:
        return {"available": False, "reason": "no bestfit row"}
    metrics = leo_metrics_from_bestfit(bestfit_df.iloc[0].to_dict())
    if not metrics:
        return {"available": False, "reason": "no LEO-Vetter metrics in bestfit"}
    try:
        _, strings, flags = check_all(metrics, verbose=False, collect_output_strings=True)
        tests = label_leo_results(metrics, list(flags), list(strings))
    except Exception as exc:  # metric missing, NaN comparisons, order change
        return {"available": False, "reason": f"LEO-Vetter check failed: {exc}"}
    return {"available": True, "disposition": leo_disposition(tests), "tests": tests}


def summarize(lc_df, transit_results, check_all: Callable) -> dict:
    """Build the full JSON summary for one NEMESIS run."""
    out: dict = {"light_curve": light_curve_payload(lc_df)}
    if not transit_results:
        out["search"] = None
        return out
    bls_ps, _bls_model, bls_fit, tls_ps, _tls_model, tls_fit = transit_results
    searches = {}
    for method, ps, fit in (("BLS", bls_ps, bls_fit), ("TLS", tls_ps, tls_fit)):
        if fit is None:
            continue
        params = bestfit_params(fit, method)
        searches[method] = {
            "params": params,
            "periodogram": periodogram_payload(ps, method),
            "fold": fold_payload(lc_df, params.get("period_d"), params.get("t0_btjd")),
            "leo": leo_payload(fit, check_all),
        }
    # TLS refines BLS when it ran, so it is the headline result.
    out["search"] = searches
    out["primary"] = "TLS" if "TLS" in searches else ("BLS" if "BLS" in searches else None)
    return out
