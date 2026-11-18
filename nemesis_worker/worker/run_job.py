"""Run one NEMESIS job in its own process.

Usage: python -m worker.run_job <job_dir>

Reads <job_dir>/request.json, runs ``nemesis_tess.full_pipeline`` with
outputs under <job_dir>/out/, and writes <job_dir>/result.json (or
error.json). stdout/stderr are redirected to <job_dir>/log.txt by the parent,
which reads the log to report progress.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

# Settings a vetstar user may change. Everything else keeps NEMESIS defaults.
ALLOWED_OVERRIDES = {
    "minP": float,
    "maxP": float,
    "N_transits": int,
    "do_TLS_Search": bool,
    "SDE_threshold": float,
}

CADENCES = {"30 minute", "10 minute", "2 minute", "20 second"}


def build_settings(req: dict, out_dir: Path) -> dict:
    from nemesis_tess import default_NEMESIS_pipeline_settings

    s = default_NEMESIS_pipeline_settings()
    # Keep the report products (PDF/PNG pages and CSVs) for the UI.
    s["keep_extra_transit_files"] = True
    s["verbose"] = True  # progress markers in the log
    s["show_fig"] = False
    s["cache_dir_path"] = os.environ.get("NEMESIS_CACHE_DIR") or str(out_dir / "cache")
    s["N_TLS_threads"] = int(os.environ.get("NEMESIS_TLS_THREADS", "1"))
    # NEMESIS defaults to the Primary Mission (sectors 1-26). Allow later sectors.
    s["last_updated_sector"] = int(os.environ.get("NEMESIS_LAST_SECTOR", "120"))
    for key, cast in ALLOWED_OVERRIDES.items():
        if req.get(key) is not None:
            s[key] = cast(req[key])
    return s


def main(job_dir: str) -> int:
    job = Path(job_dir)
    req = json.loads((job / "request.json").read_text())
    out_dir = job / "out"
    out_dir.mkdir(exist_ok=True)

    started = time.time()
    try:
        cadence = req.get("cadence", "30 minute")
        if cadence not in CADENCES:
            raise ValueError(f"cadence must be one of {sorted(CADENCES)}")
        tic = int(req["tic"])
        sector = int(req["sector"])

        import nemesis_tess
        from nemesis_tess import full_pipeline
        from nemesis_tess.pipeline import check_ALL_thresholds

        from .summarize import summarize

        print(f"[vetstar] NEMESIS {nemesis_tess.__version__} TIC {tic} S{sector} {cadence}", flush=True)
        settings = build_settings(req, out_dir)
        _hdu, lc_df, transit_results, _used = full_pipeline(
            ID=tic, path=str(out_dir) + "/", cadence=cadence, Sector=sector, settings=settings,
        )
        if lc_df is None or len(lc_df) == 0:
            raise RuntimeError("NEMESIS produced no usable light curve for this star and sector.")
        print("[vetstar] summarising", flush=True)
        result = summarize(lc_df, transit_results, check_ALL_thresholds)
        result["meta"] = {
            "tic": tic, "sector": sector, "cadence": cadence,
            "nemesis_version": nemesis_tess.__version__,
            "runtime_s": round(time.time() - started, 1),
        }
        (job / "result.json").write_text(json.dumps(result))
        print("[vetstar] done", flush=True)
        return 0
    except Exception as exc:
        traceback.print_exc()
        (job / "error.json").write_text(json.dumps({
            "error": f"{type(exc).__name__}: {exc}",
            "runtime_s": round(time.time() - started, 1),
        }))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
