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
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

import account_forms
import applied_index
import apply_fields
import apply_steps as aps
import google_recipe
import session_windows
import execution_style as xs
import session_checkpoints as cps
from deps import _session_browser_url, get_db
from models import ObservedJob, TrainingSession
from settings import settings

router = APIRouter()

INITIATORS = ("operator", "auto", "teacher")

#: The front door. A session opens the HOME page and clicks on from there — never a deep URL.
INDEED_HOME = "https://www.indeed.com/"

# --- the aggregators this ladder can climb ----------------------------------------------------
# The CADENCE is the same for every job engine — one query per session, floor the radius, one page
# at a time, click into what you shortlist — because it is about how we behave, not whose markup we
# are reading. These are the only things that actually differ, so they are DATA rather than five
# more branches: the front door, how a results URL is recognised, which param carries the query,
# and how many results a page holds.
#
# Why matching is done against ALL engines rather than the session's declared domain: the tab is a
# fact and the session's `domain_id` is a label, and the label is wrong often enough to matter (a
# session started as `indeed_jobs` that the operator drove to LinkedIn, a domain_id someone typed).
# Read the world, then fall back to what we were told. Same precedence as perception/facets.
ENGINES: list[dict[str, Any]] = [
    {"id": "indeed_jobs", "platform": "indeed", "host": "indeed.com", "results_path": "/jobs",
     "query_param": "q", "page_size": 10, "home": INDEED_HOME, "search_tab": "indeed.com/jobs",
     # HOW THE QUERY IS COMMITTED. Indeed has a real Search button; LinkedIn has none and commits on
     # Enter (measured from the operator's own /observe recording, 2026-07-28). `_run_query` used to
     # require a submit CONTROL on every engine, so on LinkedIn it reported "found submit" — it had
     # matched `Skip to search`, the skip-link `linkedin_recipe` warns about — and refused to run.
     "commit": "button",
     "label": "Indeed", "spa": False},
    {"id": "linkedin_jobs", "platform": "linkedin", "host": "linkedin.com", "results_path": "/jobs",
     "query_param": "keywords", "page_size": 25, "home": "https://www.linkedin.com/jobs/",
     "search_tab": "linkedin.com/jobs", "label": "LinkedIn",
     # No submit button exists on the jobs home; Enter on the query box is the commit.
     "commit": "enter",
     # A SINGLE-PAGE APP: query, filters and pagination all pushState and re-render in place, so
     # nothing here may treat a navigation (or its absence) as proof an action landed.
     "spa": True},
]
_ENGINE_BY_ID = {e["id"]: e for e in ENGINES}
DEFAULT_ENGINE = ENGINES[0]


def engine_of_url(url: str) -> Optional[dict[str, Any]]:
    """Which engine's results page is this URL, if any. Requires BOTH the host and the results
    path: `linkedin.com/feed` is LinkedIn but is not a job search, and treating it as one is how a
    ladder ends up reporting a query that was never run."""
    u = (url or "").lower()
    for e in ENGINES:
        if e["host"] in u and e["results_path"] in u:
            return e
    return None


def engine_for(session: Any, tab: Optional[dict] = None) -> dict[str, Any]:
    """The engine this session is working. A live tab wins (it is a fact), then the session's
    declared domain, then Indeed — so nothing that exists today changes behaviour."""
    if tab:
        found = engine_of_url(tab.get("url", "") or "")
        if found:
            return found
    return _ENGINE_BY_ID.get((getattr(session, "domain_id", "") or "").lower(), DEFAULT_ENGINE)


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
async def _observe(browser_url: str, query: str, *,
                   session_id: Any = None, note: str = "", actor: str = "system") -> dict[str, Any]:
    """What is actually true right now, as a tri-state map for `session_checkpoints.next_step`.

    True / False / **None**, and the None matters: "we did not check" must never read as a
    regression, or one flaky probe would send us re-running a rung that costs a real query.
    `radius_set` is always None — there is no cheap read-back of the distance pill, and guessing
    would be exactly the wrong kind of confident-wrong.
    """
    tabs_res = await _capture_post("/list_tabs", {"browser_url": browser_url}, timeout=8.0)
    tabs = tabs_res.get("tabs") or []

    # RECORD EVERY LOOK. `/list_tabs` is the browser's own /json/list over a local socket, so this
    # costs nothing and can therefore run on every observation rather than behind a flag someone
    # has to remember. A diff does not care who caused the change — the drive, the operator, or a
    # redirect — which is precisely why it can see an operator's mistake as readily as our own.
    if session_id is not None:
        session_windows.record(session_id, tabs, note=note, actor=actor)
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

    # THE AUTH PROBE HAS TO LOOK AT AN ENGINE TAB. Found live 2026-07-25 on a session left open
    # two days: with no tab hint `/auth_state` resolves whatever target CDP lists first — here a
    # Workday application open in the other tab — and the Indeed login JS, finding no Indeed
    # markers on a Workday page, returned logged_in=false. `authenticated` read REGRESSED and the
    # panel's next move was "sign in again" on a session that never signed out. Same shape as
    # 069eb61 (classify taking the last tab instead of the apply tab): probe the tab the rung is
    # ABOUT, not whichever one we are handed. The capture server reads the same tab to pick WHICH
    # site's login markers to look for, so handing it the right tab answers both questions at once.
    auth_tab = search_tab or _find_site_tab(tabs)
    if auth_tab is None:
        # No engine tab open, so auth is UNKNOWN — not false. False here would be a regression we
        # never observed, and this rung's reason to exist (logged-out data is provenance-invalid)
        # only bites while gathering FROM the engine, which needs such a tab.
        observed["authenticated"] = None
    else:
        auth = await _capture_post("/auth_state",
                                   {"browser_url": browser_url, "tab_id": auth_tab.get("tab_id")},
                                   timeout=8.0)
        observed["authenticated"] = bool(auth.get("ok") and auth.get("logged_in"))

    block = await _detect_block(browser_url, [t.get("url", "") for t in tabs])
    return {"observed": observed, "tabs": tabs, "search_tab": search_tab, "block": block,
            "reachable": True}


def _find_site_tab(tabs: list[dict], engine: Optional[dict] = None) -> Optional[dict]:
    """Any tab on a job engine — the fallback the auth probe uses when no results tab matches this
    session's query. Auth is a property of the SITE, not of the query, so a job-detail or home tab
    answers it just as well as a results page. Pass `engine` to insist on one particular site."""
    hosts = [engine["host"]] if engine else [e["host"] for e in ENGINES]
    for t in tabs:
        url = (t.get("url", "") or "").lower()
        if any(h in url for h in hosts):
            return t
    return None


def _find_search_tab(tabs: list[dict], query: str) -> Optional[dict]:
    """The tab showing results for THIS session's query, on ANY engine we know. Matching on the
    query keeps us from mistaking somebody else's search (or a stale one) for our own — the same
    context-bound validity rule the blackboard's provenance fields enforce. Each engine names the
    param that carries the query (`q` on Indeed, `keywords` on LinkedIn); everything else about
    the check is identical."""
    from urllib.parse import parse_qs, urlparse
    want = " ".join((query or "").split()).lower()
    for t in tabs:
        url = t.get("url", "") or ""
        engine = engine_of_url(url)
        if engine is None:
            continue
        if not want:
            return t
        got = (parse_qs(urlparse(url).query).get(engine["query_param"], [""])[0] or "")
        if " ".join(got.replace("+", " ").lower().split()) == want:
            return t
    return None


def _page_from_url(url: str) -> int:
    """1-based page number from a results URL, 1 when absent. Both engines paginate with `?start=`;
    they differ only in how many results a page holds (Indeed 10, LinkedIn 25) — so read the size
    off the engine rather than assuming Indeed's, which would report page 3 of a LinkedIn search as
    page 6 and make the ladder think it had climbed twice as far as it had."""
    from urllib.parse import parse_qs, urlparse
    engine = engine_of_url(url) or DEFAULT_ENGINE
    try:
        start = parse_qs(urlparse(url or "").query).get("start", [None])[0]
        return (int(start) // engine["page_size"]) + 1 if start is not None else 1
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
def _account_state(bb: Any) -> Optional[dict[str, Any]]:
    """Which account leg is due for the step being worked, and whether we can run it.

    Read-only and cheap — registry + vault metadata, no browser and no secret. Returns None when
    the current step has no company/ATS to have an account with, which is most of them.
    """
    try:
        import accounts as accounts_mod
        import ats_accounts
        step = aps.Queue.from_dict((bb.world or {}).get("apply_queue")).current()
        if step is None or not step.company or not step.platform:
            return None
        action = ats_accounts.next_account_action(step.company, step.platform)
        rec = accounts_mod.get_account(action["account_id"]) or {}
        return {
            "job_id": step.job_id,
            "company": step.company,
            "ats": step.platform,
            "leg": action.get("leg"),               # create_account | sign_in
            "button": action.get("button"),
            "status": action.get("account_status"),
            # Whether a credential is stored to run the leg WITH. `suffix_configured` is not the
            # same question: the password is derivable long before the account exists.
            "has_creds": bool(rec.get("has_creds")),
            "login_url": rec.get("login_url") or "",
        }
    except Exception:  # noqa: BLE001 — a read model must not break the panel
        return None


def _view(session: TrainingSession, bb: Any, ledger: cps.Ledger, obs: dict[str, Any], *,
          page: int, results: Optional[list[dict]] = None,
          awaiting: Optional[str] = None, last: Optional[dict] = None) -> dict[str, Any]:
    """Everything the control panel renders: the declared query, where we are on the ladder,
    which page we are on, and this page's results."""
    ss = bb.search_state
    observed = obs.get("observed", {})
    # The rungs are worded for the engine actually being driven. They used to read "Signed in to
    # Indeed" on every ladder, which a LinkedIn session renders as an instruction to go and sign in
    # to the wrong site — found the first time a LinkedIn session was started, 2026-07-27.
    engine_label = engine_for(session, obs.get("search_tab"))["label"]
    return {
        "session_id": session.id,
        "goal": bb.goal,
        "query": ss.query,
        "location": ss.location,
        "radius_miles": (bb.world or {}).get("radius_miles"),
        "page": page,
        "engine": engine_label,
        "ladder": cps.status_rows(ledger, observed, page=page, engine=engine_label,
                                  has_results=bool(results if results is not None
                                                   else (bb.world or {}).get("page_results"))),
        "next": cps.next_step(ledger, observed, page=page, engine=engine_label).as_dict(),
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
        # THE ACCOUNT'S STANDING STATE for the step being worked — which leg is due, and whether a
        # credential exists to run it. Separate from `account_handoff`, which is a pending REQUEST
        # and is cleared the moment the account is made. Without this the panel went blank at
        # exactly the wrong moment: the Teradyne account was created, `mark_created` cleared the
        # handoff, the browser was sitting on SAP's sign-in wall, and the cockpit offered nothing
        # to press (2026-07-28). A settled create rung is not the end of the account's business —
        # the sign-in leg is what comes next, and it needs a surface.
        "account_state": _account_state(bb),
        # What the OPEN PANE says the application is. Read at open_pane and surfaced here so a
        # proposal is made against the observed apply type rather than an assumed one — on
        # 2026-07-24 a proposal cited "apply_type=indeed_apply" as evidence for a posting whose
        # pane had plainly reported `company_site`. Fabricated evidence is worse than none: it
        # lands in the corpus looking exactly like the real thing.
        "open_pane": (bb.world or {}).get("open_pane"),
        # What the WINDOW did since the last crank. The apply stage opens tabs on its own (an
        # employer landing, then the ATS), so "nothing changed" has to be something the drive can
        # actually see rather than assume.
        "tab_drift": (bb.world or {}).get("tab_drift"),
        # Whether the DATABASE already holds an application for the job in hand. Part of the
        # context every proposal is made against: "have we been here before" is a question the
        # reasoner should never have to spend a drive answering.
        "applied_check": (bb.world or {}).get("applied_check"),
        "queue_summary": aps.Queue.from_dict((bb.world or {}).get("apply_queue")).summary(),
        "awaiting": awaiting,
        # WHICH ATSes hide their form behind section bars. The panel needs this to know whether to
        # offer the section reader at all, and the declaration lives in apply_fields — so it is
        # sent rather than duplicated as a hardcoded name in the UI, which is how the two would
        # drift the first time a second accordion ATS is added.
        "accordion_ats": sorted(apply_fields.SECTION_BARS),
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
#:
#: This is a FALLBACK, consulted only when a mini-step does not state `staged` for itself. A rung
#: id is a category, and some categories do both: `account` types credentials in "auto"/"fill" and
#: types nothing whatever in "handoff". It cannot be split by adding a rung id — the ladder settles
#: rungs BY NAME (see `_ACCOUNT_RUNG`), so a second name would leave `account` unsettled forever.
_READ_ONLY_RUNGS = frozenset({"open_pane", "verify_identity", "classify", "orient"})


def _mini_staged_input(m: Any) -> bool:
    """Did this one mini-step put something INTO the page?

    When the mini-step SAYS so, that answer wins: the code that drove the page is the only thing
    that knows whether it typed, and it is the one place that can say so without inference. Silence
    falls back to the rung, which is what every mini-step recorded before the field existed.
    """
    if isinstance(m, dict):
        staged, rung = m.get("staged"), m.get("rung", "")
    else:
        staged, rung = getattr(m, "staged", None), getattr(m, "rung", "")
    return bool(staged) if staged is not None else rung not in _READ_ONLY_RUNGS


def _queue_in_progress(bb: Any) -> bool:
    """Is an application holding input a reload would throw away?

    NOT simply "has started" — that was the first version and it was too broad: session 21 had
    opened a pane and confirmed the job's identity, which stages nothing, and the panel duly
    suppressed a refresh it should have offered. Withholding the remedy is as much a failure as
    proposing a destructive one; it just fails quietly. A step counts only once it has run a rung
    that puts something INTO the page.

    The SECOND version was still too broad, and in the same shape. Session 21 again (Teradyne /
    SuccessFactors, 2026-07-28): 18.4 hours idle, red, on a verifiably EMPTY SAP signup form — and
    the refresh withheld to protect nothing, because the `account` rung had run. It had run in
    HANDOFF mode, where it types nothing at all and only surfaces the credential to the operator.
    Judging by rung could not see that; a mini-step that reports for itself can. A manual reload
    fixed the session that the panel had talked itself out of offering.
    """
    try:
        queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
        return any(
            not s.done and any(_mini_staged_input(m) for m in (s.minis or ()))
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
    # No tab to read yet at setup time, so the engine comes from the session's declared domain —
    # which is the one place it IS authoritative, because the operator picked it when they created
    # the session.
    engine = engine_for(session)

    if ledger.holds("query_entered"):
        spent = " ".join((bb.search_state.query or "").split())
        if spent.lower() != query.lower():
            raise HTTPException(
                status_code=409,
                detail=(f"This session already ran {spent!r}. A session holds ONE query — "
                        f"re-searching is what makes {engine['label']} collapse results. Start a "
                        f"new session for {query!r}."))

    bb.search_state.query = query
    bb.search_state.location = " ".join((body.location or "").split())
    bb.goal = (f"Search {engine['label']} for {query!r}"
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
    obs = await _observe(_session_browser_url(session), bb.search_state.query, session_id=session.id)
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

    obs = await _observe(browser_url, query, session_id=session.id)
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
    obs_after = await _observe(browser_url, query, session_id=session.id)
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
    # WHICH engine are we climbing? Resolved once per step from the live results tab, falling back
    # to the session's declared domain. Every branch below reads it instead of naming a site.
    engine = engine_for(session, obs.get("search_tab"))

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
                "browser_url": browser_url, "url": engine["home"],
                "tab_id": ((obs.get("tabs") or [{}])[0]).get("tab_id", ""),
                "settle_seconds": 3.0})
            await asyncio.sleep(xs.pause_for(style, xs.NAVIGATION))
            if not nav.get("ok"):
                bb.log("handoff", f"could not open {engine['label']} — {str(nav.get('detail'))[:90]}")
                return {"ok": False, "action": action, "awaiting": "operator_open_engine",
                        "detail": f"No {engine['label']} tab was open and this session could not "
                                  f"open one ({str(nav.get('detail') or 'no detail')[:120]}). The "
                                  f"rung is left as it was rather than guessed."}
            bb.log("nav", f"opened {engine['label']}'s home page to probe sign-in ({style.name} pace)")
            obs = await _observe(browser_url, bb.search_state.query, session_id=session.id)

        if obs["observed"].get("authenticated"):
            ledger.mark("authenticated", evidence="/auth_state reported logged_in",
                        initiator=initiator)
            bb.log("checkpoint", f"authenticated — signed in to {engine['label']}")
            return {"ok": True, "action": action, "detail": "Signed in."}

        # STILL UNKNOWN AFTER OPENING THE DOOR — we navigated but no Indeed tab came back, so we
        # never looked at Indeed. Releasing the rung here would invent a regression, and the login
        # survey below would run against whatever page happens to be in front. Same rule
        # `session_checkpoints` enforces one layer up: an unknown is not a regression.
        if obs["observed"].get("authenticated") is None:
            bb.log("handoff", f"auth unknown — no {engine['label']} tab to probe after navigating")
            return {"ok": False, "action": action, "awaiting": "operator_open_engine",
                    "detail": f"Opened {engine['label']} but no {engine['label']} tab came back, so "
                              f"sign-in could not be checked. The rung is left as it was rather "
                              f"than guessed."}

        # NOT SIGNED IN IS A STEP, NOT A DEAD END. The boundary is that we never CLEAR a 2FA
        # challenge or solve a captcha — it was never that we refuse to sign in. Reporting "the
        # operator signs in" and offering nothing meant login was the one rung the system did not
        # own: the operator could see it was next and had nothing to press (operator, live
        # 2026-07-24).
        ledger.release("authenticated")

        # A PROVIDER'S WINDOW BEATS EVERYTHING. If Google (or Apple, or Microsoft) already has a
        # sign-in window open, that window IS the state — driving the engine's own credential form
        # underneath it would be answering a question nobody asked, on a page that is not in front.
        # Checked before the credential drive for exactly that reason.
        if find_sso_popup(obs.get("tabs") or []) is not None:
            login = await _login_survey(browser_url, obs, engine, bb)
            bb.log("handoff", f"provider window open ({login['state']}) — {login['detail'][:80]}")
            return {"ok": False, "action": action,
                    "awaiting": "operator_login" if not login["can_drive"] else "operator_pick",
                    "login": login, "detail": login["detail"]}

        # FIRST, TRY THE LOGIN WE WERE GIVEN. If this domain has an account with credentials the
        # operator stored in the vault, sign in with them — that is what the credentials are FOR,
        # and it is the same reasoning loop `/api/accounts/{id}/login` already runs for ATS logins
        # (login_reasoner: observe → classify → reason → act → verify, credentials filled AT MOST
        # ONCE so a wrong password escalates instead of hammering a real account). MFA, captcha and
        # checkpoints still escalate untouched — those are the actual boundary.
        drove = await _drive_login(engine=engine, bb=bb, browser_url=browser_url, obs=obs,
                                   initiator=initiator)
        if drove is not None:
            if drove.get("authenticated"):
                ledger.mark("authenticated",
                            evidence=f"signed in as {drove['account']} ({drove['steps']} step(s))",
                            initiator=initiator)
                bb.log("checkpoint", f"authenticated — signed in to {engine['label']} "
                                     f"as {drove['account']}")
                return {"ok": True, "action": action, "login": drove,
                        "detail": f"Signed in to {engine['label']}."}
            # It ran and did not get there. The reason matters — a human gate is not a failure.
            return {"ok": False, "action": action, "awaiting": drove["awaiting"], "login": drove,
                    "detail": drove["detail"]}

        # No stored credential: survey what this page offers and hand back real options.
        login = await _login_survey(browser_url, obs, engine, bb)
        bb.log("handoff", f"not signed in ({login['state']}) — "
                          f"{len(login['options'])} way(s) in offered")
        return {"ok": False, "action": action, "awaiting": "operator_login", "login": login,
                "detail": login["detail"]}

    if action == "run_query":
        return await _run_query(engine=engine, bb=bb, ledger=ledger, browser_url=browser_url,
                                obs=obs, initiator=initiator)

    if action == "set_distance":
        miles = max(int((bb.world or {}).get("radius_miles") or 50), 50)
        # Setting the pill RE-QUERIES the backend, so it is a navigation as far as pacing goes.
        # This step used to fire with no pause at all and was over in about half a second.
        style = xs.pick_style()
        await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))
        res = await _capture_post("/set_distance",
                                  {"browser_url": browser_url, "tab_url": engine["search_tab"],
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
        return await _review_page(bb=bb, browser_url=browser_url, page=page, db=db, engine=engine)

    return {"ok": False, "action": action, "detail": f"No executor for {action!r}."}


# --- the two rungs that actually drive ----------------------------------------------------------
#: Outcomes /execute can legitimately return. Anything else — a FastAPI validation body, a
#: transport error, an empty dict — is NOT a result and must never be read as one.
_ACTED_OK = {"ok", "committed_unconfirmed"}


async def _run_query(*, engine: dict[str, Any], bb: Any, ledger: cps.Ledger, browser_url: str,
                     obs: dict[str, Any],
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
    candidates = scan.get("candidates") or []
    # THE ENGINE'S OWN MATCHER FIRST. The shared one is Indeed's: its query hints do not match
    # LinkedIn's "I'm looking for…" box, and its submit hints DO match `Skip to search` — a
    # skip-link that would jump the caret to a landmark and report a query. `linkedin_recipe`
    # documented both and nothing consulted it, so the rung refused to run on a page it was
    # looking straight at (live 2026-07-30: "81 elements scanned; found submit").
    commit_by = engine.get("commit", "button")
    if engine.get("platform") == "linkedin":
        import linkedin_recipe
        found = linkedin_recipe.search_controls(candidates)
        controls = {k: v for k, v in found.items() if k in ("query", "submit") and v}
    else:
        controls = search_cadence.find_search_controls(candidates)
    needs_submit = commit_by == "button"
    if "query" not in controls or (needs_submit and "submit" not in controls):
        seen = len(candidates)
        return {"ok": False, "action": "run_query", "awaiting": "operator_search_box",
                "detail": f"Could not find a search box"
                          + (" and a submit button" if needs_submit else "")
                          + f" on this page ({seen} elements scanned; found "
                          f"{', '.join(controls) or 'neither'}). Open {engine['label']}'s job "
                          f"search, then step again."}

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
        # COMMIT THE WAY THIS ENGINE COMMITS. `submit` on the query box dispatches Enter to the
        # focused element (the fill just focused it) — which is the only way in on an engine with
        # no submit control, and is what the operator's recording measured LinkedIn doing.
        if commit_by == "enter":
            ok, detail = await _act("submit", controls["query"])
        else:
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
        # ROUTE B: THE COMMIT LANDED ON THE BLENDED SEARCH, WHICH IS ONE CLICK SHORT OF RESULTS.
        # Measured 2026-07-28 and again live 2026-07-30: committing from the jobs home can land on
        # `/search/results/all/?...&origin=GLOBAL_SEARCH_HEADER` — LinkedIn's everything-search
        # (people, posts, courses, groups AND jobs). It is not a failure and it is not a different
        # query; it is the same query one navigation short of the jobs results, and the way on is
        # the JOBS section's "Show all".
        #
        # FIVE links share that accessible name, one per section. Picking by document order happens
        # to be right today and is precisely the mistake that clicked the wrong company on
        # 2026-07-26, so it is chosen BY HREF — the jobs one is the only `/jobs/search-results/`.
        # A CSS selector is the honest addressing here: the name cannot distinguish them, and this
        # is the case `/execute`'s `selector` exists for.
        landed = ((await _tab_urls()) or [""])[0]
        if "/search/results/" in landed and engine.get("platform") == "linkedin":
            bb.log("run_query", "landed on the blended search — taking the jobs section's "
                                "'Show all' (chosen by href, not by document order)")
            res = await _capture_post("/execute", {
                "browser_url": browser_url, "tab_id": tab_id, "action_id": "click",
                "target_bbox": {}, "selector": 'a[href^="/jobs/search-results/"]',
                "driver": "humanized"})
            if res.get("outcome") in _ACTED_OK:
                await asyncio.sleep(xs.pause_for(style, xs.NAVIGATION))
                after = await _capture_post("/list_tabs", {"browser_url": browser_url}, timeout=8.0)
                tab = _find_search_tab(after.get("tabs") or [], query)

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


async def _review_page(*, bb: Any, browser_url: str, page: int, db: Session,
                       engine: dict[str, Any]) -> dict[str, Any]:
    """At the start line: read this page's cards and hand them to the operator.

    This is the stop-and-go half. It does NOT mark the page rung — the operator does that by
    choosing (`/choose`). Reading a page is free; deciding on it is theirs.
    """
    from observed_jobs import upsert_observed_jobs
    ex = await _capture_post("/extract_jobs",
                             {"browser_url": browser_url, "tab_url": engine["search_tab"]})
    if not ex.get("ok"):
        return {"ok": False, "action": "review_page", "awaiting": "operator_results",
                "detail": f"Could not read the results ({ex.get('detail') or 'extractor said no'})."}

    cards = ex.get("jobs") or []
    new_count, dup_count = upsert_observed_jobs(db, cards, engine["platform"], bb.search_state.query)
    db.commit()

    # THE APPLIED CHECK, AT SCAN TIME. One query for the whole page, so every card arrives already
    # knowing whether the database has an application on file for it — by id, by requisition, or
    # (as a warning only) by employer + role. Asking here is what stops a drive from rediscovering
    # the answer six steps into an ATS, which is exactly how this page's own BIDMC pick was spent.
    applied = applied_index.check_many(db, cards, platform=engine["platform"])

    results = [{"job_id": f"{engine['platform']}:{c.get('external_id')}", "external_id": c.get("external_id"),
                "title": c.get("title"), "company": c.get("company"),
                "location": c.get("location"), "salary": c.get("salary"),
                "url": c.get("url"),
                "applied": (applied.get(c.get("external_id")) or applied_index.AppliedVerdict()
                            ).as_dict()} for c in cards if c.get("external_id")]
    already = sum(1 for r in results if r["applied"]["status"] == applied_index.STATUS_APPLIED)
    maybe = sum(1 for r in results if r["applied"]["status"] == applied_index.STATUS_LIKELY)
    bb.search_state.page = page
    bb.search_state.observed_count = (bb.search_state.observed_count or 0) + len(results)
    bb.world = dict(bb.world or {})
    bb.world["page_results"] = results
    bb.log("review", f"page {page}: {len(results)} results ({new_count} new, {dup_count} seen before)")
    return {"ok": True, "action": "review_page", "page": page, "results": results,
            "awaiting": "choose",
            "applied_summary": {"applied": already, "likely": maybe},
            "detail": f"Page {page}: {len(results)} results — {new_count} new, "
                      f"{dup_count} already seen"
                      + (f", {already} ALREADY APPLIED" if already else "")
                      + (f", {maybe} possibly applied (worth a look)" if maybe else "")
                      + ". Choose what to do with them."}


# --- login: a step the system owns, up to the secret ------------------------------------------
#: Where the agent stops, always. These are the states whose next action IS the secret itself, and
#: no amount of "the system should own every step" changes who owns those.
# States where the screen itself is the human's, whatever else is on it. NOTE `signin_form` is NOT
# here: a page can be a password form AND offer a way in we are allowed to click. LinkedIn's
# logged-out /jobs page is exactly that — an email+password form beside a "Continue with google"
# button — and treating the password field as proof that nothing is drivable meant the ladder
# reported "you type it, not us" while a one-click SSO route sat on the same screen, already in
# SIGNIN_ENTRY_HINTS and never looked at (found live 2026-07-27, session #22).
_HUMAN_ONLY_LOGIN = {"mfa", "captcha", "login_error", "create_form"}

_HUMAN_ONLY_COPY = {
    "signin_form": "The password field is up — you type it, not us. Sign in and press Re-check.",
    "mfa": "A verification code is being asked for. Enter it yourself, then press Re-check.",
    "captcha": "A challenge is up. Clear it yourself — we never auto-solve.",
    "login_error": "The last sign-in attempt was rejected. Fix it in the window, then Re-check.",
    "create_form": "This is an account-creation form. Creating accounts is yours, not ours.",
}


#: What each non-authenticated login outcome means for the LADDER — which is a different question
#: from what it means for the login loop. `awaiting` is the operator-facing "what now", and only
#: these four are genuinely the human's; everything else is ours to report and retry.
_LOGIN_AWAITING = {
    "captcha": "operator_challenge",
    "mfa": "operator_2fa",
    "bad_credentials": "operator_login",
    "account_exists": "operator_login",
}


def _identity_username(engine: dict[str, Any]) -> str:
    """The address to sign the IDENTITY PROVIDER in with.

    Not the engine's account. LinkedIn's own account has no credential and should not — LinkedIn's
    route IS Google, so the login that answers Google's question belongs to the google PROVIDER
    (today that is the gmail domain's account, which is where the operator stores it). Resolved
    server-side and used only to fill the address field; the password half is never read here.
    """
    try:
        import accounts as accounts_mod
        import providers
    except Exception:  # noqa: BLE001
        return ""
    # The engine's own login first — a domain that really does own its identity keeps working.
    own = _domain_account(engine)
    if own:
        creds = accounts_mod.resolve_creds(own["account_id"])
        if creds:
            return creds[0]
    # Otherwise the google provider's member domains, in declared order.
    group = providers.get_provider("google") or {}
    for domain_id in group.get("member_domains", []):
        for acct in accounts_mod.list_accounts(domain_id=domain_id):
            if acct.get("has_creds") and acct.get("status") == "active":
                creds = accounts_mod.resolve_creds(acct["account_id"])
                if creds:
                    return creds[0]
    return ""


def _domain_account(engine: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The account this ENGINE signs in with — the domain login (`linkedin_default`), not one of
    the per-employer ATS accounts. Picks the first active account registered against the engine's
    domain that actually has a credential stored; None when the operator has not saved one, which
    is a legitimate state and not an error."""
    try:
        import accounts as accounts_mod
    except Exception:  # noqa: BLE001
        return None
    for acct in accounts_mod.list_accounts(domain_id=engine["id"]):
        if acct.get("kind", "domain") == "domain" and acct.get("status") == "active" \
                and acct.get("has_creds"):
            return acct
    return None


async def _drive_login(*, engine: dict[str, Any], bb: Any, browser_url: str, obs: dict[str, Any],
                       initiator: str) -> Optional[dict[str, Any]]:
    """Sign in to the engine with the operator's stored credential.

    Returns None when there is nothing to try (no stored login) so the caller falls back to the
    survey. Otherwise returns a dict the ladder can render: {authenticated, awaiting, detail,
    account, steps, trail}.

    The whole loop is `login_reasoner.run_login` — the same one the Accounts panel's Login button
    runs. Reusing it is the point: it already classifies 'account already exists' apart from a
    wrong password apart from an MFA prompt, fills the credential AT MOST ONCE (so a bad password
    escalates instead of hammering a real account), journals every step to the Open Brain, and
    recovers from a stale tab once. Re-implementing any of that here would be a second, worse
    login that nobody trains on.

    The password never passes through this function's reasoning: it is resolved server-side from
    the vault and handed straight to the driver. MFA, captcha and checkpoints escalate untouched.
    """
    import httpx

    import login_reasoner

    acct = _domain_account(engine)
    if not acct:
        return None
    try:
        import accounts as accounts_mod
        creds = accounts_mod.resolve_creds(acct["account_id"])
    except Exception:  # noqa: BLE001
        creds = None
    if not creds:
        return None
    username, password = creds

    # Drive the tab this rung is ABOUT. Same lesson as the auth probe: the login readers are chosen
    # by the tab's host, so pointing at "whatever is first" asks the wrong site's question.
    tab = obs.get("search_tab") or _find_site_tab(obs.get("tabs") or [], engine)
    if tab is None:
        return {"authenticated": False, "awaiting": "operator_open_engine",
                "account": acct["account_id"], "steps": 0, "trail": [],
                "detail": f"No {engine['label']} tab to sign in on."}

    def _journal(step, state, idx):
        try:
            from routers.accounts import _journal_login_step
            _journal_login_step(acct["account_id"], state, step)
        except Exception:  # noqa: BLE001 — journaling must never sink a login
            pass

    bb.log("login", f"signing in to {engine['label']} as {acct.get('username_hint') or acct['account_id']}")
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            result = await login_reasoner.run_login(
                client=client, capture_url=settings.capture_server_url,
                browser_url=browser_url, tab_id=tab.get("tab_id", ""),
                username=username, password=password, journal=_journal)
    except httpx.HTTPError as exc:
        return {"authenticated": False, "awaiting": "operator_login", "account": acct["account_id"],
                "steps": 0, "trail": [], "detail": f"Login driver unreachable: {exc}"}

    out = {"authenticated": bool(result.ok), "account": acct["account_id"],
           "status": result.status, "steps": result.steps, "trail": result.trail,
           "awaiting": _LOGIN_AWAITING.get(result.status, "operator_login"),
           "detail": result.detail or result.status}
    if not result.ok:
        # A stop here is a real "agent needs help" event, so it goes to the handoff log and the
        # activity feed rather than dying inside a JSON field nobody is watching.
        bb.log("handoff", f"sign-in stopped at {result.status} — {result.detail}"[:180])
        try:
            from runtime import handoff as handoff_mod
            handoff_mod.emit_escalation(
                reason=result.status,
                task_goal=f"log in to {engine['label']}",
                detail=f"{engine['label']} sign-in stopped: {result.detail or result.status}",
                url=tab.get("url", ""), state=result.status)
        except Exception:  # noqa: BLE001
            pass
    return out


#: Hosts that mean "another site's sign-in window is open and waiting". An SSO popup is its own
#: CDP page target on the SAME port, so it shows up in /list_tabs beside the engine's tab.
#: Google's are owned by `google_recipe`; the rest are named but not yet driven.
_SSO_POPUP_HOSTS = tuple(google_recipe.SSO_HOSTS) + (
    "appleid.apple.com", "login.microsoftonline.com", "login.yahoo.com")


def find_sso_popup(tabs: list[dict]) -> Optional[dict]:
    """The identity provider's own window, if one is open."""
    for t in tabs:
        url = (t.get("url", "") or "").lower()
        if any(h in url for h in _SSO_POPUP_HOSTS):
            return t
    return None


def _challenge_age(bb: Any, popup: dict[str, Any]) -> Optional[float]:
    """Seconds since this challenge screen was first seen, or None if it is new.

    Kept on the blackboard and keyed by the challenge URL, because the URL changes when Google
    moves to a different factor and a new factor deserves a fresh clock. This is the ONLY way to
    tell a live challenge from an expired one — see CHALLENGE_TTL_SECONDS.
    """
    from datetime import datetime, timezone
    url = (popup.get("url") or "")[:200]
    if not url:
        return None
    bb.world = dict(bb.world or {})
    seen = dict(bb.world.get("sso_challenge_seen") or {})
    now = datetime.now(timezone.utc)
    first = seen.get(url)
    if not first:
        seen[url] = now.isoformat()
        bb.world["sso_challenge_seen"] = seen
        return None
    try:
        started = datetime.fromisoformat(first)
        return (now - started).total_seconds()
    except Exception:  # noqa: BLE001
        return None


async def _survey_sso_popup(browser_url: str, popup: dict[str, Any],
                            username: str = "",
                            challenge_age: Optional[float] = None) -> dict[str, Any]:
    """What the identity provider is asking, and whose turn it is.

    THE BOUNDARY IS THE STATE, NOT THE HOST. Refusing everything on accounts.google.com is what
    turns a one-click SSO login into a human interruption: picking which of your own signed-in
    accounts to use is a click on a tile, not a credential. `google_recipe` owns that judgement —
    this just asks it, and reports the answer in the survey's shape so the panel renders it the
    same way as any other login screen.
    """
    scan = await _capture_post("/ax_scan", {"browser_url": browser_url,
                                            "tab_id": popup.get("tab_id", "")}, timeout=25.0)
    candidates = scan.get("candidates") or []
    url = popup.get("url", "") or ""
    state = google_recipe.classify(url, str(scan.get("page_text") or ""))
    plan = google_recipe.next_action(state, candidates, username=username)
    drivable = plan["action"] in ("click", "type")
    # A challenge screen carries a FORK and a CLOCK, and the operator needs both. The fork
    # ("Try another way") is a click, not a credential — but WHICH way to verify is their choice,
    # not ours, so it is offered rather than taken. The clock is the only thing that distinguishes
    # a live challenge from an expired one, because nothing on the page does.
    alt = google_recipe.find_alternative_control(candidates)
    age_note = google_recipe.challenge_age_note(challenge_age)
    return {
        "state": f"sso:{state}", "url": url, "seen": len(candidates),
        "can_drive": drivable,
        "alternatives": ([{"name": alt.get("name"), "role": alt.get("role"),
                           "why": "Google's other ways to verify — you pick which."}]
                         if alt and google_recipe.policy_for(state) == google_recipe.HUMAN else []),
        "challenge_age_seconds": round(challenge_age) if challenge_age is not None else None,
        "stale": bool(age_note),
        "provider": "google" if google_recipe.is_sso_url(url) else "unknown",
        "policy": plan["policy"],
        "needs_approval": bool(plan.get("needs_approval")),
        "action": plan["action"],
        "options": ([{"name": plan["target"].get("name"), "role": plan["target"].get("role"),
                      "why": plan["why"]}] if drivable else []),
        "detail": (f"{plan['why']} — I can take this step for you." if drivable else
                   f"{plan['why']} " + (age_note or
                   "Finish it in the window, then press Re-check. Once it is done this browser "
                   "profile stays signed in, so it is a one-time step.")),
    }


async def _drive_sso_step(browser_url: str, popup: dict[str, Any], username: str,
                          *, approved: bool = False) -> dict[str, Any]:
    """Take ONE step on the identity provider's screen, human-paced.

    One step, never a loop: each Google screen swaps content in place, so the only honest way to
    know what happened is to re-observe and classify again. A loop here would be guessing at
    Google's state machine, and a wrong guess on the identity provider is the expensive kind.
    """
    scan = await _capture_post("/ax_scan", {"browser_url": browser_url,
                                            "tab_id": popup.get("tab_id", "")}, timeout=25.0)
    candidates = scan.get("candidates") or []
    state = google_recipe.classify(popup.get("url", ""), str(scan.get("page_text") or ""),
                                   candidates)
    plan = google_recipe.next_action(state, candidates, username=username, approved=approved)
    if plan["action"] not in ("click", "type"):
        return {"ok": False, "state": state, "policy": plan["policy"], "detail": plan["why"]}

    style = xs.pick_style()
    target = plan["target"]
    base = {"browser_url": browser_url, "tab_id": popup.get("tab_id", ""), "target_bbox": {},
            "target_role": target.get("role"), "target_name": target.get("name"),
            "driver": "humanized"}

    if plan["action"] == "type":
        # KEYSTROKES, not an assignment. Google's identifier is a controlled input inside its own
        # view layer: setting the value and firing one event leaves the internal model empty, Next
        # re-renders the same screen, and nothing errors. This is the per-stack tailoring — the
        # opposite choice is right on React inputs, which is why it is recipe data and not a
        # global default.
        await _capture_post("/execute", {**base, "action_id": "clear"})
        await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))
        typed = await _capture_post("/execute", {**base, "action_id": "type", "value": username})
        if typed.get("outcome") not in _ACTED_OK:
            return {"ok": False, "state": state, "policy": plan["policy"],
                    "detail": f"Could not type the address ({typed.get('outcome') or 'no outcome'})."}
        await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))
        # CLICK NEXT. `press` is not in the interaction vocabulary — dispatching it typed the
        # address and then submitted nothing, which reads as "the screen did not change" and looks
        # exactly like a page that refused us. The submit control is addressed by role+name like
        # every other click.
        submit = plan["submit"]
        pressed = await _capture_post("/execute", {
            **base, "action_id": "click",
            "target_role": submit.get("role"), "target_name": submit.get("name")})
        if pressed.get("outcome") not in _ACTED_OK:
            return {"ok": False, "state": state, "policy": plan["policy"],
                    "detail": f"Typed the address but could not submit it "
                              f"({pressed.get('outcome') or 'no outcome'})."}
    else:
        res = await _capture_post("/execute", {**base, "action_id": "click"})
        if res.get("outcome") not in _ACTED_OK:
            return {"ok": False, "state": state, "policy": plan["policy"],
                    "detail": f"Could not click {target.get('name')!r} "
                              f"({res.get('outcome') or 'no outcome'})."}

    await asyncio.sleep(xs.pause_for(style, xs.NAVIGATION))
    # The popup swaps content in place, so "did it advance?" is a CONTENT question. Re-classify.
    after = await _capture_post("/ax_scan", {"browser_url": browser_url,
                                             "tab_id": popup.get("tab_id", "")}, timeout=25.0)
    tabs = (await _capture_post("/list_tabs", {"browser_url": browser_url}, timeout=8.0)).get("tabs") or []
    still = find_sso_popup(tabs)
    landed = (google_recipe.classify(still.get("url", ""), str(after.get("page_text") or ""),
                                     after.get("candidates") or [])
              if still else "closed")
    return {"ok": True, "state": state, "landed": landed, "policy": plan["policy"],
            "pace": xs.describe(style),
            "detail": (f"{state} -> {landed}." if landed != state else
                       f"Took the step on {state} but the screen did not change — "
                       f"check the window.")}


async def _login_survey(browser_url: str, obs: dict[str, Any],
                        engine: Optional[dict[str, Any]] = None,
                        bb: Any = None) -> dict[str, Any]:
    """What the system can SEE and DO about signing in, right now.

    Answers the question the old dead-end could not: not "are we logged in" (no) but "what is the
    next possible move, and can we make it". Clicks toward a login screen are ours; the credential
    itself is never.
    """
    import login_reasoner as lr

    tabs = obs.get("tabs") or []

    # AN SSO POPUP IS ITS OWN WINDOW, AND IT IS THE THING WAITING. Found live the first time a
    # LinkedIn session clicked "Continue with google" (session #22, 2026-07-27): the popup became
    # a second page target, the survey took tabs[0] — the popup — and dutifully offered "Google
    # Terms of Service" as a way into the account. Two separate faults in one reading: surveying
    # the wrong window, and treating a footer link as an entry. This answers the first.
    #
    # The handoff is deliberate and long-standing (LEARNINGS 2026-07-09): we drive up to the
    # provider's door and no further. Google's password page is the user's crown-jewel credential
    # and the most bot-fingerprinted page on the web; the training value is in capturing the
    # states, not in who typed.
    popup = find_sso_popup(tabs)
    if popup is not None:
        # THE HINT IS MASKED AND CANNOT MATCH A TILE. `username_hint` is "p***@example.com" by
        # design — it exists to be displayed, not compared. Matching a chooser tile needs the real
        # address, so it is resolved SERVER-SIDE from the vault and used only for the comparison;
        # it never enters the response. (The option label comes from the tile's own accessible
        # name, which is already on the operator's screen.)
        return await _survey_sso_popup(
            browser_url, popup, username=(_identity_username(engine) if engine else ""),
            challenge_age=(_challenge_age(bb, popup) if bb is not None else None))

    # Otherwise survey the ENGINE's own tab, never "whichever came first".
    tab = obs.get("search_tab") or (_find_site_tab(tabs, engine) if engine else None) \
        or (tabs[0] if tabs else {})
    scan = await _capture_post("/ax_scan", {"browser_url": browser_url,
                                            "tab_id": tab.get("tab_id", "")}, timeout=25.0)
    candidates = scan.get("candidates") or []
    # `/ax_scan` carries no page_text (the key is absent), so the accessible names ARE the text —
    # without this the captcha / MFA / "account already exists" tells never fire on this path.
    page_text = str(scan.get("page_text") or "") + " " + google_recipe.text_from(candidates)
    state = lr.classify_login_state(candidates, page_text, logged_in=False)
    # On a password screen only ALTERNATE routes count — the generic "Sign in" match there is the
    # form's own submit, and offering it would have us submitting an empty credential.
    entries = lr.find_signin_entries(candidates, alternates_only=(state == "signin_form"))

    if state in _HUMAN_ONLY_LOGIN:
        return {"state": state, "url": tab.get("url", ""), "options": [], "can_drive": False,
                "detail": _HUMAN_ONLY_COPY.get(state, "This screen needs you, not us."),
                "seen": len(candidates)}

    if not entries:
        # AX genuinely showing nothing is a real answer and a known one: Indeed has served a page
        # whose sign-in link AX could not see, and only a screenshot found it. Say so plainly
        # rather than implying the page has no way in. On a password form it is also the ONLY
        # case where "you type it" is the whole truth.
        detail = (_HUMAN_ONLY_COPY["signin_form"] if state == "signin_form" else
                  f"No sign-in control is visible to the accessibility tree on this page "
                  f"({len(candidates)} elements seen). A site has hidden it before — sign in "
                  f"directly in the window, then press Re-check.")
        return {"state": state, "url": tab.get("url", ""), "options": [], "can_drive": False,
                "detail": detail, "seen": len(candidates)}

    # A password form with a clickable alternative is a CHOICE, not a dead end.
    detail = (f"Not signed in. {len(entries)} way(s) in from here — pick one and I'll click it; "
              f"you take over at the password.")
    if state == "signin_form":
        detail = (f"A password form is up, and so are {len(entries)} route(s) that are clicks "
                  f"rather than credentials. Pick one and I'll click it — or type the password "
                  f"yourself and press Re-check.")
    return {"state": state, "url": tab.get("url", ""), "can_drive": True,
            "options": [{"name": e["name"], "role": e["role"], "why": e["why"]} for e in entries],
            "detail": detail, "seen": len(candidates)}


class ObserveBody(BaseModel):
    note: str = ""
    initiator: str = "operator"


@router.post("/api/session_control/{session_id}/observe/start")
async def observe_start(session_id: int, body: ObserveBody,
                        db: Session = Depends(get_db)) -> dict[str, Any]:
    """Turn the page recorder ON for this session's active tab.

    Explicitly operator-toggled. It is a diagnostic — a MutationObserver on a busy SPA is real
    overhead — so it is never started implicitly and never left on by something else finishing.
    """
    _check_initiator(body.initiator)
    session, bb, ledger = _load(session_id, db)
    browser_url = _session_browser_url(session)
    engine = engine_for(session)
    res = await _capture_post("/observe/start",
                              {"browser_url": browser_url, "tab_url": engine["search_tab"]},
                              timeout=30.0)
    if res.get("ok"):
        bb.log("observe", f"recording started{(' — ' + body.note) if body.note else ''}")
        _persist(bb, ledger)
    return {"recording": bool(res.get("ok")), "tab_url": res.get("url"),
            "detail": res.get("detail") or "Recording DOM, focus, input and key events."}


@router.post("/api/session_control/{session_id}/observe/stop")
async def observe_stop(session_id: int, body: ObserveBody,
                       db: Session = Depends(get_db)) -> dict[str, Any]:
    """Turn it off, drain the buffer, and KEEP the window as an artifact.

    Storing it is the point: a recording that exists only in one reply is a screenshot nobody
    saved. Stored whole and unfiltered — summaries are a view computed on read.
    """
    _check_initiator(body.initiator)
    session, bb, ledger = _load(session_id, db)
    browser_url = _session_browser_url(session)
    engine = engine_for(session)
    res = await _capture_post("/observe/stop",
                              {"browser_url": browser_url, "tab_url": engine["search_tab"]},
                              timeout=60.0)
    if not res.get("ok"):
        return {"recording": False, "stored": None,
                "detail": res.get("detail") or "Nothing was recording on this tab."}
    import observe_log
    head = observe_log.record(session_id, res, note=body.note)
    bb.log("observe", f"recording stopped — {head.get('count')} event(s) kept")
    _persist(bb, ledger)
    return {"recording": False, "stored": head,
            "detail": f"Kept {head.get('count')} event(s) over "
                      f"{round((head.get('duration_ms') or 0) / 1000, 1)}s."}


@router.get("/api/session_control/{session_id}/observe")
def observe_list(session_id: int) -> dict[str, Any]:
    """Every window kept for this session, newest first."""
    import observe_log
    return {"recordings": observe_log.list_for(session_id)}


@router.get("/api/session_control/{session_id}/observe/{recording_id}")
def observe_detail(session_id: int, recording_id: str, full: bool = False) -> dict[str, Any]:
    """One window. `full=true` returns every event; otherwise the INTERACTION SPINE — the clicks,
    focus moves, keys and value changes — because that is what a human reads first and the DOM
    mutations are the bulk without being the story. The full record is always there underneath."""
    import observe_log
    rec = observe_log.get(session_id, recording_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="No such recording for this session.")
    out = observe_log.header(rec)
    if full:
        out["events"] = rec.get("events") or []
    else:
        out.update(observe_log.summarize(rec))
    return out


@router.get("/api/session_control/{session_id}/windows")
def session_windows_view(session_id: int, limit: int = 100) -> dict[str, Any]:
    """What this session's browser has DONE — windows opened, closed and navigated, in order.

    Read-only and free: it serves the ledger that `_observe` folds a snapshot into on every look,
    so it reflects changes whoever caused them — the drive, the operator, or a redirect. The blind
    spot is stated rather than implied: a native OS dialog (a passkey prompt, a file picker) has no
    tab, so no diff will show it. `openers` is the correlation point — the moment a popup appeared
    is when to go looking for one with `/native_dialog`.
    """
    return {
        "summary": session_windows.summarize(session_id),
        "timeline": session_windows.timeline(session_id, limit=limit),
        "openers": session_windows.openers(session_id),
        "blind_spot": "Native OS dialogs have no tab and cannot appear here — correlate an "
                      "`opened` event with /native_dialog on that tab.",
    }


class SsoStepBody(BaseModel):
    #: The operator's per-instance yes, for the one state that needs it (OAuth consent). It is not
    #: a standing setting and is never remembered — a grant approved once is not a grant approved
    #: forever.
    approved: bool = False
    #: ARE YOU AT THE KEYBOARD? Every drivable Google step lands on a factor only a human can
    #: clear, and those expire in under a minute (google_recipe.CHALLENGE_TTL_SECONDS). Taking the
    #: address step while nobody is watching does not fail — it succeeds, spawns a native passkey
    #: prompt, and that prompt times out silently, leaving a screen indistinguishable from a live
    #: one. Measured session #22. Default False so the unattended path is the one you opt OUT of.
    attended: bool = False
    initiator: str = "operator"


@router.post("/api/session_control/{session_id}/sso_step")
async def sso_step(session_id: int, body: SsoStepBody,
                   db: Session = Depends(get_db)) -> dict[str, Any]:
    """Take ONE step on the identity provider's window.

    Separate from `/step` on purpose. The ladder's rungs are the ENGINE's process; this is the
    IDENTITY's, and it is a different domain with a different stack, a different boundary and a
    different owner. Collapsing them would mean the LinkedIn ladder claiming credit for a screen
    LinkedIn does not own.
    """
    _check_initiator(body.initiator)
    session, bb, ledger = _load(session_id, db)
    browser_url = _session_browser_url(session)
    obs = await _observe(browser_url, bb.search_state.query, session_id=session.id)
    popup = find_sso_popup(obs.get("tabs") or [])
    if popup is None:
        raise HTTPException(status_code=409,
                            detail="No identity-provider window is open — nothing to step.")

    engine = engine_for(session, obs.get("search_tab"))
    username = _identity_username(engine)
    if not username:
        raise HTTPException(
            status_code=409,
            detail="No Google login is stored to sign in with. Add one in Accounts first.")

    # The step is only worth taking if someone is here to finish what it starts.
    scan_state = google_recipe.classify(popup.get("url", ""), "", [])
    if not body.attended and scan_state in (google_recipe.EMAIL, google_recipe.CHOOSER):
        raise HTTPException(
            status_code=409,
            detail=("This step lands on a verification factor only you can clear, and Google's "
                    "expire in under a minute — taking it unattended burns the challenge and "
                    "leaves a screen that looks live but is dead. Re-send with attended=true when "
                    "you are at the keyboard."))

    res = await _drive_sso_step(browser_url, popup, username, approved=body.approved)
    bb.log("sso", f"{res.get('state')} -> {res.get('landed') or 'no change'}"[:160])
    _persist(bb, ledger)
    obs2 = await _observe(browser_url, bb.search_state.query, session_id=session.id)
    return _view(session, bb, ledger, obs2, page=_current_page(obs2, bb),
                 awaiting=None if res.get("ok") else "operator_login",
                 last={**res, "action": "sso_step"})


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
    obs = await _observe(browser_url, bb.search_state.query, session_id=session.id)

    if obs["observed"].get("authenticated"):
        raise HTTPException(status_code=409, detail="Already signed in — step instead.")

    before = await _login_survey(browser_url, obs, engine_for(session, obs.get("search_tab")))
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

    obs_after = await _observe(browser_url, bb.search_state.query, session_id=session.id)
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
    # UN-SAY IT. `mark_created` is a claim about ANOTHER system — that a login now exists on the
    # ATS — and a claim made on a wrong report needs a way back. Wrongly-active is the worse of the
    # two errors: next_account_action then offers the sign-in leg forever, the create leg becomes
    # unreachable, and every rejection reads as a bad password.
    reset: bool = False
    # What the operator ACTUALLY signed up with, when they made the account themselves and departed
    # from the suggested pair (a site rule we had not read, a password already in use). Recorded
    # into the vault by the mark_created leg. Omitted, that leg stores the derived pair the handoff
    # card showed them — which is right far more often than not, and wrong silently if we assumed it.
    username: str = ""
    password: str = ""


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


#: The account form table now lives in `account_forms`, because a router is the wrong home for a
#: recipe: nothing outside an HTTP handler could read it, which is why the account rung produced no
#: program the controller could step (see that module's header). This name stays as the local alias
#: the driver already reads.
_ACCOUNT_FORMS = account_forms.ACCOUNT_FORMS


async def _drive_account_form(browser_url: str, tab_id: str, creds: dict, *,
                              ats: str, leg: str, submit: bool,
                              extra: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """Fill (and optionally submit) this ATS's account form for `leg` — create_account or sign_in.

    Credential-safe by construction: fields are addressed by their exact accessible name, so the
    honeypot ("Enter website. This input is for robots only") is never touched; the password value
    flows only into /execute, which logs the target NAME, never the value; and nothing here writes
    the password to an event or a mini-step. Human-paced even in auto mode — speed is not what makes
    an account legitimate, and the captcha rule is unchanged.

    An ATS with no entry in `_CREATE_ACCOUNT_FORM` is refused BY NAME rather than driven on
    Workday's field names and left to fail as "could not fill 'Email Address'" — an unmapped
    platform and a moved field are different problems and only one of them is a stale recipe.

    EVERY return carries `staged`: did anything land in the page? Only this function knows — half
    of its refusals happen before a keystroke and half after — and the panel's reload remedy hangs
    on the answer. It is tracked, never inferred from the reason: a caller reading `no_value` has
    no way to tell the first field from the fourth.
    """
    import ats_accounts

    staged = False   # nothing is in the page until a fill is dispatched

    username = creds.get("username") or ""
    password = creds.get("suggested_password") or ""
    if not username or not password:
        return {"ok": False, "reason": "no_credentials", "staged": staged,
                "detail": "No username or generated password available (is ATS_ACCOUNT_PW_SUFFIX "
                          "configured?). Cannot fill the form."}

    by_ats = _ACCOUNT_FORMS.get((leg or "").strip().lower()) or {}
    form = by_ats.get((ats or "").strip().lower())
    if form is None:
        return {"ok": False, "reason": "no_form_recipe", "staged": staged,
                "detail": f"No {leg} form mapped for {ats!r} (mapped: "
                          f"{', '.join(sorted(by_ats)) or 'none'}). Scan the form and add it to "
                          f"apply_fields + _ACCOUNT_FORMS — do not drive it blind."}

    # Does the password we are about to type satisfy the rules this ATS states? Checked BEFORE a
    # keystroke, and only on the CREATE leg — on sign-in the password is whatever the account was
    # made with, and refusing to type it because a policy has since been read differently would
    # lock us out of our own account. A rejected password costs a submit and leaves a half-made
    # account that reads, from the outside, exactly like a made one.
    if leg == "create_account":
        violations = apply_fields.check_password(ats, password)
        if violations:
            return {"ok": False, "reason": "password_policy", "staged": staged,
                    "detail": f"The derived password does not satisfy {ats}'s stated rules: "
                              f"{'; '.join(violations)}. Nothing was typed. Adjust "
                              f"ATS_ACCOUNT_PW_SUFFIX (it applies to every account) or set this "
                              f"account's password by hand in the Accounts panel."}

    values = {"username": username, "password": password,
              "first_name": ats_accounts.default_first_name(),
              "last_name": ats_accounts.default_last_name()}
    # Non-credential answers the form needs (a country, a state). Passed IN rather than read here,
    # because this function is about credentials and the answer store is the caller's business.
    values.update({k: v for k, v in (extra or {}).items() if v})

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
            return {"ok": False, "reason": "no_value", "staged": staged,
                    "detail": f"No value for {field!r} (source {source!r}) — set "
                              f"ATS_ACCOUNT_{source.upper()} in .env. Nothing was submitted."}
        # Set BEFORE the await, not after: a fill that reports a bad outcome may still have put
        # characters in the box, and the safe error here is claiming input we did not leave.
        staged = True
        r = await _fill(field, value)
        if r.get("outcome") not in ("ok", "committed_unconfirmed"):
            return {"ok": False, "reason": "fill_failed", "staged": staged,
                    "detail": f"Could not fill {field!r} ({r.get('outcome') or r.get('detail')})."}

    # SELECTS — a dropdown the form insists on before it will accept the rest.
    for field, source in form.get("selects", ()):
        value = values.get(source) or ""
        if not value:
            return {"ok": False, "reason": "no_value", "staged": staged,
                    "detail": f"No value for the {field!r} dropdown (source {source!r}). Store it "
                              f"as an application answer first; nothing was submitted."}
        addr = apply_fields.addressing_for(ats, field)
        staged = True
        res = await _capture_post("/execute", {
            "browser_url": browser_url, "tab_id": tab_id, "action_id": "select",
            "target_bbox": {}, "target_role": addr["role"], "target_name": addr["name"],
            "value": value, "driver": "humanized"})
        if res.get("outcome") not in ("ok", "committed_unconfirmed"):
            return {"ok": False, "reason": "select_failed", "staged": staged,
                    "detail": f"Could not set {field!r} to {value!r} "
                              f"({res.get('outcome') or res.get('detail')})."}
        await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))

    # REFUSALS — opt-ins that arrive ALREADY ON, unticked before the form is submitted.
    #
    # This exists because naming a field so we never touch it protects nothing when the site has
    # already ticked it. SAP's two marketing boxes were recorded as "default-off" and simply left
    # out of every list; on the live form (2026-07-28) both render CHECKED, so leaving them alone
    # meant consenting by default — against the operator's stored marketing_contact_consent=No,
    # and silently: the account is made, the application goes through, and the only symptom is
    # marketing email arriving weeks later with nothing to trace it to.
    #
    # Driven through /check_group with an EMPTY value set, which unticks by click (so the page's
    # own handlers fire) and then RE-READS the DOM to confirm — a refusal we cannot verify is not
    # a refusal. A failure here stops the submit: consenting to marketing on someone's behalf is
    # not a thing to do on a best-effort basis.
    for field in form.get("refusals", ()):
        addr = apply_fields.addressing_for(ats, field)
        staged = True
        res = await _capture_post("/check_group", {
            "browser_url": browser_url, "tab_id": tab_id,
            "selector": addr["selector"], "values": []})
        if not res.get("ok"):
            return {"ok": False, "reason": "refusal_failed", "staged": staged,
                    "detail": f"Could not switch OFF {field!r} "
                              f"({res.get('code') or res.get('detail')}). It arrives checked, so "
                              f"submitting now would opt you in — nothing was submitted."}
        await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))

    # CONFIRMS — required consents, always a deliberate act against a field somebody wrote down,
    # never a control swept up by a fill-everything pass.
    #
    # A consent is a STAGED WIDGET, and each of its three parts is here because the other shapes
    # failed live (SuccessFactors, 2026-07-28):
    #   opener — raises the dialog. Addressed however apply_fields says, which for SAP means BY
    #            SELECTOR: the accessible name AX offers is the whole row fused into one node, and
    #            clicking that navigates back to the sign-in gate, taking the half-filled form with
    #            it, while /execute reports ok.
    #   commit — the Accept INSIDE the dialog. A separate widget; AX does not connect it to its
    #            opener, so nothing infers it.
    #   proof  — text that appears on the page OUTSIDE the dialog once consent is recorded. The
    #            dialog closing proves nothing: Decline and the X close it too.
    for opener, commit, proof in form.get("confirms", ()):
        # ALREADY ACCEPTED? Then leave it alone. A re-run has to CONVERGE, not thrash: the account
        # rung is re-entered constantly — the operator presses the button again, a drive resumes
        # after a captcha, a session picks the step back up hours later — and on SAP the opener is
        # the single most dangerous control on the page. Clicking it a second time asks a dialog to
        # re-open over a consent that is already recorded, on the row whose sibling addressing
        # navigated away and destroyed a filled form. The cheap read comes first.
        if proof:
            before = await _capture_post("/ax_scan", {"browser_url": browser_url,
                                                      "tab_id": tab_id}, timeout=20.0)
            seen = (str(before.get("page_text") or "")
                    + " ".join(c.get("name", "") for c in (before.get("candidates") or [])))
            if proof.lower() in seen.lower():
                continue

        staged = True
        open_addr = apply_fields.addressing_for(ats, opener)
        open_payload = {"browser_url": browser_url, "tab_id": tab_id, "action_id": "click",
                        "target_bbox": {}, "driver": "humanized"}
        if open_addr["addressed_by"] == apply_fields.ADDRESSED_BY_SELECTOR:
            open_payload["selector"] = open_addr["selector"]
        else:
            open_payload["target_role"], open_payload["target_name"] = (open_addr["role"],
                                                                       open_addr["name"])
        res = await _capture_post("/execute", open_payload)
        if res.get("outcome") not in ("ok", "committed_unconfirmed"):
            return {"ok": False, "reason": "confirm_failed", "staged": staged,
                    "detail": f"Could not open the {opener!r} consent "
                              f"({res.get('outcome') or res.get('detail')})."}
        await asyncio.sleep(xs.pause_for(style, xs.NAVIGATION))

        commit_addr = apply_fields.addressing_for(ats, commit)
        # DID THE DIALOG ACTUALLY OPEN? The opener's `ok` does not say so — it never did. It says a
        # click was dispatched at a node, and on SAP the same click ALSO does nothing visible when
        # the form is not yet valid: it just paints the required-field errors. So the run of
        # 2026-07-28 reported "Opened the 'terms' consent but could not click 'Accept'", which named
        # the wrong step as the failure. The dialog had never opened, and the reason was elsewhere
        # on the form entirely.
        #
        # Polled rather than slept-on, because a dialog is a rendering race and one fixed pause is
        # either too short on a slow paint or wasted on a fast one.
        appeared = False
        for _ in range(6):
            scan = await _capture_post("/ax_scan", {"browser_url": browser_url, "tab_id": tab_id},
                                       timeout=20.0)
            names = [str(c.get("name") or "") for c in (scan.get("candidates") or [])]
            if any((commit_addr["name"] or "").lower() == n.strip().lower() for n in names):
                appeared = True
                break
            await asyncio.sleep(0.6)
        if not appeared:
            # Say WHY, with the page's own answer rather than a guess. An unanswered required field
            # is the known cause: SAP will not raise the consent dialog over an invalid form.
            blocking = await _remaining_required(browser_url, tab_id, ats, leg)
            unmet = [f["label"] for f in blocking.get("system", [])] + blocking.get("operator", [])
            return {"ok": False, "reason": "consent_did_not_open", "staged": staged,
                    "detail": (f"Clicked the {opener!r} consent and no dialog appeared — so there "
                               f"was no {commit_addr['name']!r} to press. "
                               + (f"The form still has unanswered required fields "
                                  f"({', '.join(unmet)}), and this site will not raise the consent "
                                  f"over an invalid form — fix those first."
                                  if unmet else
                                  "The form reads as complete, so this is the consent widget "
                                  "itself: re-scan it before driving again.")
                               + " Nothing was submitted.")}
        res = await _capture_post("/execute", {
            "browser_url": browser_url, "tab_id": tab_id, "action_id": "click",
            "target_bbox": {}, "target_role": commit_addr["role"],
            "target_name": commit_addr["name"], "driver": "humanized"})
        if res.get("outcome") not in ("ok", "committed_unconfirmed"):
            return {"ok": False, "reason": "confirm_failed", "staged": staged,
                    "detail": f"Opened the {opener!r} consent but could not click "
                              f"{commit_addr['name']!r} in it "
                              f"({res.get('outcome') or res.get('detail')})."}
        await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))

        # CONFIRM FROM OUTSIDE. Without this the drive submits an unconsented form and reads the
        # site's rejection as some other failure.
        after = await _capture_post("/ax_scan", {"browser_url": browser_url, "tab_id": tab_id},
                                    timeout=20.0)
        page_text = (str(after.get("page_text") or "")
                     + " ".join(c.get("name", "") for c in (after.get("candidates") or [])))
        if proof and proof.lower() not in page_text.lower():
            return {"ok": False, "reason": "confirm_unverified", "staged": staged,
                    "detail": f"Clicked Accept on the {opener!r} consent, but the page never said "
                              f"{proof!r}. The dialog closing is not consent — Decline closes it "
                              f"too. Nothing was submitted."}
        await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))

    submit_addr = apply_fields.addressing_for(ats, form["submit"])
    button = submit_addr["name"] or submit_addr["selector"]
    if not submit:
        return {"ok": True, "submitted": False, "button": button, "staged": staged,
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
        return {"ok": False, "reason": "submit_failed", "staged": staged,
                "detail": f"Filled the form but could not click {button!r} "
                          f"({click.get('outcome') or click.get('detail')})."}
    # DID THE FORM GO THROUGH? A click that dispatched is not a form that was accepted. On the
    # sign-in leg this is not a nicety: a wrong password re-renders the SAME login form with an
    # error, which looks identical to success from here, and on 2026-07-28 the ledger recorded
    # "sign_in leg: signed in to Teradyne successfactors" for an account that did not exist. A rung
    # that reports a login it never got is worse than one that fails — the next rung reads every
    # gate as some other problem.
    #
    # The proof is the submit control's ABSENCE. It is the one signal that needs no new site
    # knowledge: every one of these forms replaces itself on success and keeps itself on failure.
    after = await _capture_post("/ax_scan", {"browser_url": browser_url, "tab_id": tab_id},
                                timeout=20.0)
    still_there = any((submit_addr["name"] or "").strip().lower() == str(c.get("name") or "").strip().lower()
                      for c in (after.get("candidates") or []))
    if still_there and submit_addr["name"]:
        text = str(after.get("page_text") or "")
        said = next((ln.strip() for ln in text.splitlines()
                     if ln.strip() and any(w in ln.lower() for w in
                                           ("invalid", "incorrect", "not match", "error",
                                            "required", "try again", "does not exist"))), "")
        return {"ok": False, "reason": "submit_not_accepted", "staged": staged,
                "detail": (f"Clicked {button!r} and the form is still on screen, so it was not "
                           f"accepted. " + (f"The page says: {said!r}. " if said else "")
                           + "Nothing here counts as a completed "
                           + ("sign-in." if leg == "sign_in" else "account.")),
                "button": button}
    # INTERSTITIALS — a gate that arrives ON ITS OWN once the form lands, with no opener and
    # nothing on the page that predicted it. SAP raises its Data Privacy Consent dialog again right
    # after a successful sign-in, and leaving it costs the whole session rather than one rung:
    # observed 2026-07-29, dialog dismissed unaccepted and the tab was back at the sign-in wall
    # with logged_in false. So it is cleared here, in the same breath as the submit, rather than
    # left for a later rung to be surprised by.
    cleared = []
    for control, state_id in form.get("interstitials", ()):
        addr = apply_fields.addressing_for(ats, control)
        seen = False
        for _ in range(5):
            scan = await _capture_post("/ax_scan", {"browser_url": browser_url, "tab_id": tab_id},
                                       timeout=20.0)
            if any((addr["name"] or "").strip().lower() == str(c.get("name") or "").strip().lower()
                   for c in (scan.get("candidates") or [])):
                seen = True
                break
            await asyncio.sleep(0.6)
        if not seen:
            continue          # it did not appear this time; that is normal, not a failure
        res = await _capture_post("/execute", {
            "browser_url": browser_url, "tab_id": tab_id, "action_id": "click",
            "target_bbox": {}, "target_role": addr["role"], "target_name": addr["name"],
            "driver": "humanized"})
        if res.get("outcome") not in ("ok", "committed_unconfirmed"):
            return {"ok": False, "reason": "interstitial_failed", "staged": staged,
                    "detail": f"{state_id}: the gate appeared and {addr['name']!r} could not be "
                              f"clicked ({res.get('outcome') or res.get('detail')}). It drops the "
                              f"session if it is left, so nothing here counts as signed in."}
        cleared.append(state_id)
        await asyncio.sleep(xs.pause_for(style, xs.NAVIGATION))

    return {"ok": True, "submitted": True, "button": button, "staged": staged,
            "cleared": cleared,
            "detail": f"Submitted the {leg.replace('_', ' ')} form ({button!r}) and it was taken."
                      + (f" Cleared on the way through: {', '.join(cleared)}." if cleared else "")}


async def _remaining_required(browser_url: str, tab_id: str, ats: str, leg: str) -> dict[str, Any]:
    """What the LIVE form still needs, split by WHO SUPPLIES IT.

    The plan says what a drive from scratch would do. This says what is actually left — and by the
    time an operator is reading the card those are rarely the same, because the form is usually
    part-filled already, by a previous run or by them.

    But "what is left" is the wrong question to put in front of a person on its own, and putting it
    there was a mistake: the card listed "Choose Password" and "Retype Password" as though the
    operator had to think of a password, when the credential scheme has ALREADY decided it —
    derived from the company initials plus the shared suffix, checked against the site's stated
    rules, and stored in the vault the moment it is used (`ats_accounts`). Those fields are empty
    on the page and answered in the system. Presenting them as work is asking someone to redo what
    the account system exists to do.

    So each remaining field is attributed to its source, by matching the live control back to the
    step that would fill it:
      * `system`   — the program names it and knows where its value comes from (`value_ref`:
                     `account.password` from the vault, `first_name` from the answer store).
      * `operator` — nothing in the recipe supplies it. THIS is the only list that is a request.

    Two kinds of row are dropped as scanner artifacts rather than work: the fields this leg
    deliberately REFUSES (an unticked marketing box reads as an unanswered required field, and
    listing it would ask the operator to undo the refusal the `refusals` loop exists to make), and
    run-together captions, where the scanner labels a control with every label above it.

    Best-effort: a probe that fails reports nothing rather than raising. This decorates a card.
    """
    empty: dict[str, Any] = {"operator": [], "system": [], "checked": False}
    if not tab_id:
        return empty
    form = account_forms.form_for(ats, leg) or {}
    refused, by_label = set(), {}
    for field in form.get("refusals", ()):
        try:
            refused.add(apply_fields.resolve(ats, field).get("selector"))
        except apply_fields.FieldNotFound:
            continue
    # The site's own label for each field the program would fill -> where its value comes from.
    for stp in account_forms.program_steps(ats, leg):
        field = stp["params"].get("field")
        ref = stp["params"].get("value_ref")
        if not field or not ref:
            continue
        try:
            entry = apply_fields.resolve(ats, field)
        except apply_fields.FieldNotFound:
            continue
        for key in (entry.get("name"), entry.get("selector")):
            if key:
                by_label[str(key).strip().lower()] = {"field": field, "source": ref}

    try:
        scan = await _capture_post("/scan_required",
                                   {"browser_url": browser_url, "tab_id": tab_id}, timeout=25.0)
    except Exception:  # noqa: BLE001 — a card decoration must not fail the rung
        return empty

    out: dict[str, Any] = {"operator": [], "system": [], "checked": True}
    for row in (scan.get("unanswered") or []):
        selector = row.get("selector")
        if selector in refused:
            continue
        label = str(row.get("field") or "").strip()
        if not label or len(label) > 60:      # a run-together caption, not a field
            continue
        known = by_label.get(label.lower()) or by_label.get(str(selector or "").strip().lower())
        if known:
            out["system"].append({"label": label, **known})
        else:
            out["operator"].append(label)
    return out


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

    if body.reset:
        res = ats_accounts.reset_account(company, step.platform)
        if not res.get("ok"):
            raise HTTPException(status_code=409, detail=res.get("detail", "could not reset"))
        # RECORDED, not silently undone. The ledger already holds the claim that the account was
        # created; a correction that leaves no trace turns the ledger into a thing that is only
        # true when nobody was wrong. Both entries stay — §10: keep both sides of a correction.
        step.record(_ACCOUNT_RUNG, aps.FAILED,
                    f"reset: the {company} {step.platform} account was marked created and was not "
                    f"— back to pending, stored credential cleared",
                    initiator=body.initiator, staged=False)
        _save_queue(bb, queue)
        bb.world.pop("account_handoff", None)
        bb.log("account_reset",
               f"{company} {step.platform}: marked-created retracted; the create leg is due again")
        _persist(bb, ledger)
        obs = await _observe(_session_browser_url(session), bb.search_state.query,
                             session_id=session.id)
        return _view(session, bb, ledger, obs, page=_current_page(obs, bb), awaiting="apply",
                     last={"ok": True, "action": "apply_account", "queue": queue.summary(),
                           "detail": f"{company}: back to 'pending' and the credential cleared. "
                                     f"The create leg is due again."})

    if body.mark_created:
        res = ats_accounts.mark_created(company, step.platform)
        if not res.get("ok"):
            raise HTTPException(status_code=409, detail=res.get("detail", "could not mark created"))
        # Store the credential on THIS leg too. An account the operator typed themselves is no
        # more recoverable from the derivation than one we typed — it is the same two drifting
        # inputs — and this is the leg that runs whenever the create is human-required, which is
        # every captcha, every verification wall, and every account the agent may not make itself.
        # Leaving it unstored here would mean the accounts most likely to need a password later are
        # exactly the ones that never got one written down.
        #
        # `body.credentials` is what the operator ACTUALLY used, for the case where they departed
        # from the suggestion (a site-specific rule, a password already in use). Absent, we record
        # the derived pair the handoff card showed them.
        used_pw = body.password or (ats_accounts.suggested_credentials(company, step.platform)
                                    .get("suggested_password") or "")
        used_user = body.username or ats_accounts.default_username()
        stored = ats_accounts.record_credentials(company, step.platform, used_user, used_pw)
        saved = bool(stored.get("ok"))
        step.record(_ACCOUNT_RUNG, aps.OK,
                    f"handoff leg: {company} {step.platform} account created by the operator"
                    + (", credential stored" if saved else ", CREDENTIAL NOT STORED"),
                    initiator=body.initiator,
                    # The OPERATOR typed this form, in their own browser, and it is already
                    # submitted. Nothing of ours is staged in the page.
                    staged=False)
        _save_queue(bb, queue)
        bb.world.pop("account_handoff", None)   # the handoff is resolved
        _persist(bb, ledger)
        obs = await _observe(_session_browser_url(session), bb.search_state.query, session_id=session.id)
        return _view(session, bb, ledger, obs, page=_current_page(obs, bb), awaiting="apply",
                     last={"ok": True, "action": "apply_account", "queue": queue.summary(),
                           "credentials_stored": saved,
                           "detail": f"{company} account marked created. The application can "
                                     f"continue — orient, then work the next rung."
                                     + ("" if saved else
                                        f" CREDENTIAL NOT STORED ({stored.get('detail')}) — save "
                                        f"it in the Accounts panel before this session ends.")})

    # Observe FIRST, because the account record wants a URL and the only trustworthy source of it
    # is the live tab. `orient.url` — what this used to record — is where the ORIENT rung last
    # looked, which is the employer's job page, and the blackboard's own `apply_tab.url` goes stale
    # the moment the tab navigates without a rung writing it back (verified live on Teradyne
    # 2026-07-28: same tab_id, url still saying jobs.teradyne.com while the tab was on
    # career41.sapsf.com). Recording either one gives the account a login_url pointing at a job
    # posting — and this is precisely the ATS family where that is wrong by construction, since SAP
    # serves the application from sapsf.com while the posting lives on the employer's own domain.
    # A wrong login_url is quiet: nothing fails now, and the sign-in leg weeks later opens a job ad.
    obs = await _observe(browser_url, bb.search_state.query, session_id=session.id)
    live_url = _apply_tab_url(bb, obs) or (bb.world or {}).get("orient", {}).get("url", "")

    # Register the account record; next_account_action decides the leg (create vs sign-in) and
    # derives the credentials.
    ensure = ats_accounts.ensure_account(company, step.platform, login_url=live_url)
    if not ensure.get("ok"):
        raise HTTPException(status_code=409, detail=ensure.get("detail", "could not open account"))
    action = ats_accounts.next_account_action(company, step.platform)
    creds = action.get("credentials") or {}

    # AUTOMATED PATH — the default. The system fills (and, in "auto", submits) the create-account
    # form itself. A CAPTCHA or an email/2FA verification prompt still escalates: those are real
    # external gates, not the manual-handoff boundary, and they hold regardless of mode.
    if body.mode in ("auto", "fill") and action.get("leg") in ("create_account", "sign_in"):
        block = obs.get("block")
        if block and block.get("strength") == "active":
            step.record(_ACCOUNT_RUNG, aps.BLOCKED,
                        f"create leg: active {block.get('provider')} on signup",
                        initiator=body.initiator,
                        # We bail before `_drive_account_form`; not a keystroke was dispatched.
                        staged=False)
            _save_queue(bb, queue); _persist(bb, ledger)
            return _view(session, bb, ledger, obs, page=_current_page(obs, bb),
                         awaiting="operator_challenge",
                         last={"ok": False, "action": "apply_account", "queue": queue.summary(),
                               "detail": "A challenge is up on the signup form — clear it yourself. "
                                         "We never auto-solve, on any form."})
        tab_id = _apply_tab(bb, obs).get("tab_id", "")
        # Answers the form needs beyond the credential — resolved here, where the DB is.
        from sqlalchemy import select as _select

        from models import ApplicationAnswer
        wanted = {src for _f, src in
                  (_ACCOUNT_FORMS.get(action.get("leg") or "", {})
                   .get(step.platform, {}).get("selects", ()))}
        extra = {}
        if wanted:
            rows = db.scalars(_select(ApplicationAnswer).where(
                ApplicationAnswer.answer_key.in_(tuple(wanted)))).all()
            extra = {r.answer_key: str(r.value or "") for r in rows
                     if getattr(r, "answer_key", None) in wanted}
        drive = await _drive_account_form(browser_url, tab_id, creds, ats=step.platform,
                                          leg=action.get("leg") or "create_account",
                                          submit=(body.mode == "auto"), extra=extra)
        if not drive.get("ok"):
            step.record(_ACCOUNT_RUNG, aps.FAILED, f"create leg: {drive.get('detail', '')}"[:200],
                        initiator=body.initiator,
                        # HALF of these refusals happen before a keystroke (no credential, no form
                        # recipe, a password the site's own rules reject) and half after (a fill
                        # that errored on field four). The driver tracked which; a missing answer
                        # means an older driver, so assume it typed — over-protecting the page is
                        # the recoverable mistake.
                        staged=bool(drive.get("staged", True)))
            _save_queue(bb, queue); _persist(bb, ledger)
            obs2 = await _observe(browser_url, bb.search_state.query, session_id=session.id)
            return _view(session, bb, ledger, obs2, page=_current_page(obs2, bb), awaiting="apply",
                         last={"ok": False, "action": "apply_account", "queue": queue.summary(),
                               "detail": drive.get("detail")})

        if not drive.get("submitted"):
            step.record(_ACCOUNT_RUNG, aps.HUMAN_REQUIRED,
                        f"create leg: filled the form, awaiting the operator's "
                        f"{drive.get('button') or 'submit'!r} click",
                        initiator=body.initiator,
                        # THE case the flag protects: a filled, unsubmitted credential form. A
                        # reload here throws away the fill and the operator's review of it.
                        staged=True)
            _save_queue(bb, queue); _persist(bb, ledger)
            obs2 = await _observe(browser_url, bb.search_state.query, session_id=session.id)
            return _view(session, bb, ledger, obs2, page=_current_page(obs2, bb),
                         awaiting="operator_account",
                         last={"ok": True, "action": "apply_account", "queue": queue.summary(),
                               "detail": f"Filled the {action.get('leg')} form with your stored "
                                         f"credentials. Review it in the window, then confirm to "
                                         f"click {drive.get('button') or action.get('button')!r}."})

        # Submitted. Did it land — or is there an email/2FA verification wall (a real gate)?
        after = await _capture_post("/ax_scan", {"browser_url": browser_url, "tab_id": tab_id},
                                    timeout=20.0)
        text = (str(after.get("page_text") or "")
                + " ".join(c.get("name", "") for c in (after.get("candidates") or []))).lower()
        if any(m in text for m in _ACCOUNT_VERIFY_MARKERS):
            step.record(_ACCOUNT_RUNG, aps.HUMAN_REQUIRED,
                        "verify leg: signup needs an email/2FA verification code — a real gate, "
                        "escalated",
                        initiator=body.initiator,
                        # Not typed input — the form is already submitted — but a signup waiting on
                        # a one-time code is a transaction in flight, and a reload is exactly how
                        # you lose the half of it the site is holding. Protected deliberately.
                        staged=True)
            _save_queue(bb, queue); _persist(bb, ledger)
            obs2 = await _observe(browser_url, bb.search_state.query, session_id=session.id)
            return _view(session, bb, ledger, obs2, page=_current_page(obs2, bb),
                         awaiting="operator_verify",
                         last={"ok": False, "action": "apply_account", "queue": queue.summary(),
                               "detail": "Account submitted, but the signup wants an email/2FA "
                                         "verification code. That is a real gate — grab the code "
                                         "(a Gmail errand we can automate next), then continue."})

        signing_in = action.get("leg") == "sign_in"
        # STORE THE CREDENTIAL, at the one moment it is proven: the site just took it. Derivation
        # is how we chose this password, not a dependable way to recover it — the shared suffix and
        # the company string both drift, and both keep returning a plausible wrong answer when they
        # do. Stored before `mark_created` so an account can never be marked usable while its
        # credential exists nowhere but in this request. If the vault write fails the account still
        # exists on the site, so that fact is still recorded — the failure rides along in the detail
        # rather than being swallowed or being allowed to erase what happened.
        stored = ats_accounts.record_credentials(company, step.platform,
                                                 creds.get("username") or "",
                                                 creds.get("suggested_password") or "")
        if not signing_in:
            # Only a CREATE makes the account exist. Signing in must not re-stamp that — the
            # lifecycle flag is what tells the next session which leg is due.
            ats_accounts.mark_created(company, step.platform)
        saved = bool(stored.get("ok"))
        # A credential we did not manage to store is worth as much noise as a step that failed —
        # the account is real either way, and the one that is unrecoverable is the quiet one.
        vault_note = ("" if saved else
                      f" CREDENTIAL NOT STORED ({stored.get('detail')}) — save it in the Accounts "
                      f"panel before this session ends.")
        step.record(_ACCOUNT_RUNG, aps.OK,
                    ((f"sign_in leg: signed in to {company} {step.platform}" if signing_in else
                      f"create leg: created the {company} {step.platform} account automatically")
                     + (", credential stored" if saved else ", CREDENTIAL NOT STORED")),
                    initiator="auto",
                    # Through the wall: the site accepted the form and we are signed in. What we
                    # typed lives on the site now, not in the page, so a reload costs nothing —
                    # and this is the state an application sits in longest before the form rung.
                    staged=False)
        bb.world.pop("account_handoff", None)
        _save_queue(bb, queue)
        bb.log("account_signin" if signing_in else "account_create",
               f"{company} {step.platform}: "
               + ("signed in automatically" if signing_in else "account created automatically")
               + (" (credential stored in the vault)" if saved else " (CREDENTIAL NOT STORED)"))
        _persist(bb, ledger)
        obs2 = await _observe(browser_url, bb.search_state.query, session_id=session.id)
        return _view(session, bb, ledger, obs2, page=_current_page(obs2, bb), awaiting="apply",
                     last={"ok": True, "action": "apply_account", "queue": queue.summary(),
                           "credentials_stored": saved,
                           "detail": (f"Signed in to {company} automatically. " if signing_in else
                                      f"Created the {company} account automatically. ")
                                     + "The application can continue — orient, then the form."
                                     + vault_note})

    step.record(_ACCOUNT_RUNG, aps.HUMAN_REQUIRED,
                f"{action.get('leg')} leg: {company} {step.platform}, operator creates it "
                f"(button {action.get('button')!r})",
                initiator=body.initiator,
                # THE HANDOFF. This leg types NOTHING — it surfaces the credential on a card and
                # waits. The form on screen is untouched, and saying otherwise is what withheld a
                # reload from an 18-hour-old, verifiably empty SAP signup (session 21, 2026-07-28).
                staged=False)
    _save_queue(bb, queue)
    tab_id = _apply_tab(bb, obs).get("tab_id", "")
    handoff = {
        "job_id": step.job_id, "leg": action.get("leg"), "button": action.get("button"),
        "company": company, "ats": step.platform,
        "username": creds.get("username"),
        "suggested_password": creds.get("suggested_password"),
        "suffix_configured": creds.get("suffix_configured"),
        # WHAT THE AUTOMATION WOULD DO, in the order it would do it — rendered from the same table
        # the driver executes, so the card cannot describe a drive that does not happen. The
        # operator is being asked to choose between doing this themselves and letting the system
        # do it; that choice is not informed while the second option is an unlabelled button.
        "plan": account_forms.program_steps(step.platform, action.get("leg") or "create_account"),
        "policy_checked": apply_fields.has_policy(step.platform),
        # WHAT THE PAGE STILL NEEDS, read from the live form. The plan says what a drive WOULD do
        # from scratch; this says what is actually left, and the two are rarely the same by the
        # time an operator is looking at the card — a form is usually part-filled by then, by a
        # previous run or by them. A card that shows only the plan reads as "twelve things to do"
        # in front of a page that needs two.
        "remaining": await _remaining_required(browser_url, tab_id, step.platform,
                                               action.get("leg") or "create_account"),
        "boundary": f"Do it yourself, or have the system do it. Either way a captcha or an "
                    f"email/2FA code stops for you, and the marketing opt-ins are switched OFF "
                    f"rather than left as the site set them.",
    }
    # Persist the handoff in world so it survives a poll or a page reload — the same durability the
    # proposal has. `last_step` alone is transient, and an operator who refreshed lost the panel.
    bb.world["account_handoff"] = handoff
    # Never log the password — the record carries the leg, not the secret.
    bb.log("account_handoff", f"{company} {step.platform}: {action.get('leg')} handoff to operator")
    _persist(bb, ledger)

    obs = await _observe(_session_browser_url(session), bb.search_state.query, session_id=session.id)
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

    obs = await _observe(browser_url, bb.search_state.query, session_id=session.id)
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
    obs2 = await _observe(browser_url, bb.search_state.query, session_id=session.id)
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


async def _scan_ax(browser_url: str, tab_id: str) -> list[dict[str, Any]]:
    """The raw AX candidates for the tab. Split out from `_scan_form_fields` because the section
    reader needs `expanded`, which the {role, name} projection below throws away."""
    scan = await _capture_post("/ax_scan", {"browser_url": browser_url, "tab_id": tab_id},
                               timeout=25.0)
    return scan.get("candidates") or []


def _form_fields_from(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"role": c.get("role"), "name": c.get("caption") or c.get("name")}
            for c in candidates if (c.get("caption") or c.get("name"))]


async def _scan_form_fields(browser_url: str, tab_id: str) -> list[dict[str, Any]]:
    return _form_fields_from(await _scan_ax(browser_url, tab_id))


def _fill_plan_for(bb: Any, fields: list[dict[str, Any]], db: Session) -> list[dict[str, Any]]:
    import form_fill
    from models import ApplicationAnswer
    from sqlalchemy import select as _select
    rows = db.scalars(_select(ApplicationAnswer).where(ApplicationAnswer.status == "active")).all()
    answers = {r.answer_key: r.value for r in rows}
    return form_fill.plan(fields, answers=answers, identity=_identity_defaults())


class ApplySectionsBody(BaseModel):
    initiator: str = "operator"
    ats: str = "successfactors"
    expand: Optional[str] = None   # None = just read; "all" = the Expand-all control; else a field key


@router.post("/api/session_control/{session_id}/apply_sections")
async def apply_sections(session_id: int, body: ApplySectionsBody,
                         db: Session = Depends(get_db)) -> dict[str, Any]:
    """Read — and optionally open — an accordion form's section bars.

    The rung that has to exist before `apply_fill` means anything on SAP: a closed section's
    fields are not in the AX tree at all, so a fill plan over a shut form is an accurate summary
    of nothing. Reading is free and always safe; `expand` clicks, and clicking a disclosure bar
    on our own profile is not a submit, so it needs no per-prospect approval.

    VERIFICATION IS A RE-READ, not the click's own `ok`. /execute returns ok when CDP dispatched,
    which on a detached or 0-size node is exactly what a no-op looks like — so this scans again
    afterwards and reports the bars' real state. If a bar did not open, the response says so.
    """
    session, bb, ledger = _load(session_id, db)
    browser_url = _session_browser_url(session)
    import form_fill
    af = apply_fields

    if not af.has_section_bars(body.ats):
        raise HTTPException(status_code=400,
                            detail=f"No section bars declared for {body.ats!r}. Absent means the "
                                   f"form is flat or nobody has checked — not that it is flat.")

    obs = await _observe(browser_url, bb.search_state.query, session_id=session.id)
    tab_id = _apply_tab(bb, obs).get("tab_id", "")
    before = form_fill.section_status(body.ats, await _scan_ax(browser_url, tab_id))

    if not body.expand:
        return _view(session, bb, ledger, obs, page=_current_page(obs, bb), awaiting="apply",
                     last={"ok": True, "action": "apply_sections", "sections": before,
                           "detail": _sections_detail(before)})

    keys = [before["expand_all"]] if body.expand == "all" else [body.expand]
    style = xs.pick_style()
    clicked, refused = [], []
    for key in keys:
        try:
            addr = af.addressing_for(body.ats, key)
        except af.FieldNotFound as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        res = await _capture_post("/execute", {
            "browser_url": browser_url, "tab_id": tab_id, "action_id": "click",
            "target_bbox": {}, "target_role": addr.get("role"), "target_name": addr.get("name"),
            "driver": "humanized"})
        (clicked if res.get("outcome") in ("ok", "committed_unconfirmed") else refused).append(key)
        await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))

    after = form_fill.section_status(body.ats, await _scan_ax(browser_url, tab_id))
    opened = sorted(set(before["closed"]) - set(after["closed"]))
    # The honest failure: the click reported ok and the bar is still shut. Naming it here is the
    # difference between "we opened it" and "we asked".
    stuck = [k for k in clicked if k in after["closed"]]
    ok = bool(opened) and not stuck
    detail = (f"Opened {len(opened)} section(s). " if opened else "Nothing opened. ")
    if stuck:
        detail += (f"{len(stuck)} bar(s) took the click and stayed shut ({', '.join(stuck)}) — "
                   f"a dispatched click is not an opened section. ")
    if refused:
        detail += f"{len(refused)} could not be resolved on the page ({', '.join(refused)}). "
    detail += _sections_detail(after)
    return _view(session, bb, ledger, obs, page=_current_page(obs, bb), awaiting="apply",
                 last={"ok": ok, "action": "apply_sections", "sections": after,
                       "opened": opened, "pace": xs.describe(style), "detail": detail})


def _sections_detail(status: dict[str, Any]) -> str:
    n_open, n_closed = len(status["open"]), len(status["closed"])
    bits = f"{n_open} open, {n_closed} closed"
    if status["unknown"]:
        # Never folded into "closed": a bar we could not read is not one we know is shut.
        bits += f", {len(status['unknown'])} not readable on this page"
    return bits + "."


class ApplyFillBody(BaseModel):
    initiator: str = "operator"
    execute: bool = False          # False = plan only (see the bunch); True = fill the fillable ones
    ats: str = "successfactors"    # whose accordion declaration to check the form against


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

    obs = await _observe(browser_url, bb.search_state.query, session_id=session.id)
    block = obs.get("block")
    if block and block.get("strength") == "active":
        return _save_queue_and_view(session, bb, ledger, queue, obs, ok=False,
                                    detail="A challenge is up — clear it yourself before filling.")
    tab_id = _apply_tab(bb, obs).get("tab_id", "")
    candidates = await _scan_ax(browser_url, tab_id)
    fields = _form_fields_from(candidates)
    rows = _fill_plan_for(bb, fields, db)
    summary = form_fill.summarise(rows)
    # A plan over a shut accordion is an accurate summary of a page nobody opened. Carry the
    # caveat with the plan so "0 fields" and "0 fields, nine sections closed" cannot read alike.
    sections = form_fill.section_status(body.ats, candidates)
    caveat = form_fill.sections_caveat(sections, summary["total"])

    if not body.execute:
        return _view(session, bb, ledger, obs, page=_current_page(obs, bb), awaiting="apply",
                     last={"ok": True, "action": "apply_fill", "queue": queue.summary(),
                           "fill_plan": rows, "fill_summary": summary, "sections": sections,
                           "detail": f"Planned {summary['fillable']} of {summary['total']} fields. "
                                     + (f"Need your data for: {', '.join(summary['missing'])}. "
                                        if summary["missing"] else "Every field has a value. ")
                                     + caveat})

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
    obs2 = await _observe(browser_url, bb.search_state.query, session_id=session.id)
    return _view(session, bb, ledger, obs2, page=_current_page(obs2, bb), awaiting="apply",
                 last={"ok": not failed, "action": "apply_fill", "queue": queue.summary(),
                       "fill_plan": rows, "fill_summary": summary, "sections": sections,
                       "pace": xs.describe(style),
                       "detail": f"Filled {len(filled)} field(s) at {style.name} pace."
                                 + (f" {len(failed)} would not take: {', '.join(failed)}."
                                    if failed else "")
                                 + (f" Still need you for: {', '.join(summary['missing'])}."
                                    if summary["missing"] else "")
                                 + (f" {caveat}" if caveat else "")})


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

    obs = await _observe(browser_url, bb.search_state.query, session_id=session.id)
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

    # THE LIVE TAB DECIDES WHICH PLATFORM WE ARE ON. `step.platform` is a memory of where classify
    # last looked, and an apply can move between platforms after that: BILH's careers page is a
    # branded wrapper, so classify saw `company_site` and the "Apply now" inside it handed off to
    # bilh.wd1.myworkdayjobs.com. With the record shadowing the live URL, orient asked the GENERIC
    # describer about a Workday page and called a state the recipe knows perfectly well "new
    # territory" (live 2026-07-27). A recorded platform only holds until the page says otherwise.
    # Gated on the platform being NAMED, not on `known`. `known` answers a different question —
    # "have we driven it end to end" — and classify_landing says so itself: naming a platform is not
    # knowing it. Gating on `known` meant a newly-recognised ATS could never correct a stale
    # `company_site`, which is exactly what Teradyne hit the hour after SuccessFactors detection
    # shipped: the registry said successfactors, orient kept saying company_site, and the recipe it
    # then consulted was the generic one (2026-07-27).
    live = aps.classify_landing(url)
    platform = step.platform or live.platform
    named = live.platform not in ("", "unknown", "company_site")
    if named and live.platform != step.platform:
        step.record("classify", aps.OK,
                    f"re-classified {step.platform or 'unclassified'} -> {live.platform}: the "
                    f"apply moved to {url[:70]}", initiator=body.initiator)
        step.platform = platform = live.platform
        _save_queue(bb, queue)
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

    obs2 = await _observe(browser_url, bb.search_state.query, session_id=session.id)
    view = _view(session, bb, ledger, obs2, page=_current_page(obs2, bb), awaiting="apply",
                 last={"ok": recognised, "action": "orient", "queue": queue.summary(),
                       "orient": {"platform": platform, "state": state, "progress": progress},
                       "detail": detail})
    return view


class AdoptWindowBody(BaseModel):
    initiator: str = "operator"


@router.post("/api/session_control/{session_id}/adopt_from_window")
async def adopt_from_window(session_id: int, body: AdoptWindowBody,
                            db: Session = Depends(get_db)) -> dict[str, Any]:
    """Adopt an application the WINDOW is already in the middle of, so the record can catch up.

    `reconcile_step` aligns a step that exists. This is the case one level up: the world moved
    ahead of the record entirely — a query was run, a card was opened, Apply was pressed, an ATS
    tab is open — and the ladder still says "run the query" because none of it went through the
    queue. There was no way back in: the queue is only filled by `choose`, `choose` requires the
    preamble to have been walked, and the preamble's first rung is CONSUMING. So the only route the
    system offered was to spend a second query re-doing something already done, which is precisely
    what the once-only rule exists to prevent. A session could be driven into a state it could not
    be driven out of. (Live 2026-07-30: a LinkedIn apply reached AppVault while the cockpit showed
    an empty queue and `query_entered: next`.)

    THE RULE IS THE SAME ONE reconcile_step FOLLOWS — the browser is truth, the record is memory,
    and memory yields — with the same limit: **it records only what the window PROVES.** A results
    page for a query is proof that query ran; an open pane names the job; an ObservedJob row makes
    it a pick we can enqueue. Anything it cannot confirm is reported as refused rather than
    assumed, because a fabricated rung is worse than a missing one.
    """
    _check_initiator(body.initiator)
    from urllib.parse import parse_qs, urlparse

    from models import ObservedJob

    session, bb, ledger = _load(session_id, db)
    browser_url = _session_browser_url(session)
    obs = await _observe(browser_url, bb.search_state.query, session_id=session.id)
    tabs = obs.get("tabs") or []
    adopted: list[str] = []
    refused: list[str] = []

    # 1. THE QUERY. Read it off the results tab's own URL rather than asking for it: the engine
    #    names the param (`q` on Indeed, `keywords` on LinkedIn) and the page IS the effect of the
    #    consuming rung. Marking it held is a RECOVERY, never a re-run.
    engine_tab, tab_engine = None, None
    for t in tabs:
        eng = engine_of_url(t.get("url", ""))
        if eng is not None:
            engine_tab, tab_engine = t, eng
            break
    if engine_tab and not (bb.search_state.query or "").strip():
        params = parse_qs(urlparse(engine_tab.get("url", "")).query)
        # ASK THE ENGINE WHAT ITS PARAM IS CALLED. `q` on Indeed, `keywords` on LinkedIn — the
        # table already knows, and guessing both would quietly pick up somebody else's `q`.
        found = (params.get(tab_engine["query_param"]) or [""])[0].strip()
        if found:
            bb.search_state.query = found
            adopted.append(f"query={found!r}")
        else:
            refused.append("the results tab carries no query parameter to read")
    if (bb.search_state.query or "").strip() and engine_tab and not ledger.holds("query_entered"):
        ledger.mark("query_entered", evidence=f"adopted from the live window: results for "
                                              f"{bb.search_state.query!r} are on screen "
                                              f"({(engine_tab.get('url') or '')[:90]})",
                    initiator=body.initiator)
        adopted.append("query_entered")

    # 2. THE JOB IN FLIGHT. The pane names it — on LinkedIn in the URL itself (`currentJobId`),
    #    which is the same id the pane's own slots carry. A pick we cannot resolve to an
    #    ObservedJob row is NOT enqueued: the queue's steps carry a job_id that the rest of the
    #    system dereferences, and inventing one would put a phantom application on the ladder.
    job_id = ""
    if engine_tab:
        cur = parse_qs(urlparse(engine_tab.get("url", "")).query).get("currentJobId") or []
        if cur:
            candidate = f"{tab_engine['platform']}:{cur[0]}"
            if db.get(ObservedJob, candidate) is not None:
                job_id = candidate
            else:
                refused.append(f"the open pane is {candidate}, which has no observed_jobs row — "
                               f"extract the results page before adopting it")
    if not job_id:
        refused.append("no open job pane to adopt as the current application")

    queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
    if job_id and not any(s.job_id == job_id for s in queue.steps):
        row = db.get(ObservedJob, job_id)
        approved = list(bb.search_state.approved or [])
        if job_id not in approved:
            approved.append(job_id)
        bb.search_state.approved = approved
        queue.page = queue.page or (bb.search_state.page or 1)
        queue.enqueue([{"job_id": job_id, "title": row.title if row else "",
                        "company": row.company if row else ""}])
        _save_queue(bb, queue)
        adopted.append(f"enqueued {job_id} ({row.title if row else '?'})")

    bb.log("adopt", f"adopted from the live window: {', '.join(adopted) or 'nothing'}")
    _persist(bb, ledger)
    detail = ("Adopted from the window: " + "; ".join(adopted)) if adopted else \
             "Nothing in the window could be adopted."
    if refused:
        detail += " | Not asserted: " + "; ".join(refused)
    return _view(session, bb, ledger, obs, page=_current_page(obs, bb),
                 awaiting="apply" if job_id else None,
                 last={"ok": bool(adopted), "action": "adopt_from_window", "detail": detail,
                       "adopted": adopted, "refused": refused})


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

    obs = await _observe(browser_url, bb.search_state.query, session_id=session.id)
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
    obs2 = await _observe(browser_url, bb.search_state.query, session_id=session.id)
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
    Used to check an ATS destination against the pick title.

    ALL the segments, not just the last one. MEASURED 2026-07-30: Ahold Delhaize's careers front
    puts the title in the MIDDLE —
    `/job/Procurement-%26-Logistics/Sr.-Reporting-Analyst/Quincy-MA/ADUSA` — so reading from the end
    returned "ADUSA", the title check found nothing to match, and `verify_identity` recorded UNKNOWN
    for a destination that names the job plainly. Reading from the end is right for Workday and
    wrong here, and there is no reason to pick: the caller only asks whether the pick's words appear.
    """
    from urllib.parse import unquote, urlparse
    path = unquote(urlparse(url or "").path)
    words: list[str] = []
    for seg in path.split("/"):
        if not seg or "job" in seg.lower():
            continue
        words += [w for w in seg.split("_")[0].replace("-", " ").replace("+", " ").split()
                  if not w.isdigit()]
    return " ".join(words)


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
    obs = await _observe(_session_browser_url(session), bb.search_state.query, session_id=session.id)
    return _view(session, bb, ledger, obs, page=_current_page(obs, bb), awaiting="apply",
                 last={"ok": True, "action": "rebuild_queue", "queue": queue.summary(),
                       "detail": f"Rebuilt the queue: {added} application(s) restored from your "
                                 f"approved picks. Progress on any already-driven step was kept."})



#: Names that mean "start the application", most-specific first. Indeed labels this differently
#: depending on where the application actually goes, and the label is our first hint at the
#: platform — "Apply on company site" is telling us we are about to leave.
#: MEASURED 2026-07-30: LinkedIn's off-site control is "Apply on company WEBSITE" where Indeed's is
#: "Apply on company SITE". One word, and without it the specific hint missed and matching fell
#: through to the bare "apply" — which found a result card. Both wordings, ordered before the
#: generic ones.
_APPLY_HINTS = ("apply now", "easily apply", "apply on company website", "apply on company site",
                "apply with indeed", "apply")

#: An apply button is LABELLED, not narrated. MEASURED 2026-07-30 on LinkedIn: a result card is
#: `role=button` whose accessible name is the WHOLE CARD — "Business Intelligence Analyst Lumicity
#: Greater Boston (On-site) Dismiss … Posted 1 week ago · Easy Apply", 139 characters — so it
#: contains an apply word and is a button, and it beat the real control on a page where the pane's
#: own link is called "Apply on company website" (24 chars).
#:
#: This is the THIRD fix to this matcher for the same class of bug: it clicked a card in document
#: order (2026-07-26), then found nothing because it demanded role=button, and now this. The
#: previous guards were name blocklists, which only ever catch the phrasing already seen. A length
#: bound is different in kind: it says what an apply control IS. Every real one measured across both
#: engines — "Apply", "Apply now", "Easily apply", "Apply on company site/website", "Easy Apply" —
#: is under 30 characters.
_MAX_APPLY_NAME_CHARS = 60

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
        want = ["apply on company website", "apply on company site", "apply on employer site"] + want
    elif apply_type == "quick_apply":
        want = ["apply now", "easily apply", "apply with indeed"] + want
    elif apply_type == "linkedin_easy_apply":
        # The on-engine apply, LinkedIn's side of the same fork Indeed calls quick_apply. Its
        # control is named exactly "Easy Apply" — and so is a chip on every Easy-Apply CARD, which
        # is why the length bound below is doing the real work here rather than the wording.
        want = ["easy apply", "easily apply"] + want

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
        # A NAME THAT LONG IS A CARD, NOT A BUTTON. See _MAX_APPLY_NAME_CHARS — this is the guard
        # that does not depend on having seen the phrasing before, and on LinkedIn it is the only
        # one that separates the pane's "Apply on company website" from a card whose name happens
        # to end in "· Easy Apply".
        if len(name) > _MAX_APPLY_NAME_CHARS:
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

    obs = await _observe(browser_url, bb.search_state.query, session_id=session.id)
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
    _note_tab_drift(bb, obs, step)      # recorded on the view; never acts on its own
    style = xs.pick_style()
    await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))

    if rung.id == "open_pane":
        ext = step.job_id.split(":", 1)[-1]
        # SAY WHICH TAB. Without one, `_discover_target` takes whatever CDP lists first — which,
        # the moment a finished application is still open, is the ATS tab. The card was on the
        # results page all along and this reported "card data-jk=… not found", which reads as a
        # rotated listing rather than a misaddressed click (live 2026-07-27, with the submitted
        # iCIMS tab still open). The search tab is the only document that HAS result cards.
        search_tab = (obs.get("search_tab") or {})
        res = await _capture_post("/open_job_card",
                                  {"browser_url": browser_url, "external_id": ext,
                                   "tab_id": search_tab.get("tab_id") or None,
                                   "tab_url": None if search_tab.get("tab_id") else "indeed.com/jobs"})
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

            # THE APPLIED CHECK, ON LANDING. Asked here — with the pane's own title, the richest
            # description of this job we will hold before entering — and not after a drive has
            # spent its way into an ATS to be told the same thing. `applied` HALTS the step;
            # `likely_applied` only warns, because a fuzzy match that silently skipped a job the
            # operator picked would be worse than the drive it saves.
            verdict = applied_index.check(db, job_id=step.job_id,
                                          title=res.get("title") or step.title,
                                          company=step.company, url=res.get("url") or "")
            bb.world["applied_check"] = verdict.as_dict()
            if verdict.applied:
                step.record("open_pane", aps.OK,
                            f"pane switched to {res.get('title', '')!r} — but we have already "
                            f"applied ({verdict.matched_on}: {'; '.join(verdict.evidence)})",
                            initiator=body.initiator)
                _save_queue(bb, queue); _persist(bb, ledger)
                return _save_queue_and_view(
                    session, bb, ledger, queue, obs, ok=False, pace=style,
                    detail=(f"We have already applied to this job — matched on "
                            f"{verdict.matched_on} ({'; '.join(verdict.evidence)}"
                            + (f", applied {verdict.applied_at[:10]}" if verdict.applied_at else "")
                            + "). Flag it rather than applying twice."))

            step.record("open_pane", aps.OK,
                        f"pane switched to {res.get('title', '')!r}"
                        + (f" · apply_type={res.get('apply_type')}" if res.get("apply_type") else "")
                        + (f" · WARNING {verdict.status}: {'; '.join(verdict.evidence)}"
                           if verdict.worth_asking else ""),
                        initiator=body.initiator)
            detail = (f"Opened {res.get('title') or step.job_id}."
                      + (f" NOTE: this looks like one we may have applied to already — "
                         f"{'; '.join(verdict.evidence)}. Check before entering."
                         if verdict.worth_asking else ""))

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
        # AND WHERE THE PAGE POINTS. A careers front names no ATS in its host, its params or its
        # prose — the only tell is the destination of its own APPLY control. This is a third axis
        # beside host and content, and it is the one that gets us through the jungle of employer
        # landing pages that are really a wrapper around an ATS we already know how to drive.
        disc = aps.classify_landing(url, page_text=content.get("text") or "",
                                    frames=content.get("frames") or [],
                                    apply_hrefs=content.get("apply_hrefs") or [])
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
    obs = await _observe(_session_browser_url(session), bb.search_state.query, session_id=session.id)
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
        obs = await _observe(_session_browser_url(session), bb.search_state.query, session_id=session.id)
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
    obs = await _observe(_session_browser_url(session), bb.search_state.query, session_id=session.id)
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
    from controller import window as window_mod

    tabs = obs.get("tabs") or []
    recorded_id = ((bb.world or {}).get("apply_tab") or {}).get("tab_id")
    if recorded_id:
        live = next((t for t in tabs if t.get("tab_id") == recorded_id), None)
        if live:
            # THE FLOW MAY HAVE HOPPED PAST THE RECORDED TAB. The record was only ever wrong when
            # its tab had closed or navigated — but an apply can also open a SECOND tab and leave
            # the first one open and inert. BILH: Indeed -> jobs.bilh.org (recorded) -> "Apply now"
            # -> bilh.wd1.myworkdayjobs.com, and the resolver kept handing back the spent landing,
            # so `orient` read a job posting and called the Workday application "new territory"
            # (live 2026-07-27). The stepping-stone is still open, so being open is not the test.
            #
            # The window manager already tells them apart — an ATS host is ROLE_APPLY, an employer
            # careers page is ROLE_UNKNOWN — so prefer a real application tab over a recorded one
            # that is not, rather than inventing an ordering rule about which tab is "newest".
            if window_mod.classify_tab(live.get("url", "")) != window_mod.ROLE_APPLY:
                hopped = next((t for t in tabs
                               if t.get("tab_id") != recorded_id
                               and window_mod.classify_tab(t.get("url", "")) == window_mod.ROLE_APPLY),
                              None)
                if hopped is not None:
                    return {"tab_id": hopped.get("tab_id"), "url": hopped.get("url", "")}
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

    # THE CLEANUP CREW RUNS ON EVERY TERMINAL, not just on success. An application abandoned at a
    # wall leaves exactly the same orphan tab as one that was submitted, and the next prospect has
    # to start from a window that means something.
    obs = await _observe(_session_browser_url(session), bb.search_state.query, session_id=session.id)
    # RECORD BEFORE CLOSE — the epilogue's own rule. A closed tab with no record is unrecoverable,
    # and the record is what the NEXT session gets to ask (applied_index).
    recorded = _record_outcome(db, step, ats_url=_apply_tab(bb, obs).get("url", ""))
    cleanup = await _apply_cleanup(bb, obs, _session_browser_url(session), step)
    bb.world.pop("apply_tab", None)          # the record dies with the tab it pointed at
    _persist(bb, ledger)
    if cleanup["closed"]:
        obs = await _observe(_session_browser_url(session), bb.search_state.query, session_id=session.id)

    summary = queue.summary()
    nxt = queue.current()
    tidied = sum(1 for c in cleanup["closed"] if c["ok"])
    return _view(session, bb, ledger, obs, page=_current_page(obs, bb),
                 awaiting="apply" if summary["blocks_page"] else "choose",
                 last={"ok": True, "action": "apply_flag", "queue": summary,
                       "cleanup": cleanup, "recorded": recorded,
                       "detail": (f"{body.job_id} ended as {body.flag}. "
                                  + (f"Closed {tidied} finished tab(s); back on the search. "
                                     if tidied else "")
                                  + (f"Next up: {nxt.title or nxt.job_id}."
                                     if nxt else
                                     "Every application from this page is accounted for — "
                                     "choose again to advance the page."))})


#: How a step's terminal flag lands in the DURABLE record. The queue is per-session state on the
#: blackboard; `ObservedJob` is what survives the session and what the next drive asks.
_TERMINAL_TO_STATUS = {
    aps.SUBMITTED: "applied",
    aps.ABANDONED_GONE: "rejected",
    aps.ABANDONED_OPERATOR: "skipped",
}


def _record_outcome(db: Session, step: aps.ApplyStep, *, ats_url: str = "") -> dict[str, Any]:
    """Write the step's terminal to `ObservedJob`, so the next session can ask the database.

    APPLY_EPILOGUE always said RECORD before CLOSE, "because a closed tab with no record is
    unrecoverable" — and `apply_flag` closed without recording. The Joslin application was
    submitted and confirmed on 2026-07-27 and its row still read `seen`, `applied_at=None`. The
    queue knew; the durable table did not; and a queue lives on one session's blackboard.

    That is what made the operator's question unanswerable: *"see if we applied in the database or
    not."* There was nothing in the database to see.

    Only SUBMITTED stamps `applied_at` — the same rule the epilogue states. A parked step writes
    nothing: parked means not now, and marking it would tell the next session a lie.
    """
    status = _TERMINAL_TO_STATUS.get(step.terminal or "")
    if not status:
        return {"recorded": False, "reason": f"{step.terminal} is not a durable outcome"}

    row = db.get(ObservedJob, step.job_id)
    if row is None:
        platform, _, ext = step.job_id.partition(":")
        row = ObservedJob(job_id=step.job_id, platform=platform or "indeed", external_id=ext,
                          title=step.title or "", company=step.company or "")
        db.add(row)
    row.application_status = status
    if step.platform:
        row.application_platform = step.platform
    # The ATS url carries the requisition id, and the requisition is how this job is recognised
    # when it is met again through a different door (applied_index, tier 2). Worth keeping even
    # when the row already has the Indeed url: that one shares no id with the ATS.
    if ats_url:
        row.url = ats_url[:1200]
    if status == "applied" and row.applied_at is None:
        row.applied_at = datetime.now(timezone.utc)
    if step.terminal_detail:
        row.notes = (step.terminal_detail or "")[:2000]
    db.commit()
    return {"recorded": True, "status": status, "job_id": step.job_id,
            "applied_at": row.applied_at.isoformat() if row.applied_at else None}


def _note_tab_drift(bb: Any, obs: dict[str, Any], step: aps.ApplyStep) -> dict[str, Any]:
    """Which tabs have appeared or vanished since this step was last worked. Pure bookkeeping.

    Operator, 2026-07-27: *"tab manager always needs to be weary especially in the beginning of the
    apply company stage or probably throughout just to check if anything's changed."* The apply
    stage is where the window changes UNDER us — Apply-on-company-site opens the employer landing
    in a new tab, and an Apply inside that can open another — and until now nothing looked between
    cranks. A drive that does not notice a new tab is a drive that can act on the wrong one, which
    is exactly how `open_pane` came to hunt for result cards in a submitted application.

    This only REPORTS. Closing during an application is how you lose live work; the cleanup runs at
    the terminal, when the ladder says the work is finished.
    """
    census = dict((bb.world or {}).get("apply_tab_census") or {})
    live = {t.get("tab_id"): (t.get("url") or "") for t in (obs.get("tabs") or []) if t.get("tab_id")}
    known = census.get("tabs") or {}
    same_step = census.get("job_id") == step.job_id

    appeared = [{"tab_id": k, "url": v[:90]} for k, v in live.items() if same_step and k not in known]
    vanished = [{"tab_id": k, "url": str(v)[:90]} for k, v in known.items() if k not in live] \
        if same_step else []

    # Tabs this STEP opened, accumulated across cranks. This is what lets the cleanup be thorough
    # without being reckless: the window manager rightly refuses to close an UNKNOWN-role tab (it
    # might be the operator's), but a tab we watched appear during our own application is ours to
    # close. The BILH landing page — an employer careers site, role UNKNOWN — is exactly that: a
    # doorway we opened, spent the moment its Apply handed off, and left behind.
    opened = list(census.get("opened") or []) if same_step else []
    opened.extend(a["tab_id"] for a in appeared if a["tab_id"] not in opened)

    bb.world = dict(bb.world or {})
    bb.world["apply_tab_census"] = {"job_id": step.job_id, "tabs": live, "opened": opened}

    drift = {"appeared": appeared, "vanished": vanished, "count": len(live),
             "baseline": bool(same_step), "opened_by_this_step": opened}
    if appeared or vanished:
        bb.log("tab_drift", f"{step.job_id}: +{len(appeared)} -{len(vanished)} tab(s) since the "
                            f"last crank")
    bb.world["tab_drift"] = drift
    return drift


async def _apply_cleanup(bb: Any, obs: dict[str, Any], browser_url: str,
                         step: aps.ApplyStep) -> dict[str, Any]:
    """Close the finished application's tab and hand the window back to the search.

    THE APPLY STAGE SPAWNS TABS. Indeed's "Apply on company site" opens the employer landing in a
    new tab, and the Apply inside THAT can open another — so an apply that nobody tidies leaves one
    or two inert tabs per prospect, and they accumulate across a session. Operator, 2026-07-27:
    *"the tab manager should've immediately been a part of the cleanup crew after submitting."*

    It was already written down — APPLY_EPILOGUE calls itself "a REQUIRED step of the loop, not a
    manual tidy-up" — and nothing on the path that ENDS a step ever called it. Prose in the recipe,
    absent from the layer.

    **The finished-ness comes from the LADDER, not from the URL.** A submitted iCIMS application
    sits on `…icims.com/jobs/…/job?mode=submit_apply`, which `classify_tab` reads as ROLE_APPLY —
    correctly, since it cannot know. The step reaching a terminal flag is the fact that makes the
    tab inert, so this closes the apply tab BY IDENTITY and leaves the URL classifier alone. The
    window manager then plans the rest (blanks, duplicates); nothing here invents its own rule
    about what may be closed, and the search tab is never a candidate.
    """
    from controller import window as window_mod

    tabs = obs.get("tabs") or []
    search_url = (obs.get("search_tab") or {}).get("url") or "indeed.com/jobs"
    apply_tab = _apply_tab(bb, obs)
    closed: list[dict[str, Any]] = []

    async def _close(tab_id: str, url: str, why: str) -> None:
        res = await _capture_post("/close_tab", {"browser_url": browser_url, "tab_id": tab_id,
                                                 "focus_tab_url": search_url})
        closed.append({"tab_id": tab_id, "url": url[:90], "why": why,
                       "ok": bool(res.get("ok")), "detail": res.get("detail", "")})

    if apply_tab.get("tab_id") and apply_tab.get("tab_id") != (obs.get("search_tab") or {}).get("tab_id"):
        await _close(apply_tab["tab_id"], apply_tab.get("url", ""),
                     f"the application tab for {step.job_id}, now {step.terminal}")

    # The doorways this step opened on its way in. An apply hops — Indeed -> the employer's careers
    # page -> the ATS — and each hop strands the one before it. They are ours by provenance (we
    # watched them appear during this step), which is the only warrant strong enough to close a tab
    # the window manager would leave alone.
    census = (bb.world or {}).get("apply_tab_census") or {}
    if census.get("job_id") == step.job_id:
        by_id = {t.get("tab_id"): t.get("url", "") for t in tabs}
        for tid in census.get("opened") or []:
            if tid in by_id and tid not in {c["tab_id"] for c in closed} \
                    and tid != (obs.get("search_tab") or {}).get("tab_id"):
                await _close(tid, by_id[tid], f"a doorway this step opened on the way to the ATS")
        bb.world.pop("apply_tab_census", None)

    # Whatever else the window manager would retire anyway — blanks, exact duplicates, orphaned
    # duplicate apply flows. Its four rails (never the active tab, the last tab, an UNKNOWN role,
    # or the only search tab) are the reason this is a survey rather than a loop over `tabs`.
    remaining = [t for t in tabs if t.get("tab_id") not in {c["tab_id"] for c in closed}]
    win = window_mod.survey(remaining, active_tab_id=(obs.get("search_tab") or {}).get("tab_id", ""))
    for tab, why in zip(win.closable, win.reasons):
        if tab.role == window_mod.ROLE_SEARCH:
            continue
        await _close(tab.tab_id, tab.url, why)

    if closed:
        bb.log("tab_cleanup", f"{step.job_id}: closed {len(closed)} tab(s) after {step.terminal}")
    return {"closed": closed, "tabs_before": len(tabs), "search_focused": search_url[:90]}


class ApplyReopenBody(BaseModel):
    job_id: str
    reason: str                    # why it is coming back — recorded on the step
    initiator: str = "operator"


@router.post("/api/session_control/{session_id}/apply_reopen")
async def apply_reopen(session_id: int, body: ApplyReopenBody,
                       db: Session = Depends(get_db)) -> dict[str, Any]:
    """Bring a PARKED application back into the queue, at its original place in the pick order.

    The other half of the parked/abandoned split, which until now existed only as prose. `enqueue`
    is idempotent by job_id (it must be — pressing Choose twice cannot double the work), `done` is
    true for any terminal flag, and nothing reopened anything, so "not now" and "not ever" behaved
    identically. The operator's TOP-PRIORITY pick sat parked under its own note — "re-queue after
    the matcher fix" — with the matcher fix already shipped.

    Operator-initiated only, and never for an ABANDONED step: reversing "not ever" on the system's
    own initiative is how dead requisitions come back forever. The parked attempt is archived on
    the step rather than deleted, so the retry can be read against what went wrong the first time.
    """
    _check_initiator(body.initiator)
    if not body.reason.strip():
        raise HTTPException(status_code=422,
                            detail="Give a reason — a step coming back should say what changed.")
    session, bb, ledger = _load(session_id, db)
    queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
    step = next((s for s in queue.steps if s.job_id == body.job_id), None)
    if step is None:
        raise HTTPException(status_code=404,
                            detail=f"{body.job_id} is not in this page's apply queue.")
    try:
        step.reopen(body.reason, initiator=body.initiator)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    _save_queue(bb, queue)
    bb.log("apply_reopen", f"{body.job_id} reopened — {body.reason[:100]}")
    _persist(bb, ledger)
    summary = queue.summary()
    nxt = queue.current()
    obs = await _observe(_session_browser_url(session), bb.search_state.query, session_id=session.id)
    return _view(session, bb, ledger, obs, page=_current_page(obs, bb), awaiting="apply",
                 last={"ok": True, "action": "apply_reopen", "queue": summary,
                       "detail": (f"{step.title or body.job_id} is back in the queue and the "
                                  f"ladder restarts at the top. "
                                  + (f"Now working: {nxt.title or nxt.job_id}." if nxt else ""))})


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
    obs = await _observe(browser_url, bb.search_state.query, session_id=session.id)
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
    obs_after = await _observe(browser_url, bb.search_state.query, session_id=session.id)
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
    obs = await _observe(browser_url, bb.search_state.query, session_id=session.id)
    page = _current_page(obs, bb)
    engine = engine_for(session, obs.get("search_tab"))

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
        obs_now = await _observe(browser_url, bb.search_state.query, session_id=session.id)
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
        # On a SPA the click re-renders in place, so "has_next" alone is not evidence the NEXT page
        # is what is now on screen. Take a signature first and require it to change; otherwise the
        # ladder marks page N+1 while still showing page N, and the operator picks from a page they
        # have already picked from. (No-op on Indeed, which navigates.)
        sig_before = {}
        if engine.get("spa"):
            sig = await _capture_post("/results_signature",
                                      {"browser_url": browser_url, "tab_url": engine["search_tab"]})
            sig_before = sig.get("signature") or {}
        nxt = await _capture_post("/next_page",
                                  {"browser_url": browser_url, "tab_url": engine["search_tab"]})
        if nxt.get("has_next") and engine.get("spa"):
            settled = await _capture_post("/await_results",
                                          {"browser_url": browser_url,
                                           "tab_url": engine["search_tab"],
                                           "before": sig_before}, timeout=40.0)
            if not settled.get("changed"):
                bb.log("advance", f"page {page}: next was clicked but the list never changed")
                advanced.update(awaiting="operator_results",
                                detail=f"Page {page} reviewed; {len(body.picks)} picked. The next "
                                       f"page was clicked but the results never changed — check "
                                       f"the window before stepping, rather than re-reading a page "
                                       f"we already have.")
                _persist(bb, ledger)
                obs_now = await _observe(browser_url, bb.search_state.query, session_id=session.id)
                return _view(session, bb, ledger, obs_now, page=page,
                             awaiting="operator_results", last=advanced)
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
    obs_after = await _observe(browser_url, bb.search_state.query, session_id=session.id)
    return _view(session, bb, ledger, obs_after, page=advanced.get("page", page),
                 awaiting=advanced.get("awaiting"), last=advanced)
