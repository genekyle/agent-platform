#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_DIR="$ROOT_DIR/.dev-pids"
TRAINING_CHROME_PROFILES_DIR="${TRAINING_CHROME_PROFILES_DIR:-/tmp/agent-platform-training-chrome}"

stop_service() {
  local name="$1"
  local pidfile="$PID_DIR/$name.pid"

  if [[ ! -f "$pidfile" ]]; then
    echo "$name not running"
    return
  fi

  local pid
  pid="$(cat "$pidfile")"
  if kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid" >/dev/null 2>&1 || true
    echo "Stopped $name (pid $pid)"
  else
    echo "$name pid file was stale"
  fi

  rm -f "$pidfile"
}

stop_service "controlplane-ui"
stop_service "capture-server"
stop_service "controlplane-api"

if pgrep -f "$TRAINING_CHROME_PROFILES_DIR" >/dev/null 2>&1; then
  pkill -f "$TRAINING_CHROME_PROFILES_DIR" >/dev/null 2>&1 || true
  echo "Stopped session-scoped Chrome processes"
else
  echo "No session-scoped Chrome processes found"
fi

echo "Stopping Postgres and Redis..."
(cd "$ROOT_DIR/infra" && docker compose down)
