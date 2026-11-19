"""Multi-sector vetting: analyse several sectors and pick a
representative one for the report.
"""
from __future__ import annotations

import logging
import math
import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from vetstar_core.parsers import parse_upload
from vetstar_core.report import build_pdf

from ..cache import _cache_lc
from ..common import _handle_exception, _run_pipeline
from .habitability import _attach_hci_summary_to_plots, _build_report_extras
from .transit import _downsample_cached_lc

log = logging.getLogger("vetting")

router = APIRouter()

class MultisectorQuery(BaseModel):
    tic_id: int
    sectors: Optional[list] = None
    detect_threshold: float = 0.997
    detect_min_snr: float = 4.0
    high_variability: bool = False
    rotation_period_days: Optional[float] = None
    secondary_sigma: float = 3.0
    odd_even_sigma: float = 3.0
    # How many distinct objects to keep when clustering per-sector events by
    # transit duration. Default 2 mirrors the historical behaviour; bumping it
    # up to 10 (MAX_SECTORS * EVENTS_PER_SECTOR) lets a real TOI surface when
    # the deepest dips belong to a brighter contaminant.
    max_objects: int = 2
    known_period_days: Optional[float] = Field(
        default=None,
        description=(
            "Optional. If set, BLS searches a ±2% window around this "
            "period (and its P/2 and 2P harmonics) instead of a blind "
            "sweep. Must be in (0, 1000] days."
        ),
    )

    @field_validator("known_period_days")
    @classmethod
    def _validate_known_period_days(cls, v):
        if v is None:
            return v
        if not math.isfinite(v) or v <= 0 or v > 1000:
            raise ValueError(
                "known_period_days must be a finite number in (0, 1000]."
            )
        return v


@router.post("/api/mast/multisector")
def mast_multisector(query: MultisectorQuery):
    result, info = _run_mast_multisector(query)
    _attach_hci_summary_to_plots(result)
    out = result.to_dict()
    out["mast"] = {
        "sectors_used": info.get("sectors_used", []),
        "sectors_attempted": info.get("sectors_attempted", 0),
        "sectors_succeeded": info.get("sectors_succeeded", 0),
        "errors": info.get("errors", []),
        "comparison": info.get("comparison", []),
        "representative_sector": info.get("representative_sector"),
        "n_sectors_observed": info.get("n_sectors_observed", 1),
        "n_sectors_with_detections": info.get("n_sectors_with_detections", 0),
    }
    out["lightcurve"] = _downsample_cached_lc(
        result.star.tic_id, info.get("representative_sector")
    )
    return out


@router.post("/api/mast/multisector/report")
def mast_multisector_report(query: MultisectorQuery):
    """Build a PDF report from the representative single-sector run."""
    result, info = _run_mast_multisector(query)
    try:
        extras = _build_report_extras(result)
        pdf = build_pdf(
            result,
            hci_bundle=extras.get("hci_bundle"),
            exominer=extras.get("exominer"),
            ffi_cutout=extras.get("ffi_cutout"),
        )
    except Exception as e:
        raise _handle_exception("build_pdf", e)

    fname = f"vetting_TIC{query.tic_id}_multisector.pdf"
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


#: Higher score = "more interesting candidate" for representative-sector picks.
_VERDICT_PRIORITY = {
    "planet_candidate": 5,
    "known_object": 4,
    "eclipsing_binary_candidate": 3,
    "ambiguous": 2,
    "no_signal": 1,
    "false_positive_blend": 0,
}


def _pick_representative_sector(summaries: list) -> Optional[int]:
    """Choose the representative sector for a multi-sector run.

    Ranks each sector by ``(verdict_score, period_alignment_count, sde)``
    in that order — so a single planet-candidate sector always beats a
    false-positive sector with a higher SDE, and within a tied verdict
    a sector whose BLS period rounds to the same integer day as another
    sector wins over a lone outlier.
    """
    if not summaries:
        return None
    int_periods = [
        round(s["bls_period_d"]) if s.get("bls_period_d") else None
        for s in summaries
    ]

    def score(i: int):
        s = summaries[i]
        v = _VERDICT_PRIORITY.get(s.get("category"), -1)
        ip = int_periods[i]
        aligned = (
            sum(1 for j, jp in enumerate(int_periods) if j != i and jp == ip)
            if ip is not None else 0
        )
        sde = float(s.get("bls_sde") or 0.0)
        return (v, aligned, sde)

    best = max(range(len(summaries)), key=score)
    return summaries[best]["sector"]


def _run_mast_multisector(query: MultisectorQuery):
    """Run up to ``MAX_SECTORS`` consecutive single-sector pipelines, pick
    the representative sector by verdict / period agreement / SDE, then
    re-run that sector's pipeline to surface its full result.

    Two passes keep memory bounded to **one** single-sector pipeline at a
    time. Pass 1 walks each sector, runs the pipeline, captures a short
    summary, then frees every per-sector object before the next iteration.
    Pass 2 re-runs only the chosen representative and keeps its full
    result for the response. The extra run is the price of getting the
    representative choice right — picking on streamed state can drop a
    real planet candidate the moment a later sector happens to outrank
    it on raw SDE.


    Returns ``(VettingResult, info_dict)``.
    """
    from vetstar_core.mast_fetch import fetch_spoc_lightcurve, list_available_sectors

    try:
        all_sectors = list_available_sectors(query.tic_id)
    except Exception as e:
        raise HTTPException(502, f"MAST sector list failed: {e}")

    if not all_sectors:
        raise HTTPException(404, f"No TESS sectors found for TIC {query.tic_id}.")

    from vetstar_core.pipeline import MAX_SECTORS

    if query.sectors:
        wanted = set(int(s) for s in query.sectors)
        sectors_to_fetch = [s for s in all_sectors if s["sector"] in wanted][:MAX_SECTORS]
    else:
        sectors_to_fetch = all_sectors[-MAX_SECTORS:]

    import gc

    import matplotlib.pyplot as _plt

    def _run_sector(sec_num: int):
        """Fetch, parse, and pipeline a single sector. Returns
        ``(result, parsed)``; caller owns deleting the FITS file."""
        fetched = fetch_spoc_lightcurve(query.tic_id, sec_num)
        path = fetched.get("path")
        parsed = parse_upload(path, fetched["filename"])
        if parsed.get("t") is None or parsed.get("flux") is None:
            raise RuntimeError("parser returned no time series")
        result = _run_pipeline(
            parsed,
            query.detect_threshold, query.detect_min_snr,
            high_variability=query.high_variability,
            rotation_period_days=query.rotation_period_days,
            secondary_sigma=query.secondary_sigma,
            odd_even_sigma=query.odd_even_sigma,
            known_period_days=query.known_period_days,
        )
        return result, parsed, path

    # ---- Pass 1: summaries only ------------------------------------------
    summaries: list = []
    errors: list = []

    for sec_info in sectors_to_fetch:
        sec_num = sec_info["sector"]
        path = None
        try:
            result, parsed, path = _run_sector(sec_num)
            sde = (result.bls.get("sde") if result.bls else None) or 0.0
            summaries.append({
                "sector": sec_num,
                "verdict": result.verdict.get("headline") if result.verdict else None,
                "category": result.verdict.get("category") if result.verdict else None,
                "n_events": len(result.events) if result.events else 0,
                "bls_period_d": result.bls.get("period") if result.bls else None,
                "bls_depth": result.bls.get("depth") if result.bls else None,
                "bls_duration_d": result.bls.get("duration") if result.bls else None,
                "bls_sde": float(sde),
            })
            del result, parsed
        except Exception as e:
            log.warning("Sector %s failed for TIC %s: %s", sec_num, query.tic_id, e)
            errors.append({"sector": sec_num, "error": str(e)})
        finally:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            _plt.close("all")
            gc.collect()

    if not summaries:
        raise HTTPException(502, f"All sector pipelines failed. Errors: {errors}")

    # ---- Pick representative ---------------------------------------------
    rep_sector = _pick_representative_sector(summaries)
    log.info(
        "Multi-sector rep TIC %s: sector S%03d (verdicts=%s, periods=%s, sdes=%s)",
        query.tic_id, rep_sector or -1,
        [s.get("category") for s in summaries],
        [s.get("bls_period_d") for s in summaries],
        [s.get("bls_sde") for s in summaries],
    )

    # Annotate each summary with its period-alignment count for the UI.
    int_periods = [
        round(s["bls_period_d"]) if s.get("bls_period_d") else None
        for s in summaries
    ]
    for i, s in enumerate(summaries):
        ip = int_periods[i]
        s["period_aligned_with"] = (
            [
                summaries[j]["sector"]
                for j, jp in enumerate(int_periods)
                if j != i and jp == ip and ip is not None
            ]
        )

    # ---- Pass 2: re-run the chosen representative ------------------------
    best_result = None
    best_path = None
    try:
        best_result, best_parsed, best_path = _run_sector(rep_sector)
        _cache_lc(best_parsed)  # ExoMiner endpoint reads this cache
        del best_parsed
    except Exception as e:
        # Fall back: re-raise so the user sees the error instead of a partial
        # response. Pass 1 already succeeded for this sector, so a Pass-2
        # failure is genuinely unexpected.
        raise HTTPException(
            502, f"Representative sector S{rep_sector:03d} re-run failed: {e}"
        )
    finally:
        if best_path and os.path.exists(best_path):
            try:
                os.remove(best_path)
            except OSError:
                pass
        _plt.close("all")
        gc.collect()

    n_with_dets = sum(1 for s in summaries if s["n_events"] > 0)
    info = {
        "sectors_used": [s["sector"] for s in summaries],
        "sectors_attempted": len(sectors_to_fetch),
        "sectors_succeeded": len(summaries),
        "errors": errors,
        "comparison": summaries,
        "representative_sector": rep_sector,
        "n_sectors_observed": len(summaries),
        "n_sectors_with_detections": n_with_dets,
    }
    return best_result, info
