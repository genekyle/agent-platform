from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from interaction.contract import Intent, Outcome, WidgetType, intent_for_action

from app.artifacts import ARTIFACTS_DIR, SCREENSHOTS_DIR, write_observation_artifact
from app.event_log import log_event as _log_event
from app.intent_api import journaled
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
    #: What the CALLER already observed this turn and the capture cannot re-derive on its own.
    #: Today: `{"unanswered": [...]}` from /scan_required — the required-field set, which is the
    #: only signal that separates two form phases of the same ATS. Semantic names only, never
    #: values (PRINCIPLES §4). Optional, so every existing caller is unaffected.
    form_state: Optional[dict] = None


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
@journaled(lambda body: intent_for_action(body.action_id))
async def execute_action(body: ExecuteRequest):
    """TIER 1 PRIMITIVE: perform one resolved action against the live page via raw CDP.

    Actions: click | type | select | scroll | clear | submit | upload. When `target_name` is
    given the node is RE-RESOLVED from a fresh AX scan at act time (immune to node-id
    staleness); otherwise `backend_node_id`, then `selector`. `files` (absolute paths) drive
    an `upload` onto a file input.

    ON WHAT `ok` MEANS HERE — and it is NOT what it means at tier 2. This endpoint's
    `Outcome.OK` means THE MECHANISM COMPLETED: the node resolved and CDP dispatched without
    throwing. It does NOT mean the page accepted the action. `DirectDriver.move_and_act`
    (driver.py:247) returns `ok=True` on any non-exceptional path — it never reads the
    result back, and `.click()` on a detached or 0x0 node no-ops silently (the same trap as
    Indeed's hidden decoy cards). Semantic verification is the PROTOCOL tier's job:
    /select_option verifies at the widget's own `value_read_at`. A tier-1 caller that needs
    "did it take?" must ask tier 2, not this.
    """
    from app.executor.driver import ActionRequest, get_driver

    node_id = body.backend_node_id
    note = ""
    addressed_by = "backend_node_id" if node_id is not None else "bbox"
    if body.target_name:
        addressed_by = "role_name"
        fresh = await _resolve_ax_node(body.browser_url, body.tab_id, body.tab_url,
                                       body.target_role, body.target_name)
        if fresh is not None:
            node_id, note = fresh, f"re-resolved {body.target_name!r} -> node {fresh}"
        elif node_id is None:
            # Previously a silent return: no event, no row. This is the single most useful
            # row in the corpus — it says the recipe is stale — and it was the one we never
            # wrote.
            return {"outcome": Outcome.NOT_FOUND, "addressed_by": addressed_by,
                    "target": f"{body.target_role or '*'}:{body.target_name}",
                    "driver": body.driver or "humanized", "action_id": body.action_id,
                    "css_point": None,
                    "detail": f"target not found by name: {body.target_name!r} (role={body.target_role})"}
    elif body.selector:
        addressed_by = "selector"
        fresh = await _resolve_node_by_selector(body.browser_url, body.tab_id, body.tab_url, body.selector)
        if fresh is not None:
            node_id, note = fresh, f"re-resolved {body.selector!r} -> node {fresh}"
        elif node_id is None:
            return {"outcome": Outcome.NOT_FOUND, "addressed_by": addressed_by,
                    "target": body.selector, "driver": body.driver or "humanized",
                    "action_id": body.action_id, "css_point": None,
                    "detail": f"target not found by selector: {body.selector!r}"}

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
        # A driver ok=False got here by catching an exception (driver.py:245), so it is a
        # mechanism failure — ERROR, not a protocol outcome.
        "outcome": Outcome.OK if result.ok else Outcome.ERROR,
        "addressed_by": addressed_by, "target": _tgt or None,
        "actions": [result.action_id],
        # record_only returns ok=True while executing nothing (record_only.py:54). Without
        # this the corpus cannot tell a rehearsal from a performance — the event log can't.
        "executed": result.driver != "record_only",
        "driver": result.driver, "action_id": result.action_id, "css_point": result.css_point,
        "detail": (note + ("; " if note and result.detail else "") + result.detail),
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
@journaled(Intent.SELECT_OPTION)
async def select_prompt(body: SelectPromptRequest):
    """Atomic Workday prompt-select: open the field → search → trusted-click the matching option.

    Reusable across all Workday prompts. Superseded by /select_option once
    widget_type=prompt_hierarchical dispatches here — kept until then because it is the only
    thing that drives a Workday prompt, and it works.
    """
    import asyncio
    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target

    from app.executor.driver import ActionRequest, get_driver

    # No outer try/except: @journaled catches and journals as Outcome.ERROR. A local catch
    # would swallow the row — which is how a broken prompt used to surface as a bare
    # {"ok": false} that taught the system nothing.
    common = {"addressed_by": "role_name",
              "target": f"{body.field_role or '*'}:{body.field_name}",
              "widget_type": WidgetType.PROMPT_HIERARCHICAL.value}
    steps: list[dict] = []

    node_id = await _resolve_ax_node(body.browser_url, body.tab_id, body.tab_url,
                                     body.field_role, body.field_name)
    steps.append({"step": "resolve", "field": body.field_name, "node": node_id})
    if node_id is None:
        return {**common, "outcome": Outcome.NOT_FOUND, "steps": steps,
                "detail": f"prompt field not found: {body.field_name!r}"}

    # 1. OPEN the prompt via the proven driver node-click (same path /execute uses) — a
    # trusted-mouse-at-box-center did NOT reliably open Workday prompt popups.
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
        steps.append({"step": "open", "search_box": bool(sb.get("found"))})
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
    steps.append({"step": "select", "option": body.value, "node": opt_node})
    if opt_node is None:
        # The searchBox flag is the diagnostic: found=no means the popup never opened
        # (not_opened); found=yes means it opened and the value isn't in the list (no_option,
        # which is a vocabulary miss -> /resolve_answer). Collapsing both into one failure is
        # what used to make this endpoint's errors un-actionable.
        return {**common, "steps": steps,
                "outcome": Outcome.NO_OPTION if sb.get("found") else Outcome.NOT_OPENED,
                "detail": f"option {body.value!r} not found "
                          f"(searchBox={'yes' if sb.get('found') else 'no'}; "
                          f"refresh if the session is stale)"}

    await get_driver("direct").move_and_act(
        browser_url=body.browser_url,
        request=ActionRequest(action_id="click", target_bbox={}, backend_node_id=opt_node),
        tab_id=body.tab_id, tab_url=body.tab_url)
    await asyncio.sleep(0.4)
    _log_event("drive", f"prompt-select '{body.field_name}' <- {body.value}",
               detail=f"searchBox={'yes' if sb.get('found') else 'no'}", domain=body.tab_url)
    # A Workday prompt applies on select (no footer), and the option node resolving by
    # accessible name after the search IS the staged confirmation.
    return {**common, "outcome": Outcome.OK, "steps": steps, "selected": body.value,
            "detail": f"searchBox={'yes' if sb.get('found') else 'no'}"}


class ProbeRequest(BaseModel):
    """DISCOVERY ONLY: run a JS expression in the tab and return its value.

    `note` is required and is the point of the endpoint: it records WHAT WE WERE TRYING TO
    LEARN, which is the part of a probe worth keeping. The expression itself is the artifact
    of a question; the question is the training signal.
    """
    browser_url: str = "http://127.0.0.1:9222"
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    expression: str
    note: str                  # e.g. "what shape is Greenhouse's 'How did you hear' widget?"
    ats: Optional[str] = None
    field: Optional[str] = None


@app.post("/probe")
@journaled(Intent.PROBE)
async def probe(body: ProbeRequest):
    """The deliberate hole in the closed vocabulary — and it stays forever.

    A closed vocabulary cannot express the novel, and discovery meets novel contracts by
    definition. The discipline is not "never script"; it is "scripting is DISCOVERY, and
    discovery ENDS IN AN ENDPOINT". This is `/eval` with the one thing `/eval` lacked: it
    leaves a trace. The ~25 /eval scripts written on 2026-07-15 appear nowhere — `eval:0` in
    the event log against `type:137` — so the knowledge in them (how a react-select commits,
    how to scope options, how to read a month) lived only in one context window and died with
    the session.

    Journaled as `kind: probe`, which makes `probe_share` in journal.summarize() a real
    metric: it should FALL as protocols land. A rising probe share means we are re-deriving
    contracts we already own.
    """
    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target

    target = await _discover_target(body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url)
    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=16 * 1024 * 1024) as ws:
        cdp = _CDPSession(ws)
        res = await cdp.send("Runtime.evaluate", {"expression": body.expression,
                                                  "returnByValue": True, "awaitPromise": True})
    exc = res.get("exceptionDetails")
    return {
        # A probe that THREW still ran — the outcome is about the probe, not the page. But a
        # thrown probe taught us nothing, so it is not OK.
        "outcome": Outcome.ERROR if exc else Outcome.OK,
        "detail": body.note[:400],
        "value": (res.get("result") or {}).get("value"),
        "exception": exc,
        # The expression rides in `steps` so the corpus keeps the actual question asked —
        # that is what a future session greps when the same widget shows up again.
        "steps": [{"step": "probe", "expression": body.expression[:600]}],
    }


class DescribeWidgetRequest(BaseModel):
    """Ask a control what it is. TIER 1 — takes resolved addressing, not (ats, field).

    `ats`/`field` are accepted but NOT used to resolve: they ride along so the journal row
    carries the semantic context. Resolution is the INTENT tier's job (see
    controlplane-api/apply_fields.resolve) — a tier-1 primitive that reached into a
    per-site recipe would put DATA inside MECHANISM, which is the layering mistake this
    whole plan is about.
    """
    browser_url: str = "http://127.0.0.1:9222"
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    selector: str
    ats: Optional[str] = None
    field: Optional[str] = None


@app.post("/describe_widget")
@journaled(Intent.DESCRIBE)
async def describe_widget(body: DescribeWidgetRequest):
    """THE missing primitive: one structured probe that replaces the ~11 hand-written ones.

    Returns the widget's own account of itself — what it IS (`widget_type`), where its TRUTH
    lives (`value_read_at`), how it OPENS (`opens_on`), which popup it OWNS (`popup.source`),
    and whether selecting stages or applies (`commit.kind`).

    READ-ONLY: it does not open the widget. See app/widget_probe.py for why that differs
    from the plan's sketch, and what it means for `options`.

    `unknown` is not a failure — it routes to /probe, and the probe's output is a new
    widget_type in the classifier. The classifier is the flywheel's memory of widget shapes.
    """
    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target
    from app.widget_probe import DESCRIBE_WIDGET_JS

    target = await _discover_target(body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url)
    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=16 * 1024 * 1024) as ws:
        cdp = _CDPSession(ws)
        r = await cdp.send("Runtime.evaluate", {
            "expression": f"({DESCRIBE_WIDGET_JS})({json.dumps({'selector': body.selector})})",
            "returnByValue": True})
    out = (r.get("result") or {}).get("value") or {}

    if not out.get("found"):
        return {"outcome": Outcome.NOT_FOUND, "addressed_by": "selector", "target": body.selector,
                "detail": out.get("detail") or f"no node matching {body.selector!r}"}
    wt = out.get("widget_type") or WidgetType.UNKNOWN.value
    return {
        # A widget we cannot classify is a real answer, not an error — but it is NOT `ok`,
        # because the caller cannot act on it. It routes to /probe, and the probe's output
        # becomes a new widget_type here.
        "outcome": Outcome.OK if wt != WidgetType.UNKNOWN.value else Outcome.NOT_FOUND,
        "widget_type": wt,
        "addressed_by": "selector", "target": body.selector,
        "detail": (f"{wt} · {out.get('label','')[:40]} · required={out.get('required')} "
                   f"({out.get('required_via')}) · answered={out.get('answered')}"),
        "steps": [{"step": "classify", "widget_type": wt,
                   "value_read_at": out.get("value_read_at"), "opens_on": out.get("opens_on")}],
        **{k: v for k, v in out.items() if k not in ("found", "widget_type", "detail")},
    }


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


class JobDescriptionRequest(BaseModel):
    external_id: str                     # Indeed jk
    browser_url: str = "http://127.0.0.1:9222"


# Scrape a single Indeed job's detail page (viewjob): full description + salary + whether
# it's Indeed Quick Apply vs a company-site redirect (the apply_type that decides which
# apply FLOW the planner uses). This is the "click into the posting" the operator does by hand.
_JOB_DESC_JS = r"""
(() => {
  // SCOPE to the open detail PANE, not the document. On the SERP the left results list holds one
  // salary/title node PER card, so a document-wide read grabs the FIRST card's — which is why
  // opening any job returned the previous (first) job's salary (2026-07-17). The pane wrapper is
  // present only on the SERP; on the standalone /viewjob page there is no wrapper and the whole
  // document IS the one job, so root falls back to document there. `#jobDescriptionText` is a
  // unique id (safe either way), but title/company/salary MUST be pane-scoped.
  //
  // INDEED REDESIGNED THE PANE (met live 2026-07-25). The old wrapper was addressed by a
  // data-testid; the redesign ships it as an ID with different casing —
  // `#jobsearch-ViewjobPaneWrapper` (lowercase j) — so this selector missed and `root` silently
  // fell back to `document`. Nothing errored: the reader just returned '' for every field, and
  // /open_job_card, which calls a job open only when it can read a description back, reported
  // "no pane" about a pane that was fully rendered and correct. Both spellings stay: the old
  // DOM is still served on some routes.
  const root = document.querySelector(
    '#jobsearch-ViewjobPaneWrapper, #rnvjContainerDesktop,'
    + '[data-testid=jobsearch-ViewJobPaneWrapper], .jobsearch-RightPane, .jobsearch-JobComponent'
  ) || document;

  // Try selectors in PRIORITY order — NOT `querySelector('a, b, h1')`, which returns the first
  // match in DOCUMENT order regardless of list position. That exact trap put the SERP's search
  // <h1> ("data analyst jobs in Nashua, NH") ahead of the pane's own header, because the h1
  // sits earlier in the tree. `pick` honours our order instead of the DOM's.
  const pick = (el, sels) => {
    for (const s of sels) { const n = el.querySelector(s); if (n && n.innerText.trim()) return n.innerText.trim(); }
    return '';
  };

  // The redesign kept almost NO ids inside the pane (one, `rnvjContainerDesktop`) and moved to
  // `vj-*` data-testids. Old selectors stay FIRST so the un-redesigned DOM keeps its exact
  // behaviour; the new ones are appended as fallbacks.
  let description = pick(root, ['#jobDescriptionText', '[id*=jobDescription]',
                                '.jobsearch-JobComponent-description']);
  if (!description) {
    // The redesign exposes only a HEADING testid ("Full job description", ~20 chars). The body is
    // its parent — so walk up until a node actually holds the description, and take the smallest
    // such node rather than the first: the outer wrappers include the header, salary and the
    // Apply button, and swallowing those makes every job's description look alike to the corpus.
    // The posting's HTML arrives with a <style> block (`@layer htmlContent { ... }`) inside the
    // description container, and innerText renders it as text — 2.7k of CSS at the head of every
    // description, identical across jobs. Read from a CLONE with style/script stripped so the
    // corpus stores the posting, not Indeed's stylesheet.
    const clean = (el) => {
      const c = el.cloneNode(true);
      c.querySelectorAll('style, script, noscript').forEach(s => s.remove());
      return (c.innerText || c.textContent || '').replace(/^\s*Full job description\s*/i, '').trim();
    };
    let n = root.querySelector('[data-testid=vj-job-description-heading]');
    for (let i = 0; i < 4 && n; i++) {
      n = n.parentElement;
      const txt = n ? clean(n) : '';
      if (txt.length > 200) { description = txt; break; }
    }
  }

  const header = pick(root, ['[data-testid=desktop-job-header]']);
  let salary = pick(root, ['#salaryInfoAndJobType', '[id*=salaryInfo]', '[class*=salary]']);
  if (!salary && header) {
    // No salary node survives the redesign; the header line carries it as text.
    const m = header.match(/\$[\d,.]+(?:\s*-\s*\$[\d,.]+)?\s*(?:an hour|a year|a month|a week|per hour|per year)/i)
           || header.match(/(?:from|up to)\s+\$[\d,.]+\s*(?:an hour|a year)/i);
    if (m) salary = m[0].trim();
  }
  const title = pick(root, ['#vjs-jobtitle', '[data-testid="jobsearch-JobInfoHeader-title"]',
                            'h2.jobsearch-JobInfoHeader-title',
                            '[data-testid=vj-job-title]', '[data-testid=vj-job-title-compact]',
                            'h1', 'h2']);
  const company = pick(root, ['[data-testid=inlineHeader-companyName]',
                              '[data-testid=jobsearch-CompanyInfoContainer] a',
                              '[data-company-name]', '.jobsearch-CompanyInfoWithoutHeaderImage a',
                              'a[href*="/cmp/"]']);

  // apply_type drives the routing: 'company_site' → cross-site ATS (Workday/...), 'quick_apply' →
  // Indeed-native (smartapply, end-to-end driveable). "Apply with Indeed" is quick-apply too. Read
  // the pane's buttons first; fall back to document only if the pane names no apply control (the
  // /viewjob page keeps its apply button outside these wrappers).
  const buttonsIn = (el) => Array.from(el.querySelectorAll('button, a'))
    .map(b => (b.innerText || b.getAttribute('aria-label') || '').trim()).join(' | ').toLowerCase();
  const classify = (t) => /apply on company site|apply on employer|you are leaving indeed/.test(t) ? 'company_site'
                        : /apply with indeed|easily apply|apply now/.test(t) ? 'quick_apply' : 'unknown';
  let apply_type = classify(buttonsIn(root));
  if (apply_type === 'unknown' && root !== document) apply_type = classify(buttonsIn(document));

  return { description, salary, title, company, apply_type };
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
    # Escape hatch, OFF by default. A silent URL rewrite is what hid a broken widget path for weeks:
    # every caller got radius=N and nobody learned the pill had stopped working. Opt in explicitly
    # when guaranteeing the floor genuinely matters more than knowing the truth.
    allow_url_fallback: bool = False


# ── The staged-commit popup protocol ───────────────────────────────────────────────────────────────
# Indeed's distance pill is an ARIA listbox popup (#radius_filter_button → ul[role=listbox] of
# li[role=option] "Within N miles") with a Reset/Update FOOTER. Mapped live 2026-07-15; every rule
# below cost a failed attempt, and they generalize to Workday + unknown-ATS popups:
#
#  1. THE POPUP WILL NOT RENDER IN A HIDDEN TAB. document.visibilityState must be 'visible'
#     (Page.bringToFront) or the opener click no-ops. A human's tab is visible when they click it.
#  2. .click() DOES NOT FOCUS. A real mousedown focuses; the synthetic one doesn't, and without focus
#     the widget's own keyboard protocol (aria-activedescendant) is dead. focus() THEN click().
#  3. THE POPUP DISMISSES ON BLUR, so it cannot survive HTTP round-trips: open→select→commit must run
#     page-side in ONE evaluation, not as separate calls.
#  4. SELECTING ONLY STAGES THE VALUE. The footer's Update button is what commits. This is why the old
#     fiber-prop hack "worked" (it did select) yet nothing ever applied — and why the URL never moved.
#  5. THE COMMIT DESTROYS ITS OWN OBSERVER: Update triggers a full navigation, so the page-side code
#     can't see the result. "Inspected target navigated" IS the success signal; CONFIRM FROM OUTSIDE.
#
# What earlier attempts got wrong: a trusted mouse click at the box centre (coordinates go stale the
# moment the menu re-renders — it landed outside and dismissed the popup), and reading React fiber
# props (Indeed's internals moved). Neither is needed. Identify by ARIA/CSS semantics, drive natively,
# confirm every step.
_POPUP_SELECT_JS = r"""
(async (cfg) => {
  const log = [];
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const until = async (fn, tries = 25, ms = 200) => {
    for (let i = 0; i < tries; i++) { const v = fn(); if (v) return v; await sleep(ms); }
    return null;
  };
  const visible = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };

  const opener = document.querySelector(cfg.opener_selector);
  if (!opener) return {ok: false, log, detail: `no opener matching ${cfg.opener_selector}`};

  // SCOPE: the widget tells us which popup it owns via aria-controls/aria-owns. Without this we
  // search options document-wide and can click ANOTHER widget's identically-named option (a Workday
  // page had 63 stray [role=option]s from other fields). Fall back to document scope only when the
  // widget declares no relationship (Indeed's distance pill doesn't).
  const scope = () => {
    const ref = opener.getAttribute('aria-controls') || opener.getAttribute('aria-owns');
    const el = ref ? document.getElementById(ref) : null;
    return el || document;
  };
  const OPTS = () => [...scope().querySelectorAll(cfg.option_selector)].filter(visible);

  log.push({step: 'precheck', visibility: document.visibilityState, hasFocus: document.hasFocus()});
  // NOTE: a hidden tab breaks SOME widgets (Indeed's pill won't render) but not others (Workday's
  // listbox opens fine). Report it; let the caller decide — don't refuse outright.

  // OPEN — focus like a real mousedown would (.click() alone does NOT focus), then native click.
  // "Is it already open?" must come from THE OPENER (aria-expanded), never from an option count:
  // before opening there is no aria-controls, so OPTS() falls back to document scope and counts
  // OTHER widgets' stray options — which read as "already open" and skipped the click entirely.
  opener.scrollIntoView({block: 'center'});
  opener.focus();
  const expanded = () => opener.getAttribute('aria-expanded');
  // Count document-scope options BEFORE the click: the DELTA is the honest open signal for a
  // widget whose ARIA lies. smartapply's EEO combobox declares aria-expanded and then never
  // flips it — it renders its options in a portal while the attribute stays "false" — so all
  // three original open-clauses failed on a popup that was visibly on screen (found live
  // 2026-07-17). Options that exist only AFTER our click are ours; the pre-existing ones are
  // the Workday strays the scope rule exists for.
  const preCount = OPTS().length;
  const isOpen = () => expanded() === 'true' || (scope() !== document && OPTS().length > 0)
                       || (scope() === document && expanded() === null && OPTS().length > 0)
                       || (scope() === document && OPTS().length > preCount);
  // assume_open: the caller ALREADY opened the popup with a TRUSTED mouse click (a synthetic
  // page-side .click() does not register on some widgets — smartapply's EEO combobox, like
  // Workday's prompts before it). Skip our own open; just confirm something is showing.
  if (cfg.assume_open) {
    // NB not isOpen(): the popup is already open when this script starts, so preCount counted
    // the OPEN popup and the delta clause can never fire. Options being visible IS the signal
    // here — the trusted click already happened, and exact-match + the stage-confirm guard
    // against acting on strays.
    const ok0 = await until(() => OPTS().length > 0, 10);
    if (!ok0) return {ok: false, log: [...log, {step: 'open', assumed: true, n_options: OPTS().length}],
                      detail: 'popup did not open (even after a trusted-mouse open)'};
    log.push({step: 'open', assumed: true, n_options: OPTS().length, trusted: true});
    // fall through to SELECT below
  } else {
  if (expanded() !== 'true') opener.click();
  // Wait for the widget's OWN popup: aria-expanded flipping, options inside a resolved scope,
  // options appearing where the widget declares nothing — or NEW options under a lying ARIA.
  let opened = await until(isOpen, 12);
  // THE FOCUS-TOGGLE CASE (smartapply's EEO combobox, live 2026-07-17): some comboboxes open
  // on FOCUS — so the protocol's own focus() opened it and the click() TOGGLED IT CLOSED.
  // The picker that historically worked here clicked WITHOUT focusing first. We keep
  // focus-then-click (Workday's keyboard protocol needs the focus), and when the popup
  // hasn't shown, click once more: on a toggler that re-opens it; on a genuinely dead
  // widget it changes nothing and we still report not_opened honestly.
  if (!opened) {
    log.push({step: 'open', retry: 'reclick (focus may have toggled it shut)'});
    opener.click();
    opened = await until(isOpen, 13);
  }
  if (!opened)
    return {ok: false, log: [...log, {step: 'open', n_options: OPTS().length, expanded: expanded()}],
            detail: 'popup did not open'};
  log.push({step: 'open', n_options: OPTS().length, scoped: scope() !== document, expanded: expanded()});
  if (OPTS().length === 0)
    return {ok: false, log, detail: `popup opened but no node matches option_selector `
            + `(${cfg.option_selector}) — this widget may render plain divs, not [role=option]`};
  }  // end of the self-open path (assume_open skips it)

  // SELECT — exact match first (a "Mobile" must not match "Mobile Phone"), then prefix.
  const txt = (o) => (o.innerText || '').trim();
  const find = () => OPTS().find(o => txt(o) === cfg.option_label)
                  || OPTS().find(o => txt(o).startsWith(cfg.option_label));
  if (!find())
    return {ok: false, log, detail: `no option "${cfg.option_label}"`, options: OPTS().map(txt)};
  const before = (opener.innerText || '').trim();
  find().click();

  // CONFIRM it took. Two honest signals, because neither is universal: aria-selected on the option
  // (Indeed) or the opener's own label changing to the choice (Workday). textContent alone is NOT
  // trustworthy on every Workday dropdown — where it isn't, aria-selected carries it.
  const staged = await until(() => {
    const o = find();
    if (o && (o.getAttribute('aria-selected') === 'true' || o.getAttribute('aria-checked') === 'true')) return 'aria';
    const now = (opener.innerText || '').trim();
    if (now !== before && now.includes(cfg.option_label)) return 'opener_label';
    return null;
  });
  log.push({step: 'select', label: cfg.option_label, staged: !!staged, via: staged || null,
            opener_text: (opener.innerText || '').trim().slice(0, 40)});
  if (!staged) return {ok: false, log, detail: 'option would not stage (no aria-selected, opener label unchanged)'};

  // COMMIT — the popup's own footer button. Absent = applies on select (the Workday case).
  const commit = cfg.commit_names.length ? [...document.querySelectorAll('button')]
    .find(b => cfg.commit_names.some(n => new RegExp(`^${n}$`, 'i').test((b.innerText || '').trim()))) : null;
  if (!commit) { log.push({step: 'commit', found: false, note: 'no footer button — applies on select'});
                 return {ok: true, log, detail: 'selected (applies on select)'}; }
  log.push({step: 'commit', found: true, disabled: commit.disabled});
  commit.click();     // may navigate → this context dies here; confirm from outside
  return {ok: true, log, detail: 'commit clicked'};
})(%s)
"""


def _popup_outcome(res: dict) -> Outcome:
    """Map _POPUP_SELECT_JS's result onto the outcome taxonomy.

    The JS already knows precisely which step broke — it has always said so in `detail` and
    thrown the information away at the HTTP boundary, where `ok:false` flattened six distinct
    failures into one bit. This function is that information being kept.

    The `commit clicked` case is the interesting one: the JS returns ok:true there, but its
    own comment says the commit navigates and destroys the context that would confirm it. So
    it maps to COMMITTED_UNCONFIRMED, not OK — see contract.Outcome.
    """
    detail = (res.get("detail") or "").lower()
    if not res.get("ok"):
        if "no opener matching" in detail:
            return Outcome.NOT_FOUND          # the recipe's selector is stale
        if "popup did not open" in detail:
            return Outcome.NOT_OPENED
        if "no node matches option_selector" in detail:
            # It opened, but nothing matches the option selector — the widget is not the
            # shape we assumed (it may render plain divs). The caller's move is not_opened's
            # ("this widget works differently than you think"), which is why it lands here
            # rather than on no_option: /resolve_answer cannot help with a shape mismatch.
            return Outcome.NOT_OPENED
        if "would not stage" in detail:
            return Outcome.NOT_STAGED
        if "no option" in detail:
            return Outcome.NO_OPTION          # a genuine vocabulary miss -> /resolve_answer
        return Outcome.ERROR
    # ok:true — but only ONE of the two success paths is actually verified.
    if "applies on select" in detail:
        return Outcome.OK                     # staged-confirm ran: aria-selected / opener label
    return Outcome.COMMITTED_UNCONFIRMED      # "commit clicked" / "navigated (commit fired)"


class WidgetSelectRequest(BaseModel):
    """Drive ANY staged-commit / listbox popup by its own semantics. The reusable widget layer:
    opener → (aria-controls) popup → option → confirm → optional footer commit."""
    browser_url: str = "http://127.0.0.1:9222"
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    opener_selector: str                 # e.g. '[data-automation-id="formField-phoneType"] button'
    option_label: str                    # e.g. 'Mobile'
    option_selector: str = ("li[role=option], [role=option], [data-automation-id=promptOption], "
                            # bare <li>s under a [role=listbox]: smartapply's EEO combobox
                            # renders these with NO role=option — the picker that worked
                            # (autofill) always included them; the protocol didn't, so it
                            # saw n_options=0 forever on an open popup (live, 2026-07-17)
                            "[role=listbox] li")
    commit_names: list[str] = []         # e.g. ['Update','Apply'] — empty = applies on select
    bring_to_front: bool = True


@app.post("/widget_select")
@journaled(Intent.SELECT_OPTION)
async def widget_select(body: WidgetSelectRequest):
    """Select an option in a popup widget, confirming each step. One layer for Indeed's distance
    pill, Workday's dropdowns, and the next unknown-ATS filter — the site-specific part is just the
    selectors, which belong in that ATS's recipe.

    The per-step trace (precheck → open → select → commit) is now JOURNALED rather than
    returned-and-forgotten. That trace is also exactly the intermediate-state vocabulary L3
    lacks (`popup_open`, `option_staged`) — which is why the loop cannot currently verify its
    own progress through a multi-step widget. It has been produced on every call for weeks and
    written down nowhere.
    """
    import asyncio

    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target

    # NB: no try/except — @journaled catches, journals as Outcome.ERROR, and returns the
    # error response. A local `except: return {"ok": False}` here would swallow the row.
    target = await _discover_target(body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url)
    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024) as ws:
        cdp = _CDPSession(ws)
        await cdp.send("Page.enable", {})
        await cdp.send("Runtime.enable", {})
        if body.bring_to_front:
            await cdp.send("Page.bringToFront", {})
            await asyncio.sleep(0.3)
        res = await _popup_select(cdp, {
            "opener_selector": body.opener_selector,
            "option_selector": body.option_selector,
            "option_label": body.option_label,
            "commit_names": body.commit_names,
        })
        # TRUSTED-OPEN FALLBACK. Some widgets ignore a synthetic page-side .click() entirely
        # (smartapply's EEO combobox — the same class of widget as Workday's prompts, whose
        # options "only register TRUSTED CDP mouse events"). Page-side JS cannot dispatch a
        # trusted event, so when the one-shot script reports not_opened we open with a real
        # CDP mouse click here, then re-run the script with assume_open to stage + confirm.
        # Two evaluations are safe: the popup dismisses on BLUR, and nothing here blurs.
        if not res.get("ok") and "did not open" in (res.get("detail") or ""):
            center = (await cdp.send("Runtime.evaluate", {"returnByValue": True, "expression":
                "(() => { const el = document.querySelector(" + json.dumps(body.opener_selector) + ");"
                " if (!el) return null; el.scrollIntoView({block:'center'});"
                " const r = el.getBoundingClientRect();"
                " return {x: r.x + r.width/2, y: r.y + r.height/2}; })()"
            })).get("result", {}).get("value")
            if center:
                await _trusted_click(cdp, center["x"], center["y"])
                await asyncio.sleep(0.8)
                res2 = await _popup_select(cdp, {
                    "opener_selector": body.opener_selector,
                    "option_selector": body.option_selector,
                    "option_label": body.option_label,
                    "commit_names": body.commit_names,
                    "assume_open": True,
                })
                res2["log"] = (res.get("log") or []) + [{"step": "open", "via": "trusted_mouse"}] \
                              + (res2.get("log") or [])
                res = res2
    _log_event("drive", f"widget_select {body.option_label}",
               detail=f"{'ok' if res.get('ok') else 'FAILED'} · {res.get('detail','')}",
               domain=(body.tab_url or ""))
    return {
        "outcome": _popup_outcome(res),
        "addressed_by": "selector", "target": body.opener_selector,
        "steps": res.get("log") or [],
        "detail": res.get("detail", ""),
        **({"options": res["options"]} if "options" in res else {}),
    }


# --- TIER 2: the protocols. Site-agnostic, dispatching on widget_type. ----------------
class SelectOptionRequest(BaseModel):
    """Choose `value` in ANY option widget. Absorbs /widget_select + /select_prompt +
    the react-select dance that was inline-only.

    `widget_type` is the dispatch key. Omit it and the endpoint asks /describe_widget's
    classifier first — that costs one extra evaluate and is the honest default, because a
    caller that guesses wrong is the bug we are removing.
    """
    browser_url: str = "http://127.0.0.1:9222"
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    selector: str
    value: str
    widget_type: Optional[str] = None
    commit: Optional[str] = None          # footer button label; None = applies on select
    option_selector: str = "li[role=option], [role=option], [data-automation-id=promptOption], [class*=select__option], [role=listbox] li"
    bring_to_front: bool = True
    ats: Optional[str] = None
    field: Optional[str] = None


async def _classify(cdp, selector: str) -> dict:
    """Run the /describe_widget classifier in an existing CDP session."""
    from app.widget_probe import DESCRIBE_WIDGET_JS
    r = await cdp.send("Runtime.evaluate", {
        "expression": f"({DESCRIBE_WIDGET_JS})({json.dumps({'selector': selector})})",
        "returnByValue": True})
    return (r.get("result") or {}).get("value") or {}


@app.post("/select_option")
@journaled(Intent.SELECT_OPTION)
async def select_option(body: SelectOptionRequest):
    """ONE endpoint for every option widget — the caller stops needing to know which it is.

    Dispatch:
      react_select / month_year  → per-char keystrokes, exact-match, verify at singleValue
      prompt_hierarchical        → the Workday prompt protocol (native open + trusted search)
      aria_listbox / native_select → the staged-commit popup protocol (aria-controls scoped)

    This is the generalization the plan asks for, earned at the SECOND site rather than
    designed up front: _POPUP_SELECT_JS was right to start Indeed-only, then Workday forced
    aria-controls scoping, then Greenhouse forced keystroke-opening. Frozen after Indeed it
    would be wrong; delayed until "perfect" it would be three scripts.
    """
    import asyncio

    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target
    from app.protocols import react_select_pick

    common = {"addressed_by": "selector", "target": body.selector}
    target = await _discover_target(body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url)
    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=16 * 1024 * 1024) as ws:
        cdp = _CDPSession(ws)
        await cdp.send("Page.enable", {})
        await cdp.send("Runtime.enable", {})
        if body.bring_to_front:
            # A popup WILL NOT RENDER in a hidden tab — document.visibilityState must be
            # 'visible' or the opener click no-ops. A human's tab is visible when they click.
            await cdp.send("Page.bringToFront", {})
            await asyncio.sleep(0.3)

        wt = body.widget_type
        commit = body.commit
        if not wt:
            desc = await _classify(cdp, body.selector)
            if not desc.get("found"):
                return {**common, "outcome": Outcome.NOT_FOUND,
                        "detail": f"no node matching {body.selector!r}"}
            wt = desc.get("widget_type")
            if commit is None and (desc.get("commit") or {}).get("kind") == "footer_button":
                commit = (desc["commit"] or {}).get("label")
        common["widget_type"] = wt

        if wt in (WidgetType.REACT_SELECT.value, WidgetType.MONTH_YEAR.value):
            outcome, steps, detail = await react_select_pick(
                cdp, selector=body.selector, value=body.value)
            return {**common, "outcome": outcome, "steps": steps, "detail": detail,
                    "actions": ["clear", "type", "click"]}

        if wt == WidgetType.UNKNOWN.value:
            # Refuse rather than guess. An unclassified widget driven by a guessed protocol
            # is how every one of 2026-07-15's bugs started.
            return {**common, "outcome": Outcome.NOT_FOUND,
                    "detail": "widget_type=unknown — refusing to guess a protocol. Send a "
                              "/probe to learn its shape, then add it to the classifier."}

    # The listbox/prompt paths open their own sessions (the prompt protocol re-discovers the
    # target after its native click), so they run outside the block above.
    if wt == WidgetType.PROMPT_HIERARCHICAL.value:
        inner = await select_prompt(SelectPromptRequest(
            browser_url=body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url,
            field_role=None, field_name=body.field or body.value, value=body.value))
        return {**common, "outcome": inner.get("outcome", Outcome.ERROR),
                "steps": inner.get("steps", []), "detail": inner.get("detail", ""),
                "actions": ["click", "type", "click"]}

    inner = await widget_select(WidgetSelectRequest(
        browser_url=body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url,
        opener_selector=body.selector, option_label=body.value,
        option_selector=body.option_selector,
        commit_names=[commit] if commit else [], bring_to_front=body.bring_to_front))
    return {**common, "outcome": inner.get("outcome", Outcome.ERROR),
            "steps": inner.get("steps", []), "detail": inner.get("detail", ""),
            "actions": ["click", "click"] + (["click"] if commit else []),
            **({"options": inner["options"]} if "options" in inner else {})}


class CheckGroupRequest(BaseModel):
    """Set a required checkbox group to exactly `values`. Exact label match."""
    browser_url: str = "http://127.0.0.1:9222"
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    selector: str                  # any checkbox in the group, or the group's wrapper
    values: list[str]
    ats: Optional[str] = None
    field: Optional[str] = None


@app.post("/check_group")
@journaled(Intent.CHECK_GROUP)
async def check_group(body: CheckGroupRequest):
    """Required checkbox groups — the thing the old scan missed ENTIRELY.

    `restrictions` and `languages` on KKR are required checkbox groups, and a scan that only
    looked at inputs/selects never saw them. Groups by the id prefix before '[]', matches
    labels EXACTLY ("No" must not match "Yes, non-compete"), toggles by click so React sees
    the event, and re-reads the DOM to confirm rather than trusting the clicks.
    """
    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target
    from app.protocols import CHECK_GROUP_JS

    common = {"addressed_by": "selector", "target": body.selector,
              "widget_type": WidgetType.CHECKBOX_GROUP.value}
    target = await _discover_target(body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url)
    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=16 * 1024 * 1024) as ws:
        cdp = _CDPSession(ws)
        r = await cdp.send("Runtime.evaluate", {
            "expression": f"({CHECK_GROUP_JS})({json.dumps({'selector': body.selector, 'values': body.values})})",
            "returnByValue": True})
    out = (r.get("result") or {}).get("value") or {}
    return {**common,
            "outcome": Outcome(out.get("code") or "error"),
            "steps": out.get("log") or [], "detail": out.get("detail", ""),
            "actions": ["click"] * len(body.values),
            **({"options": out["options"]} if "options" in out else {}),
            **({"checked": out["checked"]} if "checked" in out else {})}


class ScanRequiredRequest(BaseModel):
    browser_url: str = "http://127.0.0.1:9222"
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    ats: Optional[str] = None


@app.post("/scan_required")
@journaled(Intent.SCAN_REQUIRED)
async def scan_required(body: ScanRequiredRequest):
    """The required fields that are NOT satisfied. The live source for form_complete_gate.

    Replaced `/scan_form` (deleted 2026-07-16), which labelled every control with its
    CONTAINER's text — so on Workday First/Middle/Last were indistinguishable, and on
    Greenhouse 14 language checkboxes each became a separate required field. Measured on
    KKR's live form: /scan_form reported 21 fields / 18 "required and unfilled" and would
    have made this gate permanently un-passable, while missing ~16 real required fields it
    never found containers for. This reported 1 — the truth.

    Labels PER CONTROL (label[for] → aria-label → aria-labelledby → …), applies `disabled`
    beats a stale asterisk, counts checkbox/radio groups, reads each widget at its OWN truth
    (never `.value` on a react-select), and skips validation proxies the user cannot tab to.

    "Unsatisfied", not merely "empty": a required field that is FILLED but INVALID is
    reported too (with answered=true, valid=false), because the gate's rule is
    `satisfied = (not required) or (filled and valid)`.

    Read-only. Returns [] when the form is complete, which is a real answer — the invariant
    gate's whole job is refusing to mark a form done while this is non-empty.
    """
    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target
    from app.protocols import SCAN_REQUIRED_JS

    target = await _discover_target(body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url)
    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=16 * 1024 * 1024) as ws:
        cdp = _CDPSession(ws)
        r = await cdp.send("Runtime.evaluate", {"expression": f"({SCAN_REQUIRED_JS})()",
                                                "returnByValue": True})
    out = (r.get("result") or {}).get("value") or {}
    unanswered = out.get("unanswered") or []
    return {"outcome": Outcome.OK, "unanswered": unanswered, "count": len(unanswered),
            "detail": (f"{len(unanswered)} required field(s) unanswered: "
                       f"{[u['field'] for u in unanswered][:6]}" if unanswered
                       else "all required fields answered"),
            "steps": [{"step": "scan", "unanswered": len(unanswered), "url": out.get("url")}]}


class SetDateRequest(BaseModel):
    """Set a date. The caller says {month, year}; the API figures out the shape."""
    browser_url: str = "http://127.0.0.1:9222"
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    selector: str
    month: int                      # 1-12
    year: int
    day: Optional[int] = None
    widget_type: Optional[str] = None
    ats: Optional[str] = None
    field: Optional[str] = None


#: Greenhouse's month react-select wants the NAME — typing "08" yields ZERO options. The
#: caller says month=8 and never has to know that.
_MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"]


@app.post("/set_date")
@journaled(Intent.SET_DATE)
async def set_date(body: SetDateRequest):
    """Three known date shapes, one intent, verified at commit.

    month_year (Greenhouse)  — month is a REACT-SELECT wanting "August" (typing "08" yields
                               zero options); year is a plain number input. Two widgets, one
                               date. The month's transient text READS BACK like a value and
                               clears on blur, so the month verifies at singleValue and the
                               year at .value.
    segmented_date (Workday) — dateSectionMonth/Day/Year spinbuttons, linked and
                               auto-advancing. CDP click+type+backspace scrambles across the
                               sub-fields ("12//", "//2012"). Still routed to the operator —
                               see below.
    text                     — plain MM/YYYY.
    """
    import asyncio

    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target
    from app.protocols import react_select_pick

    if not 1 <= body.month <= 12:
        return {"outcome": Outcome.NO_OPTION, "detail": f"month {body.month} out of range 1-12"}
    common = {"addressed_by": "selector", "target": body.selector}
    target = await _discover_target(body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url)
    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=16 * 1024 * 1024) as ws:
        cdp = _CDPSession(ws)
        await cdp.send("Page.enable", {})
        await cdp.send("Runtime.enable", {})
        await cdp.send("Page.bringToFront", {})
        await asyncio.sleep(0.2)

        desc = await _classify(cdp, body.selector)
        if not desc.get("found"):
            return {**common, "outcome": Outcome.NOT_FOUND,
                    "detail": f"no node matching {body.selector!r}"}
        wt = body.widget_type or desc.get("widget_type")
        common["widget_type"] = wt

        if wt == WidgetType.SEGMENTED_DATE.value:
            # Honest refusal, not a silent best-effort. WORKDAY_LESSONS lists this as a live
            # gap: the sub-fields are linked and auto-advance, and CDP typing scrambles across
            # them. A protocol that "tries anyway" would produce 12// and report success —
            # which is the exact bug class this API exists to remove.
            return {**common, "outcome": Outcome.BLOCKED,
                    "detail": "Workday segmented date (linked auto-advancing spinbuttons) — CDP "
                              "typing scrambles across sub-fields ('12//', '//2012'). Unsolved; "
                              "route to the operator. Do not 'try anyway'.",
                    "steps": [{"step": "precheck", "widget_type": wt, "known_gap": True}]}

        if wt == WidgetType.MONTH_YEAR.value:
            steps: list[dict] = []
            month_name = _MONTH_NAMES[body.month - 1]
            outcome, msteps, mdetail = await react_select_pick(
                cdp, selector=body.selector, value=month_name)
            steps += [{**s, "part": "month"} for s in msteps]
            if outcome != Outcome.OK:
                return {**common, "outcome": outcome, "steps": steps,
                        "detail": f"month: {mdetail}"}

            year_sel = desc.get("companion_selector")
            if not year_sel:
                return {**common, "outcome": Outcome.NOT_FOUND, "steps": steps,
                        "detail": f"month set to {month_name}, but no companion year input "
                                  f"was found next to {body.selector!r} — date is HALF SET"}
            # The year is a plain number input: set react-safely, then verify at .value
            # (unlike the month, .value IS the truth here — different widget, different rule).
            set_year = (
                "(() => {"
                f"  const el = document.querySelector({json.dumps(year_sel)});"
                "   if (!el) return null;"
                "   const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;"
                f"  set.call(el, {json.dumps(str(body.year))});"
                "   el.dispatchEvent(new Event('input', {bubbles: true}));"
                "   el.dispatchEvent(new Event('change', {bubbles: true}));"
                "   el.dispatchEvent(new Event('blur', {bubbles: true}));"
                "   return el.value;"
                "})()"
            )
            r = await cdp.send("Runtime.evaluate", {"expression": set_year, "returnByValue": True})
            got = (r.get("result") or {}).get("value")
            steps.append({"step": "commit", "part": "year", "value_read_at": ".value",
                          "observed": got})
            if str(got) != str(body.year):
                return {**common, "outcome": Outcome.NOT_STAGED, "steps": steps,
                        "detail": f"month={month_name} took but year reads {got!r} not {body.year}"}
            return {**common, "outcome": Outcome.OK, "steps": steps,
                    "actions": ["clear", "type", "click", "type"],
                    "detail": f"{month_name} {body.year} (month verified at singleValue, "
                              f"year at .value)"}

        return {**common, "outcome": Outcome.NOT_FOUND,
                "detail": f"no date protocol for widget_type={wt!r} — send a /probe and add one"}


DISTANCE_OPTIONS = [0, 5, 10, 15, 25, 35, 50, 100]     # Indeed's own ladder, in miles


async def _read_radius(browser_url: str, tab_id, tab_url) -> Optional[int]:
    """Read radius from the tab list — from OUTSIDE the page, since the commit navigates and tears
    down any execution context we'd otherwise ask. The URL is CONFIRMATION, never the mechanism."""
    from urllib.parse import parse_qs, urlparse

    import httpx
    async with httpx.AsyncClient(timeout=8.0) as client:
        tabs = (await client.get(f"{browser_url.rstrip('/')}/json/list")).json()
    for t in tabs:
        if t.get("type") != "page":
            continue
        url = t.get("url") or ""
        if tab_id and t.get("id") != tab_id:
            continue
        if tab_url and tab_url not in url:
            continue
        raw = parse_qs(urlparse(url).query).get("radius", [None])[0]
        return int(raw) if (raw or "").isdigit() else None
    return None


async def _popup_select(cdp, cfg: dict) -> dict:
    """Run the staged-commit popup protocol page-side in ONE evaluation (it dismisses on blur).
    A 'target navigated' error IS the commit firing — the caller confirms from outside."""
    try:
        res = await cdp.send("Runtime.evaluate", {
            "expression": _POPUP_SELECT_JS % json.dumps(cfg),
            "returnByValue": True, "awaitPromise": True})
        return (res.get("result") or {}).get("value") or {}
    except Exception as exc:  # noqa: BLE001
        if "navigated" in str(exc).lower():
            return {"ok": True, "log": [{"step": "commit", "note": "context torn down by navigation"}],
                    "detail": "navigated (commit fired)"}
        raise


@app.post("/set_distance")
async def set_distance(body: SetDistanceRequest):
    """Set the search radius to the smallest offered option >= min_miles, BY OPERATING THE PILL —
    open it, select the option, confirm it staged, click Update, then confirm from the URL.

    The URL is confirmation, not the mechanism. A same-tab radius= rewrite is available only via
    allow_url_fallback=True: it used to be a silent last resort, which is exactly how a fully broken
    widget path went unnoticed — every caller still got its radius. A widget failure should be LOUD.

    Returns {applied, selected_miles, method, detail, log} — `log` is the per-step trace.
    """
    import asyncio

    import websockets
    from app.observer.ax_proposer import _CDPSession, _discover_target

    target_miles = next((m for m in DISTANCE_OPTIONS if m >= body.min_miles), DISTANCE_OPTIONS[-1])
    try:
        current = await _read_radius(body.browser_url, body.tab_id, body.tab_url)
        if current is not None and current >= body.min_miles:
            return {"ok": True, "applied": True, "selected_miles": current, "method": "already",
                    "detail": f"radius already {current} (>= {body.min_miles})", "log": []}

        target = await _discover_target(body.browser_url, tab_id=body.tab_id, tab_url=body.tab_url)
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024) as ws:
            cdp = _CDPSession(ws)
            await cdp.send("Page.enable", {})
            await cdp.send("Runtime.enable", {})
            await cdp.send("Page.bringToFront", {})   # the popup will not render in a hidden tab
            await asyncio.sleep(0.4)
            picked = await _popup_select(cdp, {
                "opener_selector": "#radius_filter_button, button[id*=radius], [aria-label*='Distance' i]",
                "option_selector": "li[role=option], [data-testid^='selection-pill-option-']",
                "option_label": "Exact location only" if target_miles == 0 else f"Within {target_miles} miles",
                "commit_names": ["Update", "Apply", "Done", "Save"],
            })

        # Confirm from OUTSIDE — the commit navigates, so nothing inside the page can report this.
        applied = None
        for _ in range(16):
            await asyncio.sleep(0.5)
            r = await _read_radius(body.browser_url, body.tab_id, body.tab_url)
            if r is not None and r >= body.min_miles:
                applied = r
                break
        if applied is not None:
            return {"ok": True, "applied": True, "selected_miles": applied, "method": "widget",
                    "detail": f"selected 'Within {target_miles} miles' + Update; URL confirms radius={applied}",
                    "log": picked.get("log", [])}

        if not body.allow_url_fallback:
            return {"ok": False, "applied": False, "selected_miles": None, "method": "widget_failed",
                    "detail": ("The distance pill did not commit — " + (picked.get("detail") or "unknown") +
                               ". NOT falling back to a URL rewrite (allow_url_fallback=false) so the "
                               "break stays visible. Re-map the popup before trusting this filter."),
                    "log": picked.get("log", [])}

        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024) as ws:
            cdp = _CDPSession(ws)
            await cdp.send("Page.enable", {})
            url = (await cdp.send("Runtime.evaluate", {
                "expression": ("(()=>{const u=new URL(location.href);"
                               f"u.searchParams.set('radius','{body.min_miles}');"
                               "u.searchParams.delete('vjk');return u.toString();})()"),
                "returnByValue": True})).get("result", {}).get("value")
            if not url:
                return {"ok": False, "applied": False, "method": "none",
                        "detail": "widget failed and no URL to rewrite", "log": picked.get("log", [])}
            await cdp.send("Page.navigate", {"url": url})
        await asyncio.sleep(2.4)
        r = await _read_radius(body.browser_url, body.tab_id, body.tab_url)
        ok = r is not None and r >= body.min_miles
        return {"ok": True, "applied": bool(ok), "selected_miles": (r if ok else None),
                "method": "url_fallback", "detail": f"radius={r} via same-tab rewrite (widget path failed)",
                "log": picked.get("log", [])}
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
            # Snapshot the WHOLE pane (from the same scoped reader), not just the description.
            # The old check watched description alone — so when the pane switched but a field was
            # read from a stale node, that field silently kept the previous job's value and the
            # check never noticed. We now require the pane itself to have changed.
            before = (await cdp.send("Runtime.evaluate", {
                "expression": _JOB_DESC_JS, "returnByValue": True})).get("result", {}).get("value") or {}
            for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
                ev = {"type": typ, "x": box["x"], "y": box["y"]}
                if typ != "mouseMoved":
                    ev.update({"button": "left", "clickCount": 1})
                await cdp.send("Input.dispatchMouseEvent", ev)
            # Poll until the pane has switched to the clicked job, bounded by settle_seconds. The
            # switch signal is description OR title changing from the before-snapshot — description
            # is the most reliable per-job field, title catches the rare identical-description case.
            deadline = max(0.6, min(body.settle_seconds, 8.0))
            waited, data = 0.0, {}
            def _switched(d):
                return bool(d.get("description")) and (
                    d.get("description") != before.get("description")
                    or (d.get("title") and d.get("title") != before.get("title")))
            while waited < deadline:
                await asyncio.sleep(0.4)
                waited += 0.4
                data = (await cdp.send("Runtime.evaluate", {
                    "expression": _JOB_DESC_JS, "returnByValue": True})).get("result", {}).get("value") or {}
                if _switched(data):
                    break
        data["ok"] = bool(data.get("description"))
        data["switched"] = _switched(data)
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
        # `errors` carries WHY a scan came back empty (e.g. target-tab discovery raised: no matching
        # tab, unreachable browser, ambiguous). Callers used to see count:0 with no reason — that
        # opacity made "wrong/dead browser" and "form simply not open" look identical.
        return {"ok": True, "count": len(out), "candidates": out,
                "target_url": stats.target_url, "errors": list(stats.errors)}
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


class ListTabsRequest(BaseModel):
    """List the session's page tabs — the input the controller's window manager never had."""
    browser_url: str = "http://127.0.0.1:9222"


@app.post("/list_tabs")
async def list_tabs(body: ListTabsRequest):
    """Every page tab in this session, as `{tab_id, url, title}`.

    `/close_tab` could already close one and `_discover_target` could find one, but nothing could
    ANSWER "what is open right now" — so the controller drove a window it could not see, and three
    separate faults on 2026-07-22 (a capture of a stale tab, an unnoticed new tab, a submit on a
    stale tab) all traced back to that. `controller/window.py` turns this list into a policy.

    Reads the browser's own /json/list: a local socket, so it is free even on a metered connection
    (LOW_DATA_MODE) and safe to call every turn. Never raises — an unreachable browser is an empty
    list with `ok: false`, because a drive must degrade to "I cannot see the window", never crash.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            targets = (await client.get(f"{body.browser_url}/json/list")).json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_tabs failed: %s", exc)
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}", "tabs": []}

    tabs = [{"tab_id": str(t.get("id") or ""), "url": str(t.get("url") or ""),
             "title": str(t.get("title") or "")}
            for t in (targets if isinstance(targets, list) else [])
            if t.get("type") == "page"]
    return {"ok": True, "tabs": tabs, "count": len(tabs)}


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
    // The probe already reads the body text to decide `has_sign_in`; RETURNING it costs nothing
    // and is the controller's only source of page text. Workday/Greenhouse states and the
    // anti-bot CHALLENGE markers are classified from this string alone (apply_recipe
    // _WORKDAY_STATE_MARKERS / _CHALLENGE_MARKERS), so a caller that passes page_text="" is
    // structurally blind to a captcha. Transient by contract: no caller may persist it
    // (PRINCIPLES §4 — the Bundle carries the derived STATE, never the text).
    page_text: txt,
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


#: The semantic keys a captured required-field row may carry. Mirrors
#: `interaction.decision.sanitize_unanswered` deliberately: the corpus and the Bundle must agree
#: on what a form fact IS, and the fields it drops (`selector`, `value_read_at`, `value_preview`)
#: are dropped for the same two reasons — selectors are addressing the model must never learn,
#: and a value preview is PII on a page that holds addresses and salaries.
_FORM_FIELD_KEYS = ("field", "kind", "required_via", "answered", "valid")


def _sanitize_form_state(form_state: Optional[dict]) -> Optional[dict]:
    """Keep the field SET, drop selectors and values. Applied at the boundary, not by the caller,
    so a caller cannot forget — the same discipline as the `journaled` decorator."""
    if not isinstance(form_state, dict):
        return None
    rows = form_state.get("unanswered")
    if not isinstance(rows, list):
        return None
    cleaned = [{k: r[k] for k in _FORM_FIELD_KEYS if k in r}
               for r in rows if isinstance(r, dict)]
    return {"unanswered": cleaned} if cleaned else None


@app.post("/capture")
async def trigger_capture(body: CaptureRequest, background_tasks: BackgroundTasks):
    artifact = await observe_live_capture(
        scenario=body.scenario,
        tab_id=body.tab_id,
        tab_url=body.tab_url,
        browser_url=body.browser_url,
        task_context=body.task_context,
        training_metadata=body.training_metadata,
        form_state=_sanitize_form_state(body.form_state),
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
