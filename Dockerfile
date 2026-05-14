# =====================================================================
# Stage 0 — context loader + validator.
#
# All COPY from the build context goes through this stage. If any
# required path is missing — usually because the host's "Root Directory"
# is misconfigured, the repo is one level too deep, or .dockerignore is
# excluding something — this stage fails with a clear message instead of
# BuildKit's cryptic "failed to compute cache key" error.
# =====================================================================
FROM alpine:3.20 AS context

# Bring the whole context in once.
COPY . /ctx

RUN set -e; \
    echo "=== Inspecting build context (/ctx) ==="; \
    ls -la /ctx 2>/dev/null || (echo "  (empty / unreadable)" && exit 1); \
    fail=0; \
    for required in frontend backend Dockerfile; do \
        if [ ! -e "/ctx/$required" ]; then \
            echo "  MISSING: $required"; \
            fail=1; \
        fi; \
    done; \
    for required in frontend/package.json backend/requirements.txt backend/app/main.py; do \
        if [ ! -f "/ctx/$required" ]; then \
            echo "  MISSING: $required"; \
            fail=1; \
        fi; \
    done; \
    if [ "$fail" = "1" ]; then \
        echo ""; \
        echo "============================================================"; \
        echo "BUILD CONTEXT IS WRONG."; \
        echo ""; \
        echo "Expected the build context to contain frontend/ and backend/"; \
        echo "with the standard project layout. Most common causes:"; \
        echo ""; \
        echo "1. On Render / Railway / Fly: the 'Root Directory' setting"; \
        echo "   points into a subfolder that doesn't contain frontend/"; \
        echo "   AND backend/. Set it to the directory that holds both"; \
        echo "   (often left empty, meaning the repo root)."; \
        echo ""; \
        echo "2. Repo committed one level too deep — files are inside a"; \
        echo "   nested folder. Either fix the repo or point the host's"; \
        echo "   Root Directory at that subfolder."; \
        echo ""; \
        echo "3. .dockerignore is excluding files it shouldn't."; \
        echo "============================================================"; \
        exit 1; \
    fi; \
    echo "Build context is valid."


# =====================================================================
# Stage 1 — build the React frontend (pull source from stage 0)
# =====================================================================
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend

# Source pulled from the validated context. If we got past stage 0, these
# definitely exist.
COPY --from=context /ctx/frontend/package.json /ctx/frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund

COPY --from=context /ctx/frontend/ ./
RUN npm run build && test -f dist/index.html


# =====================================================================
# Stage 2 — Python runtime that serves both the API and the built SPA
# =====================================================================
FROM python:3.12-slim AS runtime

# Native build tools for scipy/numpy wheels that need compiling.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps first for layer caching.
COPY --from=context /ctx/backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Backend source.
COPY --from=context /ctx/backend/ ./backend/

# Built frontend from stage 1.
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Final sanity check on the image.
RUN test -f /app/backend/app/main.py \
    && test -f /app/frontend/dist/index.html \
    && echo "Runtime image is complete."

ENV PYTHONPATH=/app/backend \
    PYTHONUNBUFFERED=1 \
    FRONTEND_DIST=/app/frontend/dist

# Render / Fly inject PORT at runtime; default to 8000 for local docker.
ENV PORT=8000
EXPOSE 8000

# Shell form so $PORT is expanded.
CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT
