# vetstar NEMESIS worker. NEMESIS requires Python 3.12 exactly.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY worker ./worker

# One BLAS/OpenMP thread and fewer malloc arenas: same results, much less
# memory on small instances (each BLAS thread keeps its own buffers).
ENV MPLBACKEND=Agg \
    PYTHONUNBUFFERED=1 \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMBA_NUM_THREADS=1 \
    MALLOC_ARENA_MAX=2 \
    NEMESIS_JOBS_DIR=/data/jobs \
    NEMESIS_CACHE_DIR=/data/cache
RUN mkdir -p /data/jobs /data/cache
EXPOSE 8001
CMD ["sh", "-c", "uvicorn worker.app:app --host 0.0.0.0 --port ${PORT:-8001}"]
