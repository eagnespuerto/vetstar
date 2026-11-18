---
title: vetstar NEMESIS worker
emoji: 🔭
colorFrom: indigo
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: NEMESIS transit search worker for vetstar
---

Job server that runs [NEMESIS](https://github.com/daxfeliz/NEMESIS)
(Feliz et al. 2021, AJ 161, 247; NEMESIS II, Feliz et al. 2026) with
LEO-Vetter (Kunimoto et al. 2025) for [vetstar](https://github.com/eagnespuerto/vetstar).

API: `GET /health`, `POST /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/result`.
Source: `nemesis_worker/` in the vetstar repository. Set a `WORKER_TOKEN`
secret in the Space settings to require an `X-Worker-Token` header.
