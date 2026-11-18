"""OGLE-style automatic microlensing event search on a TESS light curve.

Follows the OGLE-IV event selection of Mróz et al. (2019, ApJS 244, 29,
"Microlensing optical depth and event rate toward the Galactic bulge from
8 yr of OGLE-IV observations"), which itself refines Mróz et al. (2017,
Nature 548, 183) and builds on the OGLE Early Warning System (Udalski 2003,
AcA 53, 291).

Steps (Mróz et al. 2019, Section 3 and Table 2):
  1. Bump search: at least 3 consecutive points >= 3 sigma_base above the
     baseline, with chi2_3+ = sum (F_i - F_base) / sigma_i >= 32 over them.
     The light curve outside the event window must be flat
     (chi2_out / dof <= 2).
  2. Shape cuts: a single bump only, and amplitude >= 0.1 mag.
  3. PSPL fit: F = F_s A(u) + F_b, with F_s and F_b solved by linear least
     squares at every (t0, u0, tE) and chi2 minimised by Nelder-Mead.
     Iterative 4 sigma outlier rejection, keeping a point when either
     neighbour is also off the model (a real feature, not a single outlier).
     Final cuts on chi2/dof (all data and |t - t0| < tE), u0, tE, the blend
     flux and the blending parameter f_s = F_s / (F_s + F_b).

OGLE's difference-imaging cut (n_DIA >= 3) needs DIA frames, which a TESS
light curve does not carry, so it is reported as "not applicable". OGLE's
source-magnitude cut (I_s <= 21) is in OGLE's I-band zero point and is
replaced by the blend-flux floor expressed against the TESS baseline.

The OGLE cut values were set for multi-year, ground-based I-band data.
``TESS_CUTS`` holds the adapted values used here; each entry notes the
OGLE original.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import minimize

from .microlensing import _json_safe, pspl_magnification

PAPER = {
    "citation": "Mróz, P., Udalski, A., Skowron, J., et al. 2019, ApJS, 244, 29",
    "doi": "10.3847/1538-4365/ab426b",
    "ads": "https://ui.adsabs.harvard.edu/abs/2019ApJS..244...29M/abstract",
    "builds_on": [
        "Mróz, P., et al. 2017, Nature, 548, 183 (selection algorithm)",
        "Udalski, A. 2003, AcA, 53, 291 (OGLE Early Warning System)",
    ],
}


def tess_cuts() -> Dict[str, Dict[str, Any]]:
    """Selection cuts for TESS, each with the OGLE-IV value it adapts.

    Returns {name: {"value": ..., "ogle": ..., "why": ...}}.
    """
    return {
        "n_consecutive": {"value": 3, "ogle": 3,
                          "why": "Same as OGLE: three points in a row."},
        "sigma_bump": {"value": 3.0, "ogle": 3.0,
                       "why": "Same as OGLE."},
        "chi2_3plus_min": {"value": 32.0, "ogle": 32.0,
                           "why": "Same as OGLE."},
        "chi2_out_max": {"value": 2.0, "ogle": 2.0,
                         "why": "Same as OGLE, but 'outside' is the rest of the sector, "
                                "not a 720-day window."},
        "amplitude_mag_min": {"value": 0.1, "ogle": 0.1,
                              "why": "Same as OGLE. Rejects small-amplitude variables; "
                                     "low-magnification TESS events may fail it."},
        "chi2_fit_max": {"value": 2.0, "ogle": 2.0, "why": "Same as OGLE."},
        "chi2_fit_tE_max": {"value": 2.0, "ogle": 2.0, "why": "Same as OGLE."},
        "u0_max": {"value": 1.0, "ogle": 1.0, "why": "Same as OGLE."},
        "tE_max_d": {"value": 300.0, "ogle": 300.0,
                     "why": "Same as OGLE. One TESS sector is ~27 d, so long events "
                            "are poorly constrained; see the tE-vs-span warning."},
        "blend_floor": {"value": -0.1, "ogle": -0.1,
                        "why": "OGLE allows F_b down to -F_min (a 20.5 mag star, 0.1 in "
                               "OGLE flux units). Here F_min is 0.1 of the TESS baseline."},
        "fs_min": {"value": 0.01, "ogle": 0.01, "why": "Same as OGLE."},
        "outlier_sigma": {"value": 4.0, "ogle": 4.0, "why": "Same as OGLE."},
    }


# ---------------------------------------------------------------------------
# Step 1: baseline and bump search
# ---------------------------------------------------------------------------

def robust_baseline(f: np.ndarray, mask: Optional[np.ndarray] = None,
                    clip: float = 5.0, iters: int = 5) -> Tuple[float, float]:
    """Median baseline and MAD-based scatter after iterative 5 sigma clipping.

    Mróz et al. remove 5 sigma outliers before computing the baseline.
    """
    x = f[mask] if mask is not None else f
    x = x[np.isfinite(x)]
    keep = np.ones(len(x), dtype=bool)
    med, sig = float(np.median(x)), float(1.4826 * np.median(np.abs(x - np.median(x))))
    for _ in range(iters):
        med = float(np.median(x[keep]))
        sig = float(1.4826 * np.median(np.abs(x[keep] - med)))
        if sig <= 0:
            break
        new = np.abs(x - med) < clip * sig
        if new.sum() == keep.sum():
            break
        keep = new
    return med, sig


def find_bumps(f: np.ndarray, ferr: np.ndarray, f_base: float, sig_base: float,
               n_consec: int, nsig: float) -> List[Dict[str, Any]]:
    """Runs of >= n_consec consecutive points >= nsig * sig_base above baseline.

    Each run carries chi2_3+ = sum over the run of (F_i - F_base) / sigma_i.
    """
    above = (f - f_base) >= nsig * sig_base
    bumps = []
    i, n = 0, len(f)
    while i < n:
        if not above[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and above[j + 1]:
            j += 1
        if j - i + 1 >= n_consec:
            chi2_3p = float(np.sum((f[i:j + 1] - f_base) / ferr[i:j + 1]))
            bumps.append({"i0": i, "i1": j, "n": j - i + 1, "chi2_3plus": chi2_3p,
                          "peak_index": int(i + np.argmax(f[i:j + 1]))})
        i = j + 1
    return bumps


# ---------------------------------------------------------------------------
# Step 3: PSPL fit with analytic F_s, F_b
# ---------------------------------------------------------------------------

def _linear_fluxes(A: np.ndarray, f: np.ndarray, w: np.ndarray,
                   blend_floor: Optional[float]) -> Tuple[float, float]:
    """Weighted least squares for f = F_s A + F_b. Optional floor on F_b."""
    sw, sA, sf = w.sum(), (w * A).sum(), (w * f).sum()
    sAA, sAf = (w * A * A).sum(), (w * A * f).sum()
    det = sw * sAA - sA * sA
    if det <= 0:
        return float("nan"), float("nan")
    fs = (sw * sAf - sA * sf) / det
    fb = (sAA * sf - sA * sAf) / det
    if blend_floor is not None and fb < blend_floor:
        fb = blend_floor
        fs = ((w * A * (f - fb)).sum()) / sAA
    return float(fs), float(fb)


@dataclass
class PsplFit:
    t0: float
    u0: float
    tE: float
    fs: float
    fb: float
    chi2: float
    dof: int
    chi2_tE: float
    dof_tE: int
    n_outliers: int
    blend_fixed: bool


def _chi2_for(theta, t, f, w, blend_floor):
    t0, lu0, ltE = theta
    A = pspl_magnification(t, t0, math.exp(ltE), math.exp(lu0))
    fs, fb = _linear_fluxes(A, f, w, blend_floor)
    if not np.isfinite(fs):
        return 1e30, fs, fb
    r = f - (fs * A + fb)
    return float((w * r * r).sum()), fs, fb


def fit_pspl_ogle(t: np.ndarray, f: np.ndarray, ferr: np.ndarray, t0_guess: float,
                  cuts: Dict[str, Dict[str, Any]]) -> PsplFit:
    """PSPL fit in the Mróz et al. (2019) style. Flux is normalised to baseline 1."""
    outlier_sigma = cuts["outlier_sigma"]["value"]
    floor = cuts["blend_floor"]["value"]
    keep = np.ones(len(t), dtype=bool)

    def run(blend_floor, keep):
        tt, ff, ww = t[keep], f[keep], 1.0 / ferr[keep] ** 2
        best = None
        for tE0 in (0.5, 2.0, 8.0, 30.0):
            for u00 in (0.05, 0.3, 0.8):
                x0 = [t0_guess, math.log(u00), math.log(tE0)]
                res = minimize(lambda th: _chi2_for(th, tt, ff, ww, blend_floor)[0], x0,
                               method="Nelder-Mead",
                               options={"xatol": 1e-5, "fatol": 1e-6, "maxiter": 4000})
                if best is None or res.fun < best.fun:
                    best = res
        return best

    best = None
    blend_fixed = False
    for _ in range(4):  # iterative 4 sigma outlier removal
        best = run(None, keep)
        _, fs, fb = _chi2_for(best.x, t[keep], f[keep], 1.0 / ferr[keep] ** 2, None)
        # Negative blend below -F_min: refit with F_b held at the floor.
        if fb < floor:
            best = run(floor, keep)
            blend_fixed = True
        bf = floor if blend_fixed else None
        t0, lu0, ltE = best.x
        A = pspl_magnification(t, t0, math.exp(ltE), math.exp(lu0))
        fs, fb = _linear_fluxes(A[keep], f[keep], 1.0 / ferr[keep] ** 2, bf)
        z = (f - (fs * A + fb)) / ferr
        off = np.abs(z) > outlier_sigma
        # Keep a deviant point if a neighbour is also > 3 sigma off (a real feature).
        neigh = np.zeros_like(off)
        neigh[1:] |= np.abs(z[:-1]) > 3
        neigh[:-1] |= np.abs(z[1:]) > 3
        new_keep = ~(off & ~neigh)
        if np.array_equal(new_keep, keep):
            break
        keep = new_keep

    t0, lu0, ltE = best.x
    u0, tE = math.exp(lu0), math.exp(ltE)
    bf = floor if blend_fixed else None
    A = pspl_magnification(t, t0, tE, u0)
    fs, fb = _linear_fluxes(A[keep], f[keep], 1.0 / ferr[keep] ** 2, bf)
    r2 = ((f - (fs * A + fb)) / ferr) ** 2
    n_par = 4 if blend_fixed else 5
    in_tE = keep & (np.abs(t - t0) < tE)
    return PsplFit(
        t0=float(t0), u0=u0, tE=tE, fs=fs, fb=fb,
        chi2=float(r2[keep].sum()), dof=max(int(keep.sum()) - n_par, 1),
        chi2_tE=float(r2[in_tE].sum()), dof_tE=max(int(in_tE.sum()) - n_par, 1),
        n_outliers=int((~keep).sum()), blend_fixed=blend_fixed,
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def _cut(name: str, value, passed: Optional[bool], rule: str, cuts) -> dict:
    c = cuts.get(name, {})
    return {"name": name, "value": value, "passed": passed, "rule": rule,
            "ogle": c.get("ogle"), "why": c.get("why")}


def ogle_search(time, flux, flux_err, window_d: float = 6.0,
                cuts: Optional[Dict[str, Dict[str, Any]]] = None) -> dict:
    """Run the OGLE selection on a whole TESS light curve.

    ``window_d`` is the half-width (days) of the event window excluded from
    the flatness test, the TESS stand-in for OGLE's 720-day window.
    """
    cuts = cuts or tess_cuts()
    t = np.asarray(time, float)
    f = np.asarray(flux, float)
    e = np.asarray(flux_err, float)
    ok = np.isfinite(t) & np.isfinite(f) & np.isfinite(e) & (e > 0)
    t, f, e = t[ok], f[ok], e[ok]
    order = np.argsort(t)
    t, f, e = t[order], f[order], e[order]
    if len(t) < 30:
        raise ValueError(f"Need at least 30 good points; got {len(t)}")

    # Normalise so the baseline is 1 (fluxes are then in units of baseline).
    med, _ = robust_baseline(f)
    f, e = f / med, e / med

    f_base, sig_base = robust_baseline(f)
    bumps = find_bumps(f, e, f_base, sig_base,
                       cuts["n_consecutive"]["value"], cuts["sigma_bump"]["value"])
    strong = [b for b in bumps if b["chi2_3plus"] >= cuts["chi2_3plus_min"]["value"]]
    report: Dict[str, Any] = {
        "method": "OGLE-IV event selection (Mróz et al. 2019)",
        "paper": PAPER,
        "baseline": {"flux": f_base, "sigma": sig_base},
        "bumps": [{"t_start": float(t[b["i0"]]), "t_end": float(t[b["i1"]]),
                   "n_points": b["n"], "chi2_3plus": b["chi2_3plus"],
                   "t_peak": float(t[b["peak_index"]])} for b in bumps],
        "cuts": [],
        "fit": None,
    }
    if not strong:
        best = max(bumps, key=lambda b: b["chi2_3plus"]) if bumps else None
        report["cuts"].append(_cut(
            "chi2_3plus_min", best["chi2_3plus"] if best else None, False,
            f">= {cuts['chi2_3plus_min']['value']} over >= {cuts['n_consecutive']['value']} "
            f"points at >= {cuts['sigma_bump']['value']} sigma", cuts))
        report["passed"] = False
        report["verdict"] = "no_event"
        report["summary"] = "No bump strong enough for OGLE's detection threshold."
        return _json_safe(report)

    main = max(strong, key=lambda b: b["chi2_3plus"])
    t_peak = float(t[main["peak_index"]])
    fit = fit_pspl_ogle(t, f, e, t_peak, cuts)

    # Event window. OGLE excludes a 720-day window, far longer than any
    # event. A TESS sector is ~27 d, so size the window from the fitted
    # model instead: out to where the model excess F_s (A - 1) drops below
    # sigma_base, and never narrower than +/- window_d.
    half = window_d
    if fit is not None and fit.fs > 0 and sig_base > 0:
        u = np.logspace(-3, 2, 2000)
        excess = fit.fs * (pspl_magnification(u, 0.0, 1.0, 0.0) - 1.0)
        above = u[excess >= sig_base]
        if above.size and above.max() > fit.u0:
            half = max(half, fit.tE * math.sqrt(above.max() ** 2 - fit.u0 ** 2))
    center = fit.t0 if fit is not None else t_peak
    out = np.abs(t - center) > half

    # Re-derive the baseline from data outside the event window (OGLE step 1).
    if out.sum() >= 10:
        f_base, sig_base = robust_baseline(f, out)
        report["baseline"] = {"flux": f_base, "sigma": sig_base}
    report["event_window"] = {"t_start": center - half, "t_end": center + half}
    chi2_out = (float((((f[out] - f_base) / e[out]) ** 2).sum()) / max(int(out.sum()) - 1, 1)
                if out.sum() >= 10 else None)
    # Other brightenings = strong bumps wholly outside the event window.
    others = [b for b in strong if b is not main
              and (t[b["i1"]] < center - half or t[b["i0"]] > center + half)]
    n_bump = 1 + len(others)

    # Brightening at the peak, as a positive number of magnitudes.
    amp_mag = 2.5 * math.log10(max(f[main["peak_index"]], 1e-9) / f_base)

    cut_rows = [
        _cut("chi2_3plus_min", main["chi2_3plus"], True,
             f">= {cuts['chi2_3plus_min']['value']}", cuts),
        _cut("chi2_out_max", chi2_out, None if chi2_out is None
             else chi2_out <= cuts["chi2_out_max"]["value"],
             f"<= {cuts['chi2_out_max']['value']} outside the event window", cuts),
        {"name": "n_DIA", "value": None, "passed": None, "rule": ">= 3 on difference images",
         "ogle": 3, "why": "Not applicable: TESS light curves carry no difference-image detections."},
        _cut("n_bump", n_bump, n_bump == 1, "exactly one bump", cuts),
        _cut("amplitude_mag_min", amp_mag, amp_mag >= cuts["amplitude_mag_min"]["value"],
             f">= {cuts['amplitude_mag_min']['value']} mag", cuts),
    ]

    if fit is not None:
        fs_frac = fit.fs / (fit.fs + fit.fb) if (fit.fs + fit.fb) != 0 else float("nan")
        span = float(t.max() - t.min())
        report["fit"] = {
            "t0": fit.t0, "u0": fit.u0, "tE": fit.tE, "F_s": fit.fs, "F_b": fit.fb,
            "f_s": fs_frac, "chi2_dof": fit.chi2 / fit.dof,
            "chi2_dof_tE": fit.chi2_tE / fit.dof_tE, "n_outliers": fit.n_outliers,
            "blend_fixed_at_floor": fit.blend_fixed,
            "tE_longer_than_data": fit.tE > span,
        }
        cut_rows += [
            _cut("chi2_fit_max", fit.chi2 / fit.dof,
                 fit.chi2 / fit.dof <= cuts["chi2_fit_max"]["value"],
                 f"<= {cuts['chi2_fit_max']['value']}", cuts),
            _cut("chi2_fit_tE_max", fit.chi2_tE / fit.dof_tE,
                 fit.chi2_tE / fit.dof_tE <= cuts["chi2_fit_tE_max"]["value"],
                 f"<= {cuts['chi2_fit_tE_max']['value']} for |t - t0| < tE", cuts),
            _cut("t0_in_data", fit.t0, bool(t.min() <= fit.t0 <= t.max()),
                 "t0 inside the light curve", cuts),
            _cut("u0_max", fit.u0, fit.u0 <= cuts["u0_max"]["value"],
                 f"<= {cuts['u0_max']['value']}", cuts),
            _cut("tE_max_d", fit.tE, fit.tE <= cuts["tE_max_d"]["value"],
                 f"<= {cuts['tE_max_d']['value']} d", cuts),
            _cut("blend_floor", fit.fb, fit.fb >= cuts["blend_floor"]["value"] - 1e-12,
                 f"F_b >= {cuts['blend_floor']['value']} (baseline units)", cuts),
            _cut("fs_min", fs_frac, bool(fs_frac > cuts["fs_min"]["value"]),
                 f"f_s > {cuts['fs_min']['value']}", cuts),
        ]
    report["cuts"] = cut_rows
    decided = [c for c in cut_rows if c["passed"] is not None]
    report["passed"] = all(c["passed"] for c in decided)
    failed = [c["name"] for c in decided if not c["passed"]]
    report["verdict"] = "ogle_event" if report["passed"] else "fails_ogle_cuts"
    report["summary"] = (
        "Passes every applicable OGLE-IV cut: a microlensing-like single bump."
        if report["passed"] else
        "A bump was found, but it fails these OGLE cuts: " + ", ".join(failed) + "."
    )
    return _json_safe(report)
