"""Request validation and helpers shared by the routers.
"""
from __future__ import annotations

import logging
import math
import os
import tempfile
import traceback
from typing import Optional

from fastapi import HTTPException, UploadFile

from vetstar_core.pipeline import run_full_vetting
from vetstar_core.progress import ProgressReporter

log = logging.getLogger("vetting")

# -------------------------------------------------
# Sensitivity helpers
# -------------------------------------------------

def _clamp_params(detect_threshold: float, detect_min_snr: float):
    # Widened to support the log-scale slider range: depth 0.01%..9%.
    th = max(0.91, min(0.9999, float(detect_threshold)))
    snr = max(0.5, min(32.0, float(detect_min_snr)))
    return th, snr


def _validate_secondary_sigma(secondary_sigma: float) -> float:
    if not (0.5 <= float(secondary_sigma) <= 20.0):
        raise HTTPException(
            status_code=422,
            detail=f"secondary_sigma must be in [0.5, 20.0], got {secondary_sigma}.",
        )
    return float(secondary_sigma)


def _validate_odd_even_sigma(odd_even_sigma: float) -> float:
    if not (0.5 <= float(odd_even_sigma) <= 20.0):
        raise HTTPException(
            status_code=422,
            detail=f"odd_even_sigma must be in [0.5, 20.0], got {odd_even_sigma}.",
        )
    return float(odd_even_sigma)


def _validate_known_period_days(v: Optional[float]) -> None:
    if v is None:
        return
    if (
        not math.isfinite(v)
        or v <= 0
        or v > 1000
    ):
        raise HTTPException(
            status_code=422,
            detail="known_period_days must be a finite number in (0, 1000].",
        )


def _run_pipeline(parsed: dict, detect_threshold: float, detect_min_snr: float,
                  high_variability: bool = False,
                  rotation_period_days: Optional[float] = None,
                  secondary_sigma: float = 3.0,
                  odd_even_sigma: float = 3.0,
                  known_period_days: Optional[float] = None,
                  reporter: Optional[ProgressReporter] = None,
                  plot_detrend: bool = True,
                  plot_bin_minutes: Optional[int] = 30,
                  known_object=None):
    th, snr = _clamp_params(detect_threshold, detect_min_snr)
    sec_sig = _validate_secondary_sigma(secondary_sigma)
    oe_sig = _validate_odd_even_sigma(odd_even_sigma)
    return run_full_vetting(
        t=parsed["t"],
        flux=parsed["flux"],
        flux_err=parsed["flux_err"],
        quality=parsed["quality"],
        mom_x=parsed["mom_x"],
        mom_y=parsed["mom_y"],
        star=parsed["star"],
        detect_threshold=th,
        detect_min_snr=snr,
        high_variability=high_variability,
        rotation_period_days=rotation_period_days,
        known_period_days=known_period_days,
        secondary_sigma=sec_sig,
        odd_even_sigma=oe_sig,
        reporter=reporter,
        plot_detrend=plot_detrend,
        plot_bin_minutes=plot_bin_minutes,
        known_object=known_object,
    )


def _save_upload_to_tempfile(upload: UploadFile) -> str:
    if not upload.filename:
        raise HTTPException(status_code=400, detail="No filename provided")
    suffix = os.path.splitext(upload.filename)[1].lower() or ".bin"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(upload.file.read())
        tmp.flush()
    finally:
        tmp.close()
    return tmp.name


def _handle_exception(label: str, e: Exception) -> HTTPException:
    tb = traceback.format_exc()
    log.error("%s crashed:\n%s", label, tb)
    return HTTPException(
        status_code=500,
        detail=f"{label}: {type(e).__name__}: {e}",
    )
