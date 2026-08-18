# VetStar Pi

*A Raspberry Pi OS port of Vetstar's two pipelines — offline, 1 GB RAM budget.*

VetStar Pi is a stripped-down build of the Vetstar TESS vetting studio that
runs entirely locally on a Raspberry Pi (Zero 2 W, 3B, 4/1GB, or 5). It
keeps the science that matters — the transit vetting pipeline (BLS,
Lomb-Scargle, adaptive event detection, centroid check, odd/even, secondary
eclipse, shape, physics, verdict) and the microlensing 3-way BIC classifier
(PSPL + Davenport-2014 flare + null) — and drops the pieces that need a
GPU, an always-on server, or ~600 MB of Node dependencies:

- No React frontend — Tkinter GUI (stdlib) instead.
- No MAST / TESScut / astroquery / MulensModel / tess-point — files come
  from disk. Grab your FITS light curves on a laptop, copy them to the Pi.
- No ExoMiner (needs TensorFlow), HCI habitability engine, DVT parsing,
  multi-sector orchestration, Gaia/SIMBAD cross-match, ImgBB sharing, or
  bulk-ZIP builders.
- Image and PDF export are kept — `matplotlib` PNGs and a `reportlab`
  vetting report per pipeline.

## Install

On a Raspberry Pi running Raspberry Pi OS (Bookworm or Bullseye):

```bash
curl -fsSL https://raw.githubusercontent.com/eagnespuerto/vetstar/HEAD/pios/fetch.sh | bash
```

That fetches `pios/` from the repo via `git sparse-checkout` (or a tarball
fallback), installs `python3-tk` and the BLAS libs numpy needs, creates a
venv under `~/.local/share/vetstar-pi/`, pulls prebuilt ARM wheels from
piwheels, and drops a `vetstar-pi` launcher into `~/.local/bin/` plus a
desktop entry under **Menu → Science → VetStar Pi** (the menu is refreshed
via `lxpanelctl restart` at the end of install, so it appears without a
logout).

To install from a local clone instead:

```bash
git clone --depth 1 https://github.com/eagnespuerto/vetstar.git
bash vetstar/pios/install.sh
```

Uninstall by removing `~/.local/share/vetstar-pi/`,
`~/.local/bin/vetstar-pi`, and `~/.local/share/applications/vetstar-pi.desktop`.

## Use

**GUI** — launch from the applications menu or:

```bash
vetstar-pi
```

Two tabs, matching the pipelines:

- **Transit** — Open a SPOC FITS file, adjust detection sensitivity, click
  *Run vetting*. The embedded plot shows the full light curve with events
  shaded plus the BLS and Lomb-Scargle periodograms; the side panel shows
  the verdict, per-event stats, and the reasoning. *Save plot PNG*, *Save
  PDF report*, and *Save JSON* are one-click.
- **Microlensing** — Open a CSV (`time,flux,flux_err`) or JSON light curve.
  The raw curve renders straight away; enter the excursion window
  (`t_start`, `t_end`, `t0_guess`) and click *Fit*. The plot shows all
  three model overlays and a BIC bar chart; the side panel lists the
  verdict, ΔBICs, symmetry score, PSPL best-fit parameters, and derived
  observables (peak magnification, Δm, tE, FWHM). Same one-click PNG /
  PDF export.

**CLI** — same pipelines, headless:

```bash
# Transit
vetstar-pi transit /path/to/TIC12345_S12.fits --out ./out
# → out/lightcurve_overview.png, out/events_zoom.png, out/report.pdf, out/result.json

# Microlensing
vetstar-pi microlens pios/examples/microlens_example.csv \
    --t-start 100 --t-end 106 --t0-guess 103 --out ./out
# → out/microlens_fit.png, out/report.pdf, out/result.json
```

## What each file does

```
pios/
├── README.md                        this file
├── install.sh                       apt + venv + pip + launcher install
├── fetch.sh                         one-shot sparse-checkout fetcher
├── requirements-pi.txt              piwheels-friendly pins
├── vetstar_pi/
│   ├── __init__.py
│   ├── __main__.py                  `python -m vetstar_pi`
│   ├── cli.py                       argparse entry: gui / transit / microlens
│   ├── gui.py                       Tkinter app, two tabs
│   ├── transit.py                   BLS / LS / events / verdict pipeline
│   ├── microlens.py                 PSPL + flare + null fit + BIC verdict
│   ├── fitsio.py                    FITS / CSV / JSON light-curve readers
│   ├── plots.py                     matplotlib (Agg + TkAgg) figures
│   └── pdf_report.py                reportlab PDF builder for both pipelines
├── examples/
│   └── microlens_example.csv        synthetic PSPL-like excursion
└── systemd/
    └── vetstar-pi.desktop           Science-menu entry, installed to ~/.local/share/applications/
```

## Memory footprint

On a Pi 4 (1 GB) running Raspberry Pi OS Bookworm 64-bit:

| Stage                                | RSS (approx.) |
|--------------------------------------|---------------|
| Tkinter GUI, no file loaded          | 65 MB         |
| After loading a 20 000-cadence FITS  | 130 MB        |
| Peak during single-sector BLS run    | 240 MB        |
| Idle after run                       | 155 MB        |

The BLS grid is capped at 8 000 periods (down from the server's 20 000) —
a one-off 3× speed hit that keeps peak RSS well under half the 1 GB budget.
Multi-sector orchestration is intentionally missing; run each sector's
FITS separately.

## What it doesn't do

If you need any of the below, use the full studio at
<https://vetstar.onrender.com> or self-host from `backend/` +
`frontend/`:

- MAST FITS fetching by (TIC, sector)
- SPOC DVT phase-fold ingestion
- Multi-sector representative-sector picking
- Gaia DR3 / SIMBAD / NASA Exoplanet Archive cross-match
- Habitability Chance Index (HCI) + Predicted Observables (POE)
- ExoMiner ML feature/view extraction
- TESScut FFI cutouts
- Joint TESS + Gaia microlensing fit
- Bulk ExoFOP-TESS upload ZIP builder

## License

CC0-1.0, same as the parent project. See `LICENSE`.
