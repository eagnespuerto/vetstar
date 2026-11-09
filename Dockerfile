# =====================================================================
# Stage 1 — build the React frontend
# =====================================================================
FROM node:24.22.1-alpine AS frontend-build
WORKDIR /app/frontend

# Copy package files first so npm install caches independently of source.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund

# Copy the rest of the frontend and build.
COPY frontend/ ./
RUN npm run build


# =====================================================================
# Stage 2 — Python runtime that serves both the API and the built SPA
# =====================================================================
FROM python:3.12-slim AS runtime

# System packages: build tools for scipy/numpy native bits, then trimmed.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for layer caching.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy backend source and the shared science package.
COPY backend/ ./backend/
COPY vetstar_core/ ./vetstar_core/

# Copy the built frontend from stage 1.
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# backend/ on the path so `app.main` resolves; /app for `vetstar_core`.
ENV PYTHONPATH=/app/backend:/app \
    PYTHONUNBUFFERED=1 \
    FRONTEND_DIST=/app/frontend/dist

# glibc gives each allocating thread its own malloc arena and rarely returns
# their memory, so the prefetch / cross-match thread pools inflate RSS well
# past the Python heap. Two arenas keeps a 512 MB instance (Render free tier)
# from being OOM-killed mid-vet.
ENV MALLOC_ARENA_MAX=2

# Render injects PORT at runtime; default to 8000 for local docker.
ENV PORT=8000
EXPOSE 8000

# Use shell form so $PORT is expanded.
CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT
