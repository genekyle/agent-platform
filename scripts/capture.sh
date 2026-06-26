#!/bin/bash
# Supervised capture + auto page-state classify for a training session.
#
# Usage: scripts/capture.sh <session_id> [url_filter] [tab_tag]
#   session_id  training session whose Chrome to capture from (e.g. 9 = indeed)
#   url_filter  substring to pick the tab (e.g. "indeed.com", "mail.google"); omit = first tab
#   tab_tag     short label written to the capture's notes for multi-tab provenance
#               (e.g. gmail, indeed) — see the multi-tab-vs-multi-browser decision.
#
# Flow: activate the target tab in CDP -> POST /api/capture -> POST suggest_page_state
# (writes observed_page_state when Haiku is >=0.9 confident on a KNOWN state, else
# leaves a read-only suggestion). Prints a one-line result.
#
# Env: API (control-plane base, default http://localhost:8081)
set -euo pipefail
API="${API:-http://localhost:8081}"
SID="${1:?session_id required}"; FILT="${2:-}"; TAG="${3:-}"

TAB=$(curl -s -m 5 "${API}/api/training/sessions/${SID}/tabs" | python3 -c "
import sys,json
d=json.load(sys.stdin)
f='${FILT}'
t=[x for x in d if (not f or f in x['url'])]
if not t: print('NO_TAB'); sys.exit(1)
print(t[0]['id']+'|'+t[0]['url'])
")
[ "$TAB" = "NO_TAB" ] && { echo "no tab matching filter='${FILT}' in session ${SID}"; exit 1; }
TID="${TAB%%|*}"; TURL="${TAB##*|}"

# Activate the target tab so chrome-devtools-mcp follows the active tab.
PORT=$(curl -s "${API}/api/training/sessions/${SID}" | python3 -c "import sys,json;print(json.load(sys.stdin).get('chrome_debug_port',9222))")
curl -s "http://127.0.0.1:${PORT}/json/activate/${TID}" >/dev/null || true
sleep 0.4

RESP=$(curl -s -m 60 -X POST "${API}/api/capture" -H "Content-Type: application/json" \
  -d "{\"training_session_id\":${SID},\"tab_id\":\"$TID\",\"tab_url\":\"$TURL\"}")
FN=$(echo "$RESP" | python3 -c "import sys,json;print(json.load(sys.stdin).get('filename',''))")
CC=$(echo "$RESP" | python3 -c "import sys,json;print(json.load(sys.stdin).get('candidate_count','?'))")
[ -z "$FN" ] && { echo "capture failed: $RESP"; exit 1; }

ENC=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$FN")
PS=$(curl -s -m 30 -X POST "${API}/api/training/suggest_page_state?filename=${ENC}&write=true")
if [ -n "$TAG" ]; then
  curl -s -X PATCH "${API}/api/observations/${ENC}" -H "Content-Type: application/json" \
    -d "{\"training_annotation\":{\"notes\":\"tab=${TAG}\"}}" >/dev/null || true
fi
echo "$PS" | python3 -c "
import json,sys
d=json.load(sys.stdin); s=d['suggestion']
print(f\"  [${TAG:-?}] {'${TURL}'[:78]}\")
print(f\"  cands={${CC}}  state={s['state_id'] or '(none)'}  conf={s['confidence']}  is_new={s['is_new']}  written={d['written']}  \${s['cost_usd']:.4f}\")
if s['is_new']: print(f\"  proposed_name: {s['proposed_name']}\")
"
