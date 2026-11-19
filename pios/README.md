# VetStar Pi

*A Raspberry Pi OS port of the Vetstar TESS vetting studio — local Tkinter
GUI + CLI, still MAST-aware.*

VetStar Pi runs the same science package as the cloud studio,
[`vetstar_core`](../vetstar_core/), from a Tkinter GUI (matching the
pinight / FeedMeSomePi pattern) plus a `vetstar-pi` CLI. Since v1.1 it
ships as a release bundle, `vetstar-pi.tar.gz`, that holds this front
end and `vetstar_core` together, so site fixes reach the Pi with each
release. It runs on a Pi Zero 2 W, 3B, 4/1GB
or 5.

Two features from the cloud studio are intentionally missing:

- **Multi-sector orchestration** — the two-pass "fetch every sector, pick
  the representative one" loop was designed for a 1 GB cloud VM with
  sequential BLS runs; running it inside a Pi's memory budget is fine
  for one sector at a time, but the multi-sector representative-picker
  keeps too much per-sector state live. Analyse each sector separately
  instead.
- **Joint TESS + Gaia PSPL fit** — the Harris+2026 joint-band fit needs
  the Gaia Alerts fetch pipeline running online and is deliberately a
  research-tier workflow. The single-band TESS classifier stays; the
  Gaia Alerts fetcher stays (Classifier tab loads Gaia-band curves
  directly), just no joint fit.

Both exist in `vetstar_core`; this front end just doesn't expose them.

Everything else is here — MAST fetching, DVT ingestion, Gaia DR3 / SIMBAD /
NASA Exoplanet Archive cross-match, Habitability Chance Index, Predicted
Observables, TLCM transit geometry, ExoMiner features, TESScut FFI cutouts
(transit and microlensing), microlensing coverage via tess-point, high-
variability detrend, progress reporting, and full PDF reports.

## Install

On a Raspberry Pi running Raspberry Pi OS (Bookworm or Bullseye):

```bash
curl -fsSL https://raw.githubusercontent.com/eagnespuerto/vetstar/HEAD/pios/fetch.sh | bash
```

`fetch.sh` downloads `vetstar-pi.tar.gz` from the latest GitHub release
and runs `install.sh` from it. Options (environment variables):

| Variable | Effect |
|---|---|
| `VETSTAR_VERSION=v1.1.0` | install that release instead of the latest |
| `VETSTAR_REF=main` | build the bundle from a branch, tag or commit (unreleased code) |
| `VETSTAR_DIR=...` | where the bundle is unpacked (default `~/.local/share/vetstar-pi/src`) |
| `NO_INSTALL=1` | download only |

If a release has no bundle attached yet, `fetch.sh` builds one from the
default branch instead.

The installer:

- installs `python3-tk` and the BLAS / JPEG libs numpy / matplotlib link
  against
- creates a venv under `~/.local/share/vetstar-pi/`
- copies `vetstar_pi` and `vetstar_core` into it, first removing the
  copies an install from before v1.1 left behind
- pulls prebuilt ARM wheels from piwheels (numpy, scipy, astropy,
  matplotlib, reportlab, astroquery, tess-point)
- drops a `vetstar-pi` launcher into `~/.local/bin/` and a desktop entry
  under **Menu → Science → VetStar Pi** (the menu is refreshed via
  `lxpanelctl restart` at the end of install, so it appears without a
  logout)

Tested on Raspberry Pi OS Bookworm (Python 3.11) and Bullseye (Python 3.9),
Pi Zero 2 W / 3B / 4 / 5.

## Use

**GUI** — from the applications menu (**Menu → Science → VetStar Pi**) or:

```bash
vetstar-pi
```

Two top-level tabs:

- **Transit** — Open a FITS/JSON light curve *or* **Fetch from MAST…**
  (TIC + sector prompt). Adjust detection sensitivity (threshold,
  min SNR, secondary σ, odd/even σ, rotation period, known period,
  high-variability detrend), and toggle which extras to bundle into
  the PDF (HCI + POE + TLCM, ExoMiner, FFI cutout, DVT). Click *Run
  vetting*. The embedded matplotlib canvas shows the LC + event
  shading + zoom of the deepest event; the side panel shows the
  verdict, per-event stats, cross-match hit if any, HCI headline, and
  the full reasoning trace. One-click **Save PNG / PDF / JSON**.
- **Microlensing** → two sub-tabs:
  - *Classifier* — Open a CSV/JSON light curve or **Fetch Gaia Alert…**
    (pulls a Gaia-band curve by alert id like `Gaia23bra`). Enter
    `t_start`, `t_end`, `t0_guess`; click *Fit*. Shows all three
    model overlays + BIC bar chart and full verdict / BIC / symmetry /
    PSPL params / observables text. Export PNG / PDF.
  - *Coverage* — Load a CSV of `event_id, ra, dec, t0, tE`. Runs
    tess-point per row, checks whether `t0` (± margin·tE) falls in
    any covering sector, and shows a sortable table + observability
    bar chart. Save JSON.

**CLI** — every capability, headless:

```bash
vetstar-pi transit  TIC12345_S12.fits                            # LC + PDF + JSON (+ HCI/POE/ExoMiner/DVT/FFI)
vetstar-pi transit  TIC12345_S12.fits --high-variability         # rotator detrend
vetstar-pi transit  TIC12345_S12.fits --known-period-days 2.47   # constrained BLS
vetstar-pi mast     12345 12   --out /tmp/lc.fits                # download SPOC LC
vetstar-pi sectors  12345                                        # list available sectors
vetstar-pi microlens  events.csv --t-start 100 --t-end 106 --t0-guess 103
vetstar-pi coverage   catalogue.csv --margin-te 0.5
vetstar-pi habitability 12345 --period 3.7                       # HCI + POE + TLCM
vetstar-pi exominer   TIC12345_S12.fits                          # feature views JSON
vetstar-pi ffi        --ra 210.5 --dec -55.2 --sector 12         # TESScut cutout PNG
vetstar-pi alerts     --ra 268.7 --dec -29.1 --radius 300        # Gaia Alerts cone search
```

Every remote call (MAST, TESScut, ExoFOP, Gaia, SIMBAD, NEA) fails soft:
if the Pi is offline, the transit pipeline still runs, and the extras
that need the network are skipped from the PDF with a warning in the log.

## Package layout

```
pios/
├── README.md                        this file
├── install.sh                       apt + venv + pip + launcher install
├── fetch.sh                         downloads the release bundle (or builds it from a git ref)
├── build_bundle.py                  packs vetstar_pi + vetstar_core into vetstar-pi.tar.gz
├── requirements-pi.txt              piwheels-friendly pins
├── .gitattributes                   force LF on shell scripts + .desktop
├── vetstar_pi/                      front end only (v0.3.0)
│   ├── cli.py                       argparse entry (gui / transit / mast / sectors /
│   │                                microlens / coverage / habitability / exominer /
│   │                                ffi / alerts)
│   ├── gui.py                       Tkinter app; Transit + Microlensing tabs
│   ├── plots.py                     GUI-only matplotlib Figures (LC, fit, coverage)
│   ├── __init__.py                  version
│   └── __main__.py                  `python -m vetstar_pi`
├── examples/microlens_example.csv   synthetic PSPL-like excursion
└── systemd/vetstar-pi.desktop       Science-menu launcher
```

The science modules (pipeline, microlensing, habitability, observables,
TLCM, MAST/DVT/TESScut fetchers, catalog cross-match, PDF reports and the
rest) live in [`vetstar_core/`](../vetstar_core/) at the repo root; the
main [README](../README.md#project-layout) lists them. In the release
bundle `vetstar_core/` sits next to `vetstar_pi/`.

## Memory footprint

Measured on a Pi 4 (1 GB) running Raspberry Pi OS Bookworm 64-bit:

| Stage                                              | RSS (approx.) |
|----------------------------------------------------|---------------|
| Tkinter GUI idle, no file loaded                   | 75 MB         |
| After loading a 20 000-cadence FITS                | 140 MB        |
| Peak during single-sector BLS + cross-match run    | 320 MB        |
| Peak during full PDF (HCI + ExoMiner + FFI)        | 420 MB        |
| Idle after run                                     | 190 MB        |

Higher than the CLI-only build (~240 MB peak), but still well under
half the 1 GB budget. If you're on a 512 MB device (Pi Zero W, Pi 3A),
uncheck **ExoMiner** and **FFI cutout** in the PDF-extras panel and stay
below 300 MB.

## What it doesn't do

- **Multi-sector orchestration** — analyse one sector at a time.
- **Joint TESS + Gaia PSPL fit** — single-band classifier only.

Everything else in the cloud studio is here, including features that need
the network (MAST, TESScut, ExoFOP, Gaia, SIMBAD, NEA). See the full
studio at <https://vetstar.onrender.com> for the two features above.

## License

CC0-1.0, same as the parent project. See `LICENSE`.
