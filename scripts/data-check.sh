#!/usr/bin/env bash
# Pre-flight for a LOW-DATA (roaming / hard-capped) session.
#
# Answers one question: "if I work now, will anything DOWNLOAD?" Everything this repo pulls
# is cached after a first warm run, so the danger is never the steady state — it is a
# re-trigger (a stale pip stamp, a cleared docker cache, a model id nobody fetched yet).
#
# Exits 0 when the machine is warm and it is safe to work on cellular; 1 when something
# listed below would hit the network, so you can wait for wifi.
#
# See docs/LOW_DATA_MODE.md for what is heavy vs safe, and why.

set -uo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RED=$'\033[0;31m'; GRN=$'\033[0;32m'; YEL=$'\033[0;33m'; OFF=$'\033[0m'
warn=0

ok()   { printf "  %sOK%s    %s\n" "$GRN" "$OFF" "$1"; }
bad()  { printf "  %sPULL%s  %s\n" "$RED" "$OFF" "$1"; warn=1; }
note() { printf "  %s..%s    %s\n" "$YEL" "$OFF" "$1"; }

echo "Low-data pre-flight — would anything download?"
echo

# 1. pip. bootstrap-python.sh re-runs `pip install` whenever any requirements file is newer
#    than .venv/.requirements.stamp — and `make dev` calls it unconditionally. Even fully
#    satisfied, that round-trips PyPI (and `pip install --upgrade pip` fetches).
STAMP="$ROOT_DIR/.venv/.requirements.stamp"
REQS=("apps/controlplane-api/requirements.txt" "apps/mcp/requirements.txt"
      "packages/interaction/pyproject.toml")
if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
  bad "no .venv — bootstrap would install EVERYTHING (torch is in there; ~1.3G)"
elif [[ ! -f "$STAMP" ]]; then
  bad "no requirements stamp — 'make dev' would re-run pip install"
else
  stale=()
  for f in "${REQS[@]}"; do [[ "$f" -nt "$STAMP" ]] && stale+=("$f"); done
  if (( ${#stale[@]} )); then
    bad "'make dev' would re-run pip install (newer than the stamp: ${stale[*]})"
    note "     if the deps are genuinely installed, 'touch $STAMP' and re-check"
  else
    ok "pip — stamp is fresh; 'make dev' will skip the install"
  fi
fi

# 2. npm ci — only fires when node_modules is absent, but then it is ~71M.
if [[ -d "$ROOT_DIR/apps/controlplane-ui/node_modules" ]]; then
  ok "npm — node_modules present; 'make dev' will skip npm ci"
else
  bad "npm — node_modules MISSING; 'make dev' runs npm ci (~71M)"
fi

# 3. docker. `make dev` always runs `docker compose up -d`; absent images are pulled.
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  missing=()
  for img in postgres:16 redis:7; do
    docker image inspect "$img" >/dev/null 2>&1 || missing+=("$img")
  done
  if (( ${#missing[@]} )); then
    bad "docker — would PULL: ${missing[*]} (~850M total)"
  else
    ok "docker — postgres:16 + redis:7 already local"
  fi
else
  note "docker not running — start it on wifi if the images aren't cached yet"
fi

# 4. Hugging Face weights. Nothing loads these during normal API/driving work, but a model
#    eval or the vision proposer will, and UGround alone is 4.1G.
HF="${HF_HOME:-$HOME/.cache/huggingface}/hub"
declare -a MODELS=("models--microsoft--Florence-2-base:Florence-2-base (444M)"
                   "models--microsoft--OmniParser-v2.0:OmniParser-v2.0 (1.0G)"
                   "models--osunlp--UGround-V1-2B:UGround-V1-2B (4.1G)")
for entry in "${MODELS[@]}"; do
  dir="${entry%%:*}"; label="${entry#*:}"
  if [[ -d "$HF/$dir" ]]; then ok "hf — $label cached"
  else bad "hf — $label NOT cached; loading it downloads the weights"; fi
done

echo
if (( warn )); then
  printf "%sWait for wifi%s — something above would download.\n" "$RED" "$OFF"
else
  printf "%sWarm.%s Safe to work on cellular.\n" "$GRN" "$OFF"
fi
# The ongoing cost this script CANNOT see, and the one that actually bites on a capped plan:
echo
echo "Still costs data even when warm (nothing here is cached):"
echo "  - Live browser driving. Every page load is real MB (an Indeed/KKR page ~5-20M)."
echo "    Read-only CDP against an ALREADY-OPEN tab is free — it's a local socket."
echo "    /navigate, a reload, and react-select typing (it fetches per keystroke) are not."
echo "  - git push/pull of source, and Anthropic API calls: KB. Negligible."
exit "$warn"
