# vetstar NEMESIS worker on a Hugging Face Docker Space (free CPU: 2 vCPU, 16 GB).
# The Space holds only this file and README.md; the worker code is cloned
# from GitHub at a fixed commit. To update: change VETSTAR_REF and save.
FROM python:3.12-slim

ARG VETSTAR_REF=3834de6
RUN apt-get update \
 && apt-get install -y --no-install-recommends git build-essential \
 && rm -rf /var/lib/apt/lists/*

# Spaces run the container as uid 1000.
RUN useradd -m -u 1000 user
WORKDIR /srv
RUN git clone https://github.com/eagnespuerto/vetstar.git /tmp/vetstar \
 && git -C /tmp/vetstar checkout "${VETSTAR_REF}" \
 && cp -r /tmp/vetstar/nemesis_worker/worker /tmp/vetstar/nemesis_worker/requirements.txt /srv/ \
 && rm -rf /tmp/vetstar
RUN pip install --no-cache-dir -r requirements.txt

ENV HOME=/home/user \
    MPLBACKEND=Agg \
    PYTHONUNBUFFERED=1 \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    MALLOC_ARENA_MAX=2 \
    NEMESIS_TLS_THREADS=2 \
    NEMESIS_LITE=0 \
    NEMESIS_JOB_TIMEOUT_MIN=60 \
    NEMESIS_JOBS_DIR=/tmp/nemesis/jobs \
    NEMESIS_CACHE_DIR=/tmp/nemesis/cache \
    PORT=7860
RUN mkdir -p /tmp/nemesis/jobs /tmp/nemesis/cache && chown -R user:user /tmp/nemesis /srv
USER user
EXPOSE 7860
CMD ["sh", "-c", "uvicorn worker.app:app --host 0.0.0.0 --port ${PORT}"]
