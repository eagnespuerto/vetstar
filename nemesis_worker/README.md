# NEMESIS worker

vetstar v2 runs its main transit search with
[NEMESIS](https://github.com/daxfeliz/NEMESIS) (Feliz et al. 2021, AJ 161, 247;
NEMESIS II, Feliz et al. 2026, arXiv:2611.21758). NEMESIS needs Python 3.12 and
its own pinned stack, and one run takes several minutes and more than 512 MB,
so it runs here as a separate service. The vetstar site calls it over HTTP.

## Run

```bash
docker build -t vetstar-nemesis nemesis_worker
docker run -p 8001:8001 -e WORKER_TOKEN=change-me -v nemesis-data:/data vetstar-nemesis
```

Then set these on the vetstar site service:

| Variable | Example |
|---|---|
| `NEMESIS_WORKER_URL` | `http://localhost:8001` |
| `NEMESIS_WORKER_TOKEN` | same value as `WORKER_TOKEN` |

If `NEMESIS_WORKER_URL` is not set, the site still works with the
Quick check pipeline only.

## Sizing

Plan for at least 2 GB RAM. 20-second and 2-minute cadence runs need more.
One job runs at a time; others wait in a queue (`NEMESIS_MAX_QUEUE`, default 10).

| Variable | Default | Meaning |
|---|---|---|
| `WORKER_TOKEN` | empty (no auth) | shared secret, sent as `X-Worker-Token` |
| `NEMESIS_JOB_TIMEOUT_MIN` | 40 | kill a run after this long |
| `NEMESIS_JOB_TTL_HOURS` | 24 | delete finished jobs after this long |
| `NEMESIS_TLS_THREADS` | 1 | TLS threads per run |
| `NEMESIS_LAST_SECTOR` | 120 | newest sector NEMESIS may use |
| `NEMESIS_LITE` | 0 | light mode for small instances (see below) |

### Light mode (`NEMESIS_LITE=1`)

For instances with 512 MB-1 GB. It skips the DSS sky image and kept FITS or
raw arrays (the vetstar UI does not show them), and uses a coarser TLS grid
(oversampling 3 instead of NEMESIS II's 9; duration step 1.1 instead of 1.05).
The coarser grid can miss very shallow signals, so results carry
`meta.lite = true` and the UI shows a "Light mode" tag. Every run also
records `meta.peak_rss_mb`, the job's peak memory, to help size the instance.

The image always runs with one BLAS/OpenMP thread and `MALLOC_ARENA_MAX=2`,
which lowers memory without changing results. Each job runs in its own
process, so an out-of-memory kill stops that job, not the server.

## Citation

Please cite NEMESIS (Feliz et al. 2021; Feliz et al. 2026) and LEO-Vetter
(Kunimoto et al. 2025) when you use results from this worker.
