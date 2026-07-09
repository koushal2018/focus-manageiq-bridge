#!/usr/bin/env bash
# =============================================================
# Web container entrypoint.
#   1. wait for Postgres to accept connections
#   2. apply the schema (idempotent — DROP/CREATE)
#   3. seed: generators -> dispatcher -> join -> load -> onprem
#   4. exec the CMD (gunicorn)
# All DB access uses network psql (FOCUS_PG_MODE=network), not docker exec.
# =============================================================
set -euo pipefail

: "${FOCUS_PG_HOST:=db}"
: "${FOCUS_PG_PORT:=5432}"
: "${FOCUS_PG_USER:=focus_app}"
: "${FOCUS_PG_DB:=focus}"
export FOCUS_PG_MODE=network
# No baked-in password fallback: compose / helm always inject FOCUS_PG_PASS
# (compose carries the localhost-only demo default; helm reads a Secret).
# A silent default here would travel into a real deployment by copy-paste.
if [ -z "${FOCUS_PG_PASS:-}" ]; then
  echo "[entrypoint] FOCUS_PG_PASS is not set — refusing to start." >&2
  echo "[entrypoint] Set it via docker-compose (.env) or the deployment secret." >&2
  exit 1
fi
export PGPASSWORD="$FOCUS_PG_PASS"

echo "[entrypoint] waiting for postgres ${FOCUS_PG_HOST}:${FOCUS_PG_PORT} ..."
for i in $(seq 1 30); do
  if pg_isready -h "$FOCUS_PG_HOST" -p "$FOCUS_PG_PORT" -U "$FOCUS_PG_USER" >/dev/null 2>&1; then
    echo "[entrypoint] postgres is ready"
    break
  fi
  sleep 2
  if [ "$i" -eq 30 ]; then
    echo "[entrypoint] postgres did not become ready in time" >&2
    exit 1
  fi
done

# Skip seeding if SEED_ON_START=0 (e.g. a scale-out replica that shouldn't
# re-seed). The first/primary web container seeds; set this to 1 there.
if [ "${SEED_ON_START:-1}" = "1" ]; then
  echo "[entrypoint] applying schema ..."
  psql -h "$FOCUS_PG_HOST" -p "$FOCUS_PG_PORT" -U "$FOCUS_PG_USER" -d "$FOCUS_PG_DB" \
       -v ON_ERROR_STOP=1 -f db/schema.sql

  echo "[entrypoint] seeding data (generators -> dispatcher -> join -> load -> onprem) ..."
  python -m docker.seed
else
  echo "[entrypoint] SEED_ON_START=0 — skipping schema + seed"
fi

echo "[entrypoint] starting web: $*"
exec "$@"
