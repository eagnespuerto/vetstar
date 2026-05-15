# Vetstar: TESS Vetting Studio Alpha

A web app for full transit / eclipse vetting of TESS or Kepler light curves.
Upload a SPOC FITS file (or pull one from MAST by TIC + sector) and receive
a complete vetting report with PDF export — no install required.

## Use it now

**Live at <https://vetstar.onrender.com>**

Open the link in any modern browser. The first visit after the service has
been idle may take ~30 seconds to wake up (free-tier cold start); after
that, it runs normally.

## What it does

- **Period searches** — Box Least Squares (BLS) + Lomb-Scargle
- **Discrete dip event detection** with adjustable sensitivity (see below)
- **Centroid offset test** — distinguishes on-target events from background blends
- **Odd / even transit depth comparison** — eclipsing-binary indicator
- **Secondary eclipse search** at phase 0.5
- **Transit shape analysis** (U vs V, ingress / egress / flat-bottom)
- **Physics-based companion sizing** with CROWDSAP dilution correction
- **Automated verdict**: planet candidate / EB candidate / blend / ambiguous
- **PDF report** for archiving (centered layout, multi-page)

## How to use the app

### Step 1 — choose how to load a light curve

The page has two tabs near the top:

- **Upload file** — drag and drop, or pick a file. Accepts `.fits`,
  `.fits.gz`, `.json`, or `.customization`.
- **Fetch from MAST** — type a TIC ID and sector. Click "List sectors" first
  to see which TESS sectors have data for that TIC; click a sector chip to
  fill it in.

### Step 2 — optional: adjust detection sensitivity

Below the tabs there's a collapsed panel labeled **⚙️ Detection
sensitivity**. Click it to expand. Two sliders:

- **Depth threshold** (default `0.997`, range `0.95`–`0.999`) — controls
  how shallow a dip must be to count as an event. The label updates in real
  time to show the equivalent percent depth ("flag dips deeper than 0.30%").
  Lower it to catch shallower transits; raise it to be stricter.
- **Minimum SNR** (default `4σ`, range `1σ`–`10σ`) — dip depth must exceed
  this multiple of the local photometric scatter. Lower SNR for maximum
  sensitivity at the cost of some noise events; raise SNR if you're seeing
  false-positive wiggles.

The defaults work well for typical 2-min cadence TESS data. Each slider has
a small **reset** link to snap back to default. The panel header shows
either `(defaults)` or the current values so you can tell at a glance.

**Rule of thumb:**

- Missing a real shallow transit you can see by eye? → lower SNR (try 2–3σ)
- Lots of fake "events" flagged on a noisy star? → raise SNR (try 5–6σ)
- Need to flag a dip ≲0.2% deep? → push threshold up to 0.998 *and* lower
  SNR — the two filters work together

### Step 3 — run vetting

Click **Run vetting** (Upload tab) or **Fetch & vet** (MAST tab). Analysis
takes 10–30 seconds for a typical 2-min cadence sector. You'll see:

- A **verdict banner** with category (planet candidate / EB candidate /
  blend / ambiguous) and confidence
- **Stellar parameters** and a **light curve summary**
- **Diagnostic plots**: full detrended light curve with all detected events
  shaded (primary in red, others in orange), zoom panels for each event
  with depth/duration/SNR, centroid behavior, BLS and Lomb-Scargle
  periodograms
- **Test tables**: BLS, Lomb-Scargle, centroid, odd/even, secondary
  eclipse, shape, physical interpretation
- **Event table** listing every dip with its timing and depth

### Step 4 — optional: download PDF

Click **Download PDF report** (or **Fetch & download PDF** in MAST mode).
You get a centered multi-page PDF with verdict, all the tables, and the
diagnostic plots. Useful for archiving and sharing.

## Verdict logic

1. Implied companion radius > 2.5 R_Jup → **eclipsing binary candidate**.
2. Secondary eclipse detected or odd/even depths differ > 3σ → **EB**.
3. Centroid offset > 3σ → **likely blend** (background eclipsing binary).
4. Otherwise, if a planet-sized companion is implied → **planet candidate**.
5. Else → **ambiguous** or **no signal** based on BLS SDE.

Any candidate should still go through manual review and ground-based
follow-up.

## MAST fallback behavior

The MAST fetch tab tries data providers in this order until one returns a
light curve:

1. **SPOC 2-min** (best — includes quality flags, centroid columns, CROWDSAP)
2. **SPOC 20-s**
3. **TESS-SPOC FFI** (10-min from full-frame images; near-complete coverage)
4. **QLP** (Quick Look Pipeline FFI light curves)

When the app has to fall back past SPOC 2-min, the results page shows an
amber banner telling you which provider/cadence was used. FFI products
don't include centroid columns, so the centroid test will report
`available=false` for those — that's expected, not a bug.

## Report bugs or contribute

Click **Report an Issue or Contribute** in the page header, or go directly
to <https://github.com/eagnespuerto/vetstar>. If reporting a bug, please
include:

- The TIC and sector you were analyzing (or attach the FITS file if it's
  one you uploaded)
- The exact error message
- Whether you'd adjusted the sensitivity sliders

## Limitations and disclaimers

- This is an **alpha** release. The pipeline is meaningfully useful but is
  not a substitute for full vetting tools like DAVE, VESPA, or the
  TESS-SPOC Data Validation Report.
- Always cross-check candidates with **ExoFOP**, **Gaia DR3** (especially
  RUWE for binarity), and **high-resolution imaging** before drawing strong
  conclusions or publishing.
- Free-tier hosting (Render) sleeps after ~15 min idle and takes ~30 s to
  cold-start the next visit. Analysis itself is fast once the service is
  warm.

---

## Developer info

The rest of this document is for people who want to run, modify, or
self-host Vetstar.

### Run locally (one command)

```bash
python app.py
```

The launcher installs backend Python deps if missing, builds the React
frontend if `frontend/dist/` is missing, and starts a single Uvicorn
process on `http://127.0.0.1:8000` serving both the API and the SPA on the
same port.

Requirements: Python ≥ 3.10 and Node.js ≥ 18 (only needed the first time,
to build the frontend; the bundle is cached afterward).

Flags:

```bash
python app.py --port 9000          # custom port
python app.py --host 0.0.0.0       # listen on all interfaces
python app.py --reload             # auto-reload on backend changes
python app.py --skip-build         # don't rebuild the frontend
python app.py --api-only           # don't try to serve the SPA
```

### API endpoints

```
POST /api/analyze              multipart file=@lc.fits          → JSON
POST /api/report               multipart file=@lc.fits          → PDF
GET  /api/mast/sectors/{tic}                                    → list
POST /api/mast/analyze         {"tic_id": ..., "sector": ...}   → JSON
POST /api/mast/report          {"tic_id": ..., "sector": ...}   → PDF
GET  /api/health                                                → {"status":"ok"}
GET  /docs                                                      → Swagger UI
```

All `analyze` and `report` endpoints accept optional query / body params
`detect_threshold` (0.95–0.999) and `detect_min_snr` (1.0–20.0). For
MAST endpoints, put them in the JSON body. For upload endpoints, use
URL query params.

The JSON response matches the `VettingResult` shape in
`backend/app/pipeline.py` and includes base64-encoded PNG diagnostic plots.

### Project layout

```
vetstar/
├── app.py                  ← single-command launcher
├── backend/
│   ├── app/
│   │   ├── main.py         FastAPI endpoints + SPA mount
│   │   ├── pipeline.py     BLS, LS, centroid, odd/even, secondary, physics
│   │   ├── parsers.py      FITS + ExoFOP JSON readers
│   │   ├── mast_fetch.py   astroquery-based MAST downloader
│   │   └── report.py       Centered multi-page PDF builder
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.tsx         Upload + MAST tabs, results dashboard
│   │   ├── api.ts          API client
│   │   └── types.ts        Shared types
│   ├── index.html
│   └── package.json
├── Dockerfile              Multi-stage build (frontend + backend)
├── render.yaml             Render Blueprint
├── fly.toml                Fly.io app config
├── docker-compose.yml      Local Docker dev
└── README.md               This file
```

### Deploy your own copy

The current live deployment runs on Render. To deploy your own fork:

**Render** — push to GitHub, sign up at <https://render.com>, **New** →
**Blueprint** → connect your repo. Render reads `render.yaml` and
provisions automatically. Free tier sleeps after 15 min idle.

**Fly.io** — `fly auth login` then `fly launch --no-deploy --copy-config`
then `fly deploy`. The included `fly.toml` provisions a single
shared-cpu-1x machine with 1 GB RAM (within the free allowance). No idle
sleep on Fly's free tier, but requires a credit card on file.

Either way, every push to `main` triggers an automatic redeploy (Render
via `autoDeploy: true` in `render.yaml`; Fly via the GitHub Actions
workflow at `.github/workflows/ci-deploy.yml` if you set the
`FLY_API_TOKEN` repo secret).

### Build a standalone executable

```bash
python build_exe.py
```

PyInstaller bundles Python + all deps + the web UI into one binary
(`dist/tess-vetting-studio.exe` on Windows, `dist/tess-vetting-studio` on
macOS/Linux, ~180–230 MB). The binary auto-opens a browser to
`http://127.0.0.1:8000` on launch. PyInstaller does not cross-compile —
to build a Windows `.exe` you must run on Windows.

### Optional: Docker

```bash
docker compose up --build
# → http://localhost:8080
```

### License

CC0-1.0. See `LICENSE`.


