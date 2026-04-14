#!/usr/bin/env bash
set -euo pipefail

check_url() {
  local label="$1"
  local url="$2"
  if curl -fsS "$url" >/dev/null 2>&1; then
    echo "[ok]   $label -> $url"
  else
    echo "[down] $label -> $url"
  fi
}

check_port() {
  local label="$1"
  local host="$2"
  local port="$3"
  if nc -z "$host" "$port" >/dev/null 2>&1; then
    echo "[ok]   $label -> $host:$port"
  else
    echo "[down] $label -> $host:$port"
  fi
}

check_url "UI" "http://localhost:5173"
check_url "Control API" "http://localhost:8081/health"
check_url "Capture API" "http://localhost:8082/health"
check_port "Postgres" "localhost" "5433"
check_port "Redis" "localhost" "6379"
echo "[info] Training Chrome is session-scoped and only appears after starting a training session."
