# Vetstar: TESS Vetting Studio Alpha

A web app for full transit / eclipse vetting of TESS or Kepler light curves,
with a STEHM-based habitability scoring engine and multi-sector analysis.
Upload a SPOC FITS file (or pull one from MAST by TIC + sector) and receive
a complete vetting report with PDF export — no install required.

**Live at <https://vetstar.onrender.com>**

The first visit after the service has been idle may take ~30 seconds to
wake up (free-tier cold start); after that it runs normally.

Source code and issue tracker: <https://github.com/eagnespuerto/vetstar>


## What it does

### Transit / eclipse vetting pipeline

- **Period searches** — Box Least Squares (BLS) + Lomb-Scargle periodograms
- **Adaptive dip event detection** with adjustable sensitivity (see below)
- **Centroid offset test** — distinguishes on-target events from background blends
- **Odd / even transit depth comparison** — eclipsing-binary indicator
- **Secondary eclipse search** at phase 0.5
- **Transit shape analysis** (U vs V, ingress / egress / flat-bottom durations)
- **Physics-based companion sizing** with CROWDSAP dilution correction
- **Automated verdict**: planet candidate / EB candidate / blend / ambiguous
- **Multi-event diagnostic plots**: full light curve with all events shaded
  and numbered, multi-panel zoom grid showing depth + duration + SNR per
  event, centroid behaviour, BLS and Lomb-Scargle periodograms
- **PDF report** for archiving (centered layout, multi-page)

### Habitability Chance Index (HCI)

A 0–100 score grounded in the **STEHM** (Smaller Than Earth Habitability
Model) framework from Hill et al. (2026), arXiv:2605.00170. The score is
built from six weighted sub-components:

| Component (weight) | Science basis |
|---|---|
| **Planet size** (30%) | STEHM's primary result: ≥0.8 R⊕ retains a long-term CO₂ atmosphere under Earth-like conditions; 0.7–0.8 R⊕ is marginal; <0.7 R⊕ loses it rapidly (Fig 5, §5). |
| **Habitable zone** (25%) | Kopparapu et al. (2013/2014) HZ boundaries, scaled by stellar luminosity. Outer-HZ planets retain atmospheres more easily (STEHM §5.5). |
| **Stellar type** (15%) | STEHM is calibrated for Sun-like (FGK) stars. M-dwarfs are penalised for higher XUV flux and non-thermal escape (§6). |
| **TOI disposition** (15%) | ExoFOP-TESS vetting flag. CP/KP (confirmed) = 1.0, PC/APC (candidate) = 0.75, FP (false positive) = 0.05. |
| **Vetting flags** (10%) | Our own pipeline's centroid / odd-even / secondary / companion-size results. |
| **Multi-sector** (5%) | Consistent detections across multiple TESS sectors confirm periodicity. |

Tiers: **Promising** (≥70) · **Marginal** (45–69) · **Unlikely** (20–44) ·
**Very unlikely** (<20). Confirmed EBs and false positives are hard-capped
at 12 regardless of other scores.

The HCI panel automatically queries **ExoFOP-TESS** for TOI data (planet
radius, period, semi-major axis, disposition) and stellar parameters. If
ExoFOP is unavailable it falls back to the TIC v8 catalog via astroquery.
All ExoFOP-derived values can be overridden from the request body.

### Multi-sector analysis

Fetches all available TESS sectors for a TIC from MAST (up to 10), runs
the vetting pipeline on each, and produces:

- A **detection timeline** bar chart (red = dip detected, grey = no dip)
  showing detection consistency across sectors
- A **per-sector verdict table** with event counts, BLS period, and SDE
- A **period consensus** estimate from sectors where BLS SDE > 6
- Automatic **HCI score update** with real multi-sector counts

### MAST integration

Two input modes in the web UI:

- **Upload file** — drag and drop a `.fits`, `.fits.gz`, `.json`, or
  `.customization` file
- **Fetch from MAST** — enter a TIC ID and sector. Click "List sectors"
  to see all TESS sectors with data for that TIC, with provider info
  (SPOC, TESS-SPOC, QLP) shown as clickable coloured chips.

The MAST fetcher tries data providers in preference order:

1. **SPOC 2-min** (best — includes quality flags, centroid columns, CROWDSAP)
2. **SPOC 20-s**
3. **TESS-SPOC FFI** (10-min from full-frame images; near-complete coverage)
4. **QLP** (Quick Look Pipeline FFI light curves)

When the app falls back past SPOC 2-min, an amber banner in the results
explains which provider/cadence was used and notes that the centroid test
is unavailable for FFI products.

The fetcher uses multiple name-resolution strategies (literal TIC name →
TIC catalog coordinate cone search → MAST object resolver) with
retry-and-exponential-backoff on transient MAST errors.

If a very recent sector has observation metadata in MAST's catalog but the
SPOC/QLP pipeline hasn't finished processing the light curves yet, the app
returns a clear message explaining the data-availability timeline instead
of a cryptic error.


## How to use the app

### Step 1 — load a light curve

Use the **Upload file** or **Fetch from MAST** tab. For MAST, enter a TIC
ID and click "List sectors" first to see which sectors have downloadable
data. Sectors shown with an amber background are FFI-only (no SPOC 2-min).

### Step 2 — adjust detection sensitivity (optional)

Below the tabs is a collapsed **⚙️ Detection sensitivity** panel. Two
sliders:

- **Depth threshold** (default `0.997`, range `0.95`–`0.999`) — the
  absolute floor for flagging dips. The label updates to show the
  equivalent percent depth ("flag dips deeper than 0.30%").

- **Minimum SNR** (default `4σ`, range `1σ`–`10σ`) — dip depth must
  exceed this multiple of the star's local photometric scatter.

**How the adaptive detection works.** The pipeline computes the star's
actual scatter (MAD of out-of-dip points) and sets an adaptive threshold:
`baseline − SNR × scatter`. The effective threshold is the *more
sensitive* of the user's absolute threshold and the adaptive threshold.
This means:

- **Deep dips on noisy stars** (like a 2.8% EB): the absolute threshold
  (0.997) does the work, same as always.
- **Shallow dips on quiet stars** (like a 0.06% transit on a star with
  0.02% scatter): the adaptive threshold (e.g. `1.0 − 3×0.0002 = 0.9994`)
  catches them — the old fixed threshold couldn't.
- **Pure noise**: the per-event SNR check rejects spurious crossings.

**Rule of thumb:**

- Missing a real shallow transit you can see by eye? → lower SNR to 3σ
- Lots of fake "events" on a noisy star? → raise SNR to 5–6σ
- Need to flag a dip ≲0.2% deep? → lower SNR *and* push threshold toward
  0.999 — the two filters work together

### Step 3 — run vetting

Click **Run vetting** (Upload tab) or **Fetch & vet** (MAST tab). Analysis
takes 10–30 seconds. You'll see:

- A **verdict banner** (planet candidate / EB candidate / blend / ambiguous)
  with confidence
- **Stellar parameters** and **light curve summary**
- **Diagnostic plots**: full detrended light curve with all events shaded
  (primary in red, others in orange, each numbered), a zoom grid showing
  each event's shape / depth / duration / SNR, centroid behaviour, BLS and
  Lomb-Scargle periodograms
- **Test tables**: BLS, Lomb-Scargle, centroid, odd/even, secondary
  eclipse, transit shape, physical interpretation
- **Event table** listing every dip with timing and depth

### Step 4 — compute habitability score

At the bottom of the results, click **Compute HCI**. The app queries
ExoFOP-TESS for the target's TOI data and stellar parameters, then
computes the Habitability Chance Index. The score panel shows:

- A large **score / 100** with a colour-coded tier and progress bar
- The **planet and TOI info** used (radius, period, semi-major axis,
  disposition, data source)
- An expandable **score breakdown** with six sub-score bars, each labelled
  with its weight, tier, and a one-sentence explanation referencing the
  relevant STEHM paper section
- A **TOI table** if the star has multiple TOIs on ExoFOP
- **Caveats** listing model limitations and the paper reference

### Step 5 — multi-sector analysis

Click **Multi-sector analysis** next to the HCI button. The app fetches
up to 10 TESS sectors from MAST, runs the full pipeline on each, and
displays:

- A **detection timeline** bar chart across sectors
- A **per-sector verdict table** with event counts and BLS results
- A **period consensus** from consistent BLS peaks
- The **HCI score automatically updates** with the real sector counts

### Step 6 — download PDF

Click **Download PDF report** or **Fetch & download PDF** for a centered
multi-page PDF with the verdict, all tables, and diagnostic plots.


## Verdict logic

1. Implied companion radius > 2.5 R_Jup → **eclipsing binary candidate**.
2. Secondary eclipse detected or odd/even depths differ > 3σ → **EB**.
3. Centroid offset > 3σ → **likely blend** (background eclipsing binary).
4. Planet-sized companion implied → **planet candidate**.
5. Else → **ambiguous** or **no signal** based on BLS SDE.


## Report bugs or contribute

Click **Report an Issue or Contribute** in the page header, or go to
<https://github.com/eagnespuerto/vetstar>. When reporting a bug, include:

- The TIC ID and sector you were analysing (or attach the FITS)
- The exact error message
- Whether you adjusted the sensitivity sliders
- Browser console output if available (F12 → Console tab)


## Limitations and disclaimers

- **Alpha release.** The pipeline is useful but is not a substitute for
  full vetting tools like DAVE, VESPA, or the TESS-SPOC Data Validation
  Report.
- **Always cross-check** candidates with ExoFOP, Gaia DR3 (especially
  RUWE for binarity), and high-resolution imaging before publishing.
- **STEHM model scope.** The HCI is based on a stagnant-lid CO₂ atmosphere
  model calibrated for Sun-like stars. It does not include non-thermal
  escape, magnetic fields, plate tectonics, or M-dwarf XUV histories
  (Hill et al. 2026 §6). The score is a first-order estimate only.
- **Free-tier hosting.** Render's free plan sleeps after ~15 min idle;
  cold-start takes ~30 seconds. Analysis itself is fast once warm.
- **Recent sectors.** TESS data typically become available at MAST 1–2
  months after the sector ends. Very recent sectors may show observation
  metadata in the sector list but return a "no products yet" error — this
  is expected and the error message explains the timeline.


---

## Developer info

### Run locally

```bash
python app.py
```

Installs Python deps if missing, builds the React frontend if needed,
starts a single Uvicorn process at `http://127.0.0.1:8000`.

Requirements: Python ≥ 3.10 and Node.js ≥ 18 (first run only).

```bash
python app.py --port 9000          # custom port
python app.py --host 0.0.0.0       # listen on all interfaces
python app.py --reload             # auto-reload on backend changes
python app.py --skip-build         # don't rebuild the frontend
python app.py --api-only           # API only, no SPA
```

### API endpoints

```
POST /api/analyze              multipart file + ?detect_threshold=&detect_min_snr=  → JSON
POST /api/report               multipart file + ?detect_threshold=&detect_min_snr=  → PDF
GET  /api/mast/sectors/{tic}                                                        → sector list
POST /api/mast/analyze         {tic_id, sector, detect_threshold, detect_min_snr}   → JSON
POST /api/mast/report          {tic_id, sector, detect_threshold, detect_min_snr}   → PDF
POST /api/habitability         {tic_id, ...optional overrides}                      → HCI JSON
POST /api/mast/multisector     {tic_id, ?sectors, detect_threshold, detect_min_snr} → timeline JSON
GET  /api/health                                                                    → {"status":"ok"}
GET  /docs                                                                          → Swagger UI
```

### Project layout

```
vetstar/
├── app.py                      ← single-command launcher
├── backend/
│   ├── app/
│   │   ├── main.py             FastAPI endpoints + SPA mount
│   │   ├── pipeline.py         BLS, LS, adaptive detection, centroid, shape, physics
│   │   ├── parsers.py          FITS + ExoFOP JSON readers
│   │   ├── mast_fetch.py       Multi-strategy MAST downloader with retry
│   │   ├── habitability.py     STEHM-based HCI scoring engine
│   │   ├── exofop.py           ExoFOP-TESS + TIC catalog querier
│   │   └── report.py           Centered multi-page PDF builder
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.tsx             Tabs, sensitivity panel, results, HCI, multisector
│   │   ├── api.ts              API client
│   │   └── types.ts            Shared TypeScript types
│   ├── index.html
│   └── package.json
├── Dockerfile                  Multi-stage build with context validation
├── render.yaml                 Render Blueprint
├── fly.toml                    Fly.io app config
└── .github/workflows/
    ├── ci-deploy.yml           CI + auto-deploy to Render or Fly
    └── build.yml               Cross-platform exe builder
```

### Deploy your own copy

**Render** — push to GitHub, sign up at <https://render.com>, New →
Blueprint → connect your repo. Render reads `render.yaml` and provisions
automatically. Free tier sleeps after 15 min idle.

**Fly.io** — `fly auth login` then `fly launch --no-deploy --copy-config`
then `fly deploy`. The included `fly.toml` provisions a shared-cpu-1x
machine with 1 GB RAM (within the free allowance). No idle sleep. Requires
a credit card on file (no charges within the free tier).

Auto-deploy on push: set the `FLY_API_TOKEN` or `RENDER_DEPLOY_HOOK` repo
secret in GitHub; the CI workflow handles the rest.

### Build a standalone executable

```bash
python build_exe.py
```

PyInstaller bundles everything into one binary (~180–230 MB). Double-click
to launch; a browser opens to the app automatically.

### References

- Hill, M. L., Kane, S. R., Foley, B. J., & Schaefer, L. K. (2026).
  *Smaller Than Earth Habitability Model (STEHM): The Lower Size Limit for
  Atmosphere Retention in the Habitable Zone.* arXiv:2605.00170v1.
- Kopparapu, R. K. et al. (2013, 2014). *Habitable Zone boundaries.*
  ApJ 765, 131; ApJ 787, L29.
- Tian, F. et al. (2009). *CO₂ escape from early Mars.*
  Geophys. Res. Lett. 36, L02205.
- Kite, E. S. & Barnett, M. N. (2020). *Exoplanet secondary atmospheres.*
  PNAS 117, 18264.

### License

CC0-1.0. See `LICENSE`.
