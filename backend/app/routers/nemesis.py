"""NEMESIS front: proxy to the separate NEMESIS worker service.

vetstar v2 uses NEMESIS (Feliz et al. 2021, AJ 161, 247; NEMESIS II,
Feliz et al. 2026) with LEO-Vetter as its main transit search. NEMESIS needs
its own Python 3.12 stack and more memory than the site has, so it runs in
``nemesis_worker/`` and this router forwards requests to it.

Set NEMESIS_WORKER_URL (and NEMESIS_WORKER_TOKEN) on the site. Without a URL,
``/api/nemesis/status`` reports ``enabled: false`` and the UI offers the
Quick check pipeline instead.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from vetstar_core.hci_bundle import attach_hci_image, build_hci_bundle

log = logging.getLogger("vetting")

router = APIRouter()

TIMEOUT_S = 20


def _relay_site() -> str:
    """A Vetstar site to relay NEMESIS calls through (the desktop app uses the
    public site, so the worker token never ships in a download). Only used
    when no worker URL is set."""
    if os.environ.get("NEMESIS_WORKER_URL"):
        return ""
    return os.environ.get("NEMESIS_SITE_URL", "").rstrip("/")


def _base() -> str:
    site = _relay_site()
    return site + "/api/nemesis" if site else os.environ.get("NEMESIS_WORKER_URL", "").rstrip("/")


def _call(method: str, path: str, body: Optional[dict] = None, raw: bool = False):
    base = _base()
    if not base:
        raise HTTPException(503, "The NEMESIS worker is not set up on this server. Use Quick check.")
    if _relay_site() and path == "/health":
        # The site's /status wraps the worker's /health.
        st = _call("GET", "/status")
        if not st.get("online"):
            raise HTTPException(503, st.get("detail") or "NEMESIS is offline on the Vetstar site.")
        return {k: v for k, v in st.items() if k not in ("enabled", "online")}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if not _relay_site():
        req.add_header("X-Worker-Token", os.environ.get("NEMESIS_WORKER_TOKEN", ""))
    req.add_header("User-Agent", "vetstar-desktop")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            payload = resp.read()
            if raw:
                return payload, resp.headers.get("Content-Type", "application/octet-stream")
            return json.loads(payload)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail", str(exc))
        except Exception:
            detail = str(exc)
        raise HTTPException(exc.code, detail)
    except (urllib.error.URLError, TimeoutError) as exc:
        log.warning("NEMESIS worker unreachable: %s", exc)
        raise HTTPException(502, "The NEMESIS worker is not responding. Try again soon, or use Quick check.")


class NemesisJob(BaseModel):
    tic: int = Field(gt=0)
    sector: int = Field(ge=1, le=200)
    cadence: str = "30 minute"
    minP: Optional[float] = Field(default=None, gt=0)
    maxP: Optional[float] = Field(default=None, gt=0)
    N_transits: Optional[int] = Field(default=None, ge=1, le=10)
    do_TLS_Search: Optional[bool] = None


def _job_id(job_id: str) -> str:
    if not job_id.isalnum():
        raise HTTPException(404, "unknown job")
    return job_id


@router.get("/api/nemesis/status")
def nemesis_status():
    if not _base():
        return {"enabled": False}
    try:
        h = _call("GET", "/health")
    except HTTPException as exc:
        return {"enabled": True, "online": False, "detail": exc.detail}
    return {"enabled": True, "online": True, **h}


@router.post("/api/nemesis/jobs")
def nemesis_submit(job: NemesisJob):
    return _call("POST", "/jobs", job.model_dump(exclude_none=True))


@router.get("/api/nemesis/sectors/{tic}")
def nemesis_sectors(tic: int):
    """Sectors NEMESIS can run for this star (checked before a job is queued)."""
    if tic <= 0:
        raise HTTPException(422, "TIC must be positive")
    return _call("GET", f"/sectors/{tic}")


@router.get("/api/nemesis/jobs/{job_id}")
def nemesis_job(job_id: str):
    return _call("GET", f"/jobs/{_job_id(job_id)}")


@router.get("/api/nemesis/jobs/{job_id}/result")
def nemesis_result(job_id: str):
    return _call("GET", f"/jobs/{_job_id(job_id)}/result")


@router.get("/api/nemesis/jobs/{job_id}/files")
def nemesis_files(job_id: str):
    return _call("GET", f"/jobs/{_job_id(job_id)}/files")


@router.get("/api/nemesis/jobs/{job_id}/files/{name}")
def nemesis_file(job_id: str, name: str):
    payload, ctype = _call(
        "GET", f"/jobs/{_job_id(job_id)}/files/{urllib.parse.quote(name)}", raw=True)
    return Response(payload, media_type=ctype,
                    headers={"Content-Disposition": f'inline; filename="{name}"'})


class NemesisReportQuery(BaseModel):
    hci_image: Optional[str] = None   # base64 PNG from /api/nemesis/hci, if the user scored it
    include_ffi: bool = True
    author: Optional[str] = Field(default=None, max_length=80)     # "Prepared by" on the cover
    initials: Optional[str] = Field(default=None, max_length=8)


@router.post("/api/nemesis/jobs/{job_id}/report")
def nemesis_report(job_id: str, q: NemesisReportQuery):
    """Vetstar PDF report for a finished NEMESIS run."""
    from vetstar_core.nemesis_report import build_nemesis_pdf

    result = _call("GET", f"/jobs/{_job_id(job_id)}/result")
    meta, star = result.get("meta") or {}, result.get("star") or {}
    ffi_b64 = None
    if q.include_ffi and star.get("ra") is not None and star.get("dec") is not None:
        try:
            from ..cache import _get_ffi_cutout
            cut = _get_ffi_cutout(meta.get("tic"), meta.get("sector"), star["ra"], star["dec"])
            ffi_b64 = (cut or {}).get("image")
        except Exception as exc:  # the report still builds without the cutout
            log.warning("NEMESIS report: FFI cutout failed: %s", exc)
    pdf = build_nemesis_pdf(result, hci_image_b64=q.hci_image, ffi_image_b64=ffi_b64,
                            author=q.author, initials=q.initials)
    name = f"vetstar_nemesis_TIC{int(meta.get('tic') or 0)}_S{int(meta.get('sector') or 0):03d}.pdf"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# LEO-Vetter test key -> vetstar verdict flag that HCI's vetting term knows.
LEO_TO_VETSTAR_FLAG = {
    "odd_even": "odd_even_mismatch",
    "secondary": "secondary_eclipse_detected",
    "large": "companion_too_large_for_planet",
    "offset": "centroid_offset",
}


def leo_to_vetting_verdict(leo: dict) -> Optional[dict]:
    """Map a NEMESIS LEO-Vetter payload to the verdict dict HCI v2 scores.

    ``leo`` is ``result["search"][method]["leo"]`` from the worker:
    ``{"available": bool, "disposition": "FA"|"FP"|"PC",
       "tests": [{"key", "group", "passed", ...}, ...]}``.

    Return ``{"category": ..., "flags": [...]}`` using vetstar's categories
    (``planet_candidate``, ``eclipsing_binary_candidate``,
    ``false_positive_blend``, ``no_signal``, ``ambiguous``), or ``None`` when
    LEO-Vetter did not run (HCI then scores vetting as "Not vetted").
    """
    if not leo or not leo.get("available"):
        return None
    failed_fp = [t["key"] for t in leo.get("tests", [])
                 if t.get("group") == "FP" and not t.get("passed")]
    flags = [LEO_TO_VETSTAR_FLAG[k] for k in failed_fp if k in LEO_TO_VETSTAR_FLAG]
    disposition = leo.get("disposition")
    if disposition == "FA":
        # LEO-Vetter's false-alarm tests are strict for shallow M-dwarf
        # signals, so a failure means "look again", not "no signal".
        return {"category": "ambiguous", "flags": flags}
    if disposition == "FP":
        if "offset" in failed_fp:
            category = "false_positive_blend"
        elif any(k in failed_fp for k in ("odd_even", "secondary", "large")):
            category = "eclipsing_binary_candidate"
        else:
            # A V-shape alone can be a grazing planet; do not cap HCI for it.
            category = "ambiguous"
        return {"category": category, "flags": flags}
    return {"category": "planet_candidate", "flags": flags}


class NemesisHciQuery(BaseModel):
    tic: int = Field(gt=0)
    period_d: Optional[float] = Field(default=None, gt=0)
    radius_earth: Optional[float] = Field(default=None, gt=0)
    duration_hr: Optional[float] = Field(default=None, gt=0)
    depth_ppt: Optional[float] = None
    leo: Optional[dict] = None


@router.post("/api/nemesis/hci")
def nemesis_hci(q: NemesisHciQuery):
    """HCI v2 (an optional extra) scored on a NEMESIS detection."""
    verdict = leo_to_vetting_verdict(q.leo) if q.leo else None
    if verdict is not None:
        if q.duration_hr:
            verdict["_duration"] = q.duration_hr / 24.0
        if q.depth_ppt is not None:
            verdict["_depth"] = q.depth_ppt / 1e3
    bundle = build_hci_bundle(
        q.tic,
        radius_earth=q.radius_earth,
        orbital_period_d=q.period_d,
        vetting_verdict=verdict,
    )
    return attach_hci_image(bundle, title=f"Habitability Chance Index — TIC {q.tic} (NEMESIS)")
