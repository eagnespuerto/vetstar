"""Measure NEMESIS memory per stage, to size (or split) the worker.

Usage: python -m tools.memprobe <TIC> <sector> [cadence] [--lite] [--out report.json]

Runs the same steps as ``nemesis_tess.full_pipeline`` one by one, with the
worker's settings, and wraps NEMESIS's main internal functions (pixel
download, photometry, PLD, BLS/TLS, reports). A thread samples the RSS of
the whole process tree every 0.2 s and charges it to every label that is
active at that moment, so each stage and sub-step gets its own peak.
"""
from __future__ import annotations

import argparse
import functools
import gc
import json
import os
import sys
import threading
import time
from pathlib import Path

import psutil

_proc = psutil.Process()
_active: list[str] = []
_peak: dict[str, float] = {}
_first: dict[str, float] = {}
_duration: dict[str, float] = {}
_lock = threading.Lock()
_t0 = time.time()
_timeline: list[tuple[float, float]] = []


def rss_mb() -> float:
    total = 0
    for p in [_proc] + _proc.children(recursive=True):
        try:
            total += p.memory_info().rss
        except psutil.Error:
            pass
    return total / 2**20


def _sampler():
    while True:
        m = rss_mb()
        with _lock:
            _timeline.append((round(time.time() - _t0, 1), round(m, 1)))
            for lab in _active:
                _peak[lab] = max(_peak.get(lab, 0.0), m)
        time.sleep(0.2)


class stage:
    """Context manager / label for one measured step."""

    def __init__(self, label):
        self.label = label

    def __enter__(self):
        m = rss_mb()
        with _lock:
            _active.append(self.label)
            _first.setdefault(self.label, m)
            _peak[self.label] = max(_peak.get(self.label, 0.0), m)
        self.t = time.time()
        print(f"[memprobe] >> {self.label}  rss={m:.0f} MB", flush=True)

    def __exit__(self, *exc):
        m = rss_mb()
        with _lock:
            _active.remove(self.label)
            _peak[self.label] = max(_peak[self.label], m)
            _duration[self.label] = _duration.get(self.label, 0.0) + time.time() - self.t
        print(f"[memprobe] << {self.label}  rss={m:.0f} MB  peak={_peak[self.label]:.0f} MB", flush=True)
        return False


# NEMESIS internals to measure separately, if they exist in this version.
WRAP = [
    "collect_nearby_TIC_params", "prefetch_quaternions", "prefetch_prf", "gethdu",
    "get_LC_from_FFI", "get_pix_and_bkg_masks_and_image_fluxes", "calc_flux_contamination",
    "SAP_ALT5", "PLD_model", "TransitSearch", "Transit_plot", "Vetting_Report",
    "Vetting_Report_multi_page", "Vetting_Report_6th_page_LEOVetter",
]


def wrap_module(P):
    for name in WRAP:
        fn = getattr(P, name, None)
        if not callable(fn):
            continue

        @functools.wraps(fn)
        def wrapper(*a, __fn=fn, __name=name, **k):
            with stage(f"  {__name}"):
                return __fn(*a, **k)
        setattr(P, name, wrapper)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tic", type=int)
    ap.add_argument("sector", type=int)
    ap.add_argument("cadence", nargs="?", default="30 minute")
    ap.add_argument("--lite", action="store_true")
    ap.add_argument("--out", default="memprobe.json")
    args = ap.parse_args()
    if args.lite:
        os.environ["NEMESIS_LITE"] = "1"

    threading.Thread(target=_sampler, daemon=True).start()
    out_dir = Path(os.environ.get("PROBE_DIR", "probe_out")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ID, sector, cadence = args.tic, args.sector, args.cadence
    error = None

    with stage("0 python + worker baseline"):
        pass
    try:
        with stage("1 import nemesis_tess"):
            import pandas as pd
            import numpy as np
            from nemesis_tess import pipeline as P
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from worker.run_job import build_settings
        wrap_module(P)
        s = build_settings({}, out_dir)
        s["base_path"] = str(out_dir) + "/"
        s.setdefault("downloadpath", s.get("cache_dir_path"))

        with stage("2 catalogue (TIC + neighbours)"):
            star, cat = P.collect_nearby_TIC_params(ID, N_TESS_pixels=2)
        with stage("3 setup + quaternion prefetch"):
            P.Make_dirs(sector, cadence, s, return_paths=False)
            P._resolve_quat_savepath(s)
            secs = P._resolve_target_sectors(ID, sector, star, s)
            if secs:
                P.prefetch_quaternions(sectors=secs, default_settings=s, overwrite=False, verbose=True)
        with stage("4 light curve (LC_creation)"):
            hdu, lc, diag = P.LC_creation(ID, cat, cadence, sector, s)
        if lc is None or len(lc) == 0:
            raise RuntimeError("no light curve")

        inp = pd.DataFrame({"Time": lc["Time"].to_numpy(float),
                            "Flux": lc["Detrended Flux"].to_numpy(float),
                            "Error": lc["Detrended Error"].to_numpy(float)})
        if "QUALITY_FLAG" in lc.columns:
            inp["QUALITY_FLAG"] = lc["QUALITY_FLAG"].to_numpy(int)
        inp = P._filter_lc_by_quality_flags(inp, allowed_quality_flags=s.get("transit_search_quality_flags", [0]),
                                            quality_col="QUALITY_FLAG", verbose=False)
        ok = np.isfinite(inp["Time"]) & np.isfinite(inp["Flux"]) & np.isfinite(inp["Error"]) & (inp["Error"] > 0)
        inp = inp[ok].sort_values("Time").drop_duplicates(subset="Time").reset_index(drop=True)
        keys = {"bkg_residuals", "bkg_trend", "bkg_level_per_pix", "quat_jitter", "quat_status",
                "qlp_result", "quality_flag"}
        d = {k: v for k, v in (diag or {}).items() if k in keys}

        for method, num in (("BLS", 5), ("TLS", 7)):
            with stage(f"{num} {method} search"):
                ps, model, params = P.TransitSearch(method, ID, cat, sector, cadence, hdu, inp, s)
            with stage(f"{num + 1} {method} plots + reports (incl. LEO-Vetter)"):
                P.Transit_plot(ID, cat, sector, cadence, hdu, method, inp, ps, model, params, s)
                P.Vetting_Report(ID, cat, sector, cadence, hdu, s, BLSorTLS=method)
                P.Vetting_Report_multi_page(ID, cat, sector, cadence, hdu, method, s, **d)
            del ps, model
            gc.collect()
        with stage("9 free pixels + gc"):
            del hdu
            gc.collect()
    except Exception as exc:  # report what we got so far
        import traceback
        traceback.print_exc()
        error = f"{type(exc).__name__}: {exc}"

    time.sleep(0.5)
    rep = {
        "tic": ID, "sector": sector, "cadence": cadence, "lite": args.lite, "error": error,
        "stages": [{"label": k, "start_mb": round(_first[k], 1), "peak_mb": round(_peak[k], 1),
                    "seconds": round(_duration.get(k, 0.0), 1)} for k in _first],
        "overall_peak_mb": round(max(m for _, m in _timeline), 1) if _timeline else None,
        "timeline": _timeline[::5],
    }
    Path(args.out).write_text(json.dumps(rep, indent=1))
    print("\n=== peak memory per stage (MB, whole process tree) ===")
    for r in rep["stages"]:
        print(f"{r['label']:<55s} start {r['start_mb']:>7.0f}  peak {r['peak_mb']:>7.0f}  {r['seconds']:>7.1f} s")
    print(f"overall peak: {rep['overall_peak_mb']} MB   error: {error}")


if __name__ == "__main__":
    main()
