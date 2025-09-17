#!/usr/bin/env bash
set -euo pipefail

# Defaults (overridable via env)
: "${PGDATA:=/home/appuser/pgdata}" # user-owned dir (matches Dockerfile)
: "${POSTGRES_DB:=cbomkit}"
: "${POSTGRES_USER:=cbomkit}"
: "${POSTGRES_PASSWORD:=cbomkit}"
: "${CBOMKIT_PORT:=8081}"
: "${PG_SUPERUSER:=appuser}"   # superuser to own the cluster

# Ensure Postgres binaries are on PATH
PG_BIN_DIR=$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | head -n1 || true)
[ -n "$PG_BIN_DIR" ] && export PATH="$PG_BIN_DIR:$PATH"

mkdir -p "$PGDATA"

# Ensure CBOMkit working directory exists and is writable for repo clones
mkdir -p "/home/appuser/.cbomkit"
chmod 700 "/home/appuser/.cbomkit" || true

# Initialize cluster if needed
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  echo "[cbomkit] Initializing Postgres cluster in $PGDATA as superuser '$PG_SUPERUSER'..."
  initdb -D "$PGDATA" --username="$PG_SUPERUSER" >/dev/null
  {
    echo "listen_addresses = '127.0.0.1'"
    echo "port = 5432"
    echo "max_connections = 100"
    echo "unix_socket_directories = '/tmp'"
  } >> "$PGDATA/postgresql.conf"
  {
    echo "host all all 127.0.0.1/32 trust"
    echo "host all all ::1/128 trust"
  } >> "$PGDATA/pg_hba.conf"
fi

echo "[cbomkit] Starting Postgres..."
rm -f "$PGDATA/postmaster.pid" 2>/dev/null || true
pg_ctl -D "$PGDATA" -l "$PGDATA/server.log" \
  -o "-h 127.0.0.1 -p 5432 -k /tmp -c unix_socket_directories=/tmp" \
  -w start || { tail -n 100 "$PGDATA/server.log" >&2; exit 1; }

cleanup() {
  echo "[cbomkit] Shutting down..."
  if [ -n "${BACKEND_PID:-}" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then kill "$BACKEND_PID" || true; fi
  pg_ctl -D "$PGDATA" -m fast stop || true
}
trap cleanup EXIT INT TERM

# Use superuser for bootstrap SQL
export PGHOST=127.0.0.1
export PGPORT=5432
export PGUSER="$PG_SUPERUSER"
export PGDATABASE=postgres

# Wait for readiness
for i in {1..60}; do
  if pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "[cbomkit] Ensuring database and user..."
psql -tc "SELECT 1 FROM pg_roles WHERE rolname='${POSTGRES_USER}'" | grep -q 1 || \
  psql -c "CREATE USER ${POSTGRES_USER} WITH PASSWORD '${POSTGRES_PASSWORD}';"
psql -tc "SELECT 1 FROM pg_database WHERE datname='${POSTGRES_DB}'" | grep -q 1 || \
  psql -c "CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER};"

echo "[cbomkit] Launching CBOMkit backend on port ${CBOMKIT_PORT}..."
export QUARKUS_DATASOURCE_JDBC_URL="jdbc:postgresql://127.0.0.1:5432/${POSTGRES_DB}"
export QUARKUS_DATASOURCE_USERNAME="${POSTGRES_USER}"
export QUARKUS_DATASOURCE_PASSWORD="${POSTGRES_PASSWORD}"
export QUARKUS_HTTP_PORT="${CBOMKIT_PORT}"

java ${JAVA_OPTS:-} -jar "${JAVA_APP_JAR:-/deployments/quarkus-run.jar}" &
BACKEND_PID=$!

HEALTH1="http://127.0.0.1:${CBOMKIT_PORT}/q/health"
HEALTH2="http://127.0.0.1:${CBOMKIT_PORT}/q/openapi"
echo "[cbomkit] Waiting for backend readiness on ${HEALTH1} (or fallback ${HEALTH2}) ..."
for i in $(seq 1 90); do
  if curl -fsS "${HEALTH1}" >/dev/null 2>&1 || curl -fsS "${HEALTH2}" >/dev/null 2>&1; then
    echo "[cbomkit] Backend is responsive (after ${i}s)."
    break
  fi
  [ $((i % 5)) -eq 0 ] && echo "[cbomkit] Still waiting (${i}/90)..."
  sleep 1
done

echo "[cbomkit] Starting Python worker..."
export CBOMKIT_BASE_URL="http://127.0.0.1:${CBOMKIT_PORT}"
exec uv run workers/cbomkit/main.py
