from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.artifacts import ARTIFACTS_DIR, SCREENSHOTS_DIR, write_observation_artifact
from app.event_log import log_event as _log_event
from app.main import observe_live_capture
from app.observer.ax_proposer import MODEL_VERSION as AX_MODEL_VERSION
from app.observer.ax_proposer import AXProposerStats, propose_ax_candidates
from app.observer.vision_proposer import MODEL_VERSION, propose_candidates


logger = logging.getLogger("mcp.proposer")

app = FastAPI(title="Agent Platform MCP — CDP browser driver + capture server", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _vision_sidecar_path(artifact_filename: str) -> Path:
    """Sidecar lives next to the artifact, named <artifact>.vision.json.

    Keeps the raw artifact immutable while still letting the controlplane-api
    surface vision candidates via the existing GET /api/observations/{filename}.
    """
    return ARTIFACTS_DIR / f"{artifact_filename}.vision.json"


def _screenshot_filename_from_artifact(artifact: dict) -> Optional[str]:
    """Pull the first screenshot filename out of an artifact dict.

    Returns None if the artifact has no screenshot — the proposer needs one
    to do anything useful.
    """
    shots = artifact.get("acquisition", {}).get("screenshots") or []
    if not shots:
        return None
    return shots[0].get("filename")


def _write_vision_sidecar(artifact_filename: str, screenshot_filename: str, proposals: list[dict],
                          timing: dict | None = None) -> Path:
    sidecar = {
        "version": MODEL_VERSION,
        "artifact_filename": artifact_filename,
        "screenshot_filename": screenshot_filename,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "proposal_count": len(proposals),
        "timing": timing or {},
        "proposals": proposals,
    }
    path = _vision_sidecar_path(artifact_filename)
    path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return path


# --- CDP-AX proposer (the PRIMARY candidate source) -------------------------
# Unlike the vision proposer (runs on the saved screenshot anytime), CDP-AX needs
# the LIVE browser, so it runs at capture-time here and is persisted to a sidecar
# the controlplane-api surfaces alongside the artifact.
def _ax_sidecar_path(artifact_filename: str) -> Path:
    return ARTIFACTS_DIR / f"{artifact_filename}.ax.json"


def _device_scale_factor_from_artifact(artifact: dict) -> float:
    acq = artifact.get("acquisition", {}) or {}
    for src in (acq.get("training_metadata") or {}, acq.get("viewport_state") or {}):
        value = src.get("device_scale_factor")
        if value:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return 1.0


def _write_ax_sidecar(artifact_filename: str, proposals: list[dict], stats: AXProposerStats | None = None) -> Path:
    sidecar = {
        "version": AX_MODEL_VERSION,
        "artifact_filename": artifact_filename,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "proposal_count": len(proposals),
        "stats": stats.__dict__ if stats is not None else {},
        "proposals": proposals,
    }
    path = _ax_sidecar_path(artifact_filename)
    path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return path


def _backfill_vision_candidates(artifact_filename: str, screenshot_filename: str) -> None:
    """Background-task entry point: run the proposer and persist the sidecar.

    Exceptions are caught and logged, never raised — this runs after the
    capture HTTP response is sent, so there's no caller to handle errors.
    A missing sidecar simply means "no vision candidates yet" on the read path.
    """
    try:
        screenshot_path = SCREENSHOTS_DIR / screenshot_filename
        timing: dict = {}
        proposals = propose_candidates(screenshot_path, stats=timing)
        sidecar_path = _write_vision_sidecar(artifact_filename, screenshot_filename, proposals, timing)
        logger.info(
            "vision backfill ok: %s -> %d proposals in %dms (%s)",
            artifact_filename, len(proposals), timing.get("total_ms", 0), sidecar_path.name,
        )
    except Exception:
        logger.exception("vision backfill failed for %s", artifact_filename)


class CaptureRequest(BaseModel):
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    scenario: str = "live_capture"
    task_context: Optional[dict] = None
    training_metadata: Optional[dict] = None
    browser_url: str = "http://127.0.0.1:9222"


class ProposerPredictRequest(BaseModel):
    """In-process call: just run the proposer on a screenshot, don't persist anything."""
    screenshot_filename: str


class ExecuteRequest(BaseModel):
    """Interim executor handoff (v1 — CDP DirectDriver, the pre-diffusion-mouse bridge).
    target_bbox is in SCREENSHOT pixels (as the AX proposer emits); device_scale_factor
    converts to CSS px for CDP input. driver defaults to 'direct' (record_only = dry-run)."""
    action_id: str                       # click | type | select | clear | upload
    target_bbox: dict                    # {x, y, width, height} screenshot px
    value: Optional[str] = None
    backend_node_id: Optional[int] = None
    files: Optional[list[str]] = None    # absolute local paths for an `upload` action
    device_scale_factor: float = 1.0
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    browser_url: str = "http://127.0.0.1:9222"
    driver: Optional[str] = None         # 'direct' (default) | 'record_only' (dry-run)
    # Act-by-NAME: when set, re-resolve the target's backend_node_id from a FRESH AX scan at act time
    # (matched by role + accessible-name), immune to the node-id churn that makes a captured id stale
    # between select and act. Falls back to backend_node_id when not provided / not found.
    target_role: Optional[str] = None
    target_name: Optional[str] = None
    # CSS selector re-resolution at act time — for elements the AX tree can't name (e.g. a HIDDEN
    # <input type=file> behind an "Add photos" button, the upload target). Resolved fresh via
    # DOM.querySelector. Used when target_name isn't given.
    selector: Optional[str] = None


async def _resolve_ax_node(browser_url: str, tab_id: Optional[str], tab_url: Optional[str],
                           role: Optional[str], name: str) -> Optional[int]:
    """FRESH backend_node_id for a target described by role + accessible-name, scanned at act time.
    Exact name match wins, then substring; role-gated when a role is given. None if not found. This
    is the fix for stale node ids — the same CDP-AX proposer the capture uses, re-run just-in-time."""
    from app.observer.ax_proposer import AXProposerStats, propose_ax_candidates
    cands = await propose_ax_candidates(browser_url=browser_url, tab_id=tab_id, tab_url=tab_url,
                                        device_scale_factor=1.0, stats=AXProposerStats())
    want_role = (role or "").strip().lower()
    want = (name or "").strip().lower()

    def role_ok(c: dict) -> bool:
        return not want_role or (c.get("role") or "").strip().lower() == want_role

    def nm(c: dict) -> str:
        return (c.get("caption") or c.get("name") or "").strip().lower()

    exact = [c for c in cands if role_ok(c) and nm(c) == want]
    hit = exact[0] if exact else next((c for c in cands if role_ok(c) and want and want in nm(c)), None)
    return hit.get("backend_node_id") if hit else None


async def _resolve_node_by_selector(browser_url: str, tab_id: Optional[str], tab_url: Optional[str],
                                    selector: str) -> Optional[int]:
    """FRESH backend_node_id for a CSS selector, at act time — for elements with no accessible name
    (a hidden file input). Uses DOM.querySelector; returns None if not present."""
    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target
    try:
        target = await _discover_target(browser_url, tab_id=tab_id, tab_url=tab_url)
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024) as ws:
            cdp = _CDPSession(ws)
            await cdp.send("DOM.enable")
            doc = await cdp.send("DOM.getDocument", {"depth": 0})
            root = (doc.get("root") or {}).get("nodeId")
            found = await cdp.send("DOM.querySelector", {"nodeId": root, "selector": selector})
            node_id = found.get("nodeId")
            if not node_id:
                return None
            desc = await cdp.send("DOM.describeNode", {"nodeId": node_id})
            return (desc.get("node") or {}).get("backendNodeId")
    except Exception as exc:  # noqa: BLE001
        logger.warning("selector resolve failed for %r: %s", selector, exc)
        return None


@app.post("/execute")
async def execute_action(body: ExecuteRequest):
    """INTERIM EXECUTOR (v1): perform one resolved action against the live page via the raw-CDP
    DirectDriver. Actions: click | type | select | clear | upload. When `target_name` is given, the
    node is RE-RESOLVED from a fresh AX scan at act time (immune to node-id staleness); otherwise
    `backend_node_id` is used. `files` (absolute paths) drive an `upload` onto a file input. Returns
    the ExecResult. Best-effort; never raises into the caller."""
    from app.executor.driver import ActionRequest, get_driver

    node_id = body.backend_node_id
    note = ""
    if body.target_name:
        fresh = await _resolve_ax_node(body.browser_url, body.tab_id, body.tab_url,
                                       body.target_role, body.target_name)
        if fresh is not None:
            node_id, note = fresh, f"re-resolved {body.target_name!r} -> node {fresh}"
        elif node_id is None:
            return {"ok": False, "driver": body.driver or "direct", "action_id": body.action_id,
                    "css_point": None,
                    "detail": f"target not found by name: {body.target_name!r} (role={body.target_role})"}
    elif body.selector:
        fresh = await _resolve_node_by_selector(body.browser_url, body.tab_id, body.tab_url, body.selector)
        if fresh is not None:
            node_id, note = fresh, f"re-resolved {body.selector!r} -> node {fresh}"
        elif node_id is None:
            return {"ok": False, "driver": body.driver or "direct", "action_id": body.action_id,
                    "css_point": None, "detail": f"target not found by selector: {body.selector!r}"}

    driver = get_driver(body.driver)
    req = ActionRequest(
        action_id=body.action_id, target_bbox=body.target_bbox, value=body.value,
        backend_node_id=node_id, files=body.files, device_scale_factor=body.device_scale_factor,
    )
    result = await driver.move_and_act(
        browser_url=body.browser_url, request=req, tab_id=body.tab_id, tab_url=body.tab_url)
    _tgt = body.target_name or body.selector or (f"node {node_id}" if node_id else "")
    _log_event("drive", f"{body.action_id} {_tgt}".strip()[:90],
               detail=(f"{'ok' if result.ok else 'FAIL'} · {body.tab_url or ''}"), domain=body.tab_url)
    return {
        "ok": result.ok, "driver": result.driver, "action_id": result.action_id,
        "css_point": result.css_point, "detail": (note + ("; " if note and result.detail else "") + result.detail),
    }


# --- Reusable ATOMIC action: select from a Workday hierarchical PROMPT ("How Did You Hear About Us?",
# etc.). Standard type/click/select do NOT work on these — the options render in a portal and only
# register TRUSTED CDP mouse events (JS .click() and coordinate-less selects are ignored). This action
# encodes the reliable HOW: open the field → type into the *visible* searchBox (scoped to the open
# popup, not another prompt's) → trusted-click the matching promptOption. It's the prompt analogue of
# human_click/human_type. Wired into WORKDAY recipes so every tenant's nested prompts reuse it. -------
class SelectPromptRequest(BaseModel):
    browser_url: str = "http://127.0.0.1:9222"
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    field_role: Optional[str] = "textbox"
    field_name: str            # accessible name of the prompt field, e.g. "How Did You Hear About Us?"
    value: str                 # the leaf to select, e.g. "Indeed" (searched across the hierarchy)
    settle_seconds: float = 0.8


# Find the VISIBLE searchBox (the one in the currently-open prompt popup) — offsetParent guards against
# the other prompts' hidden search inputs. Returns its viewport-center in CSS px.
_PROMPT_SEARCHBOX_JS = r"""
(() => {
  const b=[...document.querySelectorAll("input[data-automation-id='searchBox']")].find(e=>e.offsetParent && e.getClientRects().length);
  if(!b) return {found:false};
  b.scrollIntoView({block:'center'});
  const r=b.getBoundingClientRect();
  return {found:true, x:r.x+r.width/2, y:r.y+r.height/2};
})()
"""


def _prompt_option_js(value: str) -> str:
    """JS returning the VISIBLE promptOption matching `value` (exact, then substring; case-insensitive)
    — center in CSS px. Includes a sample of what's visible when no match, for debugging."""
    v = json.dumps((value or "").strip().lower())
    return (
        "(() => {"
        f"  const want={v};"
        "  const opts=[...document.querySelectorAll(\"[data-automation-id='promptOption'],[data-automation-id='menuItem'],[role='option']\")]"
        "    .filter(e=>e.offsetParent && e.getClientRects().length);"
        "  let el=opts.find(e=>(e.textContent||'').trim().toLowerCase()===want)"
        "     || opts.find(e=>(e.textContent||'').trim().toLowerCase().includes(want));"
        "  if(!el) return {found:false, count:opts.length, sample:opts.slice(0,10).map(e=>(e.textContent||'').trim())};"
        "  el.scrollIntoView({block:'center'});"
        "  const r=el.getBoundingClientRect();"
        "  return {found:true, x:r.x+r.width/2, y:r.y+r.height/2, text:(el.textContent||'').trim()};"
        "})()"
    )


async def _trusted_click(cdp, x: float, y: float) -> None:
    """A real (isTrusted) CDP mouse click at CSS-px (x, y) — what Workday's prompt options require."""
    for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
        ev = {"type": typ, "x": x, "y": y}
        if typ != "mouseMoved":
            ev.update({"button": "left", "clickCount": 1})
        await cdp.send("Input.dispatchMouseEvent", ev)


@app.post("/select_prompt")
async def select_prompt(body: SelectPromptRequest):
    """Atomic Workday prompt-select: open the field → search → trusted-click the matching option.
    Returns {ok, selected, detail}. Best-effort; never raises. Reusable across all Workday prompts."""
    import asyncio
    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target
    try:
        node_id = await _resolve_ax_node(body.browser_url, body.tab_id, body.tab_url,
                                         body.field_role, body.field_name)
        if node_id is None:
            return {"ok": False, "detail": f"prompt field not found: {body.field_name!r}"}
        # 1. OPEN the prompt via the proven driver node-click (same path /execute uses) — a
        # trusted-mouse-at-box-center did NOT reliably open Workday prompt popups.
        from app.executor.driver import ActionRequest, get_driver
        await get_driver("direct").move_and_act(
            browser_url=body.browser_url,
            request=ActionRequest(action_id="click", target_bbox={}, backend_node_id=node_id),
            tab_id=body.tab_id, tab_url=body.tab_url)
        await asyncio.sleep(max(0.5, min(body.settle_seconds, 4.0)))
        target = await _discover_target(body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url)
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=16 * 1024 * 1024) as ws:
            cdp = _CDPSession(ws)
            await cdp.send("Page.enable", {})
            # 2. type the value into the visible searchBox (scoped to the open popup)
            sb = (await cdp.send("Runtime.evaluate", {"expression": _PROMPT_SEARCHBOX_JS,
                                                      "returnByValue": True})).get("result", {}).get("value") or {}
            if sb.get("found"):
                await _trusted_click(cdp, sb["x"], sb["y"])   # focus the searchBox → it's activeElement
                await asyncio.sleep(0.2)
                # TRUSTED per-char key events — Workday's prompt search FETCHES results server-side on
                # real keystrokes; a programmatic value-set (or insertText) does NOT trigger the fetch,
                # so nothing appears. Clear first, then type each char with keyDown(text)+keyUp.
                await cdp.send("Runtime.evaluate", {"expression":
                    "(()=>{const el=document.activeElement; if(el&&el.value){el.value='';"
                    "el.dispatchEvent(new Event('input',{bubbles:true}));}})()"})
                for ch in body.value:
                    await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "text": ch,
                                                              "key": ch, "unmodifiedText": ch})
                    await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch})
                    await asyncio.sleep(0.05)
                await asyncio.sleep(1.3)   # Workday's debounced search fetch
        # 3. resolve the option by ACCESSIBLE NAME and NATIVE-click it. Coordinate clicks mis-fire on
        # long/virtualized lists (picked "American Samoa" for "New Hampshire"); native node-click is the
        # reliable primitive. No option found here usually means a stale session (refresh first).
        opt_node = None
        for _ in range(6):
            opt_node = await _resolve_ax_node(body.browser_url, body.tab_id, body.tab_url, None, body.value)
            if opt_node is not None:
                break
            await asyncio.sleep(0.4)
        if opt_node is None:
            return {"ok": False, "detail": f"option {body.value!r} not found "
                    f"(searchBox={'yes' if sb.get('found') else 'no'}; refresh if the session is stale)"}
        await get_driver("direct").move_and_act(
            browser_url=body.browser_url,
            request=ActionRequest(action_id="click", target_bbox={}, backend_node_id=opt_node),
            tab_id=body.tab_id, tab_url=body.tab_url)
        await asyncio.sleep(0.4)
        _log_event("drive", f"prompt-select '{body.field_name}' <- {body.value}",
                   detail=f"searchBox={'yes' if sb.get('found') else 'no'}", domain=body.tab_url)
        return {"ok": True, "selected": body.value, "detail": f"searchBox={'yes' if sb.get('found') else 'no'}"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("select_prompt failed: %s", exc)
        return {"ok": False, "detail": str(exc)}


class EvalRequest(BaseModel):
    """DEBUG: run a JS expression in the tab and return its value. For building/tuning actions (e.g.
    inspecting a Workday prompt popup). Local dev tool — the MCP already fully drives the browser."""
    browser_url: str = "http://127.0.0.1:9222"
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    expression: str


@app.post("/eval")
async def eval_js(body: EvalRequest):
    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target
    try:
        target = await _discover_target(body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url)
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=16 * 1024 * 1024) as ws:
            cdp = _CDPSession(ws)
            res = await cdp.send("Runtime.evaluate", {"expression": body.expression,
                                                      "returnByValue": True, "awaitPromise": True})
        return {"ok": True, "value": (res.get("result") or {}).get("value"),
                "exception": res.get("exceptionDetails")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)}


class ExtractJobsRequest(BaseModel):
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    browser_url: str = "http://127.0.0.1:9222"


# Indeed search-result job-card extractor. Pulls (jk, title, company, location, url) from
# every [data-jk] card. Kept as a string so it runs in the page; resilient to Indeed's
# shifting class names by trying several selectors per field.
_INDEED_JOBS_JS = r"""
(() => {
  const isLoc = (s) => /(,\s*[A-Z]{2}\b)|\bRemote\b|\bHybrid\b|United States|\b\d{5}\b/.test(s);
  const noise = (s) => s === '·' || s === 'New' || /^\d+\s*(min|hour|day|week|month)/i.test(s)
                       || /^(Easily apply|Urgently hiring|Hiring multiple|Responded to|Often replies|Multiple openings|Posted|Active|\+\d+|View all)/i.test(s);
  // Indeed plants HIDDEN 0x0 decoy cards (e.g. id=job_fedcba9876543210) alongside the real ones —
  // same trap as smartapply's width-0 duplicate Continue: only ever trust the VISIBLE node.
  const visible = (el) => {
    if (el.offsetParent === null) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const anchors = Array.from(document.querySelectorAll('a[data-jk], [data-jk]'));
  const seen = new Set();
  const out = [];
  for (const a of anchors) {
    const jk = a.getAttribute('data-jk');
    if (!jk || seen.has(jk)) continue;
    if (!visible(a)) continue;
    seen.add(jk);
    const titleEl = a.querySelector('span[title]') || a;
    const title = (titleEl.getAttribute && titleEl.getAttribute('title') || titleEl.innerText || '').trim();
    const card = a.closest('li') || a.closest('.cardOutline') || a.closest('[class*=card]') || a.parentElement;
    const lines = (card ? card.innerText : '').split('\n').map(s => s.trim()).filter(Boolean);
    const ti = Math.max(0, lines.findIndex(l => l && title.startsWith(l.slice(0, 12))));
    let company = '', location = '', salary = '';
    for (let i = ti + 1; i < lines.length; i++) {
      const l = lines[i];
      if (noise(l)) continue;
      if (!salary && /\$|per (hour|year)|a year|an hour/i.test(l)) { salary = l; continue; }
      if (!location && isLoc(l)) { location = l; continue; }
      if (!company && l !== title) { company = l; continue; }
      if (company && location) break;
    }
    const url = a.href || (a.querySelector('a[href]') || {}).href || '';
    out.push({ external_id: jk, title, company, location, salary, url });
  }
  // SEARCH META — the query's size, findable on the first results page (targeted_search_and_apply
  // records it per query): the "N jobs" total + the pagination numbers visible on this page.
  const countEl = document.querySelector(
    '.jobsearch-JobCountAndSortPane-jobCount, [class*="jobCount"], [data-testid="searchResults-header"]');
  let totalText = countEl ? (countEl.innerText || '').trim() : '';
  if (!totalText) {
    // Must stay on ONE line: `\s` spans newlines, so the filter chips ("Distance\n1" + "Job Type")
    // used to parse as "1 job" and report total_results=1 for a full page of hits. Indeed often
    // renders no count at all now — null is the honest answer, not a number scraped from a badge.
    const m = (document.body.innerText || '').match(/\b[\d,]+\+?[^\S\n]+jobs\b/i);
    totalText = m ? m[0] : '';
  }
  const totalMatch = totalText.match(/([\d,]+)[^\S\n]*\+?/);
  const total_results = totalMatch ? parseInt(totalMatch[1].replace(/,/g, ''), 10) : null;
  const pageEls = [...document.querySelectorAll('a[data-testid^="pagination-page-"], nav[aria-label*="pag" i] a')];
  const visible_pages = [...new Set(pageEls.map(e => parseInt((e.innerText || '').trim(), 10))
    .filter(n => !isNaN(n)))].sort((a, b) => a - b);
  const start = parseInt(new URLSearchParams(location.search).get('start') || '0', 10);
  const meta = {
    total_results, total_text: totalText.slice(0, 40),
    current_page: isNaN(start) ? 1 : Math.floor(start / 10) + 1,
    visible_pages, has_next: !!document.querySelector('a[data-testid="pagination-page-next"]'),
  };
  return { jobs: out, meta };
})()
"""


@app.post("/extract_jobs")
async def extract_jobs(body: ExtractJobsRequest):
    """Scrape the live Indeed results DOM for job cards (data-jk). Returns the raw list;
    the control plane dedupes + persists. Best-effort — returns [] on any failure."""
    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target
    try:
        target = await _discover_target(body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url)
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024) as ws:
            cdp = _CDPSession(ws)
            res = await cdp.send("Runtime.evaluate",
                                 {"expression": _INDEED_JOBS_JS, "returnByValue": True})
        val = (res.get("result") or {}).get("value") or {}
        jobs = val.get("jobs", val if isinstance(val, list) else [])
        meta = val.get("meta") if isinstance(val, dict) else None
        return {"ok": True, "jobs": jobs, "count": len(jobs), "meta": meta,
                "url": target.get("url", "")}
    except Exception as exc:  # noqa: BLE001
        logger.warning("extract_jobs failed: %s", exc)
        return {"ok": False, "jobs": [], "count": 0, "detail": str(exc)}


class AutofillFormRequest(BaseModel):
    answers: list[dict]                  # [{key,value,options[],patterns[]}] from the profile
    browser_url: str = "http://127.0.0.1:9222"
    tab_url: Optional[str] = "smartapply"


# The TYPE-GENERALIZING form filler. Indeed's same-site form BUILDER lets employers pick the
# element type per question (radio / dropdown / free text / number / checkbox), so the SAME
# question appears as different elements across employers. This matches each question to a
# profile answer (semantics), then dispatches by the element type actually present — so we
# never need per-question nested if/else logic. Radio/select/text/number are filled in one
# pass; ARIA comboboxes are returned for the executor's open+pick (async). Returns a report.
_AUTOFILL_JS = r"""
(answers) => {
  const STOP = new Set(['what','is','are','your','the','a','an','do','you','have','to','of',
    'for','in','on','please','select','choose','enter','provide','this','will','can','we','or','and']);
  const toks = s => ((s||'').toLowerCase().match(/[a-z0-9]+/g)||[]).filter(t=>!STOP.has(t)&&t.length>1);
  function match(qtext){
    const q=(qtext||'').toLowerCase(); const qt=new Set(toks(q));
    let best=null,bs=0;
    for(const a of answers){
      let s=0;
      for(const p of (a.patterns||[])){ const pl=p.toLowerCase();
        if(pl && q.includes(pl)) s+=3; else { s += toks(p).filter(t=>qt.has(t)).length; } }
      if(s>bs){bs=s;best=a;}
    }
    return bs>=2?best:null;
  }
  function setNativeValue(el,val){
    const proto = el.tagName==='TEXTAREA'?window.HTMLTextAreaElement.prototype:window.HTMLInputElement.prototype;
    const setter=Object.getOwnPropertyDescriptor(proto,'value').set; setter.call(el,val);
    el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));
  }
  const containers=[...document.querySelectorAll('.ia-Questions-item,[class*=Questions-item]')];
  const report=[]; const combos=[];
  // Match a native <select> to whichever answer's value/options appear in its option list —
  // so MULTI-FIELD containers (e.g. one "Country / State" item with BOTH a Country and a State
  // select) each get the right answer, instead of only the first field. This is the fix for the
  // missed required State field (single-field assumption hid the empty second select).
  function fillSelect(s, qtext){
    for(const ans of answers){
      const w=((ans.options&&ans.options.length?ans.options:[ans.value])).map(x=>(x||'').toLowerCase()).filter(x=>x.length>1);
      const o=[...s.options].find(opt=>w.some(ww=>opt.text.toLowerCase().includes(ww)));
      if(o){o.selected=true;s.value=o.value;s.dispatchEvent(new Event('change',{bubbles:true}));
        return {q:qtext.slice(0,45),key:ans.key,via:'select',status:'filled'};}
    }
    return {q:qtext.slice(0,45),via:'select',status:'no_option'};
  }
  for(const c of containers){
    const qtext=(c.innerText||'').replace(/\s+/g,' ').trim();
    if(qtext.length<5) continue;
    const radios=[...c.querySelectorAll('[role=radio],input[type=radio]')];
    const selects=[...c.querySelectorAll('select')];
    const aria=[...c.querySelectorAll('[role=combobox]')];
    const texts=[...c.querySelectorAll('input[type=text],input[type=number],input:not([type]),textarea')];
    const checks=[...c.querySelectorAll('input[type=checkbox]')];
    try {
      // SELECTS: per-element (multi-field aware) — every select in the container.
      let didSelect=false;
      for(const s of selects){ report.push(fillSelect(s, qtext)); didSelect=true; }
      // RADIOS / ARIA / TEXT / CHECKBOX: one logical field per container → container question.
      const a=(radios.length||aria.length||texts.length||checks.length)?match(qtext):null;
      if(a){
        const want=((a.options&&a.options.length?a.options:[a.value])).map(x=>(x||'').toLowerCase());
        if(radios.length){
          let r=radios.find(x=>{let l=(x.closest('label')?x.closest('label').innerText:(x.getAttribute('aria-label')||'')).toLowerCase().trim();
            return want.some(w=>l===w||l.startsWith(w)||l.includes(w));});
          if(!r && radios.length===1 && (a.category==='acknowledgment' || /^(yes|i agree|i accept|accept|i have read|i acknowledge)/i.test(a.value||''))) r=radios[0];
          if(r){r.scrollIntoView({block:'center'});r.click();report.push({q:qtext.slice(0,45),key:a.key,via:'radio',status:'filled'});}
          else report.push({q:qtext.slice(0,45),key:a.key,via:'radio',status:'no_option'});
        } else if(aria.length){
          combos.push({q:qtext.slice(0,80),key:a.key,value:a.value,want});
          report.push({q:qtext.slice(0,45),key:a.key,via:'combobox',status:'needs_executor'});
        } else if(texts.length){
          setNativeValue(texts[0],a.value); texts[0].scrollIntoView({block:'center'});
          report.push({q:qtext.slice(0,45),key:a.key,via:'text',status:'filled'});
        } else if(checks.length){
          const affirm=/^(yes|true|agree|accept)/i.test(a.value||'');
          if(checks[0].checked!==affirm) checks[0].click();
          report.push({q:qtext.slice(0,45),key:a.key,via:'checkbox',status:'filled'});
        }
      } else if(!didSelect && (radios.length||aria.length||texts.length||checks.length)){
        report.push({q:qtext.slice(0,55),status:'unmatched'});
      }
    } catch(e){ report.push({q:qtext.slice(0,45),status:'error:'+e.message}); }
  }
  return {report, combos};
}
"""


@app.post("/autofill_form")
async def autofill_form(body: AutofillFormRequest):
    """Fill the current Indeed custom form by matching each question to a profile answer and
    dispatching by the element type present (the type-generalizing interaction layer). Radio/
    select/text/number/checkbox in one JS pass; ARIA comboboxes via CDP open+pick. Returns a
    report so the caller knows what filled vs what's unmatched (→ human/escalation)."""
    import asyncio
    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target
    try:
        target = await _discover_target(body.browser_url, tab_id=None, tab_url=body.tab_url)
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=16 * 1024 * 1024) as ws:
            cdp = _CDPSession(ws)
            r = await cdp.send("Runtime.evaluate", {
                "expression": f"({_AUTOFILL_JS})({json.dumps(body.answers)})", "returnByValue": True})
            out = (r.get("result") or {}).get("value") or {"report": [], "combos": []}
            # Handle ARIA comboboxes (open → wait → pick the matching option in the portal)
            for combo in out.get("combos", []):
                want = combo.get("want") or [combo.get("value", "").lower()]
                await cdp.send("Runtime.evaluate", {"expression":
                    "(()=>{const its=[...document.querySelectorAll('.ia-Questions-item,[class*=Questions-item]')];"
                    f"const it=its.find(x=>(x.innerText||'').slice(0,80).includes({json.dumps(combo['q'][:40])}));"
                    "const c=it&&it.querySelector('[role=combobox]'); if(c){c.scrollIntoView({block:'center'});c.click();}})()"})
                await asyncio.sleep(0.7)
                await cdp.send("Runtime.evaluate", {"expression":
                    f"(()=>{{const want={json.dumps(want)};"
                    "const o=[...document.querySelectorAll('[role=option],li[role=option],[role=listbox] li')]"
                    ".find(x=>want.some(w=>(x.innerText||'').toLowerCase().includes(w)));"
                    "if(o){o.scrollIntoView({block:'center'});o.click();return 'picked';}return 'no_option';})()",
                    "returnByValue": True})
                await asyncio.sleep(0.3)
        return {"ok": True, **out}
    except Exception as exc:  # noqa: BLE001
        logger.warning("autofill_form failed: %s", exc)
        return {"ok": False, "detail": str(exc), "report": [], "combos": []}


class ScanFormRequest(BaseModel):
    browser_url: str = "http://127.0.0.1:9222"
    tab_url: Optional[str] = "smartapply"


# READ-ONLY form scanner (Layer 1 observation). It reports each field's required/filled/valid
# and acts on NOTHING, so the state store's invariant gate can refuse to mark a form subtask
# done while a required field is empty/invalid. required = required attr / aria-required / a
# "*" or "required" in the label; filled = non-empty value / a checked radio-or-checkbox /
# a non-placeholder select; valid = not aria-invalid and not :invalid.
#
# It must EARN ITS PLACE on the live page, so it is multi-strategy + self-diagnosing: it tries
# Indeed's question-item container first, falls back to generic form groupings, then to flat
# controls, and ALWAYS returns a `diagnostics` block (url, strategy that matched, container
# count, a control inventory, sample class names). A live run is therefore never a silent
# "0 fields" — when a selector misses we can see the real DOM shape and fix it.
_SCAN_FORM_JS = r"""
() => {
  const txt = el => ((el && (el.innerText || el.textContent)) || '').replace(/\s+/g,' ').trim();
  const vis = el => { try { return el.offsetParent !== null || el.getClientRects().length > 0; } catch (e) { return true; } };
  const isReq = (el, c) => {
    if (el.required || el.getAttribute('aria-required') === 'true') return true;
    const lbl = txt(c.querySelector('label,legend')) || txt(c);
    return /\*/.test(lbl) || /\brequired\b/i.test(lbl);
  };
  const isInvalid = el => el.getAttribute('aria-invalid') === 'true'
    || (el.matches && (() => { try { return el.matches(':invalid'); } catch (e) { return false; } })());
  const SEL = {
    select: 'select', radio: '[role=radio],input[type=radio]', combobox: '[role=combobox]',
    text: 'input[type=text],input[type=number],input[type=email],input[type=tel],input:not([type]),textarea',
    checkbox: 'input[type=checkbox]',
  };

  function scan(containers) {
    const fields = []; let idx = 0;
    for (const c of containers) {
      const q = txt(c).slice(0, 120) || '(unlabeled)';
      const selects = [...c.querySelectorAll(SEL.select)].filter(vis);
      const radios  = [...c.querySelectorAll(SEL.radio)].filter(vis);
      const aria    = [...c.querySelectorAll(SEL.combobox)].filter(vis);
      const texts   = [...c.querySelectorAll(SEL.text)].filter(vis);
      const checks  = [...c.querySelectorAll(SEL.checkbox)].filter(vis);
      if (!(selects.length || radios.length || aria.length || texts.length || checks.length)) continue;
      // SELECTS: per-element (multi-field aware — the Country+State case).
      for (const s of selects) fields.push({ field_id: 'f' + (idx++), label: q, kind: 'select',
        required: isReq(s, c), filled: s.selectedIndex > 0 && s.value !== '',
        valid: !isInvalid(s), value_preview: (s.value || '').slice(0, 40) });
      // RADIOS: one logical field per container.
      if (radios.length) {
        const ck = radios.find(r => r.checked || r.getAttribute('aria-checked') === 'true');
        fields.push({ field_id: 'f' + (idx++), label: q, kind: 'radio',
          required: isReq(radios[0], c), filled: !!ck, valid: true,
          value_preview: ck ? txt(ck.closest('label')).slice(0, 40) : '' });
      }
      for (const t of texts) fields.push({ field_id: 'f' + (idx++), label: q, kind: 'text',
        required: isReq(t, c), filled: !!(t.value && t.value.trim()),
        valid: !isInvalid(t), value_preview: (t.value || '').slice(0, 40) });
      for (const a of aria) { const v = txt(a) || (a.value || '').trim();
        fields.push({ field_id: 'f' + (idx++), label: q, kind: 'combobox',
          required: isReq(a, c), filled: !!v && !/^select\b/i.test(v), valid: true,
          value_preview: v.slice(0, 40) }); }
      for (const ch of checks) fields.push({ field_id: 'f' + (idx++), label: q, kind: 'checkbox',
        required: isReq(ch, c), filled: ch.checked, valid: true,
        value_preview: ch.checked ? 'checked' : '' });
    }
    return fields;
  }

  const strategies = [
    ['ia-questions',  '.ia-Questions-item,[class*=Questions-item]'],
    ['fieldset/group', 'fieldset,[role=group],[role=radiogroup]'],
    ['question-class', '[class*=question],[class*=Question],[data-testid*=question]'],
  ];
  let used = 'none', containers = [], fields = [];
  for (const [name, sel] of strategies) {
    containers = [...document.querySelectorAll(sel)].filter(vis);
    if (containers.length) { const f = scan(containers); if (f.length) { used = name; fields = f; break; } }
  }
  // Last resort: enumerate visible controls and group by nearest plausible wrapper.
  if (!fields.length) {
    const all = [...document.querySelectorAll('select,input,textarea,[role=combobox],[role=radio]')].filter(vis);
    const seen = new Set(); const pseudo = [];
    for (const el of all) {
      const c = el.closest('label,fieldset,[role=group],li,section,div') || el.parentElement || el;
      if (!seen.has(c)) { seen.add(c); pseudo.push(c); }
    }
    fields = scan(pseudo);
    used = fields.length ? 'flat-controls' : 'none';
    containers = pseudo;
  }

  const inv = sel => document.querySelectorAll(sel).length;
  const diagnostics = {
    url: (location.href || '').slice(0, 140),
    strategy: used,
    container_count: containers.length,
    controls: { select: inv(SEL.select), radio: inv(SEL.radio), text: inv(SEL.text),
      checkbox: inv(SEL.checkbox), combobox: inv(SEL.combobox) },
    sample_classes: [...document.querySelectorAll('[class*=Questions],[class*=question],fieldset')]
      .slice(0, 5).map(e => (e.className || '').toString().slice(0, 70)),
  };
  return { fields, diagnostics };
}
"""


@app.post("/scan_form")
async def scan_form(body: ScanFormRequest):
    """Read-only form scan: report each field's required/filled/valid (no writes), plus a
    diagnostics block so a live run is never a silent "0 fields". The live source for the
    state store's form_state + invariant gate."""
    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target
    try:
        target = await _discover_target(body.browser_url, tab_id=None, tab_url=body.tab_url)
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=16 * 1024 * 1024) as ws:
            cdp = _CDPSession(ws)
            r = await cdp.send("Runtime.evaluate", {
                "expression": f"({_SCAN_FORM_JS})()", "returnByValue": True})
            out = (r.get("result") or {}).get("value") or {"fields": [], "diagnostics": {}}
        return {"ok": True, **out}
    except Exception as exc:  # noqa: BLE001
        logger.warning("scan_form failed: %s", exc)
        return {"ok": False, "detail": str(exc), "fields": [], "diagnostics": {}}


class JobDescriptionRequest(BaseModel):
    external_id: str                     # Indeed jk
    browser_url: str = "http://127.0.0.1:9222"


# Scrape a single Indeed job's detail page (viewjob): full description + salary + whether
# it's Indeed Quick Apply vs a company-site redirect (the apply_type that decides which
# apply FLOW the planner uses). This is the "click into the posting" the operator does by hand.
_JOB_DESC_JS = r"""
(() => {
  const descEl = document.querySelector('#jobDescriptionText, [id*=jobDescription], .jobsearch-JobComponent-description');
  const description = descEl ? descEl.innerText.trim() : '';
  const salEl = document.querySelector('#salaryInfoAndJobType, [id*=salaryInfo], [class*=salary]');
  const salary = salEl ? salEl.innerText.trim() : '';
  // Prefer the posting's own header (present on both the in-pane SERP view and the viewjob page).
  // Plain h1 is LAST — on the results page h1 is the search title ("reporting analyst jobs in …"),
  // not the job, so it must not win over the pane header.
  const titleEl = document.querySelector(
    '#vjs-jobtitle, [data-testid="jobsearch-JobInfoHeader-title"], h2.jobsearch-JobInfoHeader-title,'
    + ' .jobsearch-RightPane h1, .jobsearch-JobComponent h1, h1');
  const title = titleEl ? titleEl.innerText.trim() : '';
  // apply_type drives the routing: 'company_site' → cross-site ATS (Workday/...), 'quick_apply' →
  // Indeed-native (smartapply, end-to-end driveable). "Apply with Indeed" is quick-apply too — the
  // earlier omission read those as 'unknown'. Match button/aria text; check company-site first.
  const btnText = Array.from(document.querySelectorAll('button, a'))
    .map(b => (b.innerText || b.getAttribute('aria-label') || '').trim()).join(' | ').toLowerCase();
  const apply_type = /apply on company site|apply on employer|you are leaving indeed/.test(btnText) ? 'company_site'
                   : /apply with indeed|easily apply|apply now/.test(btnText) ? 'quick_apply' : 'unknown';
  return { description, salary, title, apply_type };
})()
"""


@app.post("/fetch_job_description")
async def fetch_job_description(body: JobDescriptionRequest):
    """Navigate to an Indeed job's viewjob page and scrape its full description + salary +
    apply_type. Returns the data; the control plane stores it on observed_jobs."""
    import asyncio
    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target
    try:
        target = await _discover_target(body.browser_url, tab_id=None, tab_url="indeed.com")
        url = f"https://www.indeed.com/viewjob?jk={body.external_id}"
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024) as ws:
            cdp = _CDPSession(ws)
            await cdp.send("Page.navigate", {"url": url})
            await asyncio.sleep(2.6)
            res = await cdp.send("Runtime.evaluate", {"expression": _JOB_DESC_JS, "returnByValue": True})
        data = (res.get("result") or {}).get("value") or {}
        data["ok"] = bool(data.get("description"))
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_job_description failed: %s", exc)
        return {"ok": False, "detail": str(exc)}


# --- Indeed search-results interaction: distance filter, card-walk, pagination -------
# These are the human-like SERP primitives the bounded sweep drives (project: card-walk +
# pagination). All operate on the SAME results tab (no URL-jumping / no tab churn — bot-safe):
# set the distance filter by clicking the pill, open a posting by clicking its card (opens the
# in-page right pane), and page forward by clicking the pagination number — exactly what a human
# does. Selectors are Indeed-fragile, so each tries several selectors + a visible-text fallback.

class SetDistanceRequest(BaseModel):
    browser_url: str = "http://127.0.0.1:9222"
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    min_miles: int = 50


# Indeed's distance control is a Downshift-style React combobox (#radius_filter_button → a listbox of
# li[role=option] "Within N miles"). Live findings on the CDP-observed training Chrome:
#   * The OPTION only selects when its OWN React onClick handler is invoked off the fiber props
#     (synthetic + trusted DOM clicks / keyboard all no-op on it).
#   * OPENING the menu via input gestures is unreliable here (the MCP-drive vs CDP-observe gap):
#     a trusted mouse click opens it sometimes, not deterministically.
# So set_distance is a CASCADE: (1) already >= min via URL → done; (2) try the human widget path —
# trusted-mouse open + fiber-select, poll the URL; (3) if the menu wouldn't open / didn't take, fall
# back to a same-tab radius= rewrite (one param on the SAME search — not a job-detail URL-jump) so the
# floor is guaranteed and the sweep is never blocked. Reports which method actually applied it.

# Prep + idempotency: radius already satisfied? options already open? where's the button to click?
_DISTANCE_PREP_JS = r"""
(min_miles) => {
  const r = parseInt(new URLSearchParams(location.search).get('radius') || '', 10);
  if (!isNaN(r) && r >= min_miles) return {already:true, current:r};
  const OPT = '[data-testid^="selection-pill-option-"], li[role=option]';
  const opts_present = !!document.querySelector(OPT);
  const btn = document.querySelector('#radius_filter_button, button[id*=radius], [aria-label*="Distance" i]');
  if (!btn) return {already:false, btn:false, opts_present};
  btn.scrollIntoView({block:'center'});
  const b = btn.getBoundingClientRect();
  return {already:false, btn:true, opts_present, x:b.x + b.width/2, y:b.y + b.height/2};
}
"""

# Select the smallest option >= min by invoking its React handler, then poll the URL for radius.
_PICK_DISTANCE_JS = r"""
(min_miles) => new Promise((resolve) => {
  const OPT = '[data-testid^="selection-pill-option-"], li[role=option]';
  const opts = [...document.querySelectorAll(OPT)].map(el => {
    const t = (el.innerText || el.getAttribute('aria-label') || '').trim();
    const m = t.match(/(\d+)\s*mile/i);
    return {el, miles: m ? parseInt(m[1], 10) : null, text: t};
  }).filter(o => o.miles !== null);
  if (!opts.length) { resolve({applied:false, detail:'menu did not open'}); return; }
  const atLeast = opts.filter(o => o.miles >= min_miles).sort((a,b)=>a.miles-b.miles);
  const choice = atLeast[0] || opts.sort((a,b)=>b.miles-a.miles)[0];
  const key = Object.keys(choice.el).find(k => k.startsWith('__reactProps$'));
  if (key) {
    const p = choice.el[key];
    const ev = {target:choice.el, currentTarget:choice.el, preventDefault(){}, stopPropagation(){}, nativeEvent:{}, type:'click', button:0};
    try { if (p.onMouseDown) p.onMouseDown(ev); if (p.onClick) p.onClick(ev); } catch (e) { choice.el.click(); }
  } else { choice.el.click(); }
  let tries = 0;
  const iv = setInterval(() => {
    const r = parseInt(new URLSearchParams(location.search).get('radius') || '', 10);
    if ((!isNaN(r) && r >= min_miles) || ++tries > 9) {
      clearInterval(iv);
      resolve({applied: (!isNaN(r) && r >= min_miles), selected: choice.miles, detail: choice.text});
    }
  }, 280);
})
"""

# Same-tab radius= rewrite — the guaranteed fallback when the widget won't open. Returns the URL to
# navigate to (current search + radius=min), preserving every other param.
_DISTANCE_URL_JS = r"""
(min_miles) => {
  const u = new URL(location.href);
  u.searchParams.set('radius', String(min_miles));
  u.searchParams.delete('vjk');   // drop any open-posting anchor so we land on the clean list
  return u.toString();
}
"""


@app.post("/set_distance")
async def set_distance(body: SetDistanceRequest):
    """Force the search radius to >= min_miles. Cascade: (1) already >= min in the URL → done;
    (2) the human widget path — trusted-mouse open of the distance pill + invoke the option's React
    handler, poll the URL; (3) if the widget won't open in this observed Chrome, fall back to a
    same-tab radius= rewrite (one param on the same search, not a job-detail URL-jump). Returns
    {applied, selected_miles, method, detail}. `applied` is true only once radius>=min is in the URL."""
    import asyncio
    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target
    try:
        target = await _discover_target(body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url)
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024) as ws:
            cdp = _CDPSession(ws)
            await cdp.send("Page.enable", {})
            await cdp.send("Page.bringToFront", {})

            prep = (await cdp.send("Runtime.evaluate", {
                "expression": f"({_DISTANCE_PREP_JS})({body.min_miles})",
                "returnByValue": True})).get("result", {}).get("value") or {}
            if prep.get("already"):
                return {"ok": True, "applied": True, "selected_miles": prep.get("current"),
                        "method": "already", "detail": "radius already >= min via URL"}

            # (2) Human widget path: open via trusted mouse click, then fiber-select the option.
            if prep.get("btn") and not prep.get("opts_present"):
                x, y = prep["x"], prep["y"]
                for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
                    ev = {"type": typ, "x": x, "y": y}
                    if typ != "mouseMoved":
                        ev.update({"button": "left", "clickCount": 1})
                    await cdp.send("Input.dispatchMouseEvent", ev)
                await asyncio.sleep(0.7)  # let the listbox render
            picked = (await cdp.send("Runtime.evaluate", {
                "expression": f"({_PICK_DISTANCE_JS})({body.min_miles})",
                "returnByValue": True, "awaitPromise": True})).get("result", {}).get("value") or {}
            if picked.get("applied"):
                return {"ok": True, "applied": True, "selected_miles": picked.get("selected"),
                        "method": "widget", "detail": picked.get("detail", "")}

            # (3) Guaranteed fallback: same-tab radius= rewrite of the current search.
            url = (await cdp.send("Runtime.evaluate", {
                "expression": f"({_DISTANCE_URL_JS})({body.min_miles})",
                "returnByValue": True})).get("result", {}).get("value")
            if not url:
                return {"ok": True, "applied": False, "method": "none",
                        "detail": "widget would not open and no URL to rewrite"}
            await cdp.send("Page.navigate", {"url": url})
            await asyncio.sleep(2.4)
            r = (await cdp.send("Runtime.evaluate", {
                "expression": "parseInt(new URLSearchParams(location.search).get('radius')||'',10)",
                "returnByValue": True})).get("result", {}).get("value")
            ok = isinstance(r, (int, float)) and r >= body.min_miles
            return {"ok": True, "applied": bool(ok), "selected_miles": (r if ok else None),
                    "method": "url_fallback", "detail": f"radius={r} via same-tab rewrite"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_distance failed: %s", exc)
        return {"ok": False, "applied": False, "method": "error", "detail": str(exc)}


class OpenJobCardRequest(BaseModel):
    external_id: str                     # Indeed jk
    browser_url: str = "http://127.0.0.1:9222"
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    settle_seconds: float = 1.6


# Return the card's title-anchor center (screenshot/CSS px) so the caller can TRUSTED-click it.
# A plain JS .click() does NOT reliably switch Indeed's detail pane (React-handled; isTrusted gated —
# same lesson as the distance widget). It only "worked" on a page where the card was a native anchor.
_CARD_BBOX_JS = r"""
(jk) => {
  const card = document.querySelector(`[data-jk="${jk}"]`);
  if (!card) return {found:false};
  const el = card.matches('a') ? card : (card.querySelector('a') || card);
  el.scrollIntoView({block:'center', inline:'center'});
  const r = el.getBoundingClientRect();
  return {found:true, x:r.x + r.width/2, y:r.y + r.height/2};
}
"""


@app.post("/open_job_card")
async def open_job_card(body: OpenJobCardRequest):
    """Click a result card by its data-jk to open the IN-PAGE right-hand detail pane, then scrape
    its description/salary/apply_type from that pane (reuses _JOB_DESC_JS — #jobDescriptionText is
    present in the pane). Uses a TRUSTED CDP mouse click (a synthetic .click() doesn't switch the
    React pane), and CONFIRMS the pane actually changed by polling the description (Indeed auto-opens
    the first result, so a no-op click would silently return the wrong job). Same tab, no navigation."""
    import asyncio
    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target
    try:
        target = await _discover_target(body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url)
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024) as ws:
            cdp = _CDPSession(ws)
            await cdp.send("Page.bringToFront", {})
            box = (await cdp.send("Runtime.evaluate", {
                "expression": f"({_CARD_BBOX_JS})({json.dumps(body.external_id)})",
                "returnByValue": True})).get("result", {}).get("value") or {}
            if not box.get("found"):
                return {"ok": False, "detail": f"card data-jk={body.external_id} not found"}
            before = (await cdp.send("Runtime.evaluate", {
                "expression": "(document.querySelector('#jobDescriptionText')||{}).innerText||''",
                "returnByValue": True})).get("result", {}).get("value") or ""
            for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
                ev = {"type": typ, "x": box["x"], "y": box["y"]}
                if typ != "mouseMoved":
                    ev.update({"button": "left", "clickCount": 1})
                await cdp.send("Input.dispatchMouseEvent", ev)
            # Poll until the pane's description changes (the job loaded), bounded by settle_seconds.
            deadline = max(0.6, min(body.settle_seconds, 8.0))
            waited, data = 0.0, {}
            while waited < deadline:
                await asyncio.sleep(0.4)
                waited += 0.4
                data = (await cdp.send("Runtime.evaluate", {
                    "expression": _JOB_DESC_JS, "returnByValue": True})).get("result", {}).get("value") or {}
                if data.get("description") and data["description"] != before:
                    break
        data["ok"] = bool(data.get("description"))
        data["switched"] = bool(data.get("description") and data.get("description") != before)
        data["external_id"] = body.external_id
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("open_job_card failed: %s", exc)
        return {"ok": False, "detail": str(exc)}


class NextPageRequest(BaseModel):
    browser_url: str = "http://127.0.0.1:9222"
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None


# Scroll to the bottom (also satisfies "move down the page" + triggers lazy load), then click the
# pagination control for the NEXT page. Indeed renders pagination as a row of numbered links
# (a[data-testid=pagination-page-N]) plus an explicit Next link; we prefer the exact next number,
# falling back to the Next-labeled control. Searches the whole doc (top + bottom).
_NEXT_PAGE_JS = r"""
(() => {
  window.scrollTo(0, document.body.scrollHeight);
  const cur = (() => {
    const s = parseInt(new URLSearchParams(location.search).get('start') || '0', 10);
    return isNaN(s) ? 1 : Math.floor(s / 10) + 1;
  })();
  const byNum = document.querySelector(`a[data-testid="pagination-page-${cur + 1}"]`)
             || [...document.querySelectorAll('nav[aria-label*="pag" i] a, [role=navigation] a')]
                  .find(a => (a.innerText || '').trim() === String(cur + 1));
  const next = document.querySelector('a[data-testid="pagination-page-next"], [aria-label*="Next" i]');
  const el = byNum || next;
  if (!el) return {clicked:false, current:cur, has_next:false};
  el.scrollIntoView({block:'center'}); el.click();
  return {clicked:true, current:cur, next_page:cur + 1, has_next:true};
})()
"""


@app.post("/next_page")
async def next_page(body: NextPageRequest):
    """Page the results forward by CLICKING the pagination control (never a ?start= URL-jump):
    scroll to the bottom, then click the next page number (or the Next link). Returns whether a
    next page existed and was clicked, and the new page number. Best-effort."""
    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target
    try:
        target = await _discover_target(body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url)
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024) as ws:
            cdp = _CDPSession(ws)
            res = await cdp.send("Runtime.evaluate", {"expression": _NEXT_PAGE_JS, "returnByValue": True})
        data = (res.get("result") or {}).get("value") or {"clicked": False, "has_next": False}
        data["ok"] = True
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("next_page failed: %s", exc)
        return {"ok": False, "clicked": False, "has_next": False, "detail": str(exc)}


# Is a captcha challenge ACTUALLY SHOWN + BLOCKING, or just preloaded invisibly / already solved?
# Sites (Indeed) preload the reCAPTCHA Enterprise anchor+bframe iframes hidden on every page, so
# "frame present" over-triggers a human stop. We read from the host document (no cross-origin access):
#   1. iframe ELEMENT visibility (walk ancestors for display:none/visibility:hidden/opacity:0; rect
#      non-trivial + on-screen — the hidden challenge is parked off-screen at top:-10000px).
#   2. the SITE WRAPPER (Indeed reveals #captcha-wrapper only when the challenge goes live) — the
#      cleanest "it fired NOW" signal on this very-clever preloaded system.
#   3. the g-recaptcha-response TOKEN textarea: EMPTY = unsolved, FILLED = the human passed it. This is
#      the definitive cross-site "is it actually gating / has it been solved (resume)" signal.
# blocking = (something visibly challenging) AND (not yet solved). That is the one flag the apply loop
# gates on; `solved` flipping true is the resume signal after a human checks the box.
_CHALLENGE_VISIBILITY_JS = r"""
(() => {
  const shown = (el) => {
    let n = el;
    while (n && n.nodeType === 1) {
      const s = getComputedStyle(n);
      if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity || '1') === 0) return false;
      n = n.parentElement;
    }
    const r = el.getBoundingClientRect();
    if (r.width < 10 || r.height < 10) return false;
    const vw = window.innerWidth || 0, vh = window.innerHeight || 0;
    if (r.bottom < 0 || r.right < 0 || r.top > vh + 200 || r.left > vw + 200) return false;
    return true;
  };
  const ifr = Array.from(document.querySelectorAll('iframe'));
  const match = (re) => ifr.filter(f => re.test(f.src || ''));
  const bframes  = match(/recaptcha\/(enterprise|api2)\/bframe/);   // the image-challenge popup
  const anchors  = match(/recaptcha\/(enterprise|api2)\/anchor/);   // the "I'm not a robot" checkbox
  const hcap     = match(/hcaptcha\.com\/(captcha|challenge)/);
  const visBframes = bframes.filter(shown), visAnchors = anchors.filter(shown), visHcap = hcap.filter(shown);
  const challenge_visible = visBframes.length > 0 || visHcap.length > 0;   // image challenge up
  const checkbox_visible  = visAnchors.length > 0;                         // v2 checkbox on screen
  // CRITICAL: a page can host MULTIPLE reCAPTCHAs — an invisible Enterprise SCORER (whose
  // g-recaptcha-response holds a token from the passive check) AND the visible v2 CHECKBOX (whose
  // token stays EMPTY until the human clicks it). A global token check false-reads the scorer's
  // token as "solved". So scope the token to the VISIBLE checkbox's own wrapper.
  const tokenLenFor = (f) => {
    const w = f.closest('#captcha-wrapper, [id*="captcha" i], .g-recaptcha') || f.parentElement;
    const ta = w && w.querySelector('textarea.g-recaptcha-response, textarea[name="g-recaptcha-response"]');
    return ta ? (((ta.value || '') + '').trim().length) : 0;
  };
  const checkbox_unsolved = visAnchors.some(f => tokenLenFor(f) === 0);
  const checkbox_solved   = visAnchors.length > 0 && visAnchors.every(f => tokenLenFor(f) > 0);
  // THE gate: an image challenge is open, OR a visible checkbox that hasn't been passed yet.
  const blocking = challenge_visible || checkbox_unsolved;
  return {
    blocking,
    // solved only makes sense once something WAS challenging; a page with no visible gate is "n/a".
    solved: !blocking && (checkbox_solved || (!checkbox_visible && !challenge_visible)),
    challenge_visible, checkbox_visible, checkbox_unsolved, checkbox_solved,
    bframe_count: bframes.length, anchor_count: anchors.length, hcaptcha_count: hcap.length,
  };
})()
"""


class ChallengeVisibilityRequest(BaseModel):
    browser_url: str = "http://127.0.0.1:9222"
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None


@app.post("/challenge_visibility")
async def challenge_visibility(body: ChallengeVisibilityRequest):
    """Probe whether a captcha challenge is ACTUALLY SHOWN to the user (vs an invisible preload) by
    reading the captcha iframe elements' visibility from the host document over CDP. This is the
    signal `detect_block_frames` (URL-only, $0) can't have — it turns "a bframe exists" into "a
    human is actually being challenged", killing the false-positive hard stop on every Indeed search.
    Returns {ok, challenge_visible, checkbox_visible, counts}. Best-effort; never raises."""
    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target
    try:
        target = await _discover_target(body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url)
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024) as ws:
            cdp = _CDPSession(ws)
            res = await cdp.send("Runtime.evaluate",
                                 {"expression": _CHALLENGE_VISIBILITY_JS, "returnByValue": True})
        data = (res.get("result") or {}).get("value") or {}
        data["ok"] = True
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("challenge_visibility failed: %s", exc)
        return {"ok": False, "detail": str(exc)}


class AXScanRequest(BaseModel):
    """Read-only CDP-AX scan of the live page. Returns the actionable elements
    (role + accessible-name + backend_node_id + bbox) so a caller can pick a target BY ROLE +
    ACCESSIBLE-NAME and drive it BY NODE via /execute. The generic "what can I act on here?"
    primitive — no screenshot, no persisted artifact, so it is safe inside credential flows.
    device_scale_factor only scales the returned bbox; node-based /execute ignores it."""
    browser_url: str = "http://127.0.0.1:9222"
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    device_scale_factor: float = 1.0


@app.post("/ax_scan")
async def ax_scan(body: AXScanRequest):
    """Enumerate the page's CDP-AX candidates (the SAME proposer the training capture uses) and
    return them as {role, name, backend_node_id, bbox, dpr}. This is how driven flows — login
    included — find controls by role + accessible-name instead of hardcoded selectors, so a
    <div role=button> "Log in" is found exactly like a <button> would be. Best-effort."""
    try:
        stats = AXProposerStats()
        candidates = await propose_ax_candidates(
            browser_url=body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url,
            device_scale_factor=body.device_scale_factor, stats=stats,
        )
        out = [{
            "candidate_id": c.get("candidate_id"),
            "role": c.get("role"),
            "name": c.get("caption"),
            "backend_node_id": c.get("backend_node_id"),
            "bbox": c.get("bbox"),
            "dpr": (c.get("_debug") or {}).get("dpr", body.device_scale_factor),
        } for c in candidates]
        return {"ok": True, "count": len(out), "candidates": out, "target_url": stats.target_url}
    except Exception as exc:  # noqa: BLE001
        logger.warning("ax_scan failed: %s", exc)
        return {"ok": False, "count": 0, "candidates": [], "detail": str(exc)}


class CloseTabRequest(BaseModel):
    """Close ONE finished tab and (optionally) bring another to the front — the apply-flow
    epilogue primitive. Indeed opens the quick-apply (smartapply) or a cross-site ATS (Workday/…)
    in a NEW tab; once that application is submitted (or abandoned at a human-required wall like a
    Workday account gate), a human closes that tab and returns to the search. This makes that
    cleanup a real capability the cadence/recipe can drive, instead of leaving orphan apply tabs."""
    browser_url: str = "http://127.0.0.1:9222"
    tab_id: Optional[str] = None       # exact target id to close
    tab_url: Optional[str] = None      # OR a URL substring identifying the tab to close (e.g. "smartapply")
    focus_tab_url: Optional[str] = None  # after closing, activate the tab whose URL contains this (e.g. "indeed.com/jobs")


@app.post("/close_tab")
async def close_tab(body: CloseTabRequest):
    """Close the identified tab via the CDP HTTP endpoint (GET /json/close/<id>), then optionally
    activate the tab matching focus_tab_url so we land back on the search — the 'return to the
    search' seam of the apply cadence. SAFETY: refuses to close the control panel (localhost:5173)
    and refuses to close the last remaining page tab (never leave the browser tab-less). This is
    intentional single-tab cleanup, NOT the tab-churn the bounds forbid. Best-effort; never raises."""
    import httpx
    from app.observer.ax_proposer import _discover_target
    if not (body.tab_id or body.tab_url):
        return {"ok": False, "detail": "tab_id or tab_url is required (won't guess which tab to close)"}
    try:
        target = await _discover_target(body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url)
        url = str(target.get("url", ""))
        # GUARD: _discover_target falls through to the FIRST page tab when the requested id/url
        # doesn't match — which would silently close the wrong tab (a truncated tab_id did exactly
        # that live 2026-07-12). If the caller named a specific tab, it MUST match, or we refuse.
        if body.tab_id and str(target.get("id")) != body.tab_id and str(target.get("targetId")) != body.tab_id:
            return {"ok": False, "detail": f"tab_id {body.tab_id!r} not found — refusing to close a different tab"}
        if body.tab_url and body.tab_url not in url:
            return {"ok": False, "detail": f"tab_url {body.tab_url!r} matched no tab — refusing to close a different tab"}
        if "localhost:5173" in url:
            return {"ok": False, "detail": "refusing to close the control panel tab"}
        # Count page tabs — never leave the browser with zero.
        async with httpx.AsyncClient(timeout=5.0) as client:
            pages_before = [t for t in (await client.get(f"{body.browser_url}/json/list")).json()
                            if t.get("type") == "page"]
            if len(pages_before) <= 1:
                return {"ok": False, "detail": "refusing to close the only remaining tab", "url": url[:90]}
            closed_id = target.get("id")
            await client.get(f"{body.browser_url}/json/close/{closed_id}")
            activated = None
            if body.focus_tab_url:
                remaining = [t for t in (await client.get(f"{body.browser_url}/json/list")).json()
                             if t.get("type") == "page" and body.focus_tab_url in str(t.get("url", ""))]
                if remaining:
                    activated = remaining[0].get("id")
                    await client.get(f"{body.browser_url}/json/activate/{activated}")
            remaining_pages = [t for t in (await client.get(f"{body.browser_url}/json/list")).json()
                               if t.get("type") == "page"]
        _log_event("tab", f"Closed tab {url[:70]}", detail=f"remaining {len(remaining_pages)}")
        return {"ok": True, "closed_tab_id": closed_id, "closed_url": url[:90],
                "activated_tab_id": activated, "remaining_tab_count": len(remaining_pages)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("close_tab failed: %s", exc)
        return {"ok": False, "detail": str(exc)}


class NavigateRequest(BaseModel):
    url: str
    browser_url: str = "http://127.0.0.1:9222"
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None      # substring to pick the tab to drive (e.g. "indeed.com")
    settle_seconds: float = 2.0        # let the page settle before reading back where we landed


@app.post("/navigate")
async def navigate(body: NavigateRequest):
    """Drive a tab to a URL over raw CDP — the foundational 'move the browser' primitive of OUR
    driver (reuses _CDPSession + Page.navigate, same plumbing as /fetch_job_description). Teacher-
    teaches: the operator/agent navigates the existing tab human-paced; the capture loop records
    the resulting state. Returns where we actually landed (final url + title) so the caller can
    confirm. Best-effort; never raises into the caller.

    Note on safety: this is a generic primitive. The job-search BOUNDS (search-results URLs only,
    never job-detail URL-jumps, no tab churn) are policy enforced by the control plane / cadence —
    not re-implemented here."""
    import asyncio
    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target
    try:
        target = await _discover_target(body.browser_url, tab_id=body.tab_id,
                                        tab_url=body.tab_url)
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024) as ws:
            cdp = _CDPSession(ws)
            await cdp.send("Page.navigate", {"url": body.url})
            await asyncio.sleep(max(0.0, min(body.settle_seconds, 10.0)))
            res = await cdp.send("Runtime.evaluate",
                                 {"expression": "({url: location.href, title: document.title})",
                                  "returnByValue": True})
        landed = (res.get("result") or {}).get("value") or {}
        _log_event("nav", f"Navigated → {(landed.get('title') or landed.get('url') or body.url)[:80]}",
                   detail=landed.get("url", "")[:120])
        return {"ok": True, "requested_url": body.url,
                "landed_url": landed.get("url", ""), "title": landed.get("title", ""),
                "tab_id": target.get("id")}
    except Exception as exc:  # noqa: BLE001
        logger.warning("navigate failed: %s", exc)
        return {"ok": False, "detail": str(exc), "requested_url": body.url}


class LocateRequest(BaseModel):
    browser_url: str = "http://127.0.0.1:9222"
    css: Optional[str] = None        # CSS selector to find
    text: Optional[str] = None       # OR visible text / aria-label / placeholder substring
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None


@app.post("/locate")
async def locate(body: LocateRequest):
    """Find a clickable element by CSS selector or visible text, scroll it into view, and return its
    bbox in SCREENSHOT px (+ dpr) — the 'find a thing to click' primitive that lets us drive by
    CLICKING (humanized coordinate path) instead of forcing URLs (principle 3). Returns
    {found, bbox, dpr, tag, text}. Best-effort."""
    import json as _json
    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target
    sel = _json.dumps(body.css or "")
    txt = _json.dumps((body.text or "").lower())
    expr = (
        "(() => {"
        f"  const sel={sel}, txt={txt};"
        "  let el = sel ? document.querySelector(sel) : null;"
        "  if (!el && txt) {"
        "    const c=[...document.querySelectorAll('a,button,input,textarea,[role=button],[role=link],[role=searchbox]')];"
        "    el=c.find(e=>((e.innerText||e.value||e.getAttribute('aria-label')||e.placeholder||'').trim().toLowerCase().includes(txt)) && e.offsetParent!==null);"
        "  }"
        "  if(!el) return {found:false};"
        "  el.scrollIntoView({block:'center',inline:'center'});"
        "  const r=el.getBoundingClientRect(); const dpr=window.devicePixelRatio||1;"
        "  return {found:true,dpr,bbox:{x:r.x*dpr,y:r.y*dpr,width:r.width*dpr,height:r.height*dpr},"
        "          tag:el.tagName,text:(el.innerText||el.value||el.getAttribute('aria-label')||'').slice(0,60)};"
        "})()"
    )
    try:
        target = await _discover_target(body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url)
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024) as ws:
            cdp = _CDPSession(ws)
            res = await cdp.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        data = (res.get("result") or {}).get("value") or {"found": False}
        data["ok"] = True
        data["tab_id"] = target.get("id")
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("locate failed: %s", exc)
        return {"ok": False, "found": False, "detail": str(exc)}


class ScreenshotRequest(BaseModel):
    browser_url: str = "http://127.0.0.1:9222"
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None


@app.post("/screenshot")
async def screenshot(body: ScreenshotRequest):
    """Capture the visible page as a PNG over CDP (Page.captureScreenshot), save to a temp file, and
    return the path — the 'eyes' of our driver, for supervising multi-step flows (login) and
    confirming where we landed. Best-effort; never raises."""
    import base64
    import tempfile
    import time
    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target
    try:
        target = await _discover_target(body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url)
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=32 * 1024 * 1024) as ws:
            cdp = _CDPSession(ws)
            await cdp.send("Page.enable", {})
            await cdp.send("Page.bringToFront", {})  # surface an occluded window so capture works
            res = await cdp.send("Page.captureScreenshot", {"format": "png", "fromSurface": True,
                                                            "captureBeyondViewport": False})
        # _CDPSession.send() returns the UNWRAPPED CDP result, so the PNG base64 is at
        # res["data"] directly (not res["result"]["data"]).
        data = res.get("data")
        if not data:
            return {"ok": False, "detail": "no screenshot data"}
        out_dir = Path(tempfile.gettempdir()) / "agent-mcp-live-shots"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"shot_{int(time.time() * 1000)}.png"
        path.write_bytes(base64.b64decode(data))
        return {"ok": True, "path": str(path), "tab_id": target.get("id")}
    except Exception as exc:  # noqa: BLE001
        logger.warning("screenshot failed: %s", exc)
        return {"ok": False, "detail": str(exc)}


# Indeed auth-state probe: deterministic logged-in vs login-wall signal — the basis for the
# login GATE (no search/automation until authenticated). Reads gnav affordances + body text.
_INDEED_AUTH_JS = r"""
(() => {
  const url = location.href, path = location.pathname || '', title = document.title || '';
  const txt = (document.body && document.body.innerText || '').slice(0, 6000);
  const q = (sel) => !!document.querySelector(sel);
  // In the auth FLOW specifically — by PATH, not host. secure.indeed.com also serves logged-in
  // pages like /settings/account, so a host check wrongly marks those as "signing in".
  const on_auth = /\/auth\b|\/account\/login/.test(path) || /\bSign In\b/i.test(title);
  const has_sign_in = q('a[href*="/account/login"]') || q('a[href*="secure.indeed.com/auth"]')
                      || /\bSign in\b/.test(txt);
  // Logged-IN requires an account affordance on a NON-auth page (the gnav account menu / Sign out).
  const has_account = q('[data-gnav-element-name="AccountMenu"]')
                      || q('[data-gnav-element-name="UserMenu"]')
                      || /\bSign out\b/i.test(txt);
  return {
    logged_in: !on_auth && has_account && !has_sign_in,
    on_auth, has_sign_in, has_account,
    url, title,
  };
})()
"""


@app.post("/auth_state")
async def auth_state(body: ScreenshotRequest):
    """Deterministic Indeed login-state probe (logged_in + raw signals). Feeds the state manager's
    login gate: search/automation stays blocked until logged_in. Best-effort."""
    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target
    try:
        target = await _discover_target(body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url)
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024) as ws:
            cdp = _CDPSession(ws)
            res = await cdp.send("Runtime.evaluate",
                                 {"expression": _INDEED_AUTH_JS, "returnByValue": True})
        data = (res.get("result") or {}).get("value") or {}
        data["ok"] = True
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("auth_state failed: %s", exc)
        return {"ok": False, "detail": str(exc)}


@app.get("/health")
def health():
    return {"ok": True, "service": "mcp-capture-server"}


@app.post("/capture")
async def trigger_capture(body: CaptureRequest, background_tasks: BackgroundTasks):
    artifact = await observe_live_capture(
        scenario=body.scenario,
        tab_id=body.tab_id,
        tab_url=body.tab_url,
        browser_url=body.browser_url,
        task_context=body.task_context,
        training_metadata=body.training_metadata,
    )
    path = write_observation_artifact(artifact)
    candidate_count = len(artifact.get("ranked_candidates", []))

    # PRIMARY proposer: run CDP-AX now, while the browser is still live. Boxes are
    # scaled to screenshot pixels via the capture's device_scale_factor and saved
    # to an .ax.json sidecar. Best-effort — a failure must not fail the capture.
    ax_candidate_count = 0
    try:
        ax_stats = AXProposerStats()
        ax_candidates = await propose_ax_candidates(
            browser_url=body.browser_url,
            tab_id=body.tab_id,
            tab_url=body.tab_url,
            device_scale_factor=_device_scale_factor_from_artifact(artifact),
            stats=ax_stats,
        )
        _write_ax_sidecar(path.name, ax_candidates, ax_stats)
        ax_candidate_count = len(ax_candidates)
        if ax_candidate_count == 0:
            # An empty sidecar still gets written (so the shape is uniform), but 0 candidates
            # means the faucet ran dry for this capture — target discovery failed, the tab was
            # unreachable, or node-ids were stale. It passes the downstream `only_with_sidecar`
            # existence check yet carries no Select-training data, so make it LOUD, not INFO.
            logger.warning(
                "CDP-AX capture-time: %s -> 0 candidates (%dms) — EMPTY sidecar; "
                "browser likely unreachable/stale at capture. errors=%s",
                path.name, ax_stats.total_ms, ax_stats.errors,
            )
        else:
            logger.info("CDP-AX capture-time: %s -> %d candidates (%dms)",
                        path.name, ax_candidate_count, ax_stats.total_ms)
    except Exception:
        logger.exception("CDP-AX proposal failed for %s", path.name)

    # NOTE: the vision proposer (OmniParser) is the parked super-fallback — NOT run
    # here or on labeler-open. It only activates when AX yields nothing and we
    # explicitly need it (not wired yet).
    _dom = (body.training_metadata or {}).get("domain_id") if body.training_metadata else None
    _log_event("capture", f"Captured '{body.scenario}' — {ax_candidate_count} AX candidates",
               detail=f"{path.name} · {body.tab_url or ''}", domain=_dom)
    return {
        "filename": path.name,
        "candidate_count": candidate_count,
        "ax_candidate_count": ax_candidate_count,
    }


@app.post("/proposer/predict")
def proposer_predict(body: ProposerPredictRequest):
    """Run the proposer on demand against an existing screenshot.

    Used for ad-hoc debugging and for the bulk backfill below. Does not
    persist — caller decides what to do with the result.
    """
    screenshot_path = SCREENSHOTS_DIR / body.screenshot_filename
    if not screenshot_path.exists():
        raise HTTPException(status_code=404, detail=f"Screenshot not found: {body.screenshot_filename}")
    proposals = propose_candidates(screenshot_path)
    return {
        "screenshot_filename": body.screenshot_filename,
        "model_version": MODEL_VERSION,
        "proposal_count": len(proposals),
        "proposals": proposals,
    }


@app.post("/proposer/backfill/{artifact_filename}")
def proposer_backfill_one(artifact_filename: str, include_captions: bool = False):
    """Run the proposer for a single capture and write its sidecar.

    This is the lazy entry point: the labeler calls it when a capture is opened
    without candidates (detect-only, fast), and again with include_captions=true
    when the annotator explicitly asks for Florence-2 captions.
    """
    artifact_path = ARTIFACTS_DIR / artifact_filename
    if not artifact_path.exists():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_filename}")

    try:
        artifact = json.loads(artifact_path.read_text())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read artifact: {exc}")

    screenshot_filename = _screenshot_filename_from_artifact(artifact)
    if not screenshot_filename:
        raise HTTPException(status_code=400, detail="Artifact has no screenshot to propose against.")

    screenshot_path = SCREENSHOTS_DIR / screenshot_filename
    if not screenshot_path.exists():
        raise HTTPException(status_code=404, detail=f"Screenshot file missing: {screenshot_filename}")

    timing: dict = {}
    proposals = propose_candidates(screenshot_path, include_captions=include_captions, stats=timing)
    sidecar_path = _write_vision_sidecar(artifact_filename, screenshot_filename, proposals, timing)
    return {
        "artifact_filename": artifact_filename,
        "screenshot_filename": screenshot_filename,
        "sidecar_path": str(sidecar_path),
        "proposal_count": len(proposals),
        "captioned": include_captions,
        "timing": timing,
    }
