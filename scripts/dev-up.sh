#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/.dev-logs"
PID_DIR="$ROOT_DIR/.dev-pids"

mkdir -p "$LOG_DIR" "$PID_DIR"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_cmd docker
require_cmd python3
require_cmd npm
require_cmd curl

if [[ ! -d "$ROOT_DIR/apps/controlplane-ui/node_modules" ]]; then
  echo "UI dependencies are not installed. Run: cd apps/controlplane-ui && npm install" >&2
  exit 1
fi

if ! python3 -c "import fastapi, uvicorn, sqlalchemy, psycopg, pydantic_settings, httpx" >/dev/null 2>&1; then
  echo "Control Plane API dependencies are missing. Run: python3 -m pip install -r apps/controlplane-api/requirements.txt" >&2
  exit 1
fi

if ! python3 -c "import fastapi, uvicorn, httpx" >/dev/null 2>&1; then
  echo "Capture server dependencies are missing. Run: python3 -m pip install -r apps/mcp-mock/requirements.txt" >&2
  exit 1
fi

echo "Starting Postgres and Redis..."
(cd "$ROOT_DIR/infra" && docker compose up -d)

start_service() {
  local name="$1"
  local workdir="$2"
  local pidfile="$PID_DIR/$name.pid"
  local logfile="$LOG_DIR/$name.log"
  shift 2

  if [[ -f "$pidfile" ]]; then
    local pid
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" >/dev/null 2>&1; then
      echo "$name already running on pid $pid"
      return
    fi
    rm -f "$pidfile"
  fi

  (
    cd "$workdir"
    nohup "$@" >"$logfile" 2>&1 &
    echo $! >"$pidfile"
  )

  echo "Started $name"
}

start_service \
  "controlplane-api" \
  "$ROOT_DIR/apps/controlplane-api" \
  python3 -m uvicorn main:app --host 0.0.0.0 --port 8081 --reload

start_service \
  "capture-server" \
  "$ROOT_DIR/apps/mcp-mock" \
  python3 -m uvicorn app.main_server:app --host 0.0.0.0 --port 8082 --reload

start_service \
  "controlplane-ui" \
  "$ROOT_DIR/apps/controlplane-ui" \
  npm run dev -- --host 0.0.0.0 --port 5173

wait_for_url() {
  local label="$1"
  local url="$2"
  local attempts="${3:-30}"
  local i
  for ((i = 1; i <= attempts; i++)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "$label ready -> $url"
      return 0
    fi
    sleep 1
  done
  echo "$label did not become ready in time -> $url" >&2
  return 1
}

wait_for_url "Control API" "http://localhost:8081/health"
wait_for_url "Capture API" "http://localhost:8082/health"
wait_for_url "UI" "http://localhost:5173"

cat <<EOF

Application startup complete.

UI:              http://localhost:5173
Control API:     http://localhost:8081/health
Capture API:     http://localhost:8082/health
Postgres:        localhost:5433
Redis:           localhost:6379

Chrome is no longer started globally during dev boot.
Start a training session in the UI to provision a dedicated Chrome profile and debug port on demand.

Logs:
  $LOG_DIR/controlplane-ui.log
  $LOG_DIR/controlplane-api.log
  $LOG_DIR/capture-server.log

Stop everything started by this script:
  make dev-stop
EOF
