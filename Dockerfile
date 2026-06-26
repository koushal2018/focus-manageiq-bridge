# syntax=docker/dockerfile:1
# =============================================================
# ENBD Multi-Cloud FinOps PoC — production web image
# =============================================================
# Multi-stage: a builder that compiles wheels, then a slim runtime
# that carries only the venv + app code. Runs as a non-root user.
# Serves the FastAPI app with gunicorn supervising uvicorn workers.
# =============================================================

# ---- Stage 1: builder ----
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build deps for psycopg2 (libpq) — only in the builder layer.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .

# Build a self-contained venv we can copy wholesale into the runtime.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade pip && pip install -r requirements.txt


# ---- Stage 2: runtime ----
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    # FinOps DB connection (overridden by compose / k8s env)
    FOCUS_PG_HOST=db \
    FOCUS_PG_PORT=5432 \
    FOCUS_PG_USER=focus_app \
    FOCUS_PG_DB=focus \
    # AI layer off by default — the stack runs fully without Bedrock.
    BEDROCK_DISABLED=1

# Runtime needs only libpq (not the -dev headers) + a psql client for the
# entrypoint's schema apply / readiness check.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Non-root user.
RUN useradd --create-home --uid 10001 finops
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=finops:finops . .

# Writable artifact dir for the seed pipeline (generators/dispatcher write
# CSVs + JSON here). .dockerignore excludes the host's out/, so create it.
RUN mkdir -p /app/out && chown -R finops:finops /app/out

# Drop privileges.
USER finops

EXPOSE 8000

# Container-level healthcheck hits the app's own /healthz (which checks DB).
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)"

# The entrypoint waits for Postgres, seeds the DB idempotently, then execs
# gunicorn. See docker/entrypoint.sh.
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "web.app:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "2", \
     "--bind", "0.0.0.0:8000", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--timeout", "60"]
