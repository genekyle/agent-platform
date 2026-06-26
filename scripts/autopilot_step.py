#!/usr/bin/env python3
"""Supervised autopilot: ONE step of capture -> classify -> select -> (execute) -> verify.

The interim, human-supervised live loop. For one tab it:
  1. captures the current page (+ auto page-state classify via the Haiku teacher)
  2. runs the SELECT cascade (cache -> Haiku SoM) to ground the goal to an element
  3. EXECUTES the pick via the CDP DirectDriver — but ONLY when it's confident, not an
     escalation, and not a final-submit-like control (those HARD-STOP for a human)
  4. re-captures and verifies the action advanced the page

Prints a single structured block. Designed to be run once per step so a supervisor
(Claude or the operator) reads the result and decides whether to continue.

Usage:
  scripts/autopilot_step.py --session 9 --filter smartapply --goal "click Continue to advance the application"
  add --go to allow execution (default is PREVIEW: select but do not act)
"""
import argparse, json, re, sys, time, urllib.parse, urllib.request

API = "http://localhost:8081"
# Never auto-click these — final/irreversible or money/identity actions. Human only.
SUBMIT_GUARD = re.compile(r"\b(submit|send application|apply now|pay|confirm|delete|withdraw)\b", re.I)
MIN_CONF = 0.6


def _post(path, body=None, params=None):
    url = f"{API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())


def _get(path):
    with urllib.request.urlopen(f"{API}{path}", timeout=30) as r:
        return json.loads(r.read())


def _pick_tab(session, filt):
    tabs = _get(f"/api/training/sessions/{session}/tabs")
    for t in tabs:
        if not filt or filt in t["url"]:
            return t
    return None


def _session_port(session):
    for s in _get("/api/training/sessions"):
        if s.get("id") == session:
            return s.get("chrome_debug_port", 9222)
    return 9222


def _activate(session, tab_id):
    port = _session_port(session)
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/activate/{tab_id}", timeout=5).read()
    except Exception:
        pass


def _capture(session, tab):
    return _post("/api/capture", {"training_session_id": session, "tab_id": tab["id"], "tab_url": tab["url"]})


def _classify(fn):
    enc = urllib.parse.quote(fn)
    try:
        return _post(f"/api/training/suggest_page_state", params={"filename": fn, "write": "true"})
    except Exception as e:
        return {"suggestion": {"state_id": f"(classify err: {e})", "confidence": 0}}


def _resolve_candidate_id(fn, backend_node_id):
    import os
    # sidecar lives under the capture artifacts dir; read via the API's observation? simplest: read file
    # The control-plane exposes it indirectly; here we read the on-disk sidecar.
    base = os.environ.get("AX_TRACES", "apps/mcp-mock/output/observer-traces")
    path = f"{base}/{fn}.ax.json"
    try:
        props = json.load(open(path)).get("proposals", [])
    except Exception:
        return None, None
    for c in props:
        if c.get("backend_node_id") == backend_node_id:
            return c.get("candidate_id"), c.get("bbox")
    return None, None


def step(session, filt, goal, go):
    out = {"goal": goal}
    tab = _pick_tab(session, filt)
    if not tab:
        return {"status": "ERROR", "reason": f"no tab matching {filt!r}"}
    _activate(session, tab["id"])
    time.sleep(0.4)

    cap = _capture(session, tab)
    fn = cap.get("filename")
    out["url"] = tab["url"][:90]
    out["candidates"] = cap.get("candidate_count")
    cls = _classify(fn)["suggestion"]
    out["state"] = f"{cls.get('state_id')} ({cls.get('confidence')})"

    sel = _post(f"/api/observations/{urllib.parse.quote(fn)}/select", params={"element_query": goal})
    cand = sel.get("candidate") or {}
    out["pick"] = {"name": cand.get("name"), "role": cand.get("role"),
                   "action": sel.get("action_id"), "layer": sel.get("layer"),
                   "conf": sel.get("confidence"), "needs_human": sel.get("needs_human"),
                   "reason": sel.get("reason_code")}

    # --- escalation gates -----------------------------------------------------
    name = (cand.get("name") or "")
    if sel.get("needs_human") or sel.get("status") == "escalate":
        out["status"] = "PAUSE_ESCALATE"; out["why"] = sel.get("reason_code"); return out
    if not cand or sel.get("target_backend_node_id") is None:
        out["status"] = "PAUSE_NO_PICK"; out["why"] = sel.get("reason_code"); return out
    if (sel.get("confidence") or 0) < MIN_CONF:
        out["status"] = "PAUSE_LOW_CONF"; return out
    if SUBMIT_GUARD.search(name):
        out["status"] = "PAUSE_SUBMIT_GUARD"; out["why"] = f"{name!r} looks irreversible — human confirm"; return out
    if not go:
        out["status"] = "PREVIEW_ONLY (pass --go to execute)"; return out

    # --- execute --------------------------------------------------------------
    cid, _ = _resolve_candidate_id(fn, sel["target_backend_node_id"])
    if not cid:
        out["status"] = "PAUSE_NO_CANDIDATE_MAP"; return out
    ex = _post("/api/runtime/execute", {
        "training_session_id": session, "tab_id": tab["id"], "action_id": sel.get("action_id", "click"),
        "filename": fn, "candidate_id": cid})
    out["execute"] = {"ok": ex.get("ok"), "css": ex.get("css_point")}
    time.sleep(1.6)

    # --- verify (re-capture + before/after) -----------------------------------
    tab2 = _pick_tab(session, filt) or tab
    cap2 = _capture(session, tab2)
    fn2 = cap2.get("filename")
    ver = _post("/api/observations/verify", params={"before": fn, "after": fn2,
                "action_id": sel.get("action_id", "click")})
    out["verify"] = {"ok": ver.get("ok"), "observed": ver.get("observed"), "next": ver.get("next_step")}
    out["new_url"] = (tab2["url"] if tab2 else "")[:90]
    out["status"] = "ADVANCED" if ver.get("ok") else "EXECUTED_NO_CHANGE"
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--session", type=int, required=True)
    p.add_argument("--filter", default="")
    p.add_argument("--goal", required=True)
    p.add_argument("--go", action="store_true", help="actually execute (default: preview)")
    a = p.parse_args()
    print(json.dumps(step(a.session, a.filter, a.goal, a.go), indent=2))
