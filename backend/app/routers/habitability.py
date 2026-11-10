"""Habitability Chance Index route, and the extra sections (HCI, ExoMiner,
DVT, FFI) that go into PDF reports.

The HCI bundle itself is built in ``vetstar_core.hci_bundle`` so the site
and the Pi front end share one implementation.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from vetstar_core.exominer import run_exominer as _run_exominer
from vetstar_core.hci_bundle import (
    attach_hci_image,
    build_hci_bundle,
    hci_bundle_for_result,
)
from vetstar_core.locks import PLOT_LOCK

from ..cache import _get_ffi_cutout, _lookup_lc

log = logging.getLogger("vetting")

router = APIRouter()


class HabitabilityQuery(BaseModel):
    tic_id: int
    radius_earth: Optional[float] = None
    semi_major_axis_au: Optional[float] = None
    orbital_period_d: Optional[float] = None
    stellar_teff: Optional[float] = None
    stellar_radius_sun: Optional[float] = None
    stellar_mass_sun: Optional[float] = None
    R_companion_Rjup: Optional[float] = None
    planet_mass_earth: Optional[float] = None    # absolute mass override (RV, etc.)
    use_archive_rv: bool = True                  # auto-derive mass from archive RV K
    age_gyr: Optional[float] = None              # known system age
    rotation_period_d: Optional[float] = None    # stellar rotation, for gyrochronology
    vetting_verdict: Optional[dict] = None


def _habitability_bundle(query: HabitabilityQuery, exofop: Optional[dict] = None) -> dict:
    """HCI + observables + TLCM bundle for a /api/habitability query."""
    return build_hci_bundle(
        query.tic_id,
        radius_earth=query.radius_earth,
        semi_major_axis_au=query.semi_major_axis_au,
        orbital_period_d=query.orbital_period_d,
        stellar_teff=query.stellar_teff,
        stellar_radius_sun=query.stellar_radius_sun,
        stellar_mass_sun=query.stellar_mass_sun,
        r_companion_rjup=query.R_companion_Rjup,
        planet_mass_earth=query.planet_mass_earth,
        use_archive_rv=query.use_archive_rv,
        vetting_verdict=query.vetting_verdict,
        age_gyr=query.age_gyr,
        rotation_period_d=query.rotation_period_d,
        rotation_source="user rotation period" if query.rotation_period_d else None,
        exofop=exofop,
    )


def _attach_hci_summary_to_plots(result, **kwargs) -> Optional[dict]:
    """Compute the HCI bundle for ``result`` and stash its summary PNG into
    ``result.plots['hci_summary']`` so the frontend ZIP exporter picks it
    up without a separate /api/habitability round-trip. Returns the bundle
    so the PDF can reuse it. Fails soft: leaves ``plots`` untouched.
    """
    bundle = hci_bundle_for_result(result, **kwargs)
    if bundle and bundle.get("hci_image"):
        result.plots["hci_summary"] = bundle["hci_image"]
    return bundle


@router.post("/api/habitability")
def habitability(query: HabitabilityQuery):
    bundle = _habitability_bundle(query)
    return attach_hci_image(
        bundle, title=f"Habitability Chance Index — TIC {query.tic_id}")


_UNSET = object()


def _build_report_extras(
    result,
    period_override: Optional[float] = None,
    dvt: Optional[dict] = None,
    hci_bundle: Optional[dict] = None,
    ffi_cutout=_UNSET,
) -> dict:
    """Server-side recompute of HCI, observables, TLCM and ExoMiner for a
    finished VettingResult, so the PDF (and the multi-sector panel) can embed
    *all current analyses* without the frontend forwarding its panel state.

    Pass ``hci_bundle`` / ``ffi_cutout`` when the caller already has them
    (the vet-and-report job computes the HCI for the page and prefetches
    the cutout) to skip the recompute / TESScut fetch.

    Every step fails safe: a missing TIC, an offline catalogue, or an
    ExoMiner error simply omits that block.
    """
    extras: dict = {"hci_bundle": None, "exominer": None, "ffi_cutout": None}
    star = result.star
    tic = getattr(star, "tic_id", None)
    period = period_override or (result.bls.get("period") if result.bls else None)

    from vetstar_core.dvt_fetch import best_tce as _best_tce
    tce = _best_tce(dvt)
    dvt_period = tce.get("period_d") if tce else None
    if dvt_period and period_override is None:
        period = dvt_period

    # --- HCI + observables + TLCM (needs a TIC to query ExoFOP) ---------
    extras["hci_bundle"] = hci_bundle or hci_bundle_for_result(
        result, dvt=dvt, period_override=period_override,
    )

    # --- ExoMiner (needs the cached light curve + a period) -------------
    try:
        cached = _lookup_lc(tic, star.sector)
        t0 = result.bls.get("t0") if result.bls else None
        duration = result.bls.get("duration") if result.bls else None
        if cached and period and duration:
            with PLOT_LOCK:
                extras["exominer"] = _run_exominer(
                    t=cached["t"],
                    f=cached["f"],
                    mom_x=cached.get("mom_x"),
                    mom_y=cached.get("mom_y"),
                    period=period,
                    t0=t0 if t0 is not None else 0.0,
                    duration=duration,
                    crowdsap=getattr(star, "crowdsap", None),
                    events=result.events,
                )
    except Exception as e:
        log.warning("Report ExoMiner recompute failed: %s", e)

    # --- FFI cutout (needs coordinates; fetched/cached from TESScut) -----
    if ffi_cutout is not _UNSET:
        extras["ffi_cutout"] = ffi_cutout
    else:
        try:
            if getattr(star, "ra", None) is not None and getattr(star, "dec", None) is not None:
                extras["ffi_cutout"] = _get_ffi_cutout(
                    tic, star.sector, star.ra, star.dec
                )
        except Exception as e:
            log.warning("Report FFI cutout failed: %s", e)

    return extras
