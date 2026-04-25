#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
STAMP_FILE="$VENV_DIR/.requirements.stamp"
REQUIREMENTS_FILES=(
  "$ROOT_DIR/apps/controlplane-api/requirements.txt"
  "$ROOT_DIR/apps/mcp-mock/requirements.txt"
)

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

requirements_changed() {
  if [[ ! -f "$STAMP_FILE" ]]; then
    return 0
  fi

  local requirements_file
  for requirements_file in "${REQUIREMENTS_FILES[@]}"; do
    if [[ "$requirements_file" -nt "$STAMP_FILE" ]]; then
      return 0
    fi
  done

  return 1
}

require_cmd python3

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Creating repo virtual environment at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

if requirements_changed; then
  echo "Installing Python dependencies into $VENV_DIR"
  "$VENV_PYTHON" -m pip install --upgrade pip
  "$VENV_PYTHON" -m pip install \
    -r "$ROOT_DIR/apps/controlplane-api/requirements.txt" \
    -r "$ROOT_DIR/apps/mcp-mock/requirements.txt"
  touch "$STAMP_FILE"
fi

echo "Python environment ready: $VENV_PYTHON"
