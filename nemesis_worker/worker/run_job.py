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
    # Quaternions come from the AWS mirror (see quaternions.py), saved here.
    s["quat_savepath"] = str(Path(s["cache_dir_path"]) / "quaternions") + "/"
    # Keep the TESScut cutout so users can download the raw pixels (moved
    # into the job folder after the run, see collect_raw_fits).
    s["keep_FITS"] = True
    s["N_TLS_threads"] = int(os.environ.get("NEMESIS_TLS_THREADS", "1"))
    # NEMESIS defaults to the Primary Mission (sectors 1-26). Allow later sectors.
    s["last_updated_sector"] = int(os.environ.get("NEMESIS_LAST_SECTOR", "120"))
    if lite_mode():
        s.update(LITE_SETTINGS)
    for key, cast in ALLOWED_OVERRIDES.items():
        if req.get(key) is not None:
            s[key] = cast(req[key])
    return s


# Light mode for small instances (e.g. Render free, 512 MB). Everything here
# skips work the vetstar UI does not show, except the TLS grid, which is
# coarser than NEMESIS II's default (oversampling 9): a little less sensitive
# to very shallow signals, much less memory and CPU. Recorded in result meta.
LITE_SETTINGS = {
    "do_DSS_plot": False,            # no DSS sky image download
    "keep_FITS": False,
    "keep_imagedata": False,
    "keep_raw_and_preclip": False,
    "keep_extra_files": False,
    "oversampling_factor": 3,        # TLS/BLS period grid (default 9)
    "duration_grid_step": 1.1,       # TLS duration grid (default 1.05)
    "N_TLS_threads": 1,
}


def collect_raw_fits(cache_dir: Path, out_dir: Path, tic: int, since: float) -> list[str]:
    """Move this job's raw FITS out of the shared cache into <out>/fits/.

    TESScut cutouts (cache/TESSCut) carry no TIC in their names, but the worker
    runs one job at a time, so every cutout written since the job started is
    this job's. SPOC TPFs (cache/TESS, 2-minute runs) carry the TIC, so those
    are copied and left in the cache for the next run of the same star.
    """
    import shutil

    dest = out_dir / "fits"
    moved: list[str] = []
    tess_cut = cache_dir / "TESSCut"
    if tess_cut.exists():
        for p in tess_cut.rglob("*.fits"):
            if p.is_file() and p.stat().st_mtime >= since - 5:
                dest.mkdir(exist_ok=True)
                shutil.move(str(p), dest / p.name)
                moved.append(p.name)
    tpf = cache_dir / "TESS"
    if tpf.exists():
        tic16 = f"{tic:016d}"
        for p in tpf.rglob("*.fits"):
            if p.is_file() and (tic16 in p.name or f"TIC_{tic}" in p.parts):
                dest.mkdir(exist_ok=True)
                shutil.copy2(p, dest / p.name)
                moved.append(p.name)
    return moved


def lite_mode() -> bool:
    return os.environ.get("NEMESIS_LITE", "0").lower() in ("1", "true", "yes")


def peak_rss_mb():
    """Peak memory of this job process, so we can size the instance."""
    try:
        import resource
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)  # KiB on Linux
    except Exception:
        return None


def star_info(tic: int) -> dict:
    """TIC v8.2 basics for the site's FFI cutout and ExoFOP panels. Fails soft."""
    try:
        from astroquery.mast import Catalogs
        row = Catalogs.query_criteria(catalog="TIC", ID=tic)[0]

        def g(k):
            try:
                v = float(row[k])
                return v if v == v else None  # drop NaN
            except Exception:
                return None
        return {"tic_id": tic, "ra": g("ra"), "dec": g("dec"), "tmag": g("Tmag"),
                "teff": g("Teff"), "radius": g("rad"), "mass": g("mass"), "logg": g("logg")}
    except Exception as exc:
        print(f"[vetstar] TIC lookup failed: {exc}", flush=True)
        return {"tic_id": tic}


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
        if settings.get("use_quat_jitter_mask", True):
            from .quaternions import ensure_quaternions
            ensure_quaternions(sector, settings["quat_savepath"])
        # TIC coordinates first: they name the cutout NEMESIS looks for, and
        # the same record fills the star panel afterwards.
        star = star_info(tic)
        from .tesscut_aws import ensure_tesscut
        ensure_tesscut(tic, sector, cadence, star.get("ra"), star.get("dec"),
                       int(settings.get("cutoutsize", 11)), settings["cache_dir_path"])
        _hdu, lc_df, transit_results, _used = full_pipeline(
            ID=tic, path=str(out_dir) + "/", cadence=cadence, Sector=sector, settings=settings,
        )
        # Free the pixel data before summarising and drawing figures.
        del _hdu, _used
        import gc
        gc.collect()
        if not lite_mode():
            raw = collect_raw_fits(Path(settings["cache_dir_path"]), out_dir, tic, started)
            print(f"[vetstar] raw FITS kept: {raw or 'none'}", flush=True)
        if lc_df is None or len(lc_df) == 0:
            raise RuntimeError("NEMESIS produced no usable light curve for this star and sector.")
        print("[vetstar] summarising", flush=True)
        result = summarize(lc_df, transit_results, check_ALL_thresholds)
        primary = result.get("primary")
        params = result["search"][primary]["params"] if primary else {}
        from .figures import all_figures
        result["figures"] = all_figures(lc_df, transit_results, primary, params, tic, sector, cadence)
        result["star"] = star if star.get("ra") is not None else star_info(tic)
        result["meta"] = {
            "tic": tic, "sector": sector, "cadence": cadence,
            "nemesis_version": nemesis_tess.__version__,
            "runtime_s": round(time.time() - started, 1),
            "lite": lite_mode(),
            "peak_rss_mb": peak_rss_mb(),
        }
        (job / "result.json").write_text(json.dumps(result))
        print("[vetstar] done", flush=True)
        return 0
    except Exception as exc:
        traceback.print_exc()
        try:  # don't leave a failed run's cutouts in the shared cache
            if not lite_mode():
                collect_raw_fits(Path(build_settings(req, out_dir)["cache_dir_path"]), out_dir,
                                 int(req.get("tic", 0)), started)
        except Exception:
            pass
        (job / "error.json").write_text(json.dumps({
            "error": f"{type(exc).__name__}: {exc}",
            "runtime_s": round(time.time() - started, 1),
        }))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
