"""Command-line entrypoint for VetStar Pi.

Sub-commands
------------
gui         Launch the Tkinter GUI (default when Tk is available).
transit     Run the transit vetting pipeline on a FITS/JSON light curve.
microlens   Run the microlensing 3-way model comparison on a windowed
            CSV/JSON light curve.
version     Print the version.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

import numpy as np

from . import __version__
from .fitsio import read_any, read_csv, read_json
from .microlens import analyze_event as microlens_analyze
from .pdf_report import build_microlens_pdf, build_transit_pdf
from .plots import build_microlens_fit, build_transit_overview, build_transit_zoom, save_png
from .transit import clean_lightcurve, run_vetting


def _cmd_gui(args):
    from .gui import run_gui
    run_gui()
    return 0


def _cmd_transit(args):
    lc = read_any(args.input)
    print(f"Loaded {args.input}: {len(lc.t)} cadences, TIC={lc.star.tic_id}")

    result = run_vetting(
        lc,
        detect_threshold=args.detect_threshold,
        detect_min_snr=args.detect_min_snr,
        secondary_sigma=args.secondary_sigma,
        odd_even_sigma=args.odd_even_sigma,
    )
    t, f, _, _, _ = clean_lightcurve(lc)

    v = result.verdict
    print(f"\nVerdict: {v.get('headline')}")
    print(f"Category: {v.get('category')}   confidence={v.get('confidence'):.2f}")
    print(f"BLS period={result.bls.get('period', 0):.6f} d   "
          f"depth={result.bls.get('depth', 0):.5f}   SDE={result.bls.get('sde', 0):.2f}")
    print(f"Events detected: {len(result.events)}")
    for r in v.get("reasons", []):
        print(f"  • {r}")

    os.makedirs(args.out, exist_ok=True)
    save_png(build_transit_overview(result, t, f), os.path.join(args.out, "lightcurve_overview.png"))
    zoom = build_transit_zoom(result, t, f)
    if zoom is not None:
        save_png(zoom, os.path.join(args.out, "events_zoom.png"))
    if not args.no_pdf:
        pdf_path = os.path.join(args.out, "report.pdf")
        build_transit_pdf(result, t, f, pdf_path)
        print(f"Wrote PDF report: {pdf_path}")
    if not args.no_json:
        d = result.to_dict()
        for key in ("bls", "lomb_scargle"):
            if isinstance(d.get(key), dict):
                d[key].pop("periodogram", None)
        with open(os.path.join(args.out, "result.json"), "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=2, default=str)
    print(f"Outputs in: {args.out}")
    return 0


def _cmd_microlens(args):
    path = args.input
    if path.lower().endswith(".csv"):
        lc = read_csv(path)
    else:
        lc = read_json(path)
    print(f"Loaded {path}: {len(lc.t)} points, "
          f"time range [{lc.t.min():.3f}, {lc.t.max():.3f}]")

    fe = lc.flux_err
    if fe is None or not np.any(np.isfinite(fe)):
        fe = np.full_like(lc.flux, float(np.nanstd(lc.flux) or 1e-3))

    t_start = args.t_start if args.t_start is not None else float(lc.t.min())
    t_end = args.t_end if args.t_end is not None else float(lc.t.max())
    t0 = args.t0_guess if args.t0_guess is not None else 0.5 * (t_start + t_end)

    result = microlens_analyze(
        lc.t, lc.flux, fe,
        t_start=t_start, t_end=t_end, t0_guess=t0,
    )
    print(f"\nVerdict: {result.verdict.upper()}   confidence={result.confidence:.3f}")
    print(f"BIC: PSPL={result.pspl.bic:.2f}  "
          f"Flare={result.flare.bic:.2f}  Null={result.null.bic:.2f}")
    print(f"ΔBIC(null-PSPL)={result.delta_bic['null_minus_pspl']:.2f}  "
          f"ΔBIC(flare-PSPL)={result.delta_bic['flare_minus_pspl']:.2f}")
    print(f"Symmetry={result.symmetry_score:.3f}")
    if result.pspl.success:
        p = result.pspl.params
        print(f"PSPL: t0={p['t0']:.5f}  tE={p['tE']:.3f}  u0={p['u0']:.4f}  "
              f"f_s={p['f_s']:.3f}  f_b={p['f_b']:.3f}")

    os.makedirs(args.out, exist_ok=True)
    save_png(build_microlens_fit(result), os.path.join(args.out, "microlens_fit.png"))
    if not args.no_pdf:
        pdf_path = os.path.join(args.out, "report.pdf")
        build_microlens_pdf(result, pdf_path, target_label=args.label)
        print(f"Wrote PDF report: {pdf_path}")
    if not args.no_json:
        summary = {
            "verdict": result.verdict,
            "confidence": result.confidence,
            "window": result.window,
            "delta_bic": result.delta_bic,
            "symmetry_score": result.symmetry_score,
            "pspl": {"params": result.pspl.params, "param_err": result.pspl.param_err,
                     "bic": result.pspl.bic, "chi2": result.pspl.chi2},
            "flare": {"params": result.flare.params, "param_err": result.flare.param_err,
                      "bic": result.flare.bic, "chi2": result.flare.chi2},
            "null": {"params": result.null.params, "param_err": result.null.param_err,
                     "bic": result.null.bic, "chi2": result.null.chi2},
            "observables": result.observables,
            "planet_predictions": result.planet_predictions,
            "notes": result.notes,
        }
        with open(os.path.join(args.out, "result.json"), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, default=str)
    print(f"Outputs in: {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vetstar-pi",
        description="VetStar Pi — lightweight TESS vetting for Raspberry Pi OS.",
    )
    p.add_argument("--version", action="version", version=f"vetstar-pi {__version__}")
    sub = p.add_subparsers(dest="cmd", required=False)

    g = sub.add_parser("gui", help="Launch the Tkinter GUI (default)")
    g.set_defaults(func=_cmd_gui)

    t = sub.add_parser("transit", help="Run the transit vetting pipeline")
    t.add_argument("input", help="Light curve FITS/JSON file")
    t.add_argument("--out", "-o", default="./out", help="Output directory (default: ./out)")
    t.add_argument("--detect-threshold", type=float, default=0.997)
    t.add_argument("--detect-min-snr", type=float, default=4.0)
    t.add_argument("--secondary-sigma", type=float, default=3.0)
    t.add_argument("--odd-even-sigma", type=float, default=3.0)
    t.add_argument("--no-pdf", action="store_true", help="Skip PDF report")
    t.add_argument("--no-json", action="store_true", help="Skip JSON dump")
    t.set_defaults(func=_cmd_transit)

    m = sub.add_parser("microlens", help="Run the microlensing 3-way fit")
    m.add_argument("input", help="Light curve CSV/JSON file")
    m.add_argument("--out", "-o", default="./out")
    m.add_argument("--t-start", type=float, default=None)
    m.add_argument("--t-end", type=float, default=None)
    m.add_argument("--t0-guess", type=float, default=None)
    m.add_argument("--label", default=None, help="Event label shown on the PDF")
    m.add_argument("--no-pdf", action="store_true")
    m.add_argument("--no-json", action="store_true")
    m.set_defaults(func=_cmd_microlens)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        return _cmd_gui(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
