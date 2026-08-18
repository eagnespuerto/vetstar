"""Command-line entrypoint for VetStar Pi.

Sub-commands mirror the cloud API where they make sense for local use:

    gui           Launch the Tkinter GUI (default when no sub-command given).
    transit       Run the transit vetting pipeline on a local FITS/JSON file.
    mast          Download a SPOC light-curve FITS by (TIC, sector) from MAST.
    sectors       List available TESS sectors for a TIC.
    microlens     Run the 3-way BIC classifier on a windowed CSV/JSON.
    coverage      TESS sector-overlap targeting for a CSV of events.
    habitability  Compute HCI + POE + TLCM for a TIC or explicit parameters.
    exominer      ExoMiner feature/view extraction on the last analysed LC.
    ffi           TESScut FFI cutout PNG for (ra, dec, sector).
    alerts        Cone-search the Gaia Alerts master index.
    version       Print the version.

Everything is offline-friendly except ``mast``, ``sectors``, ``habitability``
(cross-match), ``ffi``, ``coverage`` (needs tess-point), and ``alerts`` —
each of these hits an external service and fails soft when the Pi is
offline.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

import numpy as np

from . import __version__


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _load_lc(path: str):
    """Dispatch on extension via the vendored parsers module."""
    from .parsers import parse_upload
    return parse_upload(path, os.path.basename(path))


def _write_json(path: str, obj) -> None:
    def default(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            v = float(o)
            return v if np.isfinite(v) else None
        if isinstance(o, np.ndarray):
            return o.tolist()
        return str(o)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=default)


def _strip_plots_and_periodograms(d: dict) -> dict:
    """Drop base64 plots and periodograms from a serialised VettingResult so
    the JSON stays small (~50 kB vs several MB)."""
    d.pop("plots", None)
    for key in ("bls", "lomb_scargle"):
        if isinstance(d.get(key), dict):
            d[key].pop("_periodogram", None)
            d[key].pop("periodogram", None)
    return d


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def _cmd_gui(args):
    from .gui import run_gui
    run_gui()
    return 0


def _cmd_transit(args):
    from .pipeline import run_full_vetting
    from .plots import build_transit_overview
    from .report import build_pdf
    parsed = _load_lc(args.input)
    if parsed.get("metadata_only"):
        print("Error: ExoFOP metadata-only JSON has no time series. "
              "Upload a FITS light curve instead.", file=sys.stderr)
        return 2

    print(f"Loaded {args.input}: TIC={parsed['star'].tic_id}  "
          f"sector={parsed['star'].sector}  n_cadences={len(parsed['t'])}")

    result = run_full_vetting(
        t=parsed["t"], flux=parsed["flux"], flux_err=parsed["flux_err"],
        quality=parsed["quality"], mom_x=parsed["mom_x"], mom_y=parsed["mom_y"],
        star=parsed["star"],
        detect_threshold=args.detect_threshold,
        detect_min_snr=args.detect_min_snr,
        high_variability=args.high_variability,
        rotation_period_days=args.rotation_period_days,
        known_period_days=args.known_period_days,
        secondary_sigma=args.secondary_sigma,
        odd_even_sigma=args.odd_even_sigma,
        plot_detrend=not args.no_plot_detrend,
        plot_bin_minutes=args.plot_bin_minutes,
    )

    v = result.verdict
    print(f"\nVerdict: {v.get('headline')}")
    print(f"Category: {v.get('category')}   confidence={v.get('confidence'):.2f}")
    print(f"BLS period={result.bls.get('period', 0):.6f} d   "
          f"depth={result.bls.get('depth', 0):.5f}   SDE={result.bls.get('sde', 0):.2f}")
    print(f"Events detected: {len(result.events)}")

    os.makedirs(args.out, exist_ok=True)

    # Save embedded plots
    t_c, f_c, _ = _clean_for_plot(parsed)
    fig = build_transit_overview(result, t_c, f_c)
    fig.savefig(os.path.join(args.out, "overview.png"), dpi=140, bbox_inches="tight")

    # Also dump each of the built-in plot PNGs (base64 → file) from make_plots
    from base64 import b64decode
    for key, b64 in (result.plots or {}).items():
        if not isinstance(b64, str) or not b64:
            continue
        try:
            with open(os.path.join(args.out, f"plot_{key}.png"), "wb") as fh:
                fh.write(b64decode(b64))
        except Exception:
            pass

    # ExoMiner / HCI / DVT / FFI — optional extras rolled into the PDF
    extras = _extras_for(result, parsed, args)
    if not args.no_pdf:
        pdf_path = os.path.join(args.out, "report.pdf")
        with open(pdf_path, "wb") as fh:
            fh.write(build_pdf(
                result,
                hci_bundle=extras.get("hci_bundle"),
                exominer=extras.get("exominer"),
                ffi_cutout=extras.get("ffi_cutout"),
                dvt=extras.get("dvt"),
            ))
        print(f"Wrote PDF: {pdf_path}")

    if not args.no_json:
        _write_json(os.path.join(args.out, "result.json"),
                    _strip_plots_and_periodograms(result.to_dict()))

    print(f"Outputs in: {args.out}")
    return 0


def _clean_for_plot(parsed):
    """Same cleaning the pipeline does — used for the GUI overview plot."""
    from .pipeline import clean_lightcurve
    return clean_lightcurve(parsed["t"], parsed["flux"], parsed["flux_err"],
                            parsed["quality"])


def _extras_for(result, parsed, args) -> dict:
    """Best-effort build of the HCI bundle, ExoMiner run, DVT parse and FFI
    cutout that ``report.build_pdf`` slots into the report."""
    import logging
    log = logging.getLogger("vetstar-pi.extras")
    extras = {}

    if args.no_extras:
        return extras

    tic = result.star.tic_id
    period = (result.bls.get("period") if result.bls else None)

    # DVT — cheap when the sector has a SPOC DV run.
    dvt = None
    if tic and result.star.sector is not None:
        try:
            from .dvt_fetch import fetch_dvt
            dvt = fetch_dvt(tic, result.star.sector)
        except Exception as e:
            log.warning("DVT fetch failed: %s", e)
    extras["dvt"] = dvt

    # HCI + POE + TLCM — needs ExoFOP for TOI parameters.
    if tic and not args.no_hci:
        try:
            extras["hci_bundle"] = _hci_bundle(result, dvt)
        except Exception as e:
            log.warning("HCI compute failed: %s", e)

    # ExoMiner — needs the cleaned LC arrays + a period + duration.
    if tic and period and not args.no_exominer:
        try:
            from .exominer import run_exominer
            t_c, f_c, _ = _clean_for_plot(parsed)
            t0 = result.bls.get("t0") or 0.0
            dur = result.bls.get("duration") or 0.0
            crowdsap = getattr(result.star, "crowdsap", None)
            extras["exominer"] = run_exominer(
                t=t_c, f=f_c, mom_x=parsed.get("mom_x"), mom_y=parsed.get("mom_y"),
                period=period, t0=t0, duration=dur, crowdsap=crowdsap,
            )
        except Exception as e:
            log.warning("ExoMiner failed: %s", e)

    # FFI cutout — needs coordinates.
    if tic and result.star.ra is not None and not args.no_ffi:
        try:
            from .ffi_cutout import make_ffi_cutout
            extras["ffi_cutout"] = make_ffi_cutout(
                ra=result.star.ra, dec=result.star.dec, sector=result.star.sector,
                tic_id=tic, size_px=15,
            )
        except Exception as e:
            log.warning("FFI cutout failed: %s", e)

    return extras


def _hci_bundle(result, dvt) -> dict:
    """Rebuild the HCI + POE + TLCM bundle for a single-sector result."""
    from .exofop import query_exofop
    from .habitability import PlanetCandidate, compute_hci, estimate_stellar_from_teff
    from .observables import compute_observables as compute_poe
    from .tlcm_geometry import compute_tlcm_geometry
    from .hci_image import make_hci_summary_image

    tic = result.star.tic_id
    exofop = query_exofop(tic)
    star_info = exofop.get("star", {}) or {}
    tois = exofop.get("tois", []) or []
    best_toi = None
    for t in tois:
        d = (t.get("disposition") or "").upper()
        if d in ("PC", "APC", "CP", "KP") or best_toi is None:
            best_toi = t
            if d in ("CP", "KP"):
                break

    period = (result.bls.get("period") if result.bls else None) or (
        best_toi.get("period_d") if best_toi else None
    )
    teff = star_info.get("teff") or result.star.teff
    rstar = star_info.get("radius") or result.star.radius
    mstar = star_info.get("mass") or getattr(result.star, "mass", None)

    if teff and (not rstar or not mstar):
        est = estimate_stellar_from_teff(teff)
        if est:
            rstar = rstar or est["radius_sun"]
            mstar = mstar or est["mass_sun"]

    planet = PlanetCandidate(
        radius_earth=(best_toi or {}).get("radius_earth"),
        semi_major_axis_au=(best_toi or {}).get("semi_major_axis_au"),
        orbital_period_d=period,
        toi_number=(best_toi or {}).get("toi_number"),
        disposition=(best_toi or {}).get("disposition"),
        stellar_teff=teff,
        stellar_radius_sun=rstar,
        stellar_mass_sun=mstar,
        depth_ppm=(best_toi or {}).get("depth_ppm"),
        duration_hr=(best_toi or {}).get("duration_hr"),
        source=exofop.get("source", "unknown"),
    )

    tlcm = compute_tlcm_geometry(
        period_d=period,
        t14_d=(result.shape.get("t14_d") if result.shape.get("available") else result.bls.get("duration")),
        depth_frac=(result.events[0]["depth"] if result.events else result.bls.get("depth")),
        rstar_sun=rstar,
        mstar_sun=mstar,
    ).to_dict()

    hci = compute_hci(planet=planet, vetting_verdict=result.verdict,
                      n_sectors_with_detections=1, n_sectors_observed=1,
                      a_over_rs=tlcm.get("a_over_rs"),
                      radius_ratio_k=tlcm.get("radius_ratio_k"),
                      stellar_density_rho_sun=tlcm.get("stellar_density_rho_sun"))

    poe = compute_poe(
        teff_k=teff, rstar_sun=rstar, mstar_sun=mstar,
        distance_pc=star_info.get("distance"),
        orbital_period_d=period,
        semi_major_axis_au=planet.semi_major_axis_au,
        rp_earth=planet.radius_earth,
    ).to_dict()

    bundle = {
        "hci": hci.to_dict(),
        "observables": poe,
        "tlcm": tlcm,
        "planet": {
            "radius_earth": planet.radius_earth,
            "mass_earth": planet.mass_earth,
            "stellar_teff": teff,
            "stellar_radius_sun": rstar,
            "stellar_mass_sun": mstar,
            "orbital_period_d": period,
            "semi_major_axis_au": planet.semi_major_axis_au,
            "toi_number": planet.toi_number,
            "disposition": planet.disposition,
        },
    }
    try:
        bundle["hci_image"] = make_hci_summary_image(
            bundle.get("hci"), bundle.get("observables"), bundle.get("tlcm"),
            planet=bundle.get("planet"),
            title=f"Habitability Chance Index — TIC {tic}",
        )
    except Exception:
        bundle["hci_image"] = None
    return bundle


def _cmd_mast(args):
    from .mast_fetch import fetch_spoc_lightcurve
    info = fetch_spoc_lightcurve(args.tic, args.sector)
    src = info.get("path")
    if not src or not os.path.exists(src):
        print("FITS not found after fetch.", file=sys.stderr)
        return 3
    dst = args.out or f"TIC{args.tic}_S{args.sector:03d}.fits"
    with open(src, "rb") as sh, open(dst, "wb") as dh:
        dh.write(sh.read())
    print(f"Saved: {dst}   (matched obs={info.get('matched')}, "
          f"author={info.get('author')}, exptime={info.get('exptime')})")
    return 0


def _cmd_sectors(args):
    from .mast_fetch import list_available_sectors
    sectors = list_available_sectors(args.tic)
    print(f"TIC {args.tic}: {len(sectors)} sector(s)")
    for s in sectors:
        print(f"  S{s['sector']:03d}  camera={s.get('camera')}  ccd={s.get('ccd')}  "
              f"author={s.get('author')}  exptime={s.get('exptime')}s")
    return 0


def _cmd_microlens(args):
    from .microlensing import analyze_event
    from .microlensing_report import build_microlensing_pdf
    from .plots import build_microlens_fit

    parsed = _load_lc(args.input) if args.input.lower().endswith((".fits", ".fits.gz")) \
             else _load_flat(args.input)
    t = parsed["t"]
    fe = parsed["flux_err"]
    if fe is None or not np.any(np.isfinite(fe)):
        fe = np.full_like(parsed["flux"], float(np.nanstd(parsed["flux"]) or 1e-3))

    t_start = args.t_start if args.t_start is not None else float(np.min(t))
    t_end = args.t_end if args.t_end is not None else float(np.max(t))
    t0 = args.t0_guess if args.t0_guess is not None else 0.5 * (t_start + t_end)

    result = analyze_event(t, parsed["flux"], fe,
                           t_start=t_start, t_end=t_end, t0_guess=t0)
    print(f"\nVerdict: {result['verdict'].upper()}   "
          f"confidence={result.get('confidence', 0):.3f}")
    print(f"BIC: PSPL={result['models']['pspl']['bic']:.2f}   "
          f"Flare={result['models']['flare']['bic']:.2f}   "
          f"Null={result['models']['null']['bic']:.2f}")
    dbic = result.get("delta_bic", {})
    print(f"ΔBIC(null-PSPL)={dbic.get('null_minus_pspl'):.2f}   "
          f"ΔBIC(flare-PSPL)={dbic.get('flare_minus_pspl'):.2f}")
    print(f"Symmetry score={result.get('symmetry_score'):.3f}")

    os.makedirs(args.out, exist_ok=True)
    fig = build_microlens_fit(result)
    png_path = os.path.join(args.out, "microlens_fit.png")
    fig.savefig(png_path, dpi=140, bbox_inches="tight")

    if not args.no_pdf:
        from base64 import b64encode
        with open(png_path, "rb") as fh:
            plot_b64 = b64encode(fh.read()).decode("ascii")
        meta = {"event_id": args.label} if args.label else {}
        pdf = build_microlensing_pdf(result, metadata=meta, plot_png_b64=plot_b64)
        pdf_path = os.path.join(args.out, "report.pdf")
        with open(pdf_path, "wb") as fh:
            fh.write(pdf)
        print(f"Wrote PDF: {pdf_path}")

    if not args.no_json:
        _write_json(os.path.join(args.out, "result.json"), result)

    print(f"Outputs in: {args.out}")
    return 0


def _load_flat(path: str) -> dict:
    """CSV or JSON light curve loader for the microlensing sub-command."""
    import csv
    if path.lower().endswith(".json"):
        with open(path, "rb") as fh:
            data = json.loads(fh.read().decode("utf-8", errors="replace"))
        return {
            "t": np.asarray(data["t"], dtype=float),
            "flux": np.asarray(data["flux"], dtype=float),
            "flux_err": np.asarray(data.get("flux_err", [np.nan] * len(data["t"])), dtype=float),
        }
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        lower = {n.lower(): n for n in (reader.fieldnames or [])}
        if "time" not in lower or "flux" not in lower:
            raise ValueError(f"{path}: need columns time, flux [, flux_err]")
        tk, fk = lower["time"], lower["flux"]
        ek = lower.get("flux_err")
        ts, fs, es = [], [], []
        for row in reader:
            try:
                ts.append(float(row[tk]))
                fs.append(float(row[fk]))
                es.append(float(row[ek]) if ek and row[ek] not in ("", None) else np.nan)
            except (TypeError, ValueError):
                continue
    return {"t": np.array(ts), "flux": np.array(fs), "flux_err": np.array(es)}


def _cmd_coverage(args):
    from .microlensing_coverage import evaluate_catalog, parse_events_csv
    with open(args.input, "rb") as fh:
        raw = fh.read()
    events = parse_events_csv(raw.decode("utf-8", errors="replace"))
    out = evaluate_catalog(events, margin_te=args.margin_te)
    n_total = len(out.get("events") or [])
    n_obs = sum(1 for e in out.get("events") or [] if e.get("observable"))
    print(f"{n_total} events;  {n_obs} observable  (margin_te={args.margin_te})")
    if out.get("bulge_blind_zone_flag"):
        print("Note: some events sit in TESS's bulge blind zone (ecliptic latitude ≈ −5.5°).")
    os.makedirs(args.out, exist_ok=True)
    _write_json(os.path.join(args.out, "coverage.json"), out)
    print(f"Wrote: {os.path.join(args.out, 'coverage.json')}")
    return 0


def _cmd_habitability(args):
    """Compute the HCI + POE + TLCM bundle for a TIC.

    Runs the same pipeline as the transit sub-command's HCI extras, but
    standalone (no light curve required)."""
    from .exofop import query_exofop
    from .habitability import PlanetCandidate, compute_hci
    from .observables import compute_observables as compute_poe
    from .tlcm_geometry import compute_tlcm_geometry

    ef = query_exofop(args.tic)
    star = ef.get("star", {}) or {}
    tois = ef.get("tois", []) or []
    best = next((t for t in tois
                 if (t.get("disposition") or "").upper() in ("CP", "KP", "PC", "APC")),
                tois[0] if tois else None)
    period = args.period or (best or {}).get("period_d")
    planet = PlanetCandidate(
        radius_earth=args.radius_earth or (best or {}).get("radius_earth"),
        orbital_period_d=period,
        semi_major_axis_au=(best or {}).get("semi_major_axis_au"),
        stellar_teff=args.teff or star.get("teff"),
        stellar_radius_sun=args.rstar or star.get("radius"),
        stellar_mass_sun=args.mstar or star.get("mass"),
        toi_number=(best or {}).get("toi_number"),
        disposition=(best or {}).get("disposition"),
    )
    tlcm = compute_tlcm_geometry(
        period_d=period, t14_d=None, depth_frac=(best or {}).get("depth_frac"),
        rstar_sun=planet.stellar_radius_sun, mstar_sun=planet.stellar_mass_sun,
    ).to_dict()
    hci = compute_hci(planet=planet, vetting_verdict=None,
                      n_sectors_with_detections=1, n_sectors_observed=1,
                      a_over_rs=tlcm.get("a_over_rs"),
                      radius_ratio_k=tlcm.get("radius_ratio_k"),
                      stellar_density_rho_sun=tlcm.get("stellar_density_rho_sun"))
    poe = compute_poe(
        teff_k=planet.stellar_teff, rstar_sun=planet.stellar_radius_sun,
        mstar_sun=planet.stellar_mass_sun, distance_pc=star.get("distance"),
        orbital_period_d=period, semi_major_axis_au=planet.semi_major_axis_au,
        rp_earth=planet.radius_earth,
    ).to_dict()

    d = hci.to_dict()
    print(f"HCI: {d.get('score'):.1f} / 100  tier={d.get('tier')}")
    for s in d.get("subscores", []):
        print(f"  {s.get('name'):<22} {s.get('score'):>5.1f}  (weight {s.get('weight')})")
    os.makedirs(args.out, exist_ok=True)
    _write_json(os.path.join(args.out, "habitability.json"),
                {"hci": d, "observables": poe, "tlcm": tlcm,
                 "planet": {"radius_earth": planet.radius_earth,
                            "orbital_period_d": period}})
    return 0


def _cmd_exominer(args):
    from .exominer import run_exominer
    parsed = _load_lc(args.input)
    from .pipeline import clean_lightcurve, run_full_vetting
    result = run_full_vetting(
        t=parsed["t"], flux=parsed["flux"], flux_err=parsed["flux_err"],
        quality=parsed["quality"], mom_x=parsed["mom_x"], mom_y=parsed["mom_y"],
        star=parsed["star"],
    )
    t_c, f_c, _ = clean_lightcurve(parsed["t"], parsed["flux"], parsed["flux_err"],
                                    parsed["quality"])
    period = args.period or (result.bls or {}).get("period")
    t0 = args.t0 or (result.bls or {}).get("t0", 0.0)
    dur = args.duration or (result.bls or {}).get("duration")
    if not period or not dur:
        print("Error: no BLS period/duration; pass --period and --duration.",
              file=sys.stderr)
        return 2
    r = run_exominer(
        t=t_c, f=f_c,
        mom_x=parsed.get("mom_x"), mom_y=parsed.get("mom_y"),
        period=period, t0=t0, duration=dur,
        crowdsap=getattr(parsed["star"], "crowdsap", None),
    )
    os.makedirs(args.out, exist_ok=True)
    _write_json(os.path.join(args.out, "exominer.json"), r)
    print(f"Wrote {os.path.join(args.out, 'exominer.json')}")
    return 0


def _cmd_ffi(args):
    from .ffi_cutout import make_ffi_cutout
    from base64 import b64decode
    cutout = make_ffi_cutout(
        ra=args.ra, dec=args.dec, sector=args.sector,
        tic_id=args.tic, size_px=args.size_px,
    )
    if not cutout:
        print("FFI cutout unavailable.", file=sys.stderr)
        return 4
    os.makedirs(args.out, exist_ok=True)
    if cutout.get("png_base64"):
        path = os.path.join(args.out, "ffi_cutout.png")
        with open(path, "wb") as fh:
            fh.write(b64decode(cutout["png_base64"]))
        print(f"Wrote {path}")
    _write_json(os.path.join(args.out, "ffi_cutout.json"),
                {k: v for k, v in cutout.items() if k != "png_base64"})
    return 0


def _cmd_alerts(args):
    from .gaia_photometry import search_alerts_near
    hits = search_alerts_near(
        ra=args.ra, dec=args.dec, radius_arcsec=args.radius,
        microlensing_only=args.microlensing_only,
    )
    print(f"{len(hits)} alert(s) within {args.radius}\" of ({args.ra}, {args.dec})")
    for h in hits[:50]:
        d = h.to_dict() if hasattr(h, "to_dict") else dict(h)
        print(f"  {d.get('alert_id', '?'):<22} "
              f"sep={d.get('separation_arcsec', 0):.1f}\"  "
              f"class={d.get('classification')}  date={d.get('date')}")
    return 0


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vetstar-pi",
        description="VetStar Pi — Raspberry Pi OS port of the Vetstar TESS studio.",
    )
    p.add_argument("--version", action="version", version=f"vetstar-pi {__version__}")
    sub = p.add_subparsers(dest="cmd", required=False)

    g = sub.add_parser("gui", help="Launch the Tkinter GUI (default)")
    g.set_defaults(func=_cmd_gui)

    t = sub.add_parser("transit", help="Run the transit vetting pipeline")
    t.add_argument("input", help="Light curve FITS/JSON file")
    t.add_argument("--out", "-o", default="./out")
    t.add_argument("--detect-threshold", type=float, default=0.997)
    t.add_argument("--detect-min-snr", type=float, default=4.0)
    t.add_argument("--secondary-sigma", type=float, default=3.0)
    t.add_argument("--odd-even-sigma", type=float, default=3.0)
    t.add_argument("--high-variability", action="store_true",
                   help="Enable pre-BLS sinusoidal detrend for spotted rotators")
    t.add_argument("--rotation-period-days", type=float, default=None)
    t.add_argument("--known-period-days", type=float, default=None,
                   help="Constrain BLS to ±2%% around this period (and P/2, 2P)")
    t.add_argument("--no-plot-detrend", action="store_true",
                   help="Disable the 1-day rolling-median flatten on LC plots")
    t.add_argument("--plot-bin-minutes", type=int, default=30)
    t.add_argument("--no-pdf", action="store_true")
    t.add_argument("--no-json", action="store_true")
    t.add_argument("--no-extras", action="store_true",
                   help="Skip HCI/POE/TLCM/ExoMiner/DVT/FFI cutout recompute")
    t.add_argument("--no-hci", action="store_true")
    t.add_argument("--no-exominer", action="store_true")
    t.add_argument("--no-ffi", action="store_true")
    t.set_defaults(func=_cmd_transit)

    m = sub.add_parser("mast", help="Download a SPOC light-curve FITS by (TIC, sector)")
    m.add_argument("tic", type=int)
    m.add_argument("sector", type=int)
    m.add_argument("--out", "-o", default=None,
                   help="Output filename (default: TIC{tic}_S{sector}.fits)")
    m.set_defaults(func=_cmd_mast)

    s = sub.add_parser("sectors", help="List available TESS sectors for a TIC")
    s.add_argument("tic", type=int)
    s.set_defaults(func=_cmd_sectors)

    ml = sub.add_parser("microlens", help="Run the 3-way BIC microlensing classifier")
    ml.add_argument("input")
    ml.add_argument("--out", "-o", default="./out")
    ml.add_argument("--t-start", type=float, default=None)
    ml.add_argument("--t-end", type=float, default=None)
    ml.add_argument("--t0-guess", type=float, default=None)
    ml.add_argument("--label", default=None, help="Event label for the PDF")
    ml.add_argument("--no-pdf", action="store_true")
    ml.add_argument("--no-json", action="store_true")
    ml.set_defaults(func=_cmd_microlens)

    c = sub.add_parser("coverage", help="TESS sector coverage for a CSV of events")
    c.add_argument("input", help="CSV with columns event_id, ra, dec, t0, tE")
    c.add_argument("--out", "-o", default="./out")
    c.add_argument("--margin-te", type=float, default=0.0)
    c.set_defaults(func=_cmd_coverage)

    h = sub.add_parser("habitability", help="HCI + POE + TLCM for a TIC")
    h.add_argument("tic", type=int)
    h.add_argument("--out", "-o", default="./out")
    h.add_argument("--period", type=float, default=None)
    h.add_argument("--radius-earth", type=float, default=None)
    h.add_argument("--teff", type=float, default=None)
    h.add_argument("--rstar", type=float, default=None)
    h.add_argument("--mstar", type=float, default=None)
    h.set_defaults(func=_cmd_habitability)

    e = sub.add_parser("exominer", help="ExoMiner feature/view extraction")
    e.add_argument("input")
    e.add_argument("--out", "-o", default="./out")
    e.add_argument("--period", type=float, default=None)
    e.add_argument("--t0", type=float, default=None)
    e.add_argument("--duration", type=float, default=None)
    e.set_defaults(func=_cmd_exominer)

    f = sub.add_parser("ffi", help="TESScut FFI cutout for (ra, dec, sector)")
    f.add_argument("--ra", type=float, required=True)
    f.add_argument("--dec", type=float, required=True)
    f.add_argument("--sector", type=int, default=None)
    f.add_argument("--tic", type=int, default=None)
    f.add_argument("--size-px", type=int, default=15)
    f.add_argument("--out", "-o", default="./out")
    f.set_defaults(func=_cmd_ffi)

    a = sub.add_parser("alerts", help="Cone-search Gaia Alerts near (ra, dec)")
    a.add_argument("--ra", type=float, required=True)
    a.add_argument("--dec", type=float, required=True)
    a.add_argument("--radius", type=float, default=60.0, help="arcsec")
    a.add_argument("--microlensing-only", action="store_true")
    a.set_defaults(func=_cmd_alerts)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        return _cmd_gui(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
