"""Session control — the crank the LOCAL side turns, and the panel's read model.

This is the skeleton `docs/PLAN_session_control_panel.md` §7 called missing: the muscles all
existed (perceive, decide, act, the teacher inbox, the window manager) but the CADENCE lived in
the teacher's head, so every live drive needed a human to curl the next call. Here the cadence
is an API the operator (or, later, a ticker) turns.

The shape is `session_checkpoints`' open-ended ladder rather than a task with an end flag:

    initialize(query)  ->  climb the preamble unattended  ->  stop-and-go, one page at a time

* **initialize** declares what this session is FOR — the query is an input at setup, not
  something re-derived per step. It is also the enforcement point for the once-only rule: a
  session that already spent its query cannot be re-pointed at a different one.
* **step** turns the crank once. While climbing, it works the next rung by itself (that is the
  "independence" — nothing consequential to ask about yet). At the start line it stops and hands
  the page to the operator.
* **choose** records the operator's picks for the page and, only then, advances the cursor.

Every call carries `initiator` (operator | auto | teacher) so the journal can say who advanced
each step, not just who decided (PLAN §3).

Testability: every browser/capture-server call goes through the `_capture_post` seam, matching
`/api/search/sweep`, so the whole cadence is exercised with fakes — no browser, no Postgres.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

import apply_fields
import apply_steps as aps
import execution_style as xs
import session_checkpoints as cps
from deps import _session_browser_url, get_db
from models import TrainingSession
from settings import settings

router = APIRouter()

INITIATORS = ("operator", "auto", "teacher")

#: The front door. A session opens the HOME page and clicks on from there — never a deep URL.
INDEED_HOME = "https://www.indeed.com/"


# --- seams ------------------------------------------------------------------------------------
async def _capture_post(path: str, payload: dict, timeout: float = 30.0) -> dict:
    """POST to the capture server. Never raises — an unreachable browser is an honest
    {ok: false} the panel can render, not a 500 the operator has to decode. The single seam the
    whole cadence goes through, so tests fake it."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{settings.capture_server_url}{path}", json=payload)
            return r.json() or {}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def _load(session_id: int, db: Session) -> tuple[TrainingSession, Any, cps.Ledger]:
    import apply_state_store as store
    session = db.get(TrainingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    bb = store.load_or_create(session_id)
    ledger = cps.Ledger.from_dict(bb.checkpoints)
    # A session may have spent its query before the ledger existed, or through the sweep path.
    # Adopt that prior run so the once-only rule covers history it did not personally witness.
    ss = bb.search_state
    if cps.adopt_prior_run(ledger, query=ss.query, cadence_run_id=ss.cadence_run_id,
                           run_started_at=ss.run_started_at,
                           authed=ss.gathered_authenticated):
        bb.checkpoints = ledger.as_dict()
        bb.log("adopt", f"query_entered adopted from prior cadence run {ss.cadence_run_id}")
    return session, bb, ledger


def _persist(bb: Any, ledger: cps.Ledger) -> None:
    import apply_state_store as store
    bb.checkpoints = ledger.as_dict()
    store.save(bb)


def _check_initiator(initiator: str) -> str:
    if initiator not in INITIATORS:
        raise HTTPException(status_code=422,
                            detail=f"initiator must be one of {list(INITIATORS)}")
    return initiator


# --- observation: the tri-state the ladder reads ----------------------------------------------
async def _observe(browser_url: str, query: str) -> dict[str, Any]:
    """What is actually true right now, as a tri-state map for `session_checkpoints.next_step`.

    True / False / **None**, and the None matters: "we did not check" must never read as a
    regression, or one flaky probe would send us re-running a rung that costs a real query.
    `radius_set` is always None — there is no cheap read-back of the distance pill, and guessing
    would be exactly the wrong kind of confident-wrong.
    """
    tabs_res = await _capture_post("/list_tabs", {"browser_url": browser_url}, timeout=8.0)
    tabs = tabs_res.get("tabs") or []
    observed: dict[str, Any] = {
        # A Chrome that answers but holds NO page is not ready to work — there is nothing to look
        # at, type into, or sign in on. Found live 2026-07-23: both session Chromes were up with
        # zero tabs and this rung marked itself held on the evidence "0 tabs answering", which
        # says its own opposite. Reachable is necessary, not sufficient.
        "provisioned": bool(tabs_res.get("ok")) and len(tabs) > 0,
        "authenticated": None,
        "query_entered": None,
        "radius_set": None,   # no cheap read-back — stays unknown on purpose
    }
    # `reachable` is kept separate from `provisioned` so the executor can tell "Chrome is down"
    # from "Chrome is up but empty" — two different things for the operator to do.
    reachable = bool(tabs_res.get("ok"))
    if not reachable or not tabs:
        return {"observed": observed, "tabs": tabs, "search_tab": None, "block": None,
                "reachable": reachable}

    search_tab = _find_search_tab(tabs, query)
    # The query rung is only observable as "are we looking at results for OUR query". Absent a
    # results tab we say False (the effect is gone -> RECOVER), never "re-run it".
    observed["query_entered"] = search_tab is not None

    # THE AUTH PROBE HAS TO LOOK AT AN INDEED TAB. Found live 2026-07-25 on a session left open
    # two days: with no tab hint `/auth_state` resolves whatever target CDP lists first — here a
    # Workday application open in the other tab — and the Indeed login JS, finding no Indeed
    # markers on a Workday page, returned logged_in=false. `authenticated` read REGRESSED and the
    # panel's next move was "sign in again" on a session that never signed out. Same shape as
    # 069eb61 (classify taking the last tab instead of the apply tab): probe the tab the rung is
    # ABOUT, not whichever one we are handed.
    auth_tab = search_tab or _find_indeed_tab(tabs)
    if auth_tab is None:
        # No Indeed tab open, so Indeed auth is UNKNOWN — not false. False here would be a
        # regression we never observed, and this rung's reason to exist (logged-out data is
        # provenance-invalid) only bites while gathering FROM Indeed, which needs such a tab.
        observed["authenticated"] = None
    else:
        auth = await _capture_post("/auth_state",
                                   {"browser_url": browser_url, "tab_id": auth_tab.get("tab_id")},
                                   timeout=8.0)
        observed["authenticated"] = bool(auth.get("ok") and auth.get("logged_in"))

    block = await _detect_block(browser_url, [t.get("url", "") for t in tabs])
    return {"observed": observed, "tabs": tabs, "search_tab": search_tab, "block": block,
            "reachable": True}


def _find_indeed_tab(tabs: list[dict]) -> Optional[dict]:
    """Any Indeed tab — the fallback the auth probe uses when no results tab matches this
    session's query. Auth is a property of the SITE, not of the query, so a job-detail or home
    tab answers it just as well as a results page."""
    for t in tabs:
        if "indeed.com" in (t.get("url", "") or ""):
            return t
    return None


def _find_search_tab(tabs: list[dict], query: str) -> Optional[dict]:
    """The tab showing results for THIS session's query. Matching on the query keeps us from
    mistaking somebody else's search (or a stale one) for our own — the same context-bound
    validity rule the blackboard's provenance fields enforce."""
    from urllib.parse import parse_qs, urlparse
    want = " ".join((query or "").split()).lower()
    for t in tabs:
        url = t.get("url", "") or ""
        if "indeed.com" not in url or "/jobs" not in url:
            continue
        if not want:
            return t
        got = (parse_qs(urlparse(url).query).get("q", [""])[0] or "").replace("+", " ").lower()
        if " ".join(got.split()) == want:
            return t
    return None


def _page_from_url(url: str) -> int:
    """Indeed paginates with ?start=0/10/20… — 1-based page number, 1 when absent."""
    from urllib.parse import parse_qs, urlparse
    try:
        start = parse_qs(urlparse(url or "").query).get("start", [None])[0]
        return (int(start) // 10) + 1 if start is not None else 1
    except Exception:  # noqa: BLE001
        return 1


async def _list_targets(browser_url: str) -> list[dict]:
    """EVERY CDP target, iframes included — the right source for challenge detection, and a
    separate seam from `/list_tabs` on purpose.

    `/list_tabs` filters to `type == "page"`. That is correct for the window/tab manager and
    WRONG here: a reCAPTCHA challenge is an IFRAME, so a block check fed the page list is
    structurally incapable of finding one. This pre-gate was reading the filtered list and could
    never have fired on the exact thing it exists to catch (found 2026-07-23). Reading
    `{browser_url}/json` is a local socket — free even in low-data mode.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{browser_url}/json")
            data = r.json()
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001 — an unreadable window degrades to "no frames seen"
        return []


async def _detect_block(browser_url: str, _page_urls: list[str]) -> Optional[dict]:
    """Captcha/checkpoint pre-gate. Runs on EVERY crank, before anything is decided — a blocked
    page is diagnosed as blocked, not as a broken field (feedback_captcha_first_check_on_blocked).
    Never auto-solved: an active block escalates to the operator, always."""
    import escalation_rules
    targets = await _list_targets(browser_url)
    urls = [str(t.get("url") or "") for t in targets] or _page_urls
    block = escalation_rules.detect_block_frames(urls)
    if not block or block.get("strength") != "active":
        return block
    if block.get("provider") not in {"recaptcha", "hcaptcha"}:
        return block
    vis = await _capture_post("/challenge_visibility", {"browser_url": browser_url}, timeout=8.0)
    if not vis or vis.get("ok") is False:
        return block   # probe unreachable -> keep the conservative stop
    return escalation_rules.downgrade_block_if_hidden(block, vis)


# --- the panel read model ---------------------------------------------------------------------
def _view(session: TrainingSession, bb: Any, ledger: cps.Ledger, obs: dict[str, Any], *,
          page: int, results: Optional[list[dict]] = None,
          awaiting: Optional[str] = None, last: Optional[dict] = None) -> dict[str, Any]:
    """Everything the control panel renders: the declared query, where we are on the ladder,
    which page we are on, and this page's results."""
    ss = bb.search_state
    observed = obs.get("observed", {})
    return {
        "session_id": session.id,
        "goal": bb.goal,
        "query": ss.query,
        "location": ss.location,
        "radius_miles": (bb.world or {}).get("radius_miles"),
        "page": page,
        "ladder": cps.status_rows(ledger, observed, page=page,
                                  has_results=bool(results if results is not None
                                                   else (bb.world or {}).get("page_results"))),
        "next": cps.next_step(ledger, observed, page=page).as_dict(),
        "progress": cps.progress(ledger, observed, page=page),
        "observed": observed,
        "block": obs.get("block"),
        "tab_count": len(obs.get("tabs") or []),
        "results": results if results is not None else (bb.world or {}).get("page_results", []),
        "picks": list(ss.approved or []),
        # The apply queue for this page: N picks, N steps, and what each one is waiting on.
        "queue": aps.Queue.from_dict((bb.world or {}).get("apply_queue")).as_dict(),
        # What the teacher intends next, if anything — the pause the operator steers from.
        "proposal": (bb.world or {}).get("apply_proposal"),
        # A pending account-creation handoff (durable, survives reloads like the proposal).
        "account_handoff": (bb.world or {}).get("account_handoff"),
        # What the OPEN PANE says the application is. Read at open_pane and surfaced here so a
        # proposal is made against the observed apply type rather than an assumed one — on
        # 2026-07-24 a proposal cited "apply_type=indeed_apply" as evidence for a posting whose
        # pane had plainly reported `company_site`. Fabricated evidence is worse than none: it
        # lands in the corpus looking exactly like the real thing.
        "open_pane": (bb.world or {}).get("open_pane"),
        "queue_summary": aps.Queue.from_dict((bb.world or {}).get("apply_queue")).summary(),
        "awaiting": awaiting,
        "last_step": last,
        "events": [{"ts": e.ts, "kind": e.kind, "detail": e.detail} for e in bb.events[-12:]],
        # How old is what we are looking at (perception/staleness.py — PROTOTYPE). Advisory: the
        # panel shows it and the operator decides. Nothing here acts on it.
        "staleness": _staleness_for(bb, obs),
    }


def _staleness_for(bb: Any, obs: dict[str, Any]) -> dict[str, Any]:
    """Assess this session's freshness from what the panel already observed.

    The panel does not run the controller loop, so it cannot use `LiveActuator`'s bookkeeping —
    but it has the same facts in a different shape. The drive's last action is the last event on
    the blackboard, which is a better measure here than anything in memory: it SURVIVES a restart,
    so a session picked up the morning after reports its real age instead of looking brand new
    because the process is (this is the case that motivated the detector).
    """
    from perception import staleness as st

    observed = obs.get("observed", {})
    tabs = obs.get("tabs") or []

    last_at = None
    for e in reversed(bb.events or ()):
        try:
            last_at = datetime.fromisoformat(e.ts).timestamp()
            break
        except (TypeError, ValueError):
            continue

    reachable = bool(obs.get("reachable"))
    return st.assess(st.Evidence(
        now=time.time(),
        logged_in=observed.get("authenticated"),
        blind_reason="" if reachable else "the session's browser is not answering",
        last_action_at=last_at,
        # The panel has no per-tab navigation clock. UNMEASURED, not zero — an unknown must not
        # read as freshly loaded any more than it may read as a regression.
        last_nav_at=None,
        responsive=bool(reachable and tabs),
        # A queued application that has been opened but not ended is work in progress; reloading
        # under it is the destructive case the module refuses to propose.
        holds_unsaved_work=_queue_in_progress(bb),
    )).as_dict()


#: Apply rungs that only LOOK. A step that has done nothing but these has staged no input, so a
#: reload costs it nothing — it re-opens the pane and carries on.
_READ_ONLY_RUNGS = frozenset({"open_pane", "verify_identity", "classify", "orient"})


def _queue_in_progress(bb: Any) -> bool:
    """Is an application holding input a reload would throw away?

    NOT simply "has started" — that was the first version and it was too broad: session 21 had
    opened a pane and confirmed the job's identity, which stages nothing, and the panel duly
    suppressed a refresh it should have offered. Withholding the remedy is as much a failure as
    proposing a destructive one; it just fails quietly. A step counts only once it has run a rung
    that puts something INTO the page.
    """
    try:
        queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
        return any(
            not s.done and any((m.get("rung") if isinstance(m, dict) else getattr(m, "rung", ""))
                               not in _READ_ONLY_RUNGS for m in (s.minis or ()))
            for s in (queue.steps or ())
        )
    except Exception:  # noqa: BLE001 — a malformed queue must not break the panel's read model
        return False


# --- initialize ---------------------------------------------------------------------------------
class InitializeBody(BaseModel):
    query: str
    location: str = ""
    radius_miles: int = 50
    initiator: str = "operator"


@router.post("/api/session_control/{session_id}/initialize")
async def initialize(session_id: int, body: InitializeBody,
                     db: Session = Depends(get_db)) -> dict[str, Any]:
    """Declare what this session is FOR. Provisioning, not driving — nothing is typed here.

    The query is a session-setup INPUT: one focused Chrome instance, one query, held for the
    session's life. That is enforced, not documented: if this session already spent its
    `query_entered` rung, re-pointing it at a different query is refused. Re-running searches is
    the thing that makes Indeed collapse results, so a second query means a second session.
    """
    _check_initiator(body.initiator)
    query = " ".join((body.query or "").split())
    if not query:
        raise HTTPException(status_code=422, detail="query is required — it is what the session is for")

    import job_search_targets as jst
    session, bb, ledger = _load(session_id, db)

    if ledger.holds("query_entered"):
        spent = " ".join((bb.search_state.query or "").split())
        if spent.lower() != query.lower():
            raise HTTPException(
                status_code=409,
                detail=(f"This session already ran {spent!r}. A session holds ONE query — "
                        f"re-searching is what makes Indeed collapse results. Start a new "
                        f"session for {query!r}."))

    bb.search_state.query = query
    bb.search_state.location = " ".join((body.location or "").split())
    bb.goal = (f"Search Indeed for {query!r}"
               + (f" in {bb.search_state.location}" if bb.search_state.location else "")
               + " — review page by page")
    bb.world = dict(bb.world or {})
    bb.world["radius_miles"] = max(int(body.radius_miles or 50), 50)
    bb.log("initialize", f"session declared for {query!r} "
                         f"({bb.search_state.location or 'anywhere'}) by {body.initiator}")
    # Remember the target across sessions so the cadence and the panel agree on what we search.
    jst.add_target(query, bb.search_state.location, radius_miles=bb.world["radius_miles"])

    obs = await _observe(_session_browser_url(session), query)
    _persist(bb, ledger)
    return _view(session, bb, ledger, obs, page=bb.search_state.page or 1)


# --- the read model ------------------------------------------------------------------------------
@router.get("/api/session_control/{session_id}")
async def get_panel(session_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """The panel's view. READ-ONLY — probes the tabs and auth state (a local CDP socket, free
    even in low-data mode) and drives nothing."""
    session, bb, ledger = _load(session_id, db)
    obs = await _observe(_session_browser_url(session), bb.search_state.query)
    page = _current_page(obs, bb)
    return _view(session, bb, ledger, obs, page=page)


def _current_page(obs: dict[str, Any], bb: Any) -> int:
    """The page we are actually on: read off the live search tab when there is one, else the
    last page written down. The live URL wins — the blackboard is memory, the tab is truth."""
    tab = obs.get("search_tab")
    if tab:
        return _page_from_url(tab.get("url", ""))
    return bb.search_state.page or 1


# --- the crank -----------------------------------------------------------------------------------
class StepBody(BaseModel):
    initiator: str = "operator"


@router.post("/api/session_control/{session_id}/step")
async def step(session_id: int, body: StepBody,
               db: Session = Depends(get_db)) -> dict[str, Any]:
    """Turn the crank once.

    While climbing the preamble this works the next rung on its own. At the start line it stops
    and returns the page's results with `awaiting: "choose"` — the stop-and-go boundary the
    operator asked for. A consuming rung whose effect is gone produces a RECOVER instruction,
    never a repeat.
    """
    _check_initiator(body.initiator)
    session, bb, ledger = _load(session_id, db)
    browser_url = _session_browser_url(session)
    query = bb.search_state.query
    if not query:
        raise HTTPException(status_code=409,
                            detail="Initialize the session with a query first — a session with no "
                                   "query has nothing to be for.")

    obs = await _observe(browser_url, query)
    page = _current_page(obs, bb)

    # Captcha/checkpoint first, always: never diagnose a blocked page as a broken step.
    block = obs.get("block")
    if block and block.get("strength") == "active":
        bb.log("blocked", f"active {block.get('provider')} — escalated, not solved")
        _persist(bb, ledger)
        return _view(session, bb, ledger, obs, page=page, awaiting="operator_challenge",
                     last={"ok": False, "action": "pre_gate", "detail":
                           f"An active {block.get('provider')} challenge is up. The operator "
                           f"clears it — we never auto-solve."})

    nxt = cps.next_step(ledger, obs["observed"], page=page)

    if nxt.kind == cps.RECOVER:
        # The rung is HELD and stays held. We say how to get back; we do not re-spend it.
        bb.log("recover", f"{nxt.checkpoint.id}: {nxt.checkpoint.recovery}")
        _persist(bb, ledger)
        return _view(session, bb, ledger, obs, page=page, awaiting="recover",
                     last={"ok": False, "action": "recover", "checkpoint": nxt.checkpoint.id,
                           "detail": nxt.reason, "recovery": nxt.checkpoint.recovery})

    result = await _dispatch(nxt, session=session, bb=bb, ledger=ledger, obs=obs,
                             browser_url=browser_url, page=page, initiator=body.initiator, db=db)
    _persist(bb, ledger)

    # RE-OBSERVE AFTER ACTING. `obs` was taken BEFORE the dispatch, so reusing it renders the
    # world as it was when we decided, not as the action left it. That reads as a contradiction
    # the instant a rung succeeds: `run_query` marks `query_entered` on proof, the stale
    # observation still says False, and `next_step` puts the two together as "held but its effect
    # is gone" — telling the operator to RECOVER from a search that had just worked perfectly
    # (seen live 2026-07-24). Every other endpoint here already re-observes; step was the
    # exception, and the `observed_delta` hook it used instead was never populated by anything.
    # Observing is a local CDP socket, so this costs nothing but a round trip.
    obs_after = await _observe(browser_url, query)
    return _view(session, bb, ledger, obs_after,
                 page=result.pop("page", page),
                 results=result.pop("results", None),
                 awaiting=result.pop("awaiting", None), last=result)


async def _dispatch(nxt: cps.NextStep, *, session: TrainingSession, bb: Any, ledger: cps.Ledger,
                    obs: dict[str, Any], browser_url: str, page: int, initiator: str,
                    db: Session) -> dict[str, Any]:
    """Do the one thing this rung needs. Each branch marks its rung ONLY on real evidence —
    "reached" without evidence is a claim, and a claimed consuming rung is the expensive kind
    of wrong."""
    action = nxt.checkpoint.action

    if action == "probe_browser":
        tabs = obs.get("tabs") or []
        if not obs["observed"].get("provisioned"):
            detail = ("Session Chrome is up but has no tabs open — there is nothing to drive. "
                      "Open Indeed in that window, then step again."
                      if obs.get("reachable") else
                      "Session Chrome is not answering. Start it before stepping.")
            return {"ok": False, "action": action, "awaiting": "operator_browser",
                    "detail": detail}

        # "Ready" means a window that is CLEAN and OURS. A persistent profile restores its previous
        # session's tabs, so a fresh session inherits whatever the last one left behind — and
        # inheriting a half-finished apply form is how you end up driving someone else's work
        # (found live 2026-07-23). Propose the clean start; never perform it silently.
        #
        # ONLY AT THE START, though. `provisioned` is a STANDING rung, so it is re-checked every
        # step — and mid-drive a window legitimately holds several tabs (search plus the apply it
        # opened). Re-running the inherited-tabs test then would call our own working tabs junk and
        # jam the loop forever. Once the rung is held, hygiene (`plan_hygiene`) owns the window.
        plan = _fresh_start_plan(obs) if not ledger.holds("provisioned") else {"to_close": []}
        if plan["to_close"]:
            return {"ok": False, "action": action, "awaiting": "operator_clean_start",
                    "fresh_start": plan,
                    "detail": f"This window has {len(plan['to_close'])} tab(s) inherited from a "
                              f"previous session"
                              + (f", including {len(plan['holds_work'])} that may hold real work"
                                 if plan["holds_work"] else "")
                              + ". Clean start clears them before we begin."}

        ledger.mark("provisioned", evidence=f"clean window, {len(tabs)} tab(s)", initiator=initiator)
        bb.log("checkpoint", f"provisioned — clean window with {len(tabs)} tab(s)")
        return {"ok": True, "action": action,
                "detail": f"Session Chrome is up with a clean window ({len(tabs)} tab)."}

    if action == "auth_probe":
        # NOBODY EVER OPENED THE FRONT DOOR. A freshly provisioned session is one about:blank tab,
        # so there was no Indeed page to probe and none to search from: `auth_probe` handed back
        # "open Indeed", `run_query` handed back "open Indeed's job search, then step again", and
        # the ladder could not climb from a clean browser at all (found live 2026-07-25 on session
        # 20, the first fresh session the panel ever provisioned). Initialize was specified to
        # "reach the start line" (PLAN §2.1) and nothing implemented the first move.
        #
        # Opening a site's HOME PAGE is not the URL-forcing §3 warns about. That rule is about
        # jumping into a DEEP state we should have clicked our way to — a job detail, a results
        # page, an application. There is nothing to click on about:blank, and typing indeed.com is
        # exactly what a person does first. Every deep state after this is still reached by
        # clicking.
        if obs["observed"].get("authenticated") is None:
            style = xs.pick_style()
            nav = await _capture_post("/navigate", {
                "browser_url": browser_url, "url": INDEED_HOME,
                "tab_id": ((obs.get("tabs") or [{}])[0]).get("tab_id", ""),
                "settle_seconds": 3.0})
            await asyncio.sleep(xs.pause_for(style, xs.NAVIGATION))
            if not nav.get("ok"):
                bb.log("handoff", f"could not open Indeed — {str(nav.get('detail'))[:90]}")
                return {"ok": False, "action": action, "awaiting": "operator_open_indeed",
                        "detail": f"No Indeed tab was open and this session could not open one "
                                  f"({str(nav.get('detail') or 'no detail')[:120]}). The rung is "
                                  f"left as it was rather than guessed."}
            bb.log("nav", f"opened Indeed's home page to probe sign-in ({style.name} pace)")
            obs = await _observe(browser_url, bb.search_state.query)

        if obs["observed"].get("authenticated"):
            ledger.mark("authenticated", evidence="/auth_state reported logged_in",
                        initiator=initiator)
            bb.log("checkpoint", "authenticated — signed in to Indeed")
            return {"ok": True, "action": action, "detail": "Signed in."}

        # STILL UNKNOWN AFTER OPENING THE DOOR — we navigated but no Indeed tab came back, so we
        # never looked at Indeed. Releasing the rung here would invent a regression, and the login
        # survey below would run against whatever page happens to be in front. Same rule
        # `session_checkpoints` enforces one layer up: an unknown is not a regression.
        if obs["observed"].get("authenticated") is None:
            bb.log("handoff", "auth unknown — no Indeed tab to probe after navigating")
            return {"ok": False, "action": action, "awaiting": "operator_open_indeed",
                    "detail": "Opened Indeed but no Indeed tab came back, so sign-in could not be "
                              "checked. The rung is left as it was rather than guessed."}

        # NOT SIGNED IN IS A STEP, NOT A DEAD END. The boundary is that we never type a password or
        # clear a 2FA challenge — it was never that we refuse to open the login page. Reporting
        # "the operator signs in" and offering nothing meant login was the one rung the system did
        # not own: the operator could see it was next and had nothing to press (operator, live
        # 2026-07-24). So we survey what this page actually offers and hand back real options.
        ledger.release("authenticated")
        login = await _login_survey(browser_url, obs)
        bb.log("handoff", f"not signed in ({login['state']}) — "
                          f"{len(login['options'])} way(s) in offered")
        return {"ok": False, "action": action, "awaiting": "operator_login", "login": login,
                "detail": login["detail"]}

    if action == "run_query":
        return await _run_query(bb=bb, ledger=ledger, browser_url=browser_url,
                                obs=obs, initiator=initiator)

    if action == "set_distance":
        miles = max(int((bb.world or {}).get("radius_miles") or 50), 50)
        # Setting the pill RE-QUERIES the backend, so it is a navigation as far as pacing goes.
        # This step used to fire with no pause at all and was over in about half a second.
        style = xs.pick_style()
        await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))
        res = await _capture_post("/set_distance",
                                  {"browser_url": browser_url, "tab_url": "indeed.com/jobs",
                                   "min_miles": miles})
        await asyncio.sleep(xs.pause_for(style, xs.NAVIGATION))
        if res.get("applied"):
            ledger.mark("radius_set", evidence=f"distance pill set to {res.get('selected_miles')}mi",
                        initiator=initiator)
            bb.log("checkpoint", f"radius_set — {res.get('selected_miles')}mi ({style.name} pace)")
            return {"ok": True, "action": action, "pace": xs.describe(style),
                    "detail": f"Distance filter set to {res.get('selected_miles')} miles."}
        return {"ok": False, "action": action, "awaiting": "operator_filter",
                "detail": f"Could not set the distance filter ({res.get('detail') or 'no option matched'}). "
                          f"We never gather below {miles} miles."}

    if action == "review_page":
        return await _review_page(bb=bb, browser_url=browser_url, page=page, db=db)

    return {"ok": False, "action": action, "detail": f"No executor for {action!r}."}


# --- the two rungs that actually drive ----------------------------------------------------------
#: Outcomes /execute can legitimately return. Anything else — a FastAPI validation body, a
#: transport error, an empty dict — is NOT a result and must never be read as one.
_ACTED_OK = {"ok", "committed_unconfirmed"}


async def _run_query(*, bb: Any, ledger: cps.Ledger, browser_url: str, obs: dict[str, Any],
                     initiator: str) -> dict[str, Any]:
    """Type the query and submit it — the one CONSUMING act that makes this whole design
    necessary. Driven through the AX layer (role + accessible name), human-paced, and marked
    ONLY once the resulting URL actually carries our query. If we cannot prove it landed we
    leave the rung unmarked: an unmarked rung gets retried, and retrying a search we already
    ran is precisely the harm we are avoiding — so proof matters more here than anywhere else.

    THE CONTROLS ARE DISCOVERED, NOT ASSUMED. The first version hard-coded "What"/"Where"/"Find
    jobs" from general knowledge of Indeed; the live page offers "search: Job title, keywords, or
    company", "Edit location" and "Search". All three missed, so nothing was typed and nothing was
    clicked (2026-07-24, session 19). We scan and match, every time.
    """
    import search_cadence

    query, location = bb.search_state.query, bb.search_state.location
    tab_id = ((obs.get("tabs") or [{}])[0]).get("tab_id", "")

    async def _act(action_id: str, ctrl: dict, value: str = "") -> tuple[bool, str]:
        """One AX-addressed action. Returns (acted, detail).

        Strict about what counts as acted: the same drive reported "submitted" while every call
        was in fact returning a 422 validation body, because the code asked `outcome != not_found`
        and a response with no `outcome` at all sailed through. An unrecognised reply is a
        failure, loudly.
        """
        res = await _capture_post("/execute", {
            "browser_url": browser_url, "tab_id": tab_id, "action_id": action_id,
            # Required by ExecuteRequest even on the act-by-name path, where it goes unused.
            # Omitting it is a 422, not an action — the shape LiveActuator has always sent.
            "target_bbox": {},
            "target_role": ctrl["role"], "target_name": ctrl["name"],
            "value": value, "driver": "humanized",
        })
        outcome = res.get("outcome")
        if outcome in _ACTED_OK:
            return True, ""
        if outcome:
            return False, f"{action_id} on {ctrl['name']!r} returned {outcome}"
        detail = res.get("detail")
        return False, (f"{action_id} on {ctrl['name']!r} got no outcome back "
                       f"({str(detail)[:160] if detail else 'empty reply'})")

    scan = await _capture_post("/ax_scan", {"browser_url": browser_url, "tab_id": tab_id},
                               timeout=25.0)
    controls = search_cadence.find_search_controls(scan.get("candidates") or [])
    if "query" not in controls or "submit" not in controls:
        seen = len(scan.get("candidates") or [])
        return {"ok": False, "action": "run_query", "awaiting": "operator_search_box",
                "detail": f"Could not find a search box and a submit button on this page "
                          f"({seen} elements scanned; found "
                          f"{', '.join(controls) or 'neither'}). Open Indeed's job search, then "
                          f"step again."}

    # One style for the whole sequence — a person is not brisk and dawdling in the same five
    # seconds. The hard-coded 1.2 / 1.0 / 3.0 these replace were invariant, which is its own
    # signature, and were scattered rather than expressed as a pace.
    style = xs.pick_style()

    async def _fill(ctrl: dict, value: str) -> tuple[bool, str]:
        """Clear, then type. `type` is `Input.insertText`, which inserts AT THE CARET and does not
        replace — so typing into a box that already holds text appends to it. That box is not
        hypothetical: a run that submitted and did not land leaves both fields populated, and this
        rung's whole retry story is "step again". Without the clear, the second attempt would
        search 'data warehousedata warehouse' and spend the session's one query on it."""
        await _act("clear", ctrl)
        return await _act("type", ctrl, value=value)

    acted, why = await _fill(controls["query"], query)
    if not acted:
        return {"ok": False, "action": "run_query", "awaiting": "operator_search_box",
                "detail": f"Could not enter the query — {why}."}
    await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))
    if location and "location" in controls:
        await _fill(controls["location"], location)
        await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))
    async def _tab_urls() -> list[str]:
        res = await _capture_post("/list_tabs", {"browser_url": browser_url}, timeout=8.0)
        return [t.get("url", "") for t in (res.get("tabs") or [])]

    async def _submit_and_confirm() -> tuple[bool, Optional[dict], bool, str]:
        """Click Search, then ask the PAGE whether it took.

        Returns (clicked, results_tab, page_moved, why). `page_moved` is the half that matters for
        deciding whether a retry is even allowed: `/execute` is a tier-1 primitive and says so in
        its own docstring — its `ok` means the node resolved and CDP dispatched without throwing,
        NOT that the page accepted the action. Confirming is this tier-2 caller's job.
        """
        before = await _tab_urls()
        ok, detail = await _act("click", controls["submit"])
        if not ok:
            return False, None, False, detail
        await asyncio.sleep(xs.pause_for(style, xs.NAVIGATION))
        res = await _capture_post("/list_tabs", {"browser_url": browser_url}, timeout=8.0)
        tabs_after = res.get("tabs") or []
        moved = [t.get("url", "") for t in tabs_after] != before
        return True, _find_search_tab(tabs_after, query), moved, ""

    clicked, tab, moved, why = await _submit_and_confirm()
    if not clicked:
        return {"ok": False, "action": "run_query", "awaiting": "operator_search_box",
                "detail": f"Typed the query but could not submit it — {why}."}

    if tab is None and not moved:
        # THE FIRST CLICK COMMITS THE WIDGET, THE SECOND SUBMITS THE FORM. Measured live
        # 2026-07-25 on session 20: both fields held their typed values, the Search button was the
        # hit-test target at its own centre, the click dispatched trusted — and the page did not
        # move at all. Typing into the location combobox stages a suggestion popup, and the click
        # that looks like "press Search" is spent dismissing it. The widget protocol (stage ->
        # commit -> act) showing up in the search box, same shape as the distance pill: AX finds
        # the ELEMENT, but the element sits inside a widget with a protocol.
        #
        # `not moved` IS THE WHOLE SAFETY PROPERTY, and the first version of this retry did not
        # have it. It retried whenever no results tab matched our query — which on 2026-07-25
        # included the case where the click HAD submitted and the tab re-read simply raced the
        # navigation. The second click then landed on the freshly-loaded results page, whose
        # search box is empty, and submitted `q=` from the SERP. A verification that can race the
        # thing it verifies is not a verification, and a retry behind it is blind. So: click again
        # only when NOTHING in the window changed. If the page moved anywhere at all, the click
        # did something, and doing it twice is exactly the double-spend this rung forbids.
        bb.log("run_query", "the window did not change after the submit — the click committed the "
                            "location widget; clicking Search once more")
        await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))
        clicked, tab, moved, why = await _submit_and_confirm()

    if tab is None and moved:
        # Something happened, just not what we asked for. Never click again into an unknown.
        bb.log("run_query", f"submitted {query!r}; the page moved but not to results for it")
        return {"ok": False, "action": "run_query", "awaiting": "operator_verify",
                "detail": f"Submitted {query!r} and the page moved, but not to a results page "
                          f"carrying that query. Left unmarked, and NOT retried — the click did "
                          f"something and repeating it could spend the query twice. Check the "
                          f"browser."}

    if tab is None:
        bb.log("run_query", f"submitted {query!r} but no results tab carries it — left unmarked")
        return {"ok": False, "action": "run_query", "awaiting": "operator_verify",
                "detail": f"Submitted {query!r} but could not confirm a results page for it. "
                          f"Left unmarked on purpose — check the browser before stepping again."}

    ledger.mark("query_entered", evidence=f"results URL carries q={query!r}", initiator=initiator)
    bb.search_state.page = _page_from_url(tab.get("url", ""))
    bb.log("checkpoint", f"query_entered — {query!r} spent once for this session")
    return {"ok": True, "action": "run_query", "page": bb.search_state.page,
            "pace": xs.describe(style),
            "detail": f"Ran {query!r}. This session will not search again — page forward instead."}


async def _review_page(*, bb: Any, browser_url: str, page: int, db: Session) -> dict[str, Any]:
    """At the start line: read this page's cards and hand them to the operator.

    This is the stop-and-go half. It does NOT mark the page rung — the operator does that by
    choosing (`/choose`). Reading a page is free; deciding on it is theirs.
    """
    from observed_jobs import upsert_observed_jobs
    ex = await _capture_post("/extract_jobs",
                             {"browser_url": browser_url, "tab_url": "indeed.com/jobs"})
    if not ex.get("ok"):
        return {"ok": False, "action": "review_page", "awaiting": "operator_results",
                "detail": f"Could not read the results ({ex.get('detail') or 'extractor said no'})."}

    cards = ex.get("jobs") or []
    new_count, dup_count = upsert_observed_jobs(db, cards, "indeed", bb.search_state.query)
    db.commit()

    results = [{"job_id": f"indeed:{c.get('external_id')}", "external_id": c.get("external_id"),
                "title": c.get("title"), "company": c.get("company"),
                "location": c.get("location"), "salary": c.get("salary"),
                "url": c.get("url")} for c in cards if c.get("external_id")]
    bb.search_state.page = page
    bb.search_state.observed_count = (bb.search_state.observed_count or 0) + len(results)
    bb.world = dict(bb.world or {})
    bb.world["page_results"] = results
    bb.log("review", f"page {page}: {len(results)} results ({new_count} new, {dup_count} seen before)")
    return {"ok": True, "action": "review_page", "page": page, "results": results,
            "awaiting": "choose",
            "detail": f"Page {page}: {len(results)} results — {new_count} new, "
                      f"{dup_count} already seen. Choose what to do with them."}


# --- login: a step the system owns, up to the secret ------------------------------------------
#: Where the agent stops, always. These are the states whose next action IS the secret itself, and
#: no amount of "the system should own every step" changes who owns those.
_HUMAN_ONLY_LOGIN = {"signin_form", "mfa", "captcha", "login_error", "create_form"}

_HUMAN_ONLY_COPY = {
    "signin_form": "The password field is up — you type it, not us. Sign in and press Re-check.",
    "mfa": "A verification code is being asked for. Enter it yourself, then press Re-check.",
    "captcha": "A challenge is up. Clear it yourself — we never auto-solve.",
    "login_error": "The last sign-in attempt was rejected. Fix it in the window, then Re-check.",
    "create_form": "This is an account-creation form. Creating accounts is yours, not ours.",
}


async def _login_survey(browser_url: str, obs: dict[str, Any]) -> dict[str, Any]:
    """What the system can SEE and DO about signing in, right now.

    Answers the question the old dead-end could not: not "are we logged in" (no) but "what is the
    next possible move, and can we make it". Clicks toward a login screen are ours; the credential
    itself is never.
    """
    import login_reasoner as lr

    tab = (obs.get("tabs") or [{}])[0]
    scan = await _capture_post("/ax_scan", {"browser_url": browser_url,
                                            "tab_id": tab.get("tab_id", "")}, timeout=25.0)
    candidates = scan.get("candidates") or []
    page_text = str(scan.get("page_text") or "")
    state = lr.classify_login_state(candidates, page_text, logged_in=False)
    entries = lr.find_signin_entries(candidates)

    if state in _HUMAN_ONLY_LOGIN:
        return {"state": state, "url": tab.get("url", ""), "options": [], "can_drive": False,
                "detail": _HUMAN_ONLY_COPY.get(state, "This screen needs you, not us."),
                "seen": len(candidates)}

    if not entries:
        # AX genuinely showing nothing is a real answer and a known one: Indeed has served a page
        # whose sign-in link AX could not see, and only a screenshot found it. Say so plainly
        # rather than implying the page has no way in.
        return {"state": state, "url": tab.get("url", ""), "options": [], "can_drive": False,
                "detail": f"No sign-in control is visible to the accessibility tree on this page "
                          f"({len(candidates)} elements seen). Indeed has hidden it before — sign "
                          f"in directly in the window, then press Re-check.",
                "seen": len(candidates)}

    return {"state": state, "url": tab.get("url", ""), "can_drive": True,
            "options": [{"name": e["name"], "role": e["role"], "why": e["why"]} for e in entries],
            "detail": f"Not signed in. {len(entries)} way(s) in from here — pick one and I'll "
                      f"click it; you take over at the password.",
            "seen": len(candidates)}


class LoginActionBody(BaseModel):
    control_name: str                   # the accessible NAME of the control to click
    role: str = "button"
    initiator: str = "operator"


@router.post("/api/session_control/{session_id}/login_action")
async def login_action(session_id: int, body: LoginActionBody,
                       db: Session = Depends(get_db)) -> dict[str, Any]:
    """Click ONE control on the way to a login screen, then re-survey.

    This is the half of login the agent may own: opening the account menu, choosing "sign in with
    a code", picking an SSO provider — all clicks a human makes before any secret exists. It
    refuses to touch a screen that is asking for the secret, so "drive the login" can never become
    "type the password". Driven through the AX layer by role + accessible name, like everything
    else (PRINCIPLES §6).
    """
    _check_initiator(body.initiator)
    session, bb, ledger = _load(session_id, db)
    browser_url = _session_browser_url(session)
    obs = await _observe(browser_url, bb.search_state.query)

    if obs["observed"].get("authenticated"):
        raise HTTPException(status_code=409, detail="Already signed in — step instead.")

    before = await _login_survey(browser_url, obs)
    if not before["can_drive"]:
        raise HTTPException(status_code=409, detail=before["detail"])
    if body.control_name not in {o["name"] for o in before["options"]}:
        raise HTTPException(
            status_code=422,
            detail=f"{body.control_name!r} is not one of the ways in I can see "
                   f"({', '.join(o['name'] for o in before['options']) or 'none'}). Re-check first.")

    tab = (obs.get("tabs") or [{}])[0]
    res = await _capture_post("/execute", {
        "browser_url": browser_url, "tab_id": tab.get("tab_id", ""), "action_id": "click",
        "target_bbox": {},   # required by ExecuteRequest even here, where it goes unused
        "target_role": body.role, "target_name": body.control_name, "driver": "humanized",
    })
    await asyncio.sleep(2.0)

    obs_after = await _observe(browser_url, bb.search_state.query)
    after = await _login_survey(browser_url, obs_after)
    # Only a recognised outcome counts as a click. A reply with no `outcome` is an error body,
    # not a result — reading one as success is what let a whole drive report work it never did.
    ok = res.get("outcome") in _ACTED_OK
    bb.log("login_step", f"clicked {body.control_name!r} -> {after['state']}")
    _persist(bb, ledger)
    return _view(session, bb, ledger, obs_after, page=_current_page(obs_after, bb),
                 awaiting=None if obs_after["observed"].get("authenticated") else "operator_login",
                 last={"ok": ok, "action": "login_action", "login": after,
                       "detail": (f"Clicked {body.control_name!r}. " + after["detail"]) if ok
                                 else f"Could not find {body.control_name!r} on the page any more — "
                                      f"it may have moved. Re-check."})


# --- the apply queue: one step per pick -------------------------------------------------------
class ApplyAccountBody(BaseModel):
    initiator: str = "operator"
    # How to handle the account wall:
    #   "auto"    — the system fills AND submits the create-account form (the default: this is the
    #               operator's own account for their own job search, a generalizable local task
    #               that should be automated, not gated behind a manual handoff every time)
    #   "fill"    — fill the form but stop before the outward-facing Create Account click
    #   "handoff" — surface the credentials for the operator to type themselves
    mode: str = "auto"
    mark_created: bool = False     # completes the "handoff" leg once the operator has made it


#: THE RUNG THIS ENDPOINT WALKS. It must be the ladder's own id (`apply_steps.PREFIX`), because a
#: rung is settled by NAME: `next_rung` looks for a mini-step whose `rung` is one of the prefix ids.
#: This endpoint used to record its legs under their own names — account_create, account_handoff,
#: account_created, account_verify — none of which is `account`, so the ladder asked for the account
#: rung again after the account had been made, forever. That is the same failure classify had
#: (5b596c2): a rung that reports its OUTCOME instead of ANSWERING ITSELF never settles, and the
#: platforms worth driving are exactly the ones that get stuck on it. The leg now lives in the
#: detail, where it is still legible and no longer load-bearing.
_ACCOUNT_RUNG = "account"

#: The genuinely-hard gates inside account creation. These are NOT the manual-handoff boundary —
#: they are real external gates that no automation may cross: a CAPTCHA cannot be auto-solved
#: (the project's standing rule), and an email/2FA verification code is not ours to fabricate. When
#: one appears the step escalates to the operator; everything ELSE about making the account is
#: automated.
_ACCOUNT_VERIFY_MARKERS = ("verification code", "verify your email", "check your email",
                           "enter the code", "one-time", "two-step", "two-factor", "authenticator")


#: WHICH fields a create-account form has, per ATS, and where each value comes from. The
#: ADDRESSING is not here — it is in `apply_fields`, resolved by (ats, field), because a second
#: place that says where a field lives is a second place that can be wrong about it.
#:
#: This table exists because the driver below used to BE Workday: three hardcoded accessible names
#: and a "Create Account" button. iCIMS wants six fields, calls the button "Submit Profile", and
#: puts a username field beside the email — so the first genuinely different ATS could not be
#: driven at all, which is the reverse of what a recipe system is for.
_CREATE_ACCOUNT_FORM: dict[str, dict[str, Any]] = {
    "workday": {
        "fields": (("email", "username"), ("password", "password"),
                   ("verify_password", "password")),
        "submit": "create_account_submit",
    },
    "icims": {
        # Step 1 of 4 IS the account form: identity and credential on one page (see ICIMS_FIELDS).
        "fields": (("first_name", "first_name"), ("last_name", "last_name"),
                   ("email", "username"), ("login", "username"),
                   ("password", "password"), ("verify_password", "password")),
        "submit": "create_account_submit",
    },
}


async def _drive_create_account(browser_url: str, tab_id: str, creds: dict, *,
                                ats: str, submit: bool) -> dict[str, Any]:
    """Fill (and optionally submit) this ATS's create-account form.

    Credential-safe by construction: fields are addressed by their exact accessible name, so the
    honeypot ("Enter website. This input is for robots only") is never touched; the password value
    flows only into /execute, which logs the target NAME, never the value; and nothing here writes
    the password to an event or a mini-step. Human-paced even in auto mode — speed is not what makes
    an account legitimate, and the captcha rule is unchanged.

    An ATS with no entry in `_CREATE_ACCOUNT_FORM` is refused BY NAME rather than driven on
    Workday's field names and left to fail as "could not fill 'Email Address'" — an unmapped
    platform and a moved field are different problems and only one of them is a stale recipe.
    """
    import ats_accounts

    username = creds.get("username") or ""
    password = creds.get("suggested_password") or ""
    if not username or not password:
        return {"ok": False, "reason": "no_credentials",
                "detail": "No username or generated password available (is ATS_ACCOUNT_PW_SUFFIX "
                          "configured?). Cannot fill the form."}

    form = _CREATE_ACCOUNT_FORM.get((ats or "").strip().lower())
    if form is None:
        return {"ok": False, "reason": "no_form_recipe",
                "detail": f"No create-account form mapped for {ats!r} (mapped: "
                          f"{', '.join(sorted(_CREATE_ACCOUNT_FORM))}). Scan the form and add it to "
                          f"apply_fields + _CREATE_ACCOUNT_FORM — do not drive it blind."}

    values = {"username": username, "password": password,
              "first_name": ats_accounts.default_first_name(),
              "last_name": ats_accounts.default_last_name()}

    style = xs.pick_style()

    async def _fill(field: str, value: str) -> dict:
        addr = apply_fields.addressing_for(ats, field)
        payload = {"browser_url": browser_url, "tab_id": tab_id, "action_id": "type",
                   "target_bbox": {}, "value": value, "driver": "humanized"}
        if addr["addressed_by"] == apply_fields.ADDRESSED_BY_SELECTOR:
            payload["selector"] = addr["selector"]
        else:
            payload["target_role"], payload["target_name"] = addr["role"], addr["name"]
        res = await _capture_post("/execute", payload)
        await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))
        return res

    for field, source in form["fields"]:
        value = values.get(source) or ""
        if not value:
            # A blank identity is a missing .env value, not a field to leave empty on a real
            # application — say which one, and stop before submitting a half-formed profile.
            return {"ok": False, "reason": "no_value",
                    "detail": f"No value for {field!r} (source {source!r}) — set "
                              f"ATS_ACCOUNT_{source.upper()} in .env. Nothing was submitted."}
        r = await _fill(field, value)
        if r.get("outcome") not in ("ok", "committed_unconfirmed"):
            return {"ok": False, "reason": "fill_failed",
                    "detail": f"Could not fill {field!r} ({r.get('outcome') or r.get('detail')})."}

    submit_addr = apply_fields.addressing_for(ats, form["submit"])
    button = submit_addr["name"] or submit_addr["selector"]
    if not submit:
        return {"ok": True, "submitted": False, "button": button,
                "detail": f"Filled the create-account form. Confirm to click {button!r}."}

    click_payload = {"browser_url": browser_url, "tab_id": tab_id, "action_id": "click",
                     "target_bbox": {}, "driver": "humanized"}
    if submit_addr["addressed_by"] == apply_fields.ADDRESSED_BY_SELECTOR:
        click_payload["selector"] = submit_addr["selector"]
    else:
        click_payload["target_role"] = submit_addr["role"]
        click_payload["target_name"] = submit_addr["name"]
    click = await _capture_post("/execute", click_payload)
    await asyncio.sleep(xs.pause_for(style, xs.NAVIGATION))
    if click.get("outcome") not in ("ok", "committed_unconfirmed"):
        return {"ok": False, "reason": "submit_failed",
                "detail": f"Filled the form but could not click {button!r} "
                          f"({click.get('outcome') or click.get('detail')})."}
    return {"ok": True, "submitted": True, "button": button,
            "detail": f"Submitted the create-account form ({button!r})."}


@router.post("/api/session_control/{session_id}/apply_account")
async def apply_account(session_id: int, body: ApplyAccountBody,
                        db: Session = Depends(get_db)) -> dict[str, Any]:
    """The account rung — get past the ATS's identity wall so the application can continue.

    THE DEFAULT IS AUTOMATED (`mode="auto"`, operator's call 2026-07-24). This is the operator's
    own account, for their own job search, on their own machine: a generalizable local task, and
    gating it behind a manual handoff every time was MY caution hardcoded as their architecture.
    What still stops the machine are the REAL gates, and only those — a CAPTCHA (never auto-solved,
    on any form) and an email/2FA verification code (not ours to fabricate). `mode="fill"` fills but
    leaves the outward-facing click to the operator; `mode="handoff"` types nothing and surfaces the
    credentials instead. Whatever the mode, the password value flows only into /execute, which
    records the target NAME and never the value.

    Account creation is NOT a terminal park here — it RESUMES. The step stays open until the account
    exists (marked human_required while it waits on the operator in the handoff mode); then the
    application continues into the ATS's own first step. (Parking is still available via apply_flag
    for a company the operator does not want an account with.)
    """
    _check_initiator(body.initiator)
    import ats_accounts
    session, bb, ledger = _load(session_id, db)
    browser_url = _session_browser_url(session)
    queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
    step = queue.current()
    if step is None:
        raise HTTPException(status_code=409, detail="No open application to create an account for.")
    if (step.platform or "") not in {a["ats_id"] for a in _ats_platform_ids()}:
        raise HTTPException(status_code=409,
                            detail=f"Account handoff is for a known ATS; this step is "
                                   f"{step.platform!r}. Classify it first.")
    company = step.company or ""
    if not company:
        raise HTTPException(status_code=422,
                            detail="No company on this step to open an account for.")

    if body.mark_created:
        res = ats_accounts.mark_created(company, step.platform)
        if not res.get("ok"):
            raise HTTPException(status_code=409, detail=res.get("detail", "could not mark created"))
        step.record(_ACCOUNT_RUNG, aps.OK,
                    f"handoff leg: {company} {step.platform} account created by the operator",
                    initiator=body.initiator)
        _save_queue(bb, queue)
        bb.world.pop("account_handoff", None)   # the handoff is resolved
        _persist(bb, ledger)
        obs = await _observe(_session_browser_url(session), bb.search_state.query)
        return _view(session, bb, ledger, obs, page=_current_page(obs, bb), awaiting="apply",
                     last={"ok": True, "action": "apply_account", "queue": queue.summary(),
                           "detail": f"{company} account marked created. The application can "
                                     f"continue — orient, then work the next rung."})

    # Register the account record; next_account_action decides the leg (create vs sign-in) and
    # derives the credentials.
    ensure = ats_accounts.ensure_account(company, step.platform,
                                         login_url=(bb.world or {}).get("orient", {}).get("url", ""))
    if not ensure.get("ok"):
        raise HTTPException(status_code=409, detail=ensure.get("detail", "could not open account"))
    action = ats_accounts.next_account_action(company, step.platform)
    creds = action.get("credentials") or {}

    # AUTOMATED PATH — the default. The system fills (and, in "auto", submits) the create-account
    # form itself. A CAPTCHA or an email/2FA verification prompt still escalates: those are real
    # external gates, not the manual-handoff boundary, and they hold regardless of mode.
    if body.mode in ("auto", "fill") and action.get("leg") == "create_account":
        obs = await _observe(browser_url, bb.search_state.query)
        block = obs.get("block")
        if block and block.get("strength") == "active":
            step.record(_ACCOUNT_RUNG, aps.BLOCKED,
                        f"create leg: active {block.get('provider')} on signup",
                        initiator=body.initiator)
            _save_queue(bb, queue); _persist(bb, ledger)
            return _view(session, bb, ledger, obs, page=_current_page(obs, bb),
                         awaiting="operator_challenge",
                         last={"ok": False, "action": "apply_account", "queue": queue.summary(),
                               "detail": "A challenge is up on the signup form — clear it yourself. "
                                         "We never auto-solve, on any form."})
        tab_id = _apply_tab(bb, obs).get("tab_id", "")
        drive = await _drive_create_account(browser_url, tab_id, creds, ats=step.platform,
                                            submit=(body.mode == "auto"))
        if not drive.get("ok"):
            step.record(_ACCOUNT_RUNG, aps.FAILED, f"create leg: {drive.get('detail', '')}"[:200],
                        initiator=body.initiator)
            _save_queue(bb, queue); _persist(bb, ledger)
            obs2 = await _observe(browser_url, bb.search_state.query)
            return _view(session, bb, ledger, obs2, page=_current_page(obs2, bb), awaiting="apply",
                         last={"ok": False, "action": "apply_account", "queue": queue.summary(),
                               "detail": drive.get("detail")})

        if not drive.get("submitted"):
            step.record(_ACCOUNT_RUNG, aps.HUMAN_REQUIRED,
                        f"create leg: filled the form, awaiting the operator's "
                        f"{drive.get('button') or 'submit'!r} click",
                        initiator=body.initiator)
            _save_queue(bb, queue); _persist(bb, ledger)
            obs2 = await _observe(browser_url, bb.search_state.query)
            return _view(session, bb, ledger, obs2, page=_current_page(obs2, bb),
                         awaiting="operator_account",
                         last={"ok": True, "action": "apply_account", "queue": queue.summary(),
                               "detail": "Filled the create-account form with your generated "
                                         "credentials. Review it in the window, then confirm to "
                                         "click Create Account."})

        # Submitted. Did it land — or is there an email/2FA verification wall (a real gate)?
        after = await _capture_post("/ax_scan", {"browser_url": browser_url, "tab_id": tab_id},
                                    timeout=20.0)
        text = (str(after.get("page_text") or "")
                + " ".join(c.get("name", "") for c in (after.get("candidates") or []))).lower()
        if any(m in text for m in _ACCOUNT_VERIFY_MARKERS):
            step.record(_ACCOUNT_RUNG, aps.HUMAN_REQUIRED,
                        "verify leg: signup needs an email/2FA verification code — a real gate, "
                        "escalated",
                        initiator=body.initiator)
            _save_queue(bb, queue); _persist(bb, ledger)
            obs2 = await _observe(browser_url, bb.search_state.query)
            return _view(session, bb, ledger, obs2, page=_current_page(obs2, bb),
                         awaiting="operator_verify",
                         last={"ok": False, "action": "apply_account", "queue": queue.summary(),
                               "detail": "Account submitted, but the signup wants an email/2FA "
                                         "verification code. That is a real gate — grab the code "
                                         "(a Gmail errand we can automate next), then continue."})

        ats_accounts.mark_created(company, step.platform)
        step.record(_ACCOUNT_RUNG, aps.OK,
                    f"create leg: created the {company} {step.platform} account automatically",
                    initiator="auto")
        bb.world.pop("account_handoff", None)
        _save_queue(bb, queue)
        bb.log("account_create", f"{company} {step.platform}: account created automatically")
        _persist(bb, ledger)
        obs2 = await _observe(browser_url, bb.search_state.query)
        return _view(session, bb, ledger, obs2, page=_current_page(obs2, bb), awaiting="apply",
                     last={"ok": True, "action": "apply_account", "queue": queue.summary(),
                           "detail": f"Created the {company} account automatically. The "
                                     f"application can continue — orient, then the form."})

    step.record(_ACCOUNT_RUNG, aps.HUMAN_REQUIRED,
                f"{action.get('leg')} leg: {company} {step.platform}, operator creates it "
                f"(button {action.get('button')!r})",
                initiator=body.initiator)
    _save_queue(bb, queue)
    handoff = {
        "job_id": step.job_id, "leg": action.get("leg"), "button": action.get("button"),
        "company": company, "ats": step.platform,
        "username": creds.get("username"),
        "suggested_password": creds.get("suggested_password"),
        "suffix_configured": creds.get("suffix_configured"),
        "boundary": f"You type these into the form and click {action.get('button')!r}. The agent "
                    "never enters a password or creates an account — that is yours.",
    }
    # Persist the handoff in world so it survives a poll or a page reload — the same durability the
    # proposal has. `last_step` alone is transient, and an operator who refreshed lost the panel.
    bb.world["account_handoff"] = handoff
    # Never log the password — the record carries the leg, not the secret.
    bb.log("account_handoff", f"{company} {step.platform}: {action.get('leg')} handoff to operator")
    _persist(bb, ledger)

    obs = await _observe(_session_browser_url(session), bb.search_state.query)
    return _view(session, bb, ledger, obs, page=_current_page(obs, bb), awaiting="operator_account",
                 last={"ok": False, "action": "apply_account", "queue": queue.summary(),
                       "account": handoff,
                       "detail": f"Account handoff for {company} ({step.platform}). Create the "
                                 f"account in the window with the credentials shown, then press "
                                 f"'I created it' to continue."})


def _ats_platform_ids() -> list[dict[str, Any]]:
    from ats_registry import ATS_PLATFORMS
    return ATS_PLATFORMS


def _save_queue(bb: Any, queue: aps.Queue) -> None:
    bb.world = dict(bb.world or {})
    bb.world["apply_queue"] = queue.as_dict()


class ApplyPromptBody(BaseModel):
    field_name: str                 # the prompt field, e.g. "How Did You Hear About Us?" / "State"
    value: Optional[str] = None     # an explicit single value (State = "New Hampshire")
    use_source: bool = False        # resolve candidates from the apply SOURCE (how did you hear)
    initiator: str = "operator"


@router.post("/api/session_control/{session_id}/apply_prompt_select")
async def apply_prompt_select(session_id: int, body: ApplyPromptBody,
                              db: Session = Depends(get_db)) -> dict[str, Any]:
    """Select a value in a Workday hierarchical prompt / dropdown, reusing `/select_prompt`.

    Two ways to say what to pick, one mechanism underneath:
      * `value` — an explicit single choice (State = "New Hampshire", Phone Device Type = "Mobile").
      * `use_source` — resolve candidates from where the application came FROM (`apply_source`),
        for "How did you hear about us?". This came from Indeed, so we try "Indeed", then its
        sibling "SimplyHired", then "Other" — because those are not always the offered options and
        "Other" is a truthful floor. The live prompt decides which exists; `/select_prompt` returns
        NO_OPTION when a candidate is not listed, which is exactly our signal to try the next.

    This is the reuse the operator asked for: the Workday prompt driver is old, proven tech; the
    only new thing is choosing WHICH value to feed it, by context. That context-resolution is what
    generalises to LinkedIn and the domains after it.
    """
    _check_initiator(body.initiator)
    session, bb, ledger = _load(session_id, db)
    browser_url = _session_browser_url(session)
    queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
    step = queue.current()
    if step is None:
        raise HTTPException(status_code=409, detail="No open application.")

    import apply_source
    if body.use_source:
        source = apply_source.source_from_job_id(step.job_id)
        paths = apply_source.source_paths(source)          # [["Job Board","Indeed"], ..., ["Other"]]
    elif body.value:
        paths = [[body.value]]                              # a flat dropdown: a one-level path
    else:
        raise HTTPException(status_code=422,
                            detail="Give an explicit value, or use_source for 'how did you hear'.")

    obs = await _observe(browser_url, bb.search_state.query)
    tab_id = _apply_tab(bb, obs).get("tab_id", "")
    tried: list[str] = []
    picked: Optional[str] = None
    committed = False
    last_detail = ""
    for path in paths:
        leaf = path[-1]
        tried.append(leaf)
        # /select_prompt_path drills category -> leaf in one open session and VERIFIES the commit —
        # OK only when the field actually took it, COMMITTED_UNCONFIRMED when the click landed but
        # we could not confirm (never a false OK, the lesson from /select_prompt on this field).
        res = await _capture_post("/select_prompt_path", {
            "browser_url": browser_url, "tab_id": tab_id,
            "field_name": body.field_name, "path": path})
        outcome = res.get("outcome")
        last_detail = res.get("detail", "")
        if outcome == "ok":
            picked, committed = leaf, True
            break
        if outcome == "committed_unconfirmed":
            picked = leaf          # it clicked to the leaf; do not keep thrashing other paths
            break
        if outcome not in ("no_option", "not_opened"):
            break                  # not_found (wrong field) — a real error, stop
        await asyncio.sleep(1.0)

    rung = f"prompt:{form_fill_slug(body.field_name)}"
    if picked is not None and committed:
        fell_back = body.use_source and picked == apply_source.FALLBACK
        step.record(rung, aps.OK,
                    f"selected {picked!r} in {body.field_name!r}"
                    + (f" (source not offered, used Other after {tried[:-1]})" if fell_back else ""),
                    initiator=body.initiator)
        detail = (f"Selected and confirmed {picked!r} in {body.field_name!r}."
                  + (" The exact source was not offered, so Other — a truthful answer."
                     if fell_back else ""))
        ok = True
    elif picked is not None:
        # Clicked to the leaf but the field did not confirm — surface it, do not claim success.
        step.record(rung, aps.HUMAN_REQUIRED,
                    f"clicked {picked!r} in {body.field_name!r} but could not confirm it committed",
                    initiator=body.initiator)
        detail = (f"Clicked {picked!r} in {body.field_name!r} but could not confirm it stuck "
                  f"({last_detail}). Check the field in the window.")
        ok = False
    else:
        step.record(rung, aps.UNKNOWN,
                    f"none of {tried} selectable in {body.field_name!r}: {last_detail}"[:200],
                    initiator=body.initiator)
        detail = (f"Could not select any of {tried} in {body.field_name!r} ({last_detail}). "
                  "Check the prompt in the window.")
        ok = False

    _save_queue(bb, queue)
    _persist(bb, ledger)
    obs2 = await _observe(browser_url, bb.search_state.query)
    return _view(session, bb, ledger, obs2, page=_current_page(obs2, bb), awaiting="apply",
                 last={"ok": ok, "action": "apply_prompt_select", "queue": queue.summary(),
                       "picked": picked, "tried": tried, "detail": detail})


def form_fill_slug(name: str) -> str:
    return "_".join("".join(c if c.isalnum() else " " for c in (name or "").lower()).split())[:40]


def _identity_defaults() -> dict[str, str]:
    """Account-derived values that fill identity fields without a stored answer, plus the apply
    source. `how_did_you_hear` is Indeed with high confidence — the application literally arrived
    from Indeed."""
    import ats_accounts
    return {
        "first_name": ats_accounts.default_first_name(),
        "last_name": ats_accounts.default_last_name(),
        "email": ats_accounts.default_username(),
        "how_did_you_hear": "Indeed",
    }


async def _scan_form_fields(browser_url: str, tab_id: str) -> list[dict[str, Any]]:
    scan = await _capture_post("/ax_scan", {"browser_url": browser_url, "tab_id": tab_id},
                               timeout=25.0)
    return [{"role": c.get("role"), "name": c.get("name")}
            for c in (scan.get("candidates") or []) if c.get("name")]


def _fill_plan_for(bb: Any, fields: list[dict[str, Any]], db: Session) -> list[dict[str, Any]]:
    import form_fill
    from models import ApplicationAnswer
    from sqlalchemy import select as _select
    rows = db.scalars(_select(ApplicationAnswer).where(ApplicationAnswer.status == "active")).all()
    answers = {r.answer_key: r.value for r in rows}
    return form_fill.plan(fields, answers=answers, identity=_identity_defaults())


class ApplyFillBody(BaseModel):
    initiator: str = "operator"
    execute: bool = False          # False = plan only (see the bunch); True = fill the fillable ones


@router.post("/api/session_control/{session_id}/apply_fill")
async def apply_fill(session_id: int, body: ApplyFillBody,
                     db: Session = Depends(get_db)) -> dict[str, Any]:
    """Plan (and optionally fill) a whole form step in one bunch.

    `execute=False` returns the plan — every recognised field, the value we would fill it with, and
    which fields we cannot because we hold no data. `execute=True` fills the fillable TEXT fields
    (dropdowns and the fields with no data are left for a targeted rung / the operator). This is the
    "more automatic" pass: it does the easy, confident fills at once and stops honestly at anything
    it cannot speak to — an address we do not have is a flagged blank, never an invented street.
    """
    import form_fill
    _check_initiator(body.initiator)
    session, bb, ledger = _load(session_id, db)
    browser_url = _session_browser_url(session)
    queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
    step = queue.current()
    if step is None:
        raise HTTPException(status_code=409, detail="No open application to fill.")

    obs = await _observe(browser_url, bb.search_state.query)
    block = obs.get("block")
    if block and block.get("strength") == "active":
        return _save_queue_and_view(session, bb, ledger, queue, obs, ok=False,
                                    detail="A challenge is up — clear it yourself before filling.")
    tab_id = _apply_tab(bb, obs).get("tab_id", "")
    fields = await _scan_form_fields(browser_url, tab_id)
    rows = _fill_plan_for(bb, fields, db)
    summary = form_fill.summarise(rows)

    if not body.execute:
        return _view(session, bb, ledger, obs, page=_current_page(obs, bb), awaiting="apply",
                     last={"ok": True, "action": "apply_fill", "queue": queue.summary(),
                           "fill_plan": rows, "fill_summary": summary,
                           "detail": f"Planned {summary['fillable']} of {summary['total']} fields. "
                                     + (f"Need your data for: {', '.join(summary['missing'])}."
                                        if summary["missing"] else "Every field has a value.")})

    style = xs.pick_style()
    filled, failed = [], []
    for r in rows:
        if not r["fillable"] or r["widget"] != "text":     # this pass does text fields only
            continue
        res = await _capture_post("/execute", {
            "browser_url": browser_url, "tab_id": tab_id, "action_id": "type",
            "target_bbox": {}, "target_role": "textbox", "target_name": r["field"],
            "value": r["value"], "driver": "humanized"})
        (filled if res.get("outcome") in ("ok", "committed_unconfirmed") else failed).append(r["field"])
        await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))

    step.record("form_fill", aps.OK if filled and not failed else
                (aps.FAILED if failed else aps.OK),
                f"bunch-filled {len(filled)} field(s)"
                + (f"; {len(failed)} failed: {', '.join(failed)}" if failed else "")
                + (f"; need operator for: {', '.join(summary['missing'])}"
                   if summary["missing"] else ""),
                initiator=body.initiator)
    _save_queue(bb, queue)
    _persist(bb, ledger)
    obs2 = await _observe(browser_url, bb.search_state.query)
    return _view(session, bb, ledger, obs2, page=_current_page(obs2, bb), awaiting="apply",
                 last={"ok": not failed, "action": "apply_fill", "queue": queue.summary(),
                       "fill_plan": rows, "fill_summary": summary, "pace": xs.describe(style),
                       "detail": f"Filled {len(filled)} field(s) at {style.name} pace."
                                 + (f" {len(failed)} would not take: {', '.join(failed)}."
                                    if failed else "")
                                 + (f" Still need you for: {', '.join(summary['missing'])}."
                                    if summary["missing"] else "")})


class OrientStepBody(BaseModel):
    initiator: str = "operator"


@router.post("/api/session_control/{session_id}/orient")
async def orient_step(session_id: int, body: OrientStepBody,
                      db: Session = Depends(get_db)) -> dict[str, Any]:
    """Check where we actually are — by CONTENT, not just the URL.

    The operator's "that first rung may be the 'check where I'm at' point". It reads the live
    apply tab (AX + text), recognises the sub-state against the generalised markers, and reports
    how far we are from Submit. Two things it fixes at once:

    * **The URL-blind verification.** State detection was URL-only, so clicking Apply — which opens
      a MODAL without changing the URL — landed "somewhere unexpected" even though it worked. The
      modal's own text ("Use My Last Application") is the signal the URL could never carry.

    * **Depth awareness.** A third-party apply is not always one click from the form: sometimes a
      company careers page comes first, then Apply, then the tenant Workday app. `workday_progress`
      names where we are in that spine, so a proposal can say "8 steps from Submit" instead of
      assuming we are on the real application already.

    It DRIVES NOTHING and records a lightweight `orient` mini-step only when the recognised state
    changes — a read that repeats itself is not new knowledge.
    """
    _check_initiator(body.initiator)
    import apply_recipe as ar
    session, bb, ledger = _load(session_id, db)
    browser_url = _session_browser_url(session)
    queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
    step = queue.current()
    if step is None:
        raise HTTPException(status_code=409, detail="No open application to orient within.")

    obs = await _observe(browser_url, bb.search_state.query)
    url = _apply_tab_url(bb, obs)
    if not url:
        raise HTTPException(status_code=409,
                            detail="No application tab is open to orient against.")
    apply_tab = next((t for t in (obs.get("tabs") or []) if t.get("url") == url), {})

    scan = await _capture_post("/ax_scan", {"browser_url": browser_url,
                                            "tab_id": apply_tab.get("tab_id", "")}, timeout=25.0)
    # Build the recognition text from the page text AND the control names — the modal's buttons are
    # where "Use My Last Application" lives, and that is the whole signal here.
    names = " ".join((c.get("name") or "") for c in (scan.get("candidates") or []))
    text = f"{scan.get('page_text') or ''} {names}"

    platform = step.platform or aps.classify_landing(url).platform
    if platform == "workday":
        state = ar.map_workday_state(url, text)
        progress = ar.workday_progress(state)
    else:
        state = ar.describe_for_ats(platform, url, text).get("state", "unknown")
        progress = {"state": state, "recognised": state not in ("unknown", None)}

    recognised = bool(progress.get("recognised"))
    depth = ""
    if progress.get("steps_to_submit") is not None:
        depth = f" · {progress['steps_to_submit']} step(s) from Submit"
    detail = (f"On {platform}: {state}{depth}." if recognised
              else f"On {platform} but this page ({state}) is not a state we recognise — new "
                   f"territory, worth a careful look before the next move.")

    # Record only on a CHANGE — the last orient of the same state is not news.
    prior = next((m for m in reversed(step.minis) if m.rung == "orient"), None)
    if prior is None or state not in (prior.detail or ""):
        step.record("orient", aps.OK if recognised else aps.UNKNOWN,
                    f"{state}{depth}", initiator=body.initiator)

    bb.world = dict(bb.world or {})
    bb.world["orient"] = {"platform": platform, "state": state, "progress": progress, "url": url}
    bb.world["apply_tab"] = {**apply_tab} if apply_tab else {"url": url}
    bb.world["apply_queue"] = queue.as_dict()
    bb.log("orient", f"{step.job_id}: {platform}/{state}{depth}")
    _persist(bb, ledger)

    obs2 = await _observe(browser_url, bb.search_state.query)
    view = _view(session, bb, ledger, obs2, page=_current_page(obs2, bb), awaiting="apply",
                 last={"ok": recognised, "action": "orient", "queue": queue.summary(),
                       "orient": {"platform": platform, "state": state, "progress": progress},
                       "detail": detail})
    return view


class ReconcileStepBody(BaseModel):
    initiator: str = "operator"


@router.post("/api/session_control/{session_id}/reconcile_step")
async def reconcile_step(session_id: int, body: ReconcileStepBody,
                         db: Session = Depends(get_db)) -> dict[str, Any]:
    """Align the current step's RECORD to what the live window actually shows.

    The session's founding principle is that the browser is truth and the record is memory; when
    they disagree, memory yields. This is the apply-step version of that. It exists because a
    rebuilt queue starts every step at `queued` even when the browser is plainly mid-application —
    the operator sees the Workday tab open and the record insisting nothing has happened, and is
    asked to re-drive work the world already did.

    It does NOT fabricate progress. It reads the open ATS tab and records only the rungs that tab
    is PROOF of — you cannot be standing on a Workday application without having opened the pane,
    confirmed it, and clicked Apply — with evidence pointing at the live URL. Where it cannot
    honestly confirm something (a title that drifted between the Indeed pick and the ATS req) it
    records UNKNOWN and says so, rather than quietly asserting a match. Reconciling the record is
    not the same as vouching for it.
    """
    _check_initiator(body.initiator)
    session, bb, ledger = _load(session_id, db)
    browser_url = _session_browser_url(session)
    queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
    step = queue.current()
    if step is None:
        raise HTTPException(status_code=409, detail="No open application to reconcile.")

    obs = await _observe(browser_url, bb.search_state.query)
    ats_url = _apply_tab_url(bb, obs)
    if not ats_url:
        raise HTTPException(
            status_code=409,
            detail="No ATS application tab is open, so there is nothing the window can prove about "
                   "this step. Work it forward instead.")

    disc = aps.classify_landing(ats_url)
    done = {m.rung for m in step.minis if m.outcome == aps.OK}
    added: list[str] = []

    # open_pane: an ATS tab open for this job is proof the pane was opened and Apply led here.
    if "open_pane" not in done:
        pane = (bb.world or {}).get("open_pane") or {}
        step.record("open_pane", aps.OK,
                    f"reconciled from the live window — the application tab is open ({ats_url[:90]})"
                    + (f"; pane recorded as {pane.get('title')!r}" if pane.get("title") else ""),
                    initiator=body.initiator)
        added.append("open_pane")

    # verify_identity: the near-miss guard, and the ONE rung reconcile must not rubber-stamp.
    # Check the ATS destination against the pick; a drift (the Indeed title vs the req title) is
    # exactly the kind of near-miss this rung exists to catch, so surface it rather than assume.
    if "verify_identity" not in done:
        if step.title and _title_matches(step.title, _last_path_words(ats_url)):
            step.record("verify_identity", aps.OK,
                        f"reconciled — the ATS req path matches {step.title!r}",
                        initiator=body.initiator)
            added.append("verify_identity")
        else:
            step.record("verify_identity", aps.UNKNOWN,
                        f"the ATS destination ({_last_path_words(ats_url) or ats_url[:60]!r}) does "
                        f"not obviously match the pick {step.title!r}. Confirm it is the same job "
                        f"before continuing — titles can differ between Indeed and the employer, "
                        f"but a wrong one cannot be un-applied.",
                        initiator=body.initiator)

    # enter_apply: we are on the ATS, so Apply was clicked. Only record it once identity is settled.
    if "verify_identity" in {m.rung for m in step.minis if m.outcome == aps.OK} \
            and "enter_apply" not in done:
        step.record("enter_apply", aps.OK,
                    f"reconciled — an application tab is open on {disc.platform}",
                    initiator=body.initiator)
        added.append("enter_apply")

    # classify: name the platform from the live tab.
    if "enter_apply" in {m.rung for m in step.minis if m.outcome == aps.OK} \
            and "classify" not in done:
        step.platform = disc.platform
        step.record("classify", disc.outcome, f"{ats_url[:90]} -> {disc.detail}",
                    initiator=body.initiator)
        if disc.outcome == aps.OK:
            added.append("classify")

    bb.world = dict(bb.world or {})
    bb.world["apply_tab"] = next((t for t in (obs.get("tabs") or [])
                                  if t.get("url") == ats_url), {"url": ats_url})
    bb.world["apply_queue"] = queue.as_dict()
    bb.log("reconcile_step", f"{step.job_id}: recorded {added or 'nothing new'} from the live "
                             f"window ({disc.platform})")
    _persist(bb, ledger)
    obs2 = await _observe(browser_url, bb.search_state.query)
    stuck = step.needs_operator()
    nxt = step.next_rung()
    if stuck:
        tail = "Identity could not be auto-confirmed — check it before continuing."
    elif nxt:
        tail = f"Next: {nxt.label}."
    else:
        tail = f"Next: the {step.platform} flow — not built yet, so propose the rung."
    return _view(session, bb, ledger, obs2, page=_current_page(obs2, bb), awaiting="apply",
                 last={"ok": not stuck, "action": "reconcile_step", "queue": queue.summary(),
                       "detail": (f"Aligned the record to the live window: {step.platform} "
                                  f"application, recorded {', '.join(added) or 'nothing new'}. "
                                  + tail)})


def _last_path_words(url: str) -> str:
    """The human-readable job slug from an ATS URL, spaced out — e.g. a Workday
    '/job/Boston/Compliance-Reporting-Associate_M...' becomes 'Compliance Reporting Associate'.
    Used to check an ATS destination against the Indeed pick title."""
    from urllib.parse import unquote, urlparse
    path = urlparse(url or "").path
    seg = next((s for s in reversed(path.split("/")) if s and "job" not in s.lower()), "")
    seg = unquote(seg).split("_")[0]
    return " ".join(w for w in seg.replace("-", " ").split() if not w.isdigit())


class RebuildQueueBody(BaseModel):
    initiator: str = "operator"


@router.post("/api/session_control/{session_id}/rebuild_queue")
async def rebuild_queue(session_id: int, body: RebuildQueueBody,
                        db: Session = Depends(get_db)) -> dict[str, Any]:
    """Reconstruct the apply queue from the approved picks when the queue itself is gone.

    A safety net, not a normal step. The approved job_ids live on `search_state.approved` — a
    different field than the queue — and every picked job keeps its ObservedJob row, so the queue
    is rebuildable from durable data even after something clobbers `world`. Only rebuilds what is
    MISSING: a job already in the queue keeps its recorded progress, so running this never erases a
    half-driven application. It exists because the queue was lost once (reconcile replacing `world`
    wholesale, 2026-07-24) and "your work is unrecoverable" should never be the answer when the
    work plainly survived somewhere.
    """
    _check_initiator(body.initiator)
    from models import ObservedJob
    session, bb, ledger = _load(session_id, db)
    approved = list(bb.search_state.approved or [])
    if not approved:
        raise HTTPException(status_code=409,
                            detail="Nothing to rebuild from — this session has no approved picks.")

    queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
    queue.page = queue.page or (bb.search_state.page or 1)
    have = {s.job_id for s in queue.steps}
    picks = []
    for jid in approved:
        if jid in have:
            continue
        row = db.get(ObservedJob, jid)
        picks.append({"job_id": jid, "title": row.title if row else "",
                      "company": row.company if row else ""})
    added = queue.enqueue(picks)

    bb.world = dict(bb.world or {})
    bb.world["apply_queue"] = queue.as_dict()
    bb.log("rebuild", f"restored {added} step(s) from {len(approved)} approved picks")
    _persist(bb, ledger)
    obs = await _observe(_session_browser_url(session), bb.search_state.query)
    return _view(session, bb, ledger, obs, page=_current_page(obs, bb), awaiting="apply",
                 last={"ok": True, "action": "rebuild_queue", "queue": queue.summary(),
                       "detail": f"Rebuilt the queue: {added} application(s) restored from your "
                                 f"approved picks. Progress on any already-driven step was kept."})



#: Names that mean "start the application", most-specific first. Indeed labels this differently
#: depending on where the application actually goes, and the label is our first hint at the
#: platform — "Apply on company site" is telling us we are about to leave.
_APPLY_HINTS = ("apply now", "easily apply", "apply on company site", "apply with indeed", "apply")

#: Platforms that take an application without an identity. `account` is SKIPPED on these — a real
#: answer, not an omission, and the difference matters: an unwalked rung stalls the ladder forever.
_NO_ACCOUNT_PLATFORMS = frozenset({"greenhouse", "indeed", "indeed_quick_apply"})


#: Names that belong to the RESULTS LIST or the filter bar, never to the open pane's apply
#: button. Each is a control that contains an apply word and would otherwise be clicked:
#:   "…View full details of X"  — a result card's link (this is the one that fired)
#:   "Encouraged to apply filter" — a filter chip on the search bar
#: Matched on the accessible NAME because that is all the flat AX scan gives us; the real fix is a
#: pane-scoped scan, and this is the guard until the capture server can scope one.
_NOT_THE_PANE = ("view full details", "filter", "encouraged to apply")


def _find_apply_control(candidates: list[dict], *, apply_type: str = "",
                        job_title: str = "") -> Optional[dict]:
    """The open pane's apply button, or None.

    Ordered by what the pane ALREADY TOLD US it is (`open_pane.apply_type`), because that is
    observed rather than guessed: a `company_site` posting leaves through "Apply on company site"
    and a `quick_apply` one through "Apply now"/"Easily apply". Falling back to the generic order
    only when the pane said nothing.

    Anything that names another job, or that is plainly list/filter furniture, is refused outright
    — a control whose name carries a DIFFERENT job's title cannot be the button for this one.
    """
    want = list(_APPLY_HINTS)
    if apply_type == "company_site":
        want = ["apply on company site", "apply on employer site"] + want
    elif apply_type == "quick_apply":
        want = ["apply now", "easily apply", "apply with indeed"] + want

    title_words = {w for w in "".join(c if c.isalnum() else " " for c in job_title.lower()).split()
                   if len(w) > 3}

    def usable(c: dict) -> bool:
        name = (c.get("name") or "").lower()
        # A LINK IS AS GOOD AS A BUTTON. Indeed's own apply control is an <a role="link"> with
        # data-testid="viewjob-apply"; requiring role == "button" meant the real control was
        # invisible to this matcher and the only "apply" BUTTONS on the page were the result
        # cards. That is both why the first version clicked a card and why the fixed version then
        # found nothing at all among 196 elements (live, 2026-07-26 — the Joslin step).
        if (c.get("role") or "").lower() not in ("button", "link"):
            return False
        if any(bad in name for bad in _NOT_THE_PANE):
            return False
        # A control naming a job that is NOT ours is a card, whatever else it says.
        other = {w for w in "".join(ch if ch.isalnum() else " " for ch in name).split()
                 if len(w) > 3}
        if title_words and len(other) > 4 and not (title_words & other):
            return False
        return True

    def norm(s: str) -> str:
        return " ".join((s or "").lower().replace(",", " ").split())

    # EXACT FIRST. The pane's control is named precisely "Apply on company site"; a result card is
    # a long concatenation ("Easily apply, New, View full details of X at Y, Boston, MA"). Matching
    # on `contains` alone cannot tell them apart — "analyst" appears in both our job title and half
    # the cards — so an exact name is the strongest evidence available and is tried before any
    # substring. This is the same shape as the pane reader's priority list: order beats document
    # position, and precision beats proximity.
    for hint in want:
        hit = next((c for c in candidates if usable(c) and norm(c.get("name")) == hint), None)
        if hit:
            return hit
    for hint in want:
        hit = next((c for c in candidates if usable(c) and hint in norm(c.get("name"))), None)
        if hit:
            return hit
    return None


def _title_matches(expected: str, seen: str) -> bool:
    """Is the open pane the job we picked? Deliberately loose on punctuation and case, strict on
    content: Indeed renders a card title and a pane title that differ in whitespace and suffixes
    ('- Boston', ' | Indeed.com') but never in the actual role."""
    def norm(s: str) -> set:
        return {w for w in "".join(c if c.isalnum() else " " for c in (s or "").lower()).split()
                if len(w) > 2}
    want, got = norm(expected), norm(seen)
    if not want or not got:
        return False
    return len(want & got) / len(want) >= 0.6


class ApplyStepBody(BaseModel):
    initiator: str = "operator"
    #: Optional ASSERTION of which application the caller believes it is working. The queue is
    #: sequential — `queue.current()` decides, not the caller — but a caller that names a job and
    #: is silently handed a different one is the wrong-job failure waiting to happen. Passing
    #: `job_id` used to be accepted and dropped on the floor (pydantic ignores unknown fields), so
    #: two calls naming two different jobs both worked the same step and read as if they had
    #: worked each (found 2026-07-25, driving session 21). Name it and we check it; omit it and
    #: the queue decides as before.
    job_id: Optional[str] = None


@router.post("/api/session_control/{session_id}/apply_step")
async def apply_step(session_id: int, body: ApplyStepBody,
                     db: Session = Depends(get_db)) -> dict[str, Any]:
    """Work the CURRENT application's next mini-rung. One crank, one rung, one recorded flag.

    This is the crank the queue was missing. Shipping the queue with only terminal-flag buttons
    made every step look like something to dismiss rather than something to do — the operator's
    exact words: "what am i supposed to do next, it didn't even do anything to our session."
    """
    _check_initiator(body.initiator)
    session, bb, ledger = _load(session_id, db)
    browser_url = _session_browser_url(session)
    queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
    step = queue.current()
    if step is None:
        raise HTTPException(status_code=409,
                            detail="Nothing to work — every application from this page has ended.")
    if body.job_id and body.job_id != step.job_id:
        raise HTTPException(
            status_code=409,
            detail=f"This session is working {step.job_id} ({step.title or 'untitled'}), not "
                   f"{body.job_id}. The queue is sequential — finish or flag the current "
                   f"application before another one is worked. Omit job_id to work whatever is "
                   f"current.")

    obs = await _observe(browser_url, bb.search_state.query)
    block = obs.get("block")
    if block and block.get("strength") == "active":
        step.record("challenge", aps.BLOCKED, f"active {block.get('provider')}",
                    initiator=body.initiator)
        return _save_queue_and_view(session, bb, ledger, queue, obs,
                                    ok=False, detail="A challenge is up — clear it yourself. "
                                                     "We never auto-solve.")

    rung = step.next_rung()
    if rung is None:
        return _save_queue_and_view(session, bb, ledger, queue, obs, ok=False,
                                    detail="Past the known prefix. The rungs from here depend on "
                                           f"the platform ({step.platform or 'unclassified'}), and "
                                           "those are not built yet — drive it and flag the result.")

    tab_id = ((obs.get("tabs") or [{}])[0]).get("tab_id", "")
    style = xs.pick_style()
    await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))

    if rung.id == "open_pane":
        ext = step.job_id.split(":", 1)[-1]
        res = await _capture_post("/open_job_card",
                                  {"browser_url": browser_url, "external_id": ext})
        if not res.get("ok"):
            step.record("open_pane", aps.FAILED, res.get("detail") or "card did not open",
                        initiator=body.initiator)
            detail = f"Could not open {step.title or step.job_id}: {res.get('detail') or 'no pane'}."
        else:
            # /open_job_card already CONFIRMS the pane switched, which is the expensive half of
            # the near-miss guard — Indeed auto-opens the first result, so an unconfirmed click
            # returns the previous job's pane looking perfectly fine.
            bb.world = dict(bb.world or {})
            bb.world["open_pane"] = {"title": res.get("title", ""),
                                     "apply_type": res.get("apply_type", "")}
            step.record("open_pane", aps.OK,
                        f"pane switched to {res.get('title', '')!r}"
                        + (f" · apply_type={res.get('apply_type')}" if res.get("apply_type") else ""),
                        initiator=body.initiator)
            detail = f"Opened {res.get('title') or step.job_id}."

    elif rung.id == "verify_identity":
        seen = ((bb.world or {}).get("open_pane") or {}).get("title", "")
        if not step.title:
            step.record("verify_identity", aps.UNKNOWN,
                        f"no expected title recorded for {step.job_id}; pane shows {seen!r}",
                        initiator=body.initiator)
            detail = ("I have no title to check this against, so I cannot confirm it is the job "
                      "you picked. Check the pane yourself before entering.")
        elif _title_matches(step.title, seen):
            step.record("verify_identity", aps.OK, f"pane title {seen!r} matches the pick",
                        initiator=body.initiator)
            detail = f"Confirmed: the pane is {step.title!r}."
        else:
            # Refusing loudly. An application to the wrong job cannot be taken back.
            step.record("verify_identity", aps.FAILED,
                        f"expected {step.title!r} but the pane shows {seen!r}",
                        initiator=body.initiator)
            detail = (f"STOP — the pane shows {seen!r} but you picked {step.title!r}. Not entering "
                      f"an application on a job I cannot confirm.")

    elif rung.id == "enter_apply":
        scan = await _capture_post("/ax_scan", {"browser_url": browser_url, "tab_id": tab_id},
                                   timeout=25.0)
        # THE APPLY BUTTON IS THE PANE'S, NOT THE PAGE'S. This used to take the first control in
        # DOCUMENT ORDER whose name contained an apply word, which on a results page is never the
        # right one: the left-hand list carries an "Easily apply" badge on every card, and the
        # filter bar carries "Encouraged to apply". Driven live 2026-07-26 it clicked
        # 'Easily apply, New, View full details of Enterprise Applications Analyst' — a different
        # company's job — one rung after verify_identity had confirmed the pane was BIDMC.
        #
        # Worse, the hint ORDER guaranteed it for exactly the jobs we care about: a company_site
        # posting's real button says "Apply on company site", which sat THIRD, so a card's "easily
        # apply" always matched first. The bug needed no staleness and no bad luck.
        #
        # Same lesson `_JOB_DESC_JS` already carries in the capture server ("NOT the first match in
        # document order"), unlearned in a second place.
        apply_type = ((bb.world or {}).get("open_pane") or {}).get("apply_type") or ""
        ctrl = _find_apply_control(scan.get("candidates") or [], apply_type=apply_type,
                                   job_title=step.title or "")
        if ctrl is None:
            step.record("enter_apply", aps.UNKNOWN,
                        f"no apply control found among {len(scan.get('candidates') or [])} elements",
                        initiator=body.initiator)
            detail = "I cannot see an Apply button on this pane. Scroll it into view, or flag it."
        else:
            before = {t.get("tab_id") for t in (obs.get("tabs") or [])}
            res = await _capture_post("/execute", {
                "browser_url": browser_url, "tab_id": tab_id, "action_id": "click",
                "target_bbox": {},
                # ADDRESS IT BY THE ROLE WE ACTUALLY FOUND. This was hard-coded to "button", so
                # `/execute` re-resolved by (button, "Apply on company site") and got NOT_FOUND —
                # the control is a link. The matcher had just done the work of finding the right
                # element and the dispatch threw half of its answer away.
                "target_role": ctrl.get("role") or "button",
                "target_name": ctrl.get("name"), "driver": "humanized"})
            await asyncio.sleep(xs.pause_for(style, xs.NAVIGATION))
            if res.get("outcome") not in ("ok", "committed_unconfirmed"):
                step.record("enter_apply", aps.FAILED,
                            f"click on {ctrl.get('name')!r} returned {res.get('outcome') or 'nothing'}",
                            initiator=body.initiator)
                detail = f"Could not click {ctrl.get('name')!r}."
            else:
                after = await _capture_post("/list_tabs", {"browser_url": browser_url}, timeout=8.0)
                tabs_after = after.get("tabs") or []
                new = [t for t in tabs_after if t.get("tab_id") not in before]
                bb.world = dict(bb.world or {})
                bb.world["apply_tab"] = new[0] if new else None

                # DID WE LEAVE, AND DID WE LEAVE FOR THE RIGHT JOB? `/execute` returning ok means
                # the click dispatched — the same tier-1 contract that has bitten this codebase
                # twice already. Recording OK on that alone is what let a click onto another
                # company's posting be journaled as "entered the application for BIDMC"
                # (2026-07-26). A corpus row that says we entered an application we never entered
                # is worse than a failure: it trains the wrong thing and reads as success.
                landed = (new[0] if new else
                          next((t for t in tabs_after if t.get("tab_id") == tab_id), {}))
                landed_url = (landed or {}).get("url", "")
                strayed = "indeed.com/viewjob" in landed_url or "indeed.com/jobs" in landed_url
                if strayed and not new:
                    step.record("enter_apply", aps.FAILED,
                                f"clicked {ctrl.get('name')!r} and stayed on Indeed "
                                f"({landed_url[:80]}) — that was not this job's apply control",
                                initiator=body.initiator)
                    detail = (f"That click did not enter an application — we are still on Indeed. "
                              f"It matched {ctrl.get('name')!r}, which is not this posting's Apply "
                              f"button. Nothing was recorded as entered.")
                else:
                    step.record("enter_apply", aps.OK,
                                f"clicked {ctrl.get('name')!r}"
                                + (f"; opened a new tab -> {landed_url[:70]}" if new
                                   else "; stayed in this tab"),
                                initiator=body.initiator)
                    detail = (f"Clicked {ctrl.get('name')!r}. "
                              + ("It opened a new tab — step again to find out where we landed."
                                 if new else "No new tab; step again to classify where we are."))

    elif rung.id == "classify":  # the discovery point
        # FIND the apply tab rather than guessing its position. The recorded one is only set when
        # `enter_apply` ran through this endpoint — a teach-driven Apply (propose -> Go) opens the
        # tab without going through here, so falling back to "the last tab" picked the SEARCH tab
        # and would have classified Indeed as the ATS. The apply tab is the one that is not ours.
        url = _apply_tab_url(bb, obs)
        # READ THE FRAMES, not just the top document. A branded ATS wrapper keeps a header and a
        # footer and delegates the whole job — description, apply control, account affordances —
        # to one large same-origin iframe. Classifying the wrapper called a real iCIMS job landing
        # a hospital marketing page (live 2026-07-26); `/page_content` returns both.
        content = await _capture_post("/page_content",
                                      {"browser_url": browser_url, "tab_url": url}, timeout=15.0)
        disc = aps.classify_landing(url, page_text=content.get("text") or "",
                                    frames=content.get("frames") or [])
        step.platform = disc.platform
        step.landing_state = disc.state
        # CLASSIFY'S JOB IS TO SAY WHERE WE ARE, and it has done that the moment the landing has a
        # name. Recording the platform's undriven-ness as classify's OWN outcome left the rung
        # unsettled, so `next_rung` returned classify forever and the ladder could never reach the
        # account wall in front of it (found live on iCIMS, 2026-07-26). "We do not have a recipe"
        # is still reported — in the detail, and by the step staying needs_operator — it just is
        # not allowed to masquerade as "we do not know where we are".
        named = disc.kind not in ("", "unknown", "unreadable")
        step.record("classify", aps.OK if named else disc.outcome,
                    f"{disc.state or 'unclassified'} · {url[:90]} -> {disc.detail}",
                    initiator=body.initiator)
        detail = disc.detail

    else:  # account — the wall most ATS put in front of an application
        import ats_accounts

        platform = step.platform or ""
        company = step.company or ""
        if platform in _NO_ACCOUNT_PLATFORMS:
            step.record("account", aps.SKIPPED,
                        f"{platform} takes an application without one", initiator=body.initiator)
            detail = f"No account needed on {platform} — skipped, not skipped over."
        elif not company:
            step.record("account", aps.UNKNOWN,
                        "no company on the step, so no account can be identified",
                        initiator=body.initiator)
            detail = ("I cannot tell whose account this would be — the step has no company. "
                      "Name it before creating credentials anywhere.")
        else:
            # ensure_account REGISTERS the company-ATS pair (idempotent); next_account_action
            # reads back which leg is due from its lifecycle state.
            ats_accounts.ensure_account(company, platform, login_url=_apply_tab_url(bb, obs))
            action = ats_accounts.next_account_action(company, platform)
            bb.world = dict(bb.world or {})
            # The handoff record the panel already renders, now ALSO a rung on the step, so the
            # one part of an application that involves a credential stops being the one part that
            # left no trace on the ladder.
            bb.world["account_handoff"] = {
                "job_id": step.job_id, "company": company, "ats_id": platform,
                "leg": action["leg"], "state": action["state"],
                "account_status": action["account_status"],
                "button": action["button"], "account_id": action["account_id"],
                "has_recipe": bool(action.get("recipe")),
            }
            if action["account_status"] == "active":
                step.record("account", aps.OK,
                            f"{action['account_id']} already exists — sign in",
                            initiator=body.initiator)
                detail = f"An account for {company} on {platform} exists. Signing in is the next move."
            else:
                # NOT a failure and NOT automatic. Creating an account is allowed (operator
                # directive 2026-07-24) but it is a real-world identity on somebody's ATS, so it
                # is surfaced and confirmed rather than done in passing.
                step.record("account", aps.HUMAN_REQUIRED,
                            f"{platform} wants an account for {company} "
                            f"({action['account_id']}, status={action['account_status']})",
                            initiator=body.initiator)
                detail = (f"{platform} needs an account for {company} before it will take an "
                          f"application. Credentials are staged as {action['account_id']} — "
                          f"confirm to create it.")


    return _save_queue_and_view(session, bb, ledger, queue, obs,
                                ok=step.last_flag == aps.OK, detail=detail, pace=style)


class ApplyProposeBody(BaseModel):
    """What the teacher intends to do next, and why — written down BEFORE it happens."""

    intent: str
    params: dict[str, Any] = {}
    rationale: str = ""
    evidence: list[str] = []
    expected_next: list[str] = []
    rung: str = ""
    note: str = ""                      # teacher's note to the operator about this step


@router.post("/api/session_control/{session_id}/apply_propose")
async def apply_propose(session_id: int, body: ApplyProposeBody,
                        db: Session = Depends(get_db)) -> dict[str, Any]:
    """The teacher says what it means to do next. NOTHING is driven.

    This is the pause the operator asked for: teacher runs stop anyway, so the natural surface is
    not a row of buttons but the teacher's intent and reasoning, sitting where the operator can
    read it and steer. A proposal is a claim about the next action, on the record, before the
    action exists — which is also what makes disagreement legible: when the operator corrects it,
    the two versions become a golden pair rather than the teacher's take silently vanishing.
    """
    session, bb, ledger = _load(session_id, db)
    queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
    step = queue.current()
    if step is None:
        raise HTTPException(status_code=409, detail="No application is open to propose against.")
    if not body.rationale.strip():
        raise HTTPException(status_code=422,
                            detail="A proposal needs its reasoning — that is what the operator is "
                                   "being asked to agree or disagree WITH.")
    # Well-formed BEFORE it is offered. Approving an action nobody checked is meaningless, and a
    # `click {"field": ...}` got all the way through approval to act-time once already.
    if (why := aps.validate_action(body.intent, body.params)):
        raise HTTPException(status_code=422, detail=f"Cannot propose that: {why}")

    bb.world = dict(bb.world or {})
    bb.world["apply_proposal"] = {
        "job_id": step.job_id, "intent": body.intent, "params": dict(body.params or {}),
        "rationale": body.rationale, "evidence": list(body.evidence),
        "expected_next": list(body.expected_next), "rung": body.rung or body.intent,
        "note": body.note, "at": cps._utcnow(),
    }
    bb.log("propose", f"{step.job_id}: teacher proposes {body.intent} — {body.rationale[:80]}")
    _persist(bb, ledger)
    obs = await _observe(_session_browser_url(session), bb.search_state.query)
    return _view(session, bb, ledger, obs, page=_current_page(obs, bb), awaiting="apply",
                 last={"ok": True, "action": "propose", "queue": queue.summary(),
                       "detail": f"Proposed {body.intent!r} — waiting on you."})


class ApplyDecideBody(BaseModel):
    """The operator's answer to a proposal: go with it, correct it, or drop it."""

    action: str                          # go | correct | skip
    intent: str = ""                     # correct: what to do instead
    params: dict[str, Any] = {}
    rationale: str = ""                  # correct: WHY the teacher was wrong — the golden signal
    note: str = ""


@router.post("/api/session_control/{session_id}/apply_decide")
async def apply_decide(session_id: int, body: ApplyDecideBody,
                       db: Session = Depends(get_db)) -> dict[str, Any]:
    """Answer the pending proposal.

    `correct` is the valuable one and is deliberately a peer of `go`, never quieter — the golden
    training rows come from disagreement, and a surface whose easy path is always "yes" produces
    agreement and no signal. A correction drives the OPERATOR's version and carries the teacher's
    original into the journal as the competing take, so the students learn the contrast rather
    than only the winner.
    """
    session, bb, ledger = _load(session_id, db)
    prop = (bb.world or {}).get("apply_proposal")
    if not prop:
        raise HTTPException(status_code=409, detail="There is no proposal waiting.")

    if body.action == "skip":
        bb.world = dict(bb.world or {})
        bb.world.pop("apply_proposal", None)
        bb.log("skip", f"operator dropped the proposal to {prop.get('intent')}")
        _persist(bb, ledger)
        obs = await _observe(_session_browser_url(session), bb.search_state.query)
        queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
        return _view(session, bb, ledger, obs, page=_current_page(obs, bb), awaiting="apply",
                     last={"ok": True, "action": "skip", "queue": queue.summary(),
                           "detail": f"Dropped the proposal to {prop.get('intent')!r}."})

    if body.action == "correct":
        if not body.intent:
            raise HTTPException(status_code=422, detail="A correction needs an intent.")
        if len(body.rationale.strip()) < 12:
            raise HTTPException(
                status_code=422,
                detail="A correction needs a reason. That reasoning IS the training signal — it is "
                       "the whole reason a correction is worth more than an approval.")
        if (why := aps.validate_action(body.intent, body.params)):
            raise HTTPException(status_code=422, detail=f"Cannot act that correction: {why}")
        teach = ApplyTeachBody(intent=body.intent, params=body.params,
                               rationale=body.rationale, evidence=list(prop.get("evidence") or []),
                               expected_next=list(prop.get("expected_next") or []),
                               rung=prop.get("rung") or body.intent, initiator="operator",
                               contrast={"intent": prop.get("intent"),
                                         "params": prop.get("params") or {},
                                         "rationale": prop.get("rationale", "")})
    elif body.action == "go":
        teach = ApplyTeachBody(intent=prop["intent"], params=prop.get("params") or {},
                               rationale=prop.get("rationale", ""),
                               evidence=list(prop.get("evidence") or []),
                               expected_next=list(prop.get("expected_next") or []),
                               rung=prop.get("rung") or prop["intent"], initiator="teacher")
    else:
        raise HTTPException(status_code=422, detail="action must be go | correct | skip")

    bb.world = dict(bb.world or {})
    bb.world.pop("apply_proposal", None)
    _persist(bb, ledger)
    return await apply_teach(session_id, teach, db)


class ApplyTeachBody(BaseModel):
    """One teacher-authored action inside the current application."""

    intent: str
    params: dict[str, Any] = {}
    rationale: str = ""                 # WHY — this is the training signal, not decoration
    evidence: list[str] = []
    expected_next: list[str] = []
    rung: str = ""                      # what to call this mini-step; defaults to the intent
    initiator: str = "teacher"
    #: The take that LOST, when the operator corrected one. Journaled beside the acting decision
    #: so both sides of the disagreement survive (PRINCIPLES §10 — the Open Brain keeps the
    #: correction AND what it corrected, or the students only ever see winners).
    contrast: Optional[dict[str, Any]] = None


@router.post("/api/session_control/{session_id}/apply_teach")
async def apply_teach(session_id: int, body: ApplyTeachBody,
                      db: Session = Depends(get_db)) -> dict[str, Any]:
    """Teach one action inside the current apply step — driven, journaled, AND recorded on the step.

    The teaching surface already existed (`/api/controller/teach/commit`: act one Decision through
    the humanized actuator, journal it with the teacher's rationale as evidence, hold SUBMIT for
    the operator). What it did not do is know about apply steps. Teaching through it directly would
    journal perfectly and leave the step's mini-step trail empty — two surfaces with separate
    memories of the same act, which is precisely the bug found this morning where the sweep spent
    a query the checkpoint ledger never heard about. One act, one record, in both places.

    This delegates the driving and journaling wholesale rather than reimplementing them: the Open
    Brain contract (rationale + evidence + the golden contrast) is not something to have a second
    version of.
    """
    _check_initiator(body.initiator)
    session, bb, ledger = _load(session_id, db)
    queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
    step = queue.current()
    if step is None:
        raise HTTPException(status_code=409, detail="No application is open to teach.")
    if not body.rationale.strip():
        raise HTTPException(
            status_code=422,
            detail="A taught action needs a rationale. The WHY is the training signal — an action "
                   "with no reasoning teaches the students nothing they could not have guessed.")

    # Drive the APPLY tab when there is one; the search tab otherwise.
    apply_tab = (bb.world or {}).get("apply_tab") or {}
    tab_id = apply_tab.get("tab_id") or ((await _observe(_session_browser_url(session),
                                                         bb.search_state.query))
                                         .get("tabs") or [{}])[0].get("tab_id", "")

    from routers import controller as controller_router
    commit_body = controller_router.TeachCommitBody(
        browser_url=_session_browser_url(session), tab_id=tab_id,
        task="indeed_apply", goal_text=f"apply to {step.title or step.job_id}",
        decision=controller_router.TeachDecisionIn(
            intent=body.intent, params=dict(body.params or {}), rationale=body.rationale,
            evidence=list(body.evidence), expected_next=list(body.expected_next)),
        # A correction journals BOTH sides. `proposed` is teach_commit's existing golden-contrast
        # slot, so the pair lands in the corpus the way every other correction does.
        proposed=(controller_router.TeachDecisionIn(
            intent=body.contrast.get("intent", ""),
            params=dict(body.contrast.get("params") or {}),
            rationale=body.contrast.get("rationale", "")) if body.contrast else None),
        session_id=str(session_id))
    try:
        res = await run_in_threadpool(controller_router.teach_commit, commit_body)
    except Exception as exc:  # noqa: BLE001 — a failed teach is a recorded fact, not a 500
        res = {"held": False, "outcome": "error", "detail": f"{type(exc).__name__}: {exc}",
               "journaled": False}

    rung = body.rung or body.intent
    if res.get("held"):
        # SUBMIT was held by the consequential gate. That is the system working, and it is the
        # operator's to press — recorded as needing them rather than as a failure.
        outcome, detail = aps.HUMAN_REQUIRED, res.get("detail", "held for the operator")
    elif res.get("outcome") == "ok":
        outcome, detail = aps.OK, (f"{body.intent} -> {res.get('landed_state') or 'acted'}"
                                   + ("" if res.get("verified") is not False
                                      else " (landed somewhere unexpected)"))
    else:
        outcome, detail = aps.FAILED, f"{body.intent} -> {res.get('outcome')}: {res.get('detail','')}"

    step.record(rung, outcome, detail[:300], initiator=body.initiator)
    obs = await _observe(_session_browser_url(session), bb.search_state.query)
    view = _save_queue_and_view(session, bb, ledger, queue, obs,
                                ok=outcome == aps.OK,
                                detail=f"Taught {body.intent!r}: {detail}")
    view["last_step"]["taught"] = {"journaled": res.get("journaled"), "held": res.get("held"),
                                   "outcome": res.get("outcome"),
                                   "landed_state": res.get("landed_state"),
                                   "verified": res.get("verified")}
    return view


def _apply_tab(bb: Any, obs: dict[str, Any]) -> dict[str, Any]:
    """The LIVE tab the application is on — {tab_id, url} — identified, never positional and never
    from a stale record.

    The recorded apply_tab is only a hint, and a treacherous one: it may carry a URL that has since
    navigated (Workday's create-account tab becomes the app tab, same tab, new URL) or a tab_id
    with no URL. So we resolve against the LIVE tab list every time: prefer the recorded tab_id if
    it is still open (taking its CURRENT url), otherwise the live tab that is neither the Indeed
    search nor blank. Exact-URL matching against the record was the bug — it found nothing the
    moment the page moved (2026-07-24, the My Information bunch-fill scanned an empty tab_id)."""
    tabs = obs.get("tabs") or []
    recorded_id = ((bb.world or {}).get("apply_tab") or {}).get("tab_id")
    if recorded_id:
        live = next((t for t in tabs if t.get("tab_id") == recorded_id), None)
        if live:
            return {"tab_id": live.get("tab_id"), "url": live.get("url", "")}
    search = (obs.get("search_tab") or {}).get("tab_id")
    for t in tabs:
        url = t.get("url", "") or ""
        if t.get("tab_id") == search or not url or url.startswith("about:"):
            continue
        if "indeed.com/jobs" in url:      # another results view is still not the application
            continue
        return {"tab_id": t.get("tab_id"), "url": url}
    return {"tab_id": "", "url": ""}


def _apply_tab_url(bb: Any, obs: dict[str, Any]) -> str:
    """The live application tab's current URL (thin wrapper over `_apply_tab`)."""
    return _apply_tab(bb, obs).get("url", "")


def _save_queue_and_view(session, bb, ledger, queue: aps.Queue, obs, *, ok: bool, detail: str,
                         pace=None) -> dict[str, Any]:
    bb.world = dict(bb.world or {})
    bb.world["apply_queue"] = queue.as_dict()
    _persist(bb, ledger)
    last: dict[str, Any] = {"ok": ok, "action": "apply_step", "detail": detail,
                            "queue": queue.summary()}
    if pace is not None:
        last["pace"] = xs.describe(pace)
    return _view(session, bb, ledger, obs, page=_current_page(obs, bb),
                 awaiting="apply", last=last)



class ApplyFlagBody(BaseModel):
    job_id: str
    flag: str                      # a terminal flag from apply_steps
    detail: str = ""
    initiator: str = "operator"


@router.post("/api/session_control/{session_id}/apply_flag")
async def apply_flag(session_id: int, body: ApplyFlagBody,
                     db: Session = Depends(get_db)) -> dict[str, Any]:
    """End one apply step with a terminal flag, so the page can eventually move on.

    Every step must reach one of these — that is what stops a queue from either blocking forever
    on an account wall or quietly losing an application nobody finished. `submitted` is the only
    flag that means success; the rest record honestly why this one stopped.

    `submitted` is deliberately NOT settable here without the operator saying so, because it is
    the claim that a real application was sent. Nothing in this system marks that on its own.
    """
    _check_initiator(body.initiator)
    if body.flag not in aps.TERMINAL_FLAGS:
        raise HTTPException(status_code=422,
                            detail=f"{body.flag!r} is not a terminal flag. "
                                   f"Have: {sorted(aps.TERMINAL_FLAGS)}")
    session, bb, ledger = _load(session_id, db)
    queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
    step = next((s for s in queue.steps if s.job_id == body.job_id), None)
    if step is None:
        raise HTTPException(status_code=404,
                            detail=f"{body.job_id} is not in this page's apply queue.")
    if step.done:
        raise HTTPException(status_code=409,
                            detail=f"{body.job_id} already ended as {step.terminal!r}. A finished "
                                   f"application is not re-opened by flagging it again.")

    step.finish(body.flag, body.detail)
    bb.world = dict(bb.world or {})
    bb.world["apply_queue"] = queue.as_dict()
    # A finished step must not leave its handoff or proposal lingering onto the next job.
    if (bb.world.get("account_handoff") or {}).get("job_id") == step.job_id:
        bb.world.pop("account_handoff", None)
    if (bb.world.get("apply_proposal") or {}).get("job_id") == step.job_id:
        bb.world.pop("apply_proposal", None)
    bb.log("apply_flag", f"{body.job_id} -> {body.flag}"
                         + (f" ({body.detail})" if body.detail else ""))
    _persist(bb, ledger)

    summary = queue.summary()
    nxt = queue.current()
    obs = await _observe(_session_browser_url(session), bb.search_state.query)
    return _view(session, bb, ledger, obs, page=_current_page(obs, bb),
                 awaiting="apply" if summary["blocks_page"] else "choose",
                 last={"ok": True, "action": "apply_flag", "queue": summary,
                       "detail": (f"{body.job_id} ended as {body.flag}. "
                                  + (f"Next up: {nxt.title or nxt.job_id}."
                                     if nxt else
                                     "Every application from this page is accounted for — "
                                     "choose again to advance the page."))})


# --- the clean start: provisioning through the tab manager ------------------------------------
def _fresh_start_plan(obs: dict[str, Any]) -> dict[str, Any]:
    """What this window would have to close to be a clean start, via `controller.window` — the
    system's one tab manager, not a bespoke scan. Read-only."""
    from controller import window as window_mod
    win = window_mod.survey(obs.get("tabs") or [])
    to_close, keeper, reasons = window_mod.plan_fresh_start(win.tabs)
    holds_work = window_mod.inherited_work(to_close)
    return {
        "to_close": [{"tab_id": t.tab_id, "role": t.role, "url": t.short_url} for t in to_close],
        "keeper": {"tab_id": keeper.tab_id, "role": keeper.role,
                   "url": keeper.short_url} if keeper else None,
        "reasons": list(reasons),
        "holds_work": [{"tab_id": t.tab_id, "role": t.role, "url": t.short_url}
                       for t in holds_work],
    }


class CleanStartBody(BaseModel):
    initiator: str = "operator"
    confirm_discards_work: bool = False   # required when an inherited tab may hold real work


@router.post("/api/session_control/{session_id}/clean_start")
async def clean_start(session_id: int, body: CleanStartBody,
                      db: Session = Depends(get_db)) -> dict[str, Any]:
    """Close the tabs a fresh session inherited, so it begins on a window that is clean and ours.

    OPERATOR-TRIGGERED, never automatic — the same rule the window card already follows, and it
    matters more here: a persistent profile's restored window can contain a half-finished
    application. When the plan would discard something that looks like real work (an apply flow or
    a cross-domain errand) this refuses without `confirm_discards_work`, so nobody loses an
    application to a provisioning step.
    """
    _check_initiator(body.initiator)
    session, bb, ledger = _load(session_id, db)
    browser_url = _session_browser_url(session)
    obs = await _observe(browser_url, bb.search_state.query)
    plan = _fresh_start_plan(obs)

    if not plan["to_close"]:
        return _view(session, bb, ledger, obs, page=_current_page(obs, bb),
                     last={"ok": True, "action": "clean_start",
                           "detail": "The window is already clean."})

    if plan["holds_work"] and not body.confirm_discards_work:
        raise HTTPException(
            status_code=409,
            detail=(f"{len(plan['holds_work'])} inherited tab(s) look like real work in progress "
                    f"({', '.join(t['url'] for t in plan['holds_work'])}). Confirm to discard "
                    f"them, or finish them first — a provisioning step should not silently throw "
                    f"away an application."))

    closed, failed = [], []
    for tab in plan["to_close"]:
        res = await _capture_post("/close_tab", {"browser_url": browser_url,
                                                 "tab_id": tab["tab_id"]})
        (closed if res.get("ok") else failed).append(tab["url"])

    bb.log("clean_start", f"closed {len(closed)} inherited tab(s)"
                          + (f"; {len(failed)} refused" if failed else ""))
    _persist(bb, ledger)
    obs_after = await _observe(browser_url, bb.search_state.query)
    return _view(session, bb, ledger, obs_after, page=_current_page(obs_after, bb),
                 last={"ok": not failed, "action": "clean_start",
                       "detail": f"Closed {len(closed)} inherited tab(s)."
                                 + (f" {len(failed)} would not close: {', '.join(failed)}."
                                    if failed else " Step to continue.")})


# --- the operator's choice ------------------------------------------------------------------------
class ChooseBody(BaseModel):
    picks: list[str] = []          # job_ids to act on, IN THE ORDER they should be applied to
    note: str = ""
    advance: bool = True           # page forward once the page is decided
    initiator: str = "operator"
    #: WHO chose. `operator` today, always. The field exists now so that a `classifier:<name>` or
    #: `rule:<name>` can take this same step later without the step changing shape — and so every
    #: selection already on the record says who made it. A shortlist with no decider cannot be
    #: audited and cannot train the thing meant to inherit the job.
    decided_by: str = cps.DECIDER_OPERATOR


@router.post("/api/session_control/{session_id}/choose")
async def choose(session_id: int, body: ChooseBody,
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    """Record the operator's picks for the current page, mark the page rung, and (optionally)
    page forward.

    This is the only place the ladder grows past the preamble, and it grows by the operator's
    hand — which is what keeps an open-ended session from wandering. `picks` is per-job approval
    to apply (feedback_apply_needs_approval_to_start); an empty list is a legitimate answer
    meaning "nothing on this page", and the page still counts as reviewed.
    """
    _check_initiator(body.initiator)
    if not cps.valid_decider(body.decided_by):
        raise HTTPException(status_code=422,
                            detail=f"decided_by must be 'operator' or start with "
                                   f"'classifier:' / 'rule:' — got {body.decided_by!r}")
    session, bb, ledger = _load(session_id, db)
    browser_url = _session_browser_url(session)
    obs = await _observe(browser_url, bb.search_state.query)
    page = _current_page(obs, bb)

    if not cps.at_start_line(ledger, obs["observed"]):
        raise HTTPException(status_code=409,
                            detail="Not at the start line yet — step until the preamble is held "
                                   "before choosing.")

    known = {r.get("job_id") for r in (bb.world or {}).get("page_results", [])}
    unknown = [p for p in body.picks if p not in known]
    if unknown:
        raise HTTPException(status_code=422,
                            detail=f"Not on the page under review: {unknown}. Step to re-read the "
                                   f"page before choosing.")

    approved = list(bb.search_state.approved or [])
    for job_id in body.picks:
        if job_id not in approved:
            approved.append(job_id)
    bb.search_state.approved = approved

    # CHOOSING ENQUEUES WORK; IT DOES NOT FINISH A PAGE. Operator: "if i check off 11 jobs that's
    # 11 steps and i don't continue until i fully apply." So each pick becomes its own step, and
    # `page:N` is only marked once every one of them has reached a terminal flag.
    by_id = {r.get("job_id"): r for r in (bb.world or {}).get("page_results", [])}
    queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
    if queue.page != page:
        queue = aps.Queue(page=page)     # a new page starts its own queue
    added = queue.enqueue([by_id.get(j) or {"job_id": j} for j in body.picks])
    bb.world = dict(bb.world or {})
    bb.world["apply_queue"] = queue.as_dict()
    # THE SELECTION IS A STEP, and this is where it lands on the ladder. Marked STANDING, so
    # adding to your picks later re-marks it rather than being refused — choosing costs nothing.
    # The evidence line is the audit: how many of how many, and who decided.
    ledger.mark(cps.select_rung(page).id,
                evidence=f"{len(body.picks)} of {len(known)} picked by {body.decided_by}"
                         + (f" — {body.note}" if body.note else ""),
                initiator=body.initiator)
    bb.log("choose", f"page {page}: picked {len(body.picks)} of {len(known)} "
                     f"by {body.decided_by} ({added} queued to apply)"
                     + (f" — {body.note}" if body.note else ""))

    if queue.blocks_page():
        summary = queue.summary()
        _persist(bb, ledger)
        obs_now = await _observe(browser_url, bb.search_state.query)
        return _view(session, bb, ledger, obs_now, page=page, awaiting="apply",
                     last={"ok": True, "action": "choose", "page": page, "queue": summary,
                           "detail": f"{summary['remaining']} application(s) queued from page "
                                     f"{page}. This page stays open until each one reaches a "
                                     f"terminal flag — nothing is skipped."})

    rung = cps.page_rung(page)
    ledger.mark(rung.id, evidence=f"{len(body.picks)} picked of {len(known)}; "
                                  f"{queue.summary()['submitted']} submitted"
                                  + (f" — {body.note}" if body.note else ""),
                initiator=body.initiator)

    advanced: dict[str, Any] = {"ok": True, "action": "choose", "page": page,
                                "queue": queue.summary(),
                                "detail": f"Page {page} reviewed; {len(body.picks)} picked."}
    if body.advance:
        nxt = await _capture_post("/next_page",
                                  {"browser_url": browser_url, "tab_url": "indeed.com/jobs"})
        if nxt.get("has_next"):
            bb.search_state.page = page + 1
            bb.world = dict(bb.world or {})
            bb.world["page_results"] = []
            bb.log("advance", f"paged forward to {page + 1}")
            advanced.update(page=page + 1,
                            detail=f"Page {page} reviewed; {len(body.picks)} picked. "
                                   f"Now on page {page + 1} — step to read it.")
        else:
            # The honest end: the ladder cannot grow. Not a flag we invented — an observed fact,
            # and stopping is still the operator's call.
            bb.world = dict(bb.world or {})
            bb.world["exhausted"] = True
            bb.log("exhausted", f"no page after {page} — the ladder cannot grow further")
            advanced.update(awaiting="operator_end",
                            detail=f"Page {page} was the last one. Nothing left to page into — "
                                   f"this query is walked out. Ending is your call.")

    _persist(bb, ledger)
    obs_after = await _observe(browser_url, bb.search_state.query)
    return _view(session, bb, ledger, obs_after, page=advanced.get("page", page),
                 awaiting=advanced.get("awaiting"), last=advanced)
