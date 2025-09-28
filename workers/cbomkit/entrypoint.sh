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
STATE_DIR="/home/appuser/.cbomkit"
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR" || true
# Clear old backend log on startup (container restarts keep filesystem)
rm -f "$STATE_DIR/backend.log" "$STATE_DIR/backend_oom" 2>/dev/null || true

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
  if [ -n "${LOG_TAIL_PID:-}" ] && kill -0 "$LOG_TAIL_PID" 2>/dev/null; then kill "$LOG_TAIL_PID" || true; fi
  if [ -n "${PY_PID:-}" ] && kill -0 "$PY_PID" 2>/dev/null; then kill "$PY_PID" || true; fi
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

# Redirect backend logs to a file (also streamed to stdout below)
BACKEND_LOG="$STATE_DIR/backend.log"
touch "$BACKEND_LOG" || true
export CBOMKIT_BACKEND_LOG="$BACKEND_LOG"
export CBOMKIT_BACKEND_STATE_DIR="$STATE_DIR"
export CBOMKIT_BACKEND_OOM_FILE="$STATE_DIR/backend_oom"

HEALTH1="http://127.0.0.1:${CBOMKIT_PORT}/q/health"
HEALTH2="http://127.0.0.1:${CBOMKIT_PORT}/q/openapi"

start_backend() {
  echo "[cbomkit] (re)starting backend..."
  # Start Java backend
  java ${JAVA_OPTS:-} -jar "${JAVA_APP_JAR:-/deployments/quarkus-run.jar}" >>"$BACKEND_LOG" 2>&1 &
  BACKEND_PID=$!
  # Wait for readiness
  echo "[cbomkit] Waiting for backend readiness on ${HEALTH1} (or fallback ${HEALTH2}) ..."
  READY=0
  for i in $(seq 1 120); do
    if curl -fsS "${HEALTH1}" >/dev/null 2>&1 || curl -fsS "${HEALTH2}" >/dev/null 2>&1; then
      echo "[cbomkit] Backend is responsive (after ${i}s)."
      READY=1
      break
    fi
    [ $((i % 5)) -eq 0 ] && echo "[cbomkit] Still waiting (${i}/120)..."
    sleep 1
  done
  # Small grace for WS routes to settle
  if [ "$READY" -eq 1 ]; then
    # Backend is up; clear any OOM sentinel
    rm -f "$CBOMKIT_BACKEND_OOM_FILE" 2>/dev/null || true
    sleep 1
  fi
}

stop_backend() {
  if [ -n "${BACKEND_PID:-}" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
  fi
  # Restart Postgres as well to get a clean backend state
  pg_ctl -D "$PGDATA" -m fast stop >/dev/null 2>&1 || true
  pg_ctl -D "$PGDATA" -l "$PGDATA/server.log" \
    -o "-h 127.0.0.1 -p 5432 -k /tmp -c unix_socket_directories=/tmp" \
    -w start >/dev/null 2>&1 || true
}

# Start backend initially
start_backend

echo "[cbomkit] Starting Python worker..."
export CBOMKIT_BASE_URL="http://127.0.0.1:${CBOMKIT_PORT}"

# Launch Python worker in background and keep it running
uv run --no-dev workers/cbomkit/main.py &
PY_PID=$!

# Optionally stream backend logs to stdout for docker logs visibility
if [ "${STREAM_BACKEND_LOGS:-1}" != "0" ]; then
  tail -n +1 -F "$BACKEND_LOG" &
  LOG_TAIL_PID=$!
fi

# Supervise backend and Python: restart backend on exit; if Python exits, stop backend and exit
while true; do
  if ! kill -0 "$PY_PID" 2>/dev/null; then
    echo "[cbomkit] Python worker exited; shutting down"
    stop_backend
    exit 1
  fi
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "[cbomkit] Backend not running; starting..."
    start_backend
  fi
  # Don't let a non-zero exit from the backend kill this script (set -e is active)
  set +e
  wait "$BACKEND_PID"
  EXIT_CODE=$?
  set -e
  echo "[cbomkit] Backend exited (code=$EXIT_CODE); checking for OOM and restarting..."
  if grep -qE "OutOfMemoryError|Out of memory" "$BACKEND_LOG"; then
    echo "[cbomkit] OOM detected; creating sentinel and restarting Postgres and backend."
    date +%s > "$CBOMKIT_BACKEND_OOM_FILE" || true
  fi
  stop_backend
  start_backend
done
