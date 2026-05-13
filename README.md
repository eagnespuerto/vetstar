# TESS / Kepler Vetting Studio

Self-hostable web app for full transit / eclipse vetting of TESS or Kepler
light curves. Upload a SPOC FITS file (or pull one from MAST by TIC + sector)
and receive a complete vetting report with PDF export.

## Run it (one command)

```bash
python app.py
```

That's it. The launcher:

1. Installs backend Python deps if missing (`fastapi`, `astropy`, `astroquery`, etc).
2. Builds the React frontend bundle into `frontend/dist/` if it's missing.
3. Starts a single Uvicorn process on `http://127.0.0.1:8000` that serves
   **both** the web UI and the API on the same port.

Requirements: **Python ≥ 3.10** and **Node.js ≥ 18** (only needed the first
time, to build the frontend). After the first run, the bundle is cached.

Flags:

```bash
python app.py --port 9000          # custom port
python app.py --host 0.0.0.0       # listen on all interfaces
python app.py --reload             # auto-reload on backend changes (dev)
python app.py --skip-build         # don't rebuild the frontend
python app.py --api-only           # don't try to serve the SPA
```

Open <http://localhost:8000> in a browser.

## What it does

- **Period searches** — Box Least Squares (BLS) + Lomb-Scargle
- **Discrete dip event detection** (handles single-transit cases)
- **Centroid offset test** — distinguishes on-target events from background blends
- **Odd / even transit depth comparison** — eclipsing-binary indicator
- **Secondary eclipse search** at phase 0.5
- **Transit shape analysis** (U vs V, ingress / egress / flat-bottom)
- **Physics-based companion sizing** with CROWDSAP dilution correction
- **Automated verdict**: planet candidate / EB candidate / blend / ambiguous
- **PDF report** for archiving (centered layout, multi-page)

Two input modes:

- **Upload** a `.fits`, `.fits.gz`, `.json`, or `.customization` file
- **Fetch from MAST** — enter a TIC ID and sector; the backend pulls the
  matching SPOC light curve via `astroquery.mast.Observations` and analyses
  it. Click "List sectors" to see all SPOC sectors available for a TIC.

## API endpoints

```
POST /api/analyze              multipart file=@lc.fits          → JSON
POST /api/report               multipart file=@lc.fits          → PDF
GET  /api/mast/sectors/{tic}                                    → [100, 101, ...]
POST /api/mast/analyze         {"tic_id": ..., "sector": ...}   → JSON
POST /api/mast/report          {"tic_id": ..., "sector": ...}   → PDF
GET  /api/health                                                → {"status":"ok"}
GET  /docs                                                      → Swagger UI
```

The JSON response matches the `VettingResult` shape in
`backend/app/pipeline.py` and includes base64-encoded PNG diagnostic plots.

## Verdict logic

1. Implied companion radius > 2.5 R_Jup → **eclipsing binary candidate**.
2. Secondary eclipse detected or odd/even depths differ > 3σ → **EB**.
3. Centroid offset > 3σ → **likely blend** (background eclipsing binary).
4. Otherwise, if a planet-sized companion is implied → **planet candidate**.
5. Else → **ambiguous** or **no signal** based on BLS SDE.

Any candidate should still go through manual review and ground-based follow-up.

## Project layout

```
vetting-app/
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
│   │   ├── api.ts
│   │   └── types.ts
│   └── package.json
├── Dockerfile.backend      ← optional, for two-container deployments
├── Dockerfile.frontend
└── docker-compose.yml
```

## Optional: Docker

If you prefer Docker:

```bash
docker compose up --build
# → frontend on http://localhost:8080, API on http://localhost:8000
```

But the `python app.py` single-process path is simpler and runs everything on
one port.

## License & disclaimer

Educational / research use. Not a substitute for full vetting pipelines like
DAVE, VESPA, or the TESS-SPOC Data Validation Report. Always cross-check with
ExoFOP, Gaia DR3 (RUWE), and high-resolution imaging before publishing a
candidate.

## Build a standalone executable (.exe / .app / ELF)

PyInstaller bundles Python + all deps + the web UI into one self-contained
binary. The user double-clicks it and a browser tab opens to the app — no
Python install needed.

```bash
python build_exe.py
```

That produces:

- `dist/tess-vetting-studio.exe`  on Windows  (~180–230 MB)
- `dist/tess-vetting-studio`      on macOS    (Mach-O)
- `dist/tess-vetting-studio`      on Linux    (ELF)

**PyInstaller does not cross-compile.** To produce a Windows `.exe` you must
run `python build_exe.py` on a Windows machine (or a Windows VM, or a GitHub
Actions `windows-latest` runner). Likewise for macOS.

The binary auto-opens a browser to `http://127.0.0.1:8000` on launch. Press
Ctrl-C in the console window to quit, or close the console window. Flags:

```
tess-vetting-studio --port 9000     # custom port
tess-vetting-studio --no-browser    # don't auto-open the browser
```


## Deploy to the cloud (no local install needed for users)

The simplest way to give people a "click-the-link, no install" experience
is to deploy this app to a free cloud host. A `render.yaml` is included for
[Render](https://render.com).

### One-time setup

1. Push this repo to GitHub.
2. Sign up at <https://render.com> (free tier, no credit card).
3. In Render: **New** → **Blueprint** → connect your GitHub repo.
   Render detects `render.yaml` and provisions the service automatically.
4. Wait ~5 minutes for the first build (Docker image: frontend bundle +
   Python deps). You get a permanent URL like
   `https://tess-vetting-studio.onrender.com`.

That's it. Share the URL with anyone — they open it in a browser, get the
GUI, upload a FITS file or query MAST, get results and a PDF. Nothing to
install.

### Auto-deploy on git push

`render.yaml` sets `autoDeploy: true`, so every push to `main` triggers a
fresh deploy automatically. The GitHub Actions workflow at
`.github/workflows/ci-deploy.yml` additionally:

- Runs a fast smoke-test (imports the backend, builds the frontend) on every
  push and PR.
- Pings a Render deploy hook on push to `main` (optional — Render's own
  autoDeploy covers this; the hook is useful if you ever turn autoDeploy off
  or want CI to gate deploys).

To enable the optional deploy hook:

1. In Render, open the service → **Settings** → **Deploy Hook** → copy the URL.
2. In GitHub, repo **Settings** → **Secrets and variables** → **Actions** →
   **New repository secret**, name `RENDER_DEPLOY_HOOK`, paste the URL.

### Free-tier caveats

Render's free plan sleeps the service after ~15 minutes of inactivity. The
next visit takes ~30 seconds to wake up (the app then runs normally).
For zero cold starts, upgrade to the $7/month Starter plan or keep-alive
the service with an external ping.

### Other platforms

The `Dockerfile` is portable — it works on:

- **Railway** — `New Project` → `Deploy from GitHub` → it auto-detects the
  Dockerfile.
- **Fly.io** — `fly launch` from the repo root; it reads the Dockerfile.
- **Google Cloud Run** — `gcloud run deploy --source .`
- **AWS App Runner / Azure Container Apps** — point them at the repo.


### Or deploy to Fly.io instead

Fly.io is a strong alternative — its free allowance includes always-on
machines, so there's no 15-min sleep + 30-sec cold-start like Render's
free tier. It also gives you 1 GB RAM (vs Render free's 512 MB), which
matters for big multi-sector FITS analyses.

The repo includes `fly.toml` already configured.

```bash
# One-time setup
brew install flyctl                          # or: curl -L https://fly.io/install.sh | sh
fly auth login
fly launch --no-deploy --copy-config         # picks up fly.toml; choose a unique app name
fly deploy
```

After this, your app is live at `https://<your-app-name>.fly.dev`.

**For auto-deploy on git push:**

1. Generate a long-lived token: `fly tokens create deploy -x 999999h`
2. In GitHub, repo Settings → Secrets and variables → Actions → New secret,
   name it `FLY_API_TOKEN`, paste the token.
3. Every push to `main` deploys automatically (the existing
   `.github/workflows/ci-deploy.yml` handles it).

**Free-tier requirements:** Fly requires a credit card on file (no charges
within the free allowance). The free tier comfortably covers one always-on
`shared-cpu-1x` machine with 1 GB RAM.

### Fly vs Render: which to pick?

| Concern | Render free | Fly free |
|---|---|---|
| Idle sleep + cold start | Yes, ~30 s | Optional |
| RAM | 512 MB | 1 GB |
| Credit card required | No | Yes (no charges) |
| Setup complexity | Click in browser | `flyctl` CLI, one time |
| Regions | Limited | Many (including Asia) |
| Auto-deploy on push | Native (`render.yaml`) | Via GitHub Actions |

For this app, **Fly** is the better default if you can tolerate the credit
card requirement — bigger RAM, no cold starts, closer regions. **Render**
is simpler if you want browser-only setup.

