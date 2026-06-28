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
from app.main import observe_live_capture
from app.observer.ax_proposer import MODEL_VERSION as AX_MODEL_VERSION
from app.observer.ax_proposer import AXProposerStats, propose_ax_candidates
from app.observer.vision_proposer import MODEL_VERSION, propose_candidates


logger = logging.getLogger("mcp-mock.proposer")

app = FastAPI(title="MCP Mock Capture Server", version="0.0.1")

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
    action_id: str                       # click | type | select | clear
    target_bbox: dict                    # {x, y, width, height} screenshot px
    value: Optional[str] = None
    backend_node_id: Optional[int] = None
    device_scale_factor: float = 1.0
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    browser_url: str = "http://127.0.0.1:9222"
    driver: Optional[str] = None         # 'direct' (default) | 'record_only' (dry-run)


@app.post("/execute")
async def execute_action(body: ExecuteRequest):
    """INTERIM EXECUTOR (v1): perform one resolved action against the live page via the
    existing raw-CDP DirectDriver. This is the bridge that lets us advance flows during
    burst-training WITHOUT waiting on the diffusion-mouse executor (v2). It clicks the
    bbox center (dpr-corrected) and, for type/clear, applies the value to the focused
    field. Returns the ExecResult. Best-effort; never raises into the caller."""
    from app.executor.driver import ActionRequest, get_driver

    driver = get_driver(body.driver)
    req = ActionRequest(
        action_id=body.action_id, target_bbox=body.target_bbox, value=body.value,
        backend_node_id=body.backend_node_id, device_scale_factor=body.device_scale_factor,
    )
    result = await driver.move_and_act(
        browser_url=body.browser_url, request=req, tab_id=body.tab_id, tab_url=body.tab_url)
    return {
        "ok": result.ok, "driver": result.driver, "action_id": result.action_id,
        "css_point": result.css_point, "detail": result.detail,
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
  const anchors = Array.from(document.querySelectorAll('a[data-jk], [data-jk]'));
  const seen = new Set();
  const out = [];
  for (const a of anchors) {
    const jk = a.getAttribute('data-jk');
    if (!jk || seen.has(jk)) continue;
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
  return out;
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
        jobs = (res.get("result") or {}).get("value") or []
        return {"ok": True, "jobs": jobs, "count": len(jobs), "url": target.get("url", "")}
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
  const descEl = document.querySelector('#jobDescriptionText, [id*=jobDescription], .jobsearch-JobComponent-description');
  const description = descEl ? descEl.innerText.trim() : '';
  const salEl = document.querySelector('#salaryInfoAndJobType, [id*=salaryInfo], [class*=salary]');
  const salary = salEl ? salEl.innerText.trim() : '';
  const titleEl = document.querySelector('h1, h2.jobsearch-JobInfoHeader-title');
  const title = titleEl ? titleEl.innerText.trim() : '';
  const btnText = Array.from(document.querySelectorAll('button, a')).map(b => (b.innerText||'').trim()).join(' | ');
  const apply_type = /apply on company site|apply on/i.test(btnText) ? 'company_site'
                   : /easily apply|apply now/i.test(btnText) ? 'quick_apply' : 'unknown';
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


@app.get("/health")
def health():
    return {"ok": True, "service": "mcp-mock-capture-server"}


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
        logger.info("CDP-AX capture-time: %s -> %d candidates (%dms)",
                    path.name, ax_candidate_count, ax_stats.total_ms)
    except Exception:
        logger.exception("CDP-AX proposal failed for %s", path.name)

    # NOTE: the vision proposer (OmniParser) is the parked super-fallback — NOT run
    # here or on labeler-open. It only activates when AX yields nothing and we
    # explicitly need it (not wired yet).
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
