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
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import session_checkpoints as cps
from deps import _session_browser_url, get_db
from models import TrainingSession
from settings import settings

router = APIRouter()

INITIATORS = ("operator", "auto", "teacher")


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
    return session, bb, cps.Ledger.from_dict(bb.checkpoints)


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
        "provisioned": bool(tabs_res.get("ok")),
        "authenticated": None,
        "query_entered": None,
        "radius_set": None,   # no cheap read-back — stays unknown on purpose
    }
    if not tabs_res.get("ok"):
        return {"observed": observed, "tabs": [], "search_tab": None, "block": None}

    auth = await _capture_post("/auth_state", {"browser_url": browser_url}, timeout=8.0)
    observed["authenticated"] = bool(auth.get("ok") and auth.get("logged_in"))

    search_tab = _find_search_tab(tabs, query)
    # The query rung is only observable as "are we looking at results for OUR query". Absent a
    # results tab we say False (the effect is gone -> RECOVER), never "re-run it".
    observed["query_entered"] = search_tab is not None

    block = await _detect_block(browser_url, [t.get("url", "") for t in tabs])
    return {"observed": observed, "tabs": tabs, "search_tab": search_tab, "block": block}


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


async def _detect_block(browser_url: str, urls: list[str]) -> Optional[dict]:
    """Captcha/checkpoint pre-gate. Runs on EVERY crank, before anything is decided — a blocked
    page is diagnosed as blocked, not as a broken field (feedback_captcha_first_check_on_blocked).
    Never auto-solved: an active block escalates to the operator, always."""
    import escalation_rules
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
        "ladder": cps.status_rows(ledger, observed, page=page),
        "next": cps.next_step(ledger, observed, page=page).as_dict(),
        "progress": cps.progress(ledger, observed, page=page),
        "observed": observed,
        "block": obs.get("block"),
        "tab_count": len(obs.get("tabs") or []),
        "results": results if results is not None else (bb.world or {}).get("page_results", []),
        "picks": list(ss.approved or []),
        "awaiting": awaiting,
        "last_step": last,
        "events": [{"ts": e.ts, "kind": e.kind, "detail": e.detail} for e in bb.events[-12:]],
    }


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

    obs_after = {**obs, "observed": {**obs["observed"], **(result.pop("observed_delta", {}) or {})}}
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
        ok = bool(obs["observed"].get("provisioned"))
        if ok:
            ledger.mark("provisioned", evidence=f"{len(obs.get('tabs') or [])} tabs answering",
                        initiator=initiator)
            bb.log("checkpoint", "provisioned — session Chrome is answering")
            return {"ok": True, "action": action, "detail": "Session Chrome is reachable."}
        return {"ok": False, "action": action, "awaiting": "operator_browser",
                "detail": "Session Chrome is not answering. Start it before stepping."}

    if action == "auth_probe":
        if obs["observed"].get("authenticated"):
            ledger.mark("authenticated", evidence="/auth_state reported logged_in",
                        initiator=initiator)
            bb.log("checkpoint", "authenticated — signed in to Indeed")
            return {"ok": True, "action": action, "detail": "Signed in."}
        # HARD BOUNDARY: the agent never types passwords or clears 2FA. Hand it over.
        ledger.release("authenticated")
        bb.log("handoff", "not signed in — operator owns the credential wall")
        return {"ok": False, "action": action, "awaiting": "operator_login",
                "detail": "Not signed in to Indeed. The operator signs in — we never type "
                          "passwords or clear 2FA."}

    if action == "run_query":
        return await _run_query(bb=bb, ledger=ledger, browser_url=browser_url,
                                obs=obs, initiator=initiator)

    if action == "set_distance":
        miles = max(int((bb.world or {}).get("radius_miles") or 50), 50)
        res = await _capture_post("/set_distance",
                                  {"browser_url": browser_url, "tab_url": "indeed.com/jobs",
                                   "min_miles": miles})
        if res.get("applied"):
            ledger.mark("radius_set", evidence=f"distance pill set to {res.get('selected_miles')}mi",
                        initiator=initiator)
            bb.log("checkpoint", f"radius_set — {res.get('selected_miles')}mi")
            return {"ok": True, "action": action,
                    "detail": f"Distance filter set to {res.get('selected_miles')} miles."}
        return {"ok": False, "action": action, "awaiting": "operator_filter",
                "detail": f"Could not set the distance filter ({res.get('detail') or 'no option matched'}). "
                          f"We never gather below {miles} miles."}

    if action == "review_page":
        return await _review_page(bb=bb, browser_url=browser_url, page=page, db=db)

    return {"ok": False, "action": action, "detail": f"No executor for {action!r}."}


# --- the two rungs that actually drive ----------------------------------------------------------
# Indeed's search box, by AX role + accessible name (PRINCIPLES §6 — never a bespoke selector).
_WHAT_FIELD = ("combobox", "What")
_WHERE_FIELD = ("combobox", "Where")
_SUBMIT_BUTTON = ("button", "Find jobs")


async def _run_query(*, bb: Any, ledger: cps.Ledger, browser_url: str, obs: dict[str, Any],
                     initiator: str) -> dict[str, Any]:
    """Type the query and submit it — the one CONSUMING act that makes this whole design
    necessary. Driven through the AX layer (role + accessible name), human-paced, and marked
    ONLY once the resulting URL actually carries our query. If we cannot prove it landed we
    leave the rung unmarked: an unmarked rung gets retried, and retrying a search we already
    ran is precisely the harm we are avoiding — so proof matters more here than anywhere else.
    """
    query, location = bb.search_state.query, bb.search_state.location
    tab_id = ((obs.get("tabs") or [{}])[0]).get("tab_id", "")

    async def _act(action_id: str, role: str, name: str, value: str = "") -> dict:
        return await _capture_post("/execute", {
            "browser_url": browser_url, "tab_id": tab_id, "action_id": action_id,
            "target_role": role, "target_name": name, "value": value, "driver": "humanized",
        })

    typed = await _act("type", *_WHAT_FIELD, value=query)
    if typed.get("outcome") == "not_found":
        return {"ok": False, "action": "run_query", "awaiting": "operator_search_box",
                "detail": "Could not find the 'What' search box on this page. Open Indeed's job "
                          "search and step again."}
    await asyncio.sleep(1.2)
    if location:
        await _act("type", *_WHERE_FIELD, value=location)
        await asyncio.sleep(1.0)
    await _act("click", *_SUBMIT_BUTTON)
    await asyncio.sleep(3.0)

    # PROOF, not assumption: re-read the tabs and require a results URL carrying our query.
    after = await _capture_post("/list_tabs", {"browser_url": browser_url}, timeout=8.0)
    tab = _find_search_tab(after.get("tabs") or [], query)
    if tab is None:
        bb.log("run_query", f"submitted {query!r} but no results tab carries it — left unmarked")
        return {"ok": False, "action": "run_query", "awaiting": "operator_verify",
                "detail": f"Submitted {query!r} but could not confirm a results page for it. "
                          f"Left unmarked on purpose — check the browser before stepping again."}

    ledger.mark("query_entered", evidence=f"results URL carries q={query!r}", initiator=initiator)
    bb.search_state.page = _page_from_url(tab.get("url", ""))
    bb.log("checkpoint", f"query_entered — {query!r} spent once for this session")
    return {"ok": True, "action": "run_query", "page": bb.search_state.page,
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


# --- the operator's choice ------------------------------------------------------------------------
class ChooseBody(BaseModel):
    picks: list[str] = []          # job_ids the operator wants to act on
    note: str = ""
    advance: bool = True           # page forward once the page is decided
    initiator: str = "operator"


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

    rung = cps.page_rung(page)
    ledger.mark(rung.id, evidence=f"{len(body.picks)} picked of {len(known)}"
                                  + (f" — {body.note}" if body.note else ""),
                initiator=body.initiator)
    bb.log("choose", f"page {page}: picked {len(body.picks)} of {len(known)}"
                     + (f" — {body.note}" if body.note else ""))

    advanced: dict[str, Any] = {"ok": True, "action": "choose", "page": page,
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
