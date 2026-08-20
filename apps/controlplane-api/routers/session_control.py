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
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

import account_forms
import applied_index
import apply_fields
from urllib.parse import urlparse

import apply_landing as al
import apply_steps as aps
import google_recipe
import job_dedup
import session_windows
from controller import window as _win
import execution_style as xs
import session_checkpoints as cps
from deps import _session_browser_url, get_db
# A reading knows whether it was taken; a refusal carries the way out. Imported at module level
# rather than in-function because both are TYPES in signatures here, not optional dependencies.
from interaction.measured import Reading
from interaction import refusal
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
     # The other two params a RESULTS page carries, so a spent search can be reopened rather than
     # re-submitted (`_results_url`). Named per engine for the same reason `query_param` is.
     "location_param": "l", "radius_param": "radius",
     # HOW THE QUERY IS COMMITTED. Indeed has a real Search button; LinkedIn has none and commits on
     # Enter (measured from the operator's own /observe recording, 2026-07-28). `_run_query` used to
     # require a submit CONTROL on every engine, so on LinkedIn it reported "found submit" — it had
     # matched `Skip to search`, the skip-link `linkedin_recipe` warns about — and refused to run.
     "commit": "button",
     # Indeed exposes a Distance pill; LinkedIn's results page exposes no distance control at all
     # (measured 2026-07-30: its filter row is topical — Date posted / Remote / Easy Apply /
     # Experience level — plus a LOCATION button whose name carries its value, "Greater Boston").
     "distance_filter": True,
     "label": "Indeed", "spa": False},
    {"id": "linkedin_jobs", "platform": "linkedin", "host": "linkedin.com", "results_path": "/jobs",
     "query_param": "keywords", "page_size": 25, "home": "https://www.linkedin.com/jobs/",
     "search_tab": "linkedin.com/jobs", "label": "LinkedIn",
     "location_param": "location", "radius_param": "distance",
     # No submit button exists on the jobs home; Enter on the query box is the commit.
     "commit": "enter",
     "distance_filter": False,
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


#: The engine surfaces a click can land on WITHOUT having entered an application: the results
#: list and a posting's own detail page. `engine_of_url` is not enough on its own — it requires
#: the results path, and Indeed's posting url is `/viewjob`.
_ENGINE_STAY_PATHS = ("/viewjob", "/jobs", "/jobs/view")


def _apply_task_name(bb: Any, step: Any) -> str:
    """The task id a teacher correction is journaled under — `<platform>_apply`.

    The PLATFORM here is the ATS the application actually lives in (`workday_apply`), because that
    is the surface the correction teaches and the bucket that generalizes across every employer on
    it (`ats_registry`: an ATS renders the same component library for every tenant). Only when the
    step has no platform does it fall back to the ENGINE — an on-engine apply (Indeed smartapply,
    LinkedIn Easy Apply) is the engine's own surface.
    """
    platform = (getattr(step, "platform", "") or "").strip().lower()
    if not platform:
        import apply_state_store as store
        platform = (store.search_engine_of(bb) if bb is not None else "") or \
            DEFAULT_ENGINE["platform"]
    return f"{platform}_apply"


def _engine_of_landed(url: str) -> Optional[dict[str, Any]]:
    """The engine we are STILL ON, if this url is one of its own non-apply pages — else None.

    An apply that never left the engine did not begin. Answering this per-engine (rather than
    matching two Indeed literals) is what lets the guard fire on LinkedIn, where the same
    mis-click lands on `linkedin.com/jobs/...` instead.
    """
    u = (url or "").lower()
    for e in ENGINES:
        if e["host"] not in u:
            continue
        if any(p in u for p in _ENGINE_STAY_PATHS):
            return e
    return None


def _search_focus_url(bb: Any, obs: dict[str, Any]) -> str:
    """Where to send the window when an apply tab closes — THIS session's search, never Indeed's.

    Three sources, most-factual first: the observed search tab, then any open tab that is an
    engine's results page, then the engine the blackboard remembers working. The literal
    `"indeed.com/jobs"` these replace was harmless while Indeed was the only engine and is a
    cross-domain leak now: a LinkedIn session whose search tab was not in `obs` would have its
    browser refocused onto Indeed's job search mid-drive.

    Falls back to Indeed's front door only when nothing anywhere names an engine — the same
    default `engine_for` uses, so nothing that exists today changes behaviour.
    """
    observed = (obs.get("search_tab") or {}).get("url")
    if observed:
        return observed
    for tab in obs.get("tabs") or []:
        found = engine_of_url(tab.get("url", "") or "")
        if found:
            return found["search_tab"]
    import apply_state_store as store
    platform = store.search_engine_of(bb) if bb is not None else None
    engine = next((e for e in ENGINES if e["platform"] == platform), DEFAULT_ENGINE)
    return engine["search_tab"]


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
async def _observe(browser_url: str, bb: Any, *, session_id: Any = None, note: str = "",
                   actor: str = "system",
                   belief: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """What is actually true right now, as a tri-state map for `session_checkpoints.next_step`.

    True / False / **None**, and the None matters: "we did not check" must never read as a
    regression, or one flaky probe would send us re-running a rung that costs a real query.
    `radius_set` is always None — there is no cheap read-back of the distance pill, and guessing
    would be exactly the wrong kind of confident-wrong.

    THE FUSED VERDICT RIDES HOME WITH THE OBSERVATION, and that is the point of taking `bb` here
    rather than a bare query string. `_view` has always documented itself as rendering the
    observer's verdict "INSTEAD of trusting the recipe position", and `_orient_now` says it "runs
    on EVERY panel render" — but the verdict was an OPTIONAL keyword argument on `_view`, and
    measured 2026-08-16: **3 of 56 render sites passed it**. The other 53 quietly drew the panel
    from `step.landing_state`, a stored field with ELEVEN writers, every one of them inside our
    own action paths. So the panel moved when WE moved, and an operator driving the same Chrome
    by hand desynced it instantly — with `reconcile_step` existing as a twelfth writer whose only
    job was repairing the other eleven.

    An optional argument that must always be supplied is a bug with extra steps. There are exactly
    as many `_observe` calls as `_view` calls and `bb` is in scope at every one, so the verdict is
    computed HERE, where it cannot be skipped, and travels inside `obs` — which `_view` already
    requires positionally. Forgetting it is now a type error rather than a stale panel.

    Cost: one `/page_content` on a local CDP socket, and only when an apply tab is actually open —
    `_orient_now` returns None before reading anything otherwise. Free in low-data mode.
    """
    query = bb.search_state.query
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
        # No tabs means no apply tab means nothing for the fusion to look at. `None` is the same
        # answer `_orient_now` would give, reached without the round trip.
        return {"observed": observed, "tabs": tabs, "search_tab": None, "block": None,
                "reachable": reachable, "observer": None}

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
    obs = {"observed": observed, "tabs": tabs, "search_tab": search_tab, "block": block,
           "reachable": True}
    # ASK THE WINDOW WHERE WE ARE, EVERY TIME. An unreadable tab is an abstaining witness inside
    # the fusion, so a failure here degrades the verdict rather than the observation.
    obs["observer"] = await _orient_now(bb, obs, browser_url, belief)
    return obs


def _engine_page_tab(tabs: list[dict]) -> Optional[dict]:
    """The engine's OWN page — results or search, whatever query it currently shows.

    Not `_find_search_tab`, deliberately: that one is context-bound to THIS session's query
    ("which tab PROVES our query ran") and rightly answers None the moment a new query is
    declared. Typing the new query happens exactly where a human would type it — the engine tab
    that already exists, showing the previous search. And not `_find_site_tab` either: its host
    match accepts `smartapply.indeed.com`, which is an APPLICATION, and pointing the query rung
    at it is how drive 2 scanned a screener for a search box (2026-08-10)."""
    for t in tabs:
        if engine_of_url(t.get("url", "") or "") is not None:
            return t
    return None


def _navigable_tab(tabs: list[dict]) -> Optional[dict]:
    """A tab that may be POINTED somewhere else without destroying work. Never an apply or errand
    tab: a parked application lives in one, deliberately left open, and navigating it away is the
    exact loss `parked` exists to prevent."""
    for t in tabs:
        if _win.classify_tab(t.get("url", "") or "") not in ("apply", "errand"):
            return t
    return None


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


def _results_url(engine: dict[str, Any], *, query: str, location: str = "",
                 radius: Optional[int] = None, page: int = 1) -> str:
    """The URL of a results page this session ALREADY reached, rebuilt from its own record.

    For REOPENING a spent search, never for running one. `query_entered` is consuming: when its
    effect is gone (a relaunched browser lands on about:blank) the ladder correctly says RECOVER —
    "return to the results we already have … never re-submit the same query" — and until this
    existed there was nothing that could carry that out, so the operator's only reachable move was
    the one the rung forbids.

    Yes, this is address-forcing, which is normally last-ditch here ([[click links, not URLs]]),
    and this is the case that earns it: the page was reached by driving, the parameters are the
    session's own declared facts, and a person reopening their browser lands exactly here. The
    alternative — re-submitting the query — is precisely what gets a search collapsed by Indeed.
    """
    from urllib.parse import urlencode
    params = {engine["query_param"]: query}
    if location and engine.get("location_param"):
        params[engine["location_param"]] = location
    if radius and engine.get("radius_param"):
        params[engine["radius_param"]] = str(radius)
    if page and page > 1:
        params["start"] = str((page - 1) * engine["page_size"])
    return f"https://www.{engine['host']}{engine['results_path']}?{urlencode(params)}"


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
def _resume_path() -> str:
    """The canonical résumé's local path, or "" when the asset is missing.

    A read model must not break the panel, and "no résumé on disk" is an ordinary answer rather
    than an error — the census renders no attach button instead of one that uploads nothing.
    """
    try:
        import assets
        return assets.resume_path() or ""
    except Exception:  # noqa: BLE001
        return ""


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
        # THE SAME AUTHORITY THE RUNG USES. This read model answered "which account leg is due"
        # for platforms that need no account at all, so the cockpit offered "Create Account
        # automatically" for BRISTOL COUNTY SAVINGS BANK over Indeed's own finished review page
        # (live 2026-07-30). The account rung would have skipped it; the card asserted it first.
        if not aps.rung_applies("account", platform=step.platform,
                                state=step.landing_state)[0]:
            return None
        # AND NOT BEFORE THE LADDER GETS THERE. The fix above stopped the card asserting an account
        # for a platform that needs none; this is its other half — asserting one before the rung
        # that would ask for it. The cockpit gives the account leg the whole surface (it is a wall,
        # so it outranks the arbitrated action), which means the moment `classify` names an ATS
        # with an account, "Create Account automatically" becomes the operator's ONLY door — for a
        # wall nobody has seen yet. Measured live 2026-08-11: naming Cornerstone flipped the
        # surface to account-creation while the ladder was still on `classify` and the page's own
        # "Apply Now" had never been pressed.
        #
        # A registry `auth` field is a PREDICTION about a platform. The page is the measurement.
        # Predictions do not get to preempt the rung whose job is to take the measurement.
        _prefix = [r.id for r in aps.PREFIX]
        nxt, _ruled = step.walk_to_next_rung()
        if nxt is not None and nxt.id in _prefix and _prefix.index(nxt.id) < _prefix.index("account"):
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


# --- arbitration: the ONE next action -----------------------------------------------------------
# TWO SURFACES ANSWER "WHAT NEXT", AND SOMETHING HAS TO RESOLVE THEM.
#
# The ladder's rungs are computed from the RECIPE'S POSITION; the observer's plan from the LIVE
# PAGE. Computing them apart is the whole point — one source cannot disagree with itself, and the
# disagreement is the finding. But both were rendered as their own primary-looking button, so the
# panel offered two suggestions and left the operator to arbitrate. That is the "flow feels off"
# recorded as the sequencing debt on 2026-07-30.
#
# The rule is one line: THE OBSERVER WINS WHENEVER THE WORLD CONTRADICTS THE RECIPE, because in
# that case the recipe is the thing that is wrong. Otherwise the rung wins — no disagreement means
# the recipe is on track.
#
# Two properties this must keep, or it becomes a third surface answering the same question:
#   * IT INVENTS NOTHING. Both options are computed elsewhere (the ladder, the observer's plan);
#     this picks one and says why. It never composes a move of its own.
#   * THE LOSER STAYS VISIBLE as `secondary`, with the reason it was demoted. Both sides of a
#     disagreement stay on the record (PRINCIPLES §10), and an operator who can only see the winner
#     cannot correct it — which is the same as not having recorded it.
def _rung_option(step: Optional[Any]) -> Optional[dict[str, Any]]:
    """The RECIPE's answer, worded as the crank will actually work it — `walk_to_next_rung`, not
    `next_rung`, so a rung the discovery ruled out is never offered as the thing to press."""
    if step is None or step.done:
        return None
    rung, _ruled_out = step.walk_to_next_rung()
    if rung is None:
        return {"source": "rung", "id": "", "label": "Read this page",
                "why": (f"We are on {step.landing_state or 'a page we have not read'} "
                        f"({step.platform or 'unclassified'}) and the recipe has no rung for it. "
                        f"Stepping will re-read the page; if it is still unrecognised, it is "
                        f"genuinely new territory."),
                "endpoint": "/apply_step", "body": {}, "driveable": True}
    # THE GATE ANNOUNCES ITSELF. Every other rung is "work this"; this one is the irreversible act,
    # and a button that reads the same as the five before it is how an application gets sent by
    # muscle memory. `consequential` is what the cockpit renders the confirm affordance from.
    if rung.id == aps.SUBMIT_RUNG.id:
        return {"source": "rung", "id": rung.id, "label": "Submit this application",
                "why": rung.why, "endpoint": "/apply_step", "body": {}, "driveable": True,
                "consequential": True, "operator_only": True}
    return {"source": "rung", "id": rung.id, "label": f"Work this · {rung.label}",
            "why": rung.why, "endpoint": "/apply_step", "body": {}, "driveable": True}


def _observer_option(observer: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The WORLD's answer: the first step of the observer's own plan, carried verbatim.

    Verbatim includes `driveable`. A step we cannot perform is often still the right next move
    ("screenshot and hand to me"); what it must never become is a button, and losing the flag here
    is exactly how it would.
    """
    plan = (observer or {}).get("plan") or []
    if not plan:
        return None
    first = plan[0]
    driveable = bool(first.get("driveable"))
    return {"source": "observer", "id": first.get("id", ""), "label": first.get("label", ""),
            "why": first.get("why", ""),
            "endpoint": "/orient_action" if driveable else "",
            "body": {"action_id": first.get("id", "")} if driveable else {},
            "driveable": driveable}


def _decided(winner: dict[str, Any], loser: Optional[dict[str, Any]], *, reason: str,
             abstained: bool = False, demoted: str = "") -> dict[str, Any]:
    """One winner, the reason it won, and the loser kept beside it rather than dropped."""
    return {**winner, "reason": reason, "observer_abstained": abstained,
            "secondary": ({**loser, "demoted_because": demoted} if loser else None)}


def _resolve_next_action(step: Optional[Any],
                         observer: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Pick between the rung and the observer's plan — see the note above for the rule."""
    rung_opt, obs_opt = _rung_option(step), _observer_option(observer)
    if rung_opt is None and obs_opt is None:
        return None

    mismatch = (observer or {}).get("mismatch") or None
    confidence = (observer or {}).get("confidence") or ""
    headline = (observer or {}).get("headline") or (observer or {}).get("state") or "this page"
    # LOW CONFIDENCE IS AN ABSTENTION, NOT AN OBJECTION. An unsure observer that raised no mismatch
    # has nothing to overrule the recipe with — but it is said out loud, because a verdict dropped
    # in silence reads exactly like one that was never taken.
    abstained = bool(obs_opt) and not mismatch and confidence == "low"

    if mismatch and obs_opt is not None:
        return _decided(
            obs_opt, rung_opt,
            reason=("The world contradicts the recipe — " + (mismatch.get("detail") or "") +
                    ". The observation leads."),
            demoted="the recipe's position, kept in view — take it only if the page reads wrong.")

    if rung_opt is None:
        return _decided(obs_opt, None,
                        reason="No application step is open, so the page's own next move is all "
                               "there is to go on.")

    # LOST IS A STATE, NOT A FOOTNOTE (operator, 2026-08-10). We read the page and recognised
    # nothing (UNKNOWN), or there was nothing to read (UNREADABLE), while the ladder wants to act
    # on a premise about a page it cannot see — after a reopen that premise is literally "open the
    # posting" on a tab already deep in the application. "The rung stands because it is all there
    # is" was false: ORIENTING is there. The primary becomes a look, the scored witnesses render
    # beside it, and the rung is kept demoted until the page is recognised.
    if rung_opt is not None and not mismatch \
            and (observer or {}).get("kind") in (al.UNKNOWN, al.UNREADABLE):
        blind = ("there was nothing on it to read"
                 if (observer or {}).get("kind") == al.UNREADABLE
                 else "we read it and recognised nothing in it")
        orient = {"source": "orient", "id": "orient", "label": "Orient — find where we are",
                  "why": (f"{headline} — {blind}, and the ladder's "
                          + (f"`{rung_opt['id']}`" if rung_opt["id"] else "next")
                          + " rung presumes a page we cannot confirm. Orienting re-reads the tab "
                            "with every witness and shows the scored candidates before anything "
                            "acts."),
                  "endpoint": "/orient_now", "body": {}, "driveable": True, "lost": True}
        return _decided(orient, rung_opt,
                        reason=(f"{headline} — {blind}. Mid-application on a screen we cannot "
                                f"name, being lost is a state of its own, and the way out is to "
                                f"look, not to act."),
                        demoted="the ladder's position — take it only once the page is recognised.")

    if mismatch:
        # Contradicted, with nowhere to go: the observer says the rung is wrong and offers no move
        # of its own. Presenting the rung anyway is honest only if the warning travels with it.
        reason = ("The world contradicts the recipe — " + (mismatch.get("detail") or "") +
                  " — and the observer offers no move here, so the rung is all there is. "
                  "Look at the page before working it.")
    elif abstained:
        reason = (f"The observer abstained: {confidence} confidence on {headline}, and no mismatch "
                  f"to raise. The rung stands, and the observer's read is kept below rather than "
                  f"dropped — an unsure witness is not a silent one.")
    elif (observer or {}).get("kind") in (al.UNKNOWN, al.UNREADABLE):
        # SILENCE IS NOT AGREEMENT. "Nothing contradicts the rung" is vacuously true when nothing
        # was READ at all, and wording it "so the recipe is on track" claims a confirmation from
        # an observation that never happened — the same class as the two narration dishonesties
        # caught against real rows on 2026-08-04. Found 2026-08-05 while checking this arbitration
        # by hand: a known host with an unreadable page now scores `medium` rather than `low`
        # (correctly — we do know whose site it is), which walks straight past the `abstained`
        # branch that used to word this honestly. A confidence fix must not silently promote a
        # non-observation into a confirmation.
        # And the two non-answers are not the same non-answer: `unreadable` is nothing to read,
        # `unknown` is words we read and recognised nothing in. The module's own vocabulary makes
        # that distinction ("not the same as nothing there") and the sentence should keep it.
        blind = ("there was nothing on it to read" if (observer or {}).get("kind") == al.UNREADABLE
                 else "we read it and recognised nothing in it")
        reason = (f"{headline} — {blind}, so there is nothing here that confirms "
                  + (f"the `{rung_opt['id']}` rung" if rung_opt["id"] else "the ladder")
                  + " or contradicts it. The rung stands because it is all there is, not because "
                    "the page agreed with it — read the page before working it.")
    elif obs_opt is not None:
        reason = (f"{headline} — nothing there contradicts "
                  + (f"the `{rung_opt['id']}` rung" if rung_opt["id"] else "the ladder")
                  + ", so the recipe is on track.")
    else:
        reason = ("Nothing is watching an application tab, so the ladder's own position is the "
                  "next move.")
    return _decided(rung_opt, obs_opt, abstained=abstained, reason=reason,
                    demoted=("the observer's read, kept in view — too unsure to overrule the rung."
                             if abstained else
                             "the observer's way out, offered with no disagreement to resolve."))


def _parked_all(bb: Any, queue: aps.Queue,
                live_urls: Optional[list[str]] = None) -> list[dict[str, Any]]:
    """Every parked application the session still owes, slim rows for the panel: the current
    queue's parked steps plus the survivors of finished searches (`world["parked_apps"]`).
    Deduped by job_id with the current queue's record winning — it is the fresher fact.

    `tab_open` is the honest half of what PARKED means. Parking says "coming back to this page",
    and the cockpit offers "Step back in" on that promise — but a shutdown closes the tab, and
    anything typed into it that was never saved server-side goes with it. Judged against the LIVE
    window rather than remembered, because the whole point is that the world can take it away
    while the record still says parked (2026-08-13: Boston Children's parked one screen from
    Submit, its tab closed by the close-down, the strip still offering to step back in).
    """
    rows: dict[str, dict[str, Any]] = {}
    for p in ((bb.world or {}).get("parked_apps") or []):
        if p.get("job_id"):
            rows[p["job_id"]] = {**p, "in_current_queue": False}
    for s in queue.steps:
        if (s.terminal or "").startswith("parked:"):
            rows[s.job_id] = {"job_id": s.job_id, "title": s.title, "company": s.company,
                              "platform": s.platform, "terminal": s.terminal,
                              "terminal_detail": s.terminal_detail,
                              "tab_url": getattr(s, "tab_url", "") or "",
                              "in_current_queue": True}

    def _open(url: str) -> Optional[bool]:
        # None, not False, when we never recorded a page: "we do not know" and "it is gone" are
        # different answers, and only one of them should warn the operator.
        if not url:
            return None
        return any((u or "").split("#")[0] == url.split("#")[0] for u in (live_urls or []))

    return [{"job_id": r.get("job_id"), "title": r.get("title"), "company": r.get("company"),
             "platform": r.get("platform"), "terminal": r.get("terminal"),
             "terminal_detail": r.get("terminal_detail"),
             "tab_url": r.get("tab_url") or "", "tab_open": _open(r.get("tab_url") or ""),
             "from_search": r.get("from_search"), "from_page": r.get("from_page"),
             "in_current_queue": bool(r.get("in_current_queue"))}
            for r in rows.values()]


def _view(session: TrainingSession, bb: Any, ledger: cps.Ledger, obs: dict[str, Any], *,
          page: int, results: Optional[list[dict]] = None,
          awaiting: Optional[str] = None, last: Optional[dict] = None,
          observer: Optional[dict] = None) -> dict[str, Any]:
    """Everything the control panel renders: the declared query, where we are on the ladder,
    which page we are on, and this page's results.

    `observer` is an OVERRIDE, not the source. The verdict now arrives inside `obs` because
    `_observe` computes it (see there for why), so a caller that passes nothing still renders what
    the window said. The two paths that do pass one are enriching it — the orient endpoint and the
    post-step render, which have a fresh perception `belief` the poll path does not.
    """
    ss = bb.search_state
    observed = obs.get("observed", {})
    # The window's verdict leads; an explicit one only wins when a caller took a better look.
    observer = observer if observer is not None else obs.get("observer")
    queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
    # The rungs are worded for the engine actually being driven. They used to read "Signed in to
    # Indeed" on every ladder, which a LinkedIn session renders as an instruction to go and sign in
    # to the wrong site — found the first time a LinkedIn session was started, 2026-07-27.
    engine_label = engine_for(session, obs.get("search_tab"))["label"]
    cached_perception = _freshest_snapshot(session.id, (bb.world or {}).get("last_belief") or None)
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
        # THE OBSERVER'S VERDICT, computed fresh for this render (None when no apply tab is open).
        # The panel renders this INSTEAD of trusting the recipe position — where they disagree,
        # `observer.mismatch` says so and `observer.plan` says the way out.
        "observer": observer,
        # The last visual the LOCAL perception stack actually received. This is intentionally a
        # collected step snapshot rather than a screenshot on every poll: it keeps the heartbeat
        # cheap and preserves the credential-flow rule (those turns collect no screenshots).
        "perception_snapshot": cached_perception,
        # THE ONE NEXT ACTION, arbitrated between the two above — the world's plan and the recipe's
        # rung. Without it the panel showed both as primary buttons and the operator did the
        # resolving; with it there is one thing to press and the loser is beside it, demoted and
        # labelled with why. See `_resolve_next_action` for the rule.
        "next_action": _resolve_next_action(queue.current(), observer),
        "block": obs.get("block"),
        "tab_count": len(obs.get("tabs") or []),
        # THE WINDOW, IN THE PANEL. A bare count is the definition of out-of-sight-out-of-mind: an
        # apply opens a SECOND tab and navigates it three times, and the cockpit's only word for
        # that was "1 Tabs" — while the page the operator was being asked about lived in the tab
        # nobody could see. Operator-directed 2026-07-30. Roles come from session_windows'
        # classifier, the same one the drift detector uses, so the panel and the ledger cannot
        # disagree about which tab is the application.
        "tabs": [{"tab_id": tb.get("tab_id"), "url": tb.get("url", ""),
                  "title": tb.get("title", ""),
                  "role": _win.classify_tab(tb.get("url", "")),
                  "is_search": bool((obs.get("search_tab") or {}).get("tab_id") == tb.get("tab_id")),
                  "is_apply": bool(_apply_tab(bb, obs).get("tab_id") == tb.get("tab_id")),
                  # WHOSE tab this is — the durable claim written while its application was being
                  # worked. The window stops being anonymous: a leftover ATS tab names the job it
                  # belonged to, which is what lets cleanup (and the operator) act on it safely.
                  "claimed_by": ((bb.world or {}).get("tab_claims") or {}).get(tb.get("tab_id"))}
                 for tb in (obs.get("tabs") or [])],
        "results": results if results is not None else (bb.world or {}).get("page_results", []),
        "picks": list(ss.approved or []),
        # The apply queue for this page: N picks, N steps, and what each one is waiting on.
        "queue": queue.as_dict(),
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
        # THE CANONICAL RÉSUMÉ, so a file field can be answered with a press. Resolved here rather
        # than in the panel because `assets.resume_path()` is the one pointer every ATS uploader
        # already shares — a path hard-coded in the UI is wrong the first time the asset moves, and
        # would teach an upload of nothing while looking like it worked. "" when the file is
        # missing, which the panel renders as no button rather than a broken one.
        "resume_path": _resume_path(),
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
        "queue_summary": queue.summary(),
        # EVERY parked application this session still owes — the current queue's parked steps
        # plus the ones harvested when their search ended (`_reset_for_new_search`). Parked is
        # attention for the whole session, not just for the search it happened in: the half-done
        # application holds a real tab whichever query the ladder is walking now.
        "parked": _parked_all(bb, queue,
                              [t.get("url") or "" for t in (obs.get("tabs") or [])]),
        "awaiting": awaiting,
        # WHICH ATSes hide their form behind section bars. The panel needs this to know whether to
        # offer the section reader at all, and the declaration lives in apply_fields — so it is
        # sent rather than duplicated as a hardcoded name in the UI, which is how the two would
        # drift the first time a second accordion ATS is added.
        "accordion_ats": sorted(apply_fields.SECTION_BARS),
        # WHICH SEARCH INSIDE THIS SESSION. A session holds several, one after another — the
        # browser and the sign-in outlive every one of them. Without this the panel could not tell
        # "page 1" of the third search from "page 1" of the first, and the operator had no way to
        # see that abandoning a query costs nothing but the query.
        "search": {
            "n": ledger.search,
            "query": ss.query,
            "spent": {str(k): v for k, v in ledger.spent_queries().items()},
            "all": ledger.searches(),
            # WHAT LEAVING THIS SEARCH WOULD COST, priced before anything is pressed. The refusal
            # that used to guard this was a 409 the operator met only AFTER typing a new query, and
            # it named a job rather than a bill. The cockpit renders this beside the form instead,
            # so "four picks nobody opened are released; one half-driven application is parked and
            # stays resumable" is something you read before deciding, not after.
            "step_back": _step_back_cost(bb),
        },
        # HOW FAR THIS APPLICATION IS FROM SUBMIT, and the screens between here and there. The
        # ladder's tail, rendered — so "what is left" stops being something only the recipe knows.
        "apply_flow": _apply_flow(queue.current() or _parked_step(queue), observer),
        # WHAT WE ALREADY KNOW ABOUT WHERE WE LANDED — the ATS tables, read rather than merely
        # written. Added 2026-08-20: instance history, the vendor's measured mismatch rate, and
        # whether this flow will stop on a human, all with their denominators. The PeopleAdmin
        # account wall on 08-19 was predictable from the posting page and nothing was asking.
        "ats_brief": _ats_brief_for_view(bb, observer),
        # WHAT THE INNER LAYERS ARE GETTING RIGHT. Both are measured on every crank and neither had
        # a surface: the operator asked for the orienter to practise, and practice nobody can see
        # is indistinguishable from no practice at all.
        "learning": _learning_scoreboard(),
        "last_step": last,
        # THE TIMELINE, WITH ITS REASONING. `why` and `next_up` are what make a run of events read
        # as a story rather than a list of arrivals: the first says why a state changed, the second
        # says what we expected to happen next — which the following event then either matches or
        # contradicts. Empty on the events that genuinely have neither; nothing invents one.
        "events": [{"ts": e.ts, "kind": e.kind, "detail": e.detail,
                    "why": e.why, "next_up": e.next_up} for e in bb.events[-12:]],
        # How old is what we are looking at (perception/staleness.py — PROTOTYPE). Advisory: the
        # panel shows it and the operator decides. Nothing here acts on it.
        "staleness": _staleness_for(bb, obs),
    }


def _parked_step(queue: Any) -> Optional[Any]:
    """The most recent PARKED step — attention, not history.

    `parked:*` is a terminal flag, so `queue.current()` skips it and every surface built on
    current() went dark the moment a step parked: apply_flow null, observer null, the panel
    falling back to the pick table while a half-finished application held the tab open (the
    2026-08-10 screenshots). Parked means "waiting on you", and the cockpit's job is exactly
    the things waiting on you."""
    for step in reversed(list(getattr(queue, "steps", []) or [])):
        if (step.terminal or "").startswith("parked"):
            return step
    return None


def _apply_flow(step: Optional[Any],
                observer: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """The application's remaining screens, and how far the gate is. None when nothing is open.

    The recipe has always known this and only Workday ever said it out loud. Rendering it is what
    turns "Work this step" pressed five times into a walk with a visible end — and the operator's
    whole complaint was not knowing where in the application they were.

    A PARKED step still renders its flow (flagged `parked`): the application is mid-flight and
    resumable, and hiding the walk is what made parked read as closed.

    THE WINDOW PLACES THE WALK, NOT THE RECORD. This drew the position from `step.landing_state`,
    so the stepper showed where our last action left us: after a refresh signed the session out,
    it rendered "My Information, 4 screens from Submit" over a sign-in wall (live 2026-08-16). The
    observation is used when it named a state in the ladder's own vocabulary — which it now can,
    because the fusion takes the page's own stepper reading — and the record remains the fallback
    for the screens the observer can only describe as a KIND.
    """
    parked = bool(step is not None and (step.terminal or "").startswith("parked"))
    if step is None or (step.done and not parked):
        return None
    import apply_recipe as ar
    seen = (observer or {}).get("state") or ""
    # Only a spine-precise reading may place the walk: a generic `<platform>_<kind>` cannot say
    # WHICH form screen this is, and guessing between My Information and My Experience would move
    # the stepper on no evidence.
    state = seen if ar.flow_progress(seen, platform=step.platform or "").get("recognised") \
        else (step.landing_state or "")
    progress = ar.flow_progress(state, platform=step.platform or "")
    if not progress.get("recognised"):
        # WHY IT IS UNPLACED, SPECIFICALLY. All three of these used to render as one sentence —
        # "New territory" — which in this system means something strong and rare: an ATS nobody
        # has driven, go by hand. The operator read it on an ordinary job on ground we know well
        # (2026-08-13): "technically yes we've never opened this job before, but we've opened
        # other job cards before". Conflating "we have not looked yet" and "a job we have not
        # applied to" with "a platform we cannot drive" makes the real warning unreadable, because
        # it fires constantly.
        #
        # The novelty that matters to the OPERATOR is about the job (have we been here before?),
        # and it is already answered elsewhere — `applied_check` runs on landing and halts an
        # exact match. The novelty that matters to the DRIVE is about the platform. So say which.
        platform = step.platform or ""
        known = ar.platform_known(platform)
        if not platform and not state:
            novelty, headline, why = ("unread", "Not read yet",
                                      "nothing has been classified on this step — open the job "
                                      "and the screen names itself")
        elif not platform:
            novelty, headline, why = ("unclassified", "Not classified yet",
                                      "this screen has no platform yet, so there is no spine to "
                                      "count along — classify it first")
        elif known:
            novelty, headline, why = ("unplaced_screen", f"Familiar platform · unnamed screen",
                                      f"we drive {platform}, but this particular screen is not on "
                                      f"its spine — name it and the walk resumes")
        else:
            novelty, headline, why = ("new_platform", "New platform — no recipe",
                                      f"nothing here has driven {platform or 'this platform'} "
                                      f"before; it is hand-driven until a recipe exists")
        return {"recognised": False, "state": state, "platform": platform,
                "parked": parked, "novelty": novelty, "headline": headline,
                "platform_known": known, "why": why}
    # A platform without a scripted recipe is still WALKED — by the generic ATS cadence, which
    # counted this one to "at most 3 screens from Submit" while `flow_order` (scripted spines
    # only) returned nothing, so the strip drew a number with no steps under it.
    order = ar.flow_order(step.platform) or (
        ar.generic_flow_order(step.platform) if progress.get("via") == "generic_ats" else [])
    gate = ar.gate_state(step.platform) or (
        f"{step.platform}_review" if progress.get("via") == "generic_ats" else None)
    here = progress.get("position") or 0
    return {
        "recognised": True, "platform": progress.get("platform"), "state": state,
        "parked": parked,
        "steps_to_submit": progress.get("steps_to_submit"),
        "at_review_gate": bool(progress.get("at_review_gate")),
        # An UPPER BOUND, and labelled as one — platforms skip screens the profile already answers,
        # and skipping only ever shortens the path (`flow_progress` explains).
        "bound": "at most",
        "screens": [{"state": s, "label": aps.screen_label(s),
                     "position": i, "past": i < here, "current": i == here,
                     "is_gate": s == gate}
                    for i, s in enumerate(order)],
    }


def _learning_scoreboard() -> dict[str, Any]:
    """What the inner layers are getting right — the orienter's practice, and shadow agreement.

    Two numbers that were both being computed and neither being shown. `shadow_agreement` reads the
    decision journal's paired rows; `prediction_stats` reads the orientation corpus's trials. Both
    are cheap reads over files already on disk, and both must degrade to a stated absence rather
    than a zero: "0% accurate" and "never asked" look identical on a dial and mean opposite things.
    """
    out: dict[str, Any] = {}
    try:
        import orientation_log
        out["orienter"] = orientation_log.prediction_stats()
    except Exception:  # noqa: BLE001
        out["orienter"] = {"error": "the orientation corpus could not be read"}
    try:
        from controller import metrics as controller_metrics
        from interaction import decision_journal
        out["shadow"] = controller_metrics.shadow_agreement(decision_journal.read_rows())
    except Exception:  # noqa: BLE001
        out["shadow"] = {"error": "the decision journal could not be read"}
    return out


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


def _mini_typed(m: Any) -> bool:
    """Did this mini-step SAY it typed? Explicit `staged=True` only — no rung fallback.

    Deliberately stricter than `_mini_staged_input`, because the two callers want opposite safe
    errors from the same fact. Protecting a RELOAD: assume staged unless told otherwise, since
    over-protecting a page is the recoverable mistake. Deciding whether to TIDY a tab: assuming
    staged means never tidying anything, and the tab accumulation that follows is the failure
    `_apply_cleanup` exists to prevent. So the tidy path asks for evidence rather than the
    absence of a denial — `enter_apply` clicks, it does not type, and the fallback cannot tell.
    """
    staged = m.get("staged") if isinstance(m, dict) else getattr(m, "staged", None)
    return staged is True


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
#: World keys that describe ONE SEARCH's results and must not survive into the next one. Named
#: explicitly rather than by clearing `world` wholesale: the blackboard also holds session-level
#: facts (auth state, window census) that a new search has no business resetting.
_SEARCH_SCOPED_WORLD: tuple[str, ...] = (
    "page_results", "apply_queue", "open_pane", "applied_check", "tab_drift", "orient",
    "apply_tab", "account_handoff", "apply_proposal", "radius_miles", "last_belief",
    # THE SEARCH ROW ITSELF (`models.Search.id`), which outranked every other key here for damage.
    # It is minted by `review_page` and it is what every JobDecision joins through — so a search
    # that changed query while this key survived would file the NEXT search's decisions against the
    # PREVIOUS search's identity. Clearing it is also correct by construction rather than by luck:
    # `/choose` cannot run before `review_page` (it refuses picks that are not on the page), and
    # `review_page` is the one place that mints the row. Absent means "not minted yet", which is a
    # true statement; the old id was a false one ([[feedback_state_is_context_bound]]).
    "search_id",
)


def _step_back_cost(bb: Any) -> dict[str, Any]:
    """What leaving THIS search would cost, priced before anything is touched.

    Read-only, and deliberately separate from the reset that spends it: the operator is entitled to
    see the bill before pressing, and the cockpit renders exactly this. Four buckets, because they
    are four different facts and conflating any two of them is how a repick loses an application:

      * **worked** — not done, and somebody has actually driven it (a real half-finished
        application). Dropping one is a decision, so it needs the operator's reason.
      * **unworked** — a pick nobody has opened: queued, zero minis. Releasing it costs nothing,
        which is why it needs no reason — but it is still NAMED, because four candidates
        disappearing without a line in the journal is precisely the confusion this exists to stop.
      * **parked** — survives the search by harvest (below). Listed so the panel can say so.
      * **submitted** — already history. Nothing here can touch it.
    """
    queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
    out: dict[str, Any] = {"worked": [], "unworked": [], "parked": [], "submitted": 0}
    for s in queue.steps:
        card = {"job_id": s.job_id, "title": s.title, "company": s.company,
                "status": s.status, "terminal": s.terminal, "minis": len(s.minis or ())}
        if s.terminal == aps.SUBMITTED:
            out["submitted"] += 1
        elif (s.terminal or "").startswith("parked:"):
            out["parked"].append(card)
        elif s.done:
            pass                                     # abandoned — already a closed decision
        elif s.status == aps.STATUS_QUEUED and not (s.minis or ()):
            out["unworked"].append(card)
        else:
            out["worked"].append(card)
    return out


def _reset_for_new_search(bb: Any, *, leaving_search: Optional[int] = None) -> dict[str, Any]:
    """Clear what belonged to the search we are leaving, and nothing else. Returns what it released.

    The picks go too: `approved` is a list of job ids chosen off the OLD result set, and carrying
    them into a new search would offer the operator a queue built from results that are no longer
    on screen — the display-side contextual clobbering the cockpit rebuild exists to prevent.

    IT USED TO DO ALL OF THAT IN SILENCE, and the silence was the defect (operator, 2026-08-13:
    *"make sure our system doesn't get confused"*). Picks vanished, a queue vanished, and the
    timeline showed one line about a query changing — so the only record that four chosen jobs had
    ever been chosen was a `select:N` rung whose evidence still claimed them. Returning the bill is
    what lets the caller journal it; the caller is the one holding the operator's reason.

    PARKED APPLICATIONS SURVIVE THE SEARCH THEY PARKED IN. Dropping `apply_queue` wholesale
    silently orphaned them: parked means "not now, come back" (a REAL half-finished application
    holding a live tab), and the only handle to come back — the step record `apply_reopen` reads —
    died with the queue. So the parked steps are harvested into the session-level `parked_apps`
    before the queue goes. They render as attention, not as the focus: a parked application from
    search 2 must not imprison search 3's surface (the arrest half of the 2026-08-10 audit), and
    it must not vanish either (this harvest). `apply_reopen` resurrects from here into whatever
    queue is current.
    """
    released = _step_back_cost(bb)
    world = dict(bb.world or {})
    queue = aps.Queue.from_dict(world.get("apply_queue"))
    survivors = {p.get("job_id"): p for p in (world.get("parked_apps") or []) if p.get("job_id")}
    for s in queue.steps:
        if (s.terminal or "").startswith("parked:"):
            survivors[s.job_id] = {**s.as_dict(),
                                   "from_search": leaving_search, "from_page": queue.page}
    bb.world = {k: v for k, v in world.items() if k not in _SEARCH_SCOPED_WORLD}
    if survivors:
        bb.world["parked_apps"] = list(survivors.values())
    bb.search_state.approved = []
    released["from_search"] = leaving_search
    released["from_page"] = queue.page
    return released


class InitializeBody(BaseModel):
    query: str
    location: str = ""
    radius_miles: int = 50
    initiator: str = "operator"
    #: THE OPERATOR'S REASON FOR STEPPING BACK OUT OF LIVE WORK — required (and only required) when
    #: this search still holds an application somebody has actually driven.
    #:
    #: A REASON RATHER THAN A BOOLEAN, deliberately. `confirm: true` records that a warning was
    #: dismissed; it cannot tell a later reader whether the work was dropped because the query was
    #: wrong, because the candidates were wrong, or because somebody misread the screen. The whole
    #: request this field came from was *"the journal needs to know why"* — so the confirmation and
    #: the rationale are the same keystroke, and there is no way to spend the one without the other.
    release_open: str = ""
    #: THE OPERATOR'S REASON FOR RUNNING TERMS THIS SESSION HAS ALREADY SPENT. Same shape and same
    #: argument as `release_open`: the once-only rule is good at refusing the ACCIDENTAL repeat,
    #: and a deliberate one — the postings have turned over since yesterday — is a decision the
    #: record should carry rather than something the system pretends cannot happen.
    rerun_spent: str = ""


@router.post("/api/session_control/{session_id}/initialize")
async def initialize(session_id: int, body: InitializeBody,
                     db: Session = Depends(get_db)) -> dict[str, Any]:
    """Declare what this session is searching for — the first search, or the next one.

    THIS USED TO REFUSE A SECOND QUERY and tell the operator to start a new session. That was the
    once-only rule applied one level too high: re-running THE SAME query is what makes Indeed
    collapse results, and a *different* query is simply new work. Refusing it meant abandoning a
    query cost an authenticated browser — close Chrome, provision another, sign in again, all to
    change a word in a search box. Operator, 2026-08-06: *"we aren't exiting Indeed and we
    shouldn't… sessions are active until we end them, querying and re-querying shouldn't end a
    session."*

    So: a different query STARTS A NEW SEARCH inside this session. The session rungs
    (`provisioned`, `authenticated`) stay held — that is the whole saving — while the search-scoped
    rungs begin again under a new scope. The previous search's rungs stay in the ledger, which is
    what keeps the promise real: **a query this session has already spent is still refused.**
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

    started_search = 0
    spent = " ".join((bb.search_state.query or "").split())
    # BACKFILL, so the guard works on ledgers written before `queries` existed. The blackboard has
    # always known the current search's query; the ledger just never wrote it down. Doing it here
    # self-heals every pre-existing session on first touch, and costs nothing when it already knows.
    # Checked against the STRUCTURED record, not `spent_queries()` — that falls back to the rung's
    # evidence prose, which is never empty, so the backfill would never fire and the guard would
    # keep comparing against a sentence.
    if spent and ledger.holds("query_entered") and not ledger.queries.get(ledger.search):
        ledger.note_query(spent)

    # RUNNING THE SAME TERMS AGAIN, ON PURPOSE (operator-directed 2026-08-14).
    #
    # `query_entered` is CONSUMING because repeating a query TOO OFTEN is what makes the board
    # cache and collapse it — results we already saw stop coming back. That is a rule about
    # frequency, and it was enforced as a rule about ever: the same terms a day later, when the
    # postings have turned over, is the most ordinary thing a job search does, and the system's
    # answers were a silent no-op (same search still current) or a flat 409 (a different search
    # spent it). Neither produces the fresh page the operator asked for.
    #
    # So the guard keeps the job it is actually good at — refusing the ACCIDENTAL repeat, the
    # double-press, the loop that re-searches to "finish properly" — and a deliberate re-run is a
    # price with a reason attached, exactly like `release_open` below. It starts a NEW SEARCH
    # (its own ordinal, its own results, its own picks), because that is what it is: the same
    # question asked again, not the previous answer revisited.
    rerun = " ".join((body.rerun_spent or "").split())
    if ledger.has_spent(query) and not rerun:
        if spent.lower() == query.lower():
            pass                                    # same query, same search — idempotent
        else:
            # THE RULE THAT SURVIVES, and it is a SESSION-WIDE question — deliberately checked
            # before anything about the current search. Nested under `holds("query_entered")` it
            # stopped running the moment a new search was declared but not yet run, which is
            # precisely when somebody would try to go back to the query they just left.
            ran_in = [n for n, q in ledger.spent_queries().items()
                      if q and (q.lower() == query.lower() or query.lower() in q.lower())]
            raise HTTPException(
                status_code=409,
                detail=(f"This session already ran {query!r} (search "
                        f"{', '.join(map(str, ran_in))}). Re-running the same query is what makes "
                        f"{engine['label']} collapse results — go back to those results instead, "
                        f"or say why you want it run again (`rerun_spent`) and it starts a new "
                        f"search."))
    elif spent.lower() == query.lower() and not ledger.holds("query_entered"):
        pass                    # declared but never run — re-pointing at the same terms is free
    elif ledger.holds("query_entered"):
        # A DIFFERENT QUERY, and this search has already spent its own. New search, same session,
        # same sign-in. (A search that has been DECLARED but not yet run is simply re-pointed —
        # nothing was spent, so there is nothing to preserve and no ordinal to burn.)
        #
        # STEPPING BACK IS A DECISION, AND IT USED TO BE PRICED WRONG IN BOTH DIRECTIONS.
        # The old guard was `queue.current() is not None` — the first step that has not reached a
        # terminal flag — which is true of a pick NOBODY HAS OPENED. So a session holding four
        # untouched candidates refused a new search with "…is still open in this search", naming a
        # job that had never been driven, over a cost of exactly nothing; and it said "finish it or
        # flag it" while the cockpit offered no way to do either from that moment. A truthful
        # refusal the operator cannot act on is still a dead end (2026-08-13).
        #
        # Priced by what is actually there instead: an application somebody has DRIVEN is worth
        # stopping for, an unopened pick is not.
        reason = " ".join((body.release_open or "").split())
        cost = _step_back_cost(bb)
        if cost["worked"] and not reason:
            names = ", ".join(w["title"] or w["job_id"] for w in cost["worked"])
            raise HTTPException(
                status_code=409,
                detail=(f"{names} {'has' if len(cost['worked']) == 1 else 'have'} real work in "
                        f"{'this' if len(cost['worked']) == 1 else 'them this'} search, and "
                        f"starting a new one would leave "
                        f"{'it' if len(cost['worked']) == 1 else 'them'} behind. Say why you are "
                        f"stepping back (`release_open`) and "
                        f"{'it is' if len(cost['worked']) == 1 else 'they are'} parked with that "
                        f"reason — still resumable, never silently dropped. Or finish "
                        f"{'it' if len(cost['worked']) == 1 else 'them'} first."))
        # PARKED, NOT DISCARDED. `parked:operator` is precisely "your call — not now, come back to
        # it", so the harvest below carries these into `parked_apps` and `apply_reopen` can bring
        # them back. Flagged BEFORE the reset, because the harvest is what reads the flag.
        if cost["worked"]:
            queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
            for step in queue.steps:
                if any(w["job_id"] == step.job_id for w in cost["worked"]):
                    step.finish(aps.PARKED_OPERATOR,
                                f"Stepped back to search for {query!r}: {reason}")
            bb.world = dict(bb.world or {})
            bb.world["apply_queue"] = queue.as_dict()

        leaving = ledger.search
        started_search = ledger.start_new_search()
        released = _reset_for_new_search(bb, leaving_search=leaving)
        # THE STEP BACK, ON THE RECORD, WITH WHAT IT COST AND WHAT COMES NEXT. The old line said
        # only that one query became another; four chosen candidates left the queue underneath it
        # and the timeline had no idea. Counted from the released bill rather than re-derived, so
        # what is journaled is what actually happened.
        kept = len(released["parked"]) + len(cost["worked"])
        # `'x' -> 'x'` is not a legible way to say "the same terms, asked again" — the one case
        # where the arrow notation lies about what happened.
        move = (f"re-running {query!r}" if spent.lower() == query.lower()
                else f"{spent!r} -> {query!r}")
        bb.log(
            "search_step_back",
            f"search {leaving} -> {started_search}: {move} "
            f"(same session, still signed in). Released "
            f"{len(released['unworked'])} unworked pick(s)"
            + (f": {', '.join(u['title'] or u['job_id'] for u in released['unworked'])}"
               if released["unworked"] else "")
            + (f". Parked {len(cost['worked'])} with work in "
               f"{'it' if len(cost['worked']) == 1 else 'them'}" if cost["worked"] else "")
            + (f". {kept} application(s) kept on the ledger" if kept else "")
            + (f". {released['submitted']} already submitted" if released["submitted"] else ""),
            # BOTH REASONS WHEN BOTH REFUSALS WERE PAID. Re-running spent terms and releasing
            # driven work are two different decisions that can be taken in one press, and a `why`
            # that reported only one of them would leave the other unexplained on the record.
            why=" · ".join(filter(None, [
                rerun and f"re-running spent terms deliberately: {rerun}",
                reason and f"released open work: {reason}",
            ])) or (f"The operator is searching for something else; search {leaving} held no "
                    f"driven application, so leaving it costs only picks nobody had opened."),
            next_up=(f"Run {query!r} as search {started_search} on the same signed-in browser, "
                     f"apply the distance filter, read page 1, and take a FRESH selection. "
                     + (f"Search {leaving}'s picks do not carry over: the same terms return a "
                        f"result set that has turned over since, so they were chosen off cards "
                        f"that may no longer be on the page."
                        if spent.lower() == query.lower() else
                        f"The picks from {spent!r} do not carry over, because they were chosen "
                        f"off results that are no longer on screen.")))

    bb.search_state.query = query
    # WHICH QUERY THIS SEARCH IS SPENDING, on the ledger and as a fact rather than as prose. This
    # is what `has_spent` checks, so the once-only guard survives any rewording of the evidence.
    ledger.note_query(query)
    bb.search_state.location = " ".join((body.location or "").split())
    bb.goal = (f"Search {engine['label']} for {query!r}"
               + (f" in {bb.search_state.location}" if bb.search_state.location else "")
               + " — review page by page")
    bb.world = dict(bb.world or {})
    bb.world["radius_miles"] = max(int(body.radius_miles or 50), 50)
    bb.log("initialize", f"session declared for {query!r} "
                         f"({bb.search_state.location or 'anywhere'}) by {body.initiator}",
           # DECLARED IS NOT RUN, and the gap between them is a state the timeline has to name:
           # nothing has been spent yet, so the once-only guard does not apply and a mis-typed
           # query is still free to change. Saying so is what stops a reader (or a rung) treating
           # a declaration as a search that happened.
           next_up=(f"Nothing spent yet — the next crank submits {query!r} to "
                    f"{engine['label']} and only then is it a search that cannot be re-run."))
    # Remember the target across sessions so the cadence and the panel agree on what we search.
    jst.add_target(query, bb.search_state.location, radius_miles=bb.world["radius_miles"])

    obs = await _observe(_session_browser_url(session), bb)
    _persist(bb, ledger)
    return _view(session, bb, ledger, obs, page=bb.search_state.page or 1)


def _freshest_snapshot(session_id: Any,
                       cached: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The Lens's snapshot, from whichever source is newest.

    The blackboard's `last_belief` only updates on ladder/StepRunner turns — a controller drive
    journals its captures instead, so after one the Lens showed a days-old visual as if it were
    current (found 2026-08-10: snapshot from 08-06 after a morning of attended driving). The
    journal is the display's source (§10): if it holds a fresher capture for this session, serve
    that — belief omitted rather than faked, which the pane renders as "not measured"."""
    from interaction import decision_journal
    try:
        rows = [r for r in decision_journal.read_rows(limit=200)
                if str(r.get("session_id")) == str(session_id) and r.get("capture_screenshot")]
        if not rows:
            return cached
        newest = rows[-1]
        if cached and str(cached.get("ts") or "") >= str(newest.get("ts") or ""):
            return cached
        return {"url": newest.get("url", ""), "ts": newest.get("ts", ""), "belief": None,
                "artifact": newest.get("capture_artifact"),
                "screenshot_filename": newest.get("capture_screenshot")}
    except Exception:  # noqa: BLE001 — the Lens is an aid; a read hiccup must not break the panel
        return cached


def _cache_belief(bb: Any, observation: Any) -> Optional[dict[str, Any]]:
    """Remember the belief this step took, keyed to the page it was taken on.

    The panel re-orients on every heartbeat and never runs the perception stack itself (a
    screenshot per poll is exactly the cost this system refuses). So the step that DOES take one
    leaves it here for the poll to render, and `_orient_now` only reuses it while the tab is
    still on that url. Returns the belief so the caller can pass it straight through.
    """
    belief = getattr(observation, "belief", None)
    url = getattr(observation, "url", "") or ""
    screenshot = getattr(observation, "screenshot", None) or ""
    artifact = getattr(observation, "artifact", None) or ""
    # Keep the safely collected visual even when no promoted local observer was loaded. A
    # screenshot only exists here for collect=True turns; credential-flow observations use
    # collect=False and therefore cannot leak into the Cockpit lens.
    if url and (belief or screenshot or artifact):
        bb.world = dict(bb.world or {})
        bb.world["last_belief"] = {
            "url": url,
            "ts": getattr(observation, "ts", ""),
            "belief": belief,
            "artifact": Path(artifact).name if artifact else None,
            "screenshot_filename": Path(screenshot).name if screenshot else None,
        }
    return belief


def _learned_witnesses(bb: Any, url: str, belief: Optional[dict[str, Any]],
                       page_text: str) -> list:
    """The perception witnesses for THIS page, at the right cost for each one.

    The two witnesses have very different prices, so they are sourced differently rather than
    being treated as one blob:

      * **the DOM witness is free** — it reads url + page text, and `/page_content` has just
        fetched both. So it is recomputed on every poll and is always current.
      * **the visual witness needs a SCREENSHOT**, which is the one thing too expensive to take
        on a heartbeat. It is reused from whatever the last acting step captured, and only while
        the tab is still on the url that belief was taken on.

    Without this the learned witnesses reached exactly one response — the step's — while the
    panel re-orients constantly, so they were computed always and rendered never (found the
    moment the card was first opened, 2026-08-04). A witness the operator cannot see is back to
    being shadow.
    """
    import orientation

    witnesses = []
    seen: set[str] = set()
    if belief is None:
        try:
            from perception import live as perception_live
            belief = perception_live.sense(url=url, page_text=page_text)
        except Exception:  # noqa: BLE001 — perception is an aid, never a dependency
            belief = None
    for w in orientation.perception_witnesses(belief):
        witnesses.append(w)
        seen.add(w.source)

    cached = (bb.world or {}).get("last_belief") or {}
    if cached.get("belief") and cached.get("url") == url:
        for w in orientation.perception_witnesses(cached["belief"]):
            if w.source in seen:
                continue                      # the fresh reading wins over the remembered one
            w.detail += f" (from the last capture, {cached.get('ts', '')[11:19]})"
            witnesses.append(w)
    return witnesses


async def _orient_now(bb: Any, obs: dict[str, Any], browser_url: str,
                      belief: Optional[dict[str, Any]] = None) -> Optional[dict]:
    """One observation of the live APPLY tab, fused into a verdict — or None when no apply is open.

    This runs on EVERY panel render (the poll is the heartbeat, so the observer fires constantly
    while the operator is looking) and after every apply_step. One /page_content call on a local
    CDP socket — free in low-data mode, and the same read the classify rung already makes.

    `belief` is the perception stack's reading of the SAME moment — the DOM and Apple-Vision
    witnesses the StepRunner already computed for this step. Passing it is what finally fills
    `orient()`'s `extra_witnesses` seam, which has been designed, documented and empty since the
    fusion was written: the belief went into the transition corpus and the observer that most
    needed it never saw it. Omitted (the poll path, where no step just ran) the fusion behaves
    exactly as before — deterministic witnesses only.
    """
    import orientation
    from apply_recipe import kind_of_state as _ar_kind_of_state
    from ats_registry import ats_for_company

    queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
    # A parked application is still THE work — its tab is open and resumable, and the observer
    # going dark on parked is how the Lens read "not watching an application" over a live
    # smartapply tab (2026-08-10).
    step = queue.current() or _parked_step(queue)
    if step is None:
        return None
    url = _apply_tab_url(bb, obs)
    if not url:
        return None
    try:
        content = await _capture_post("/page_content", {"browser_url": browser_url,
                                                        "tab_url": url}, timeout=12.0)
    except Exception:  # noqa: BLE001 — an unreadable tab is an abstaining witness, not a crash
        content = {}
    # ORIENT AGAINST THE RUNG THAT WILL ACTUALLY RUN. `next_rung` can name one the discovery has
    # ruled out — `account` on Indeed quick apply — and orienting against it manufactures a
    # mismatch for a rung the crank would skip without ever asking. A false disagreement is worse
    # than none: the arbitration below hands the wheel to the observer on the strength of it.
    nr, _ruled_out = step.walk_to_next_rung()
    o = orientation.orient(
        url,
        page_text=content.get("text") or "",
        frames=content.get("frames") or [],
        apply_hrefs=content.get("apply_hrefs") or [],
        # NO RUNG MEANS NO PREMISE — never a defaulted one. This fell back to "submit", so the
        # moment the ladder had nothing to offer (an unmapped ATS whose landing the tail could
        # not place) the orient was scored against submit's needs and MANUFACTURED the exact
        # false mismatch the note above warns about: "the `submit` rung needs an application
        # form, but the page is a job posting" — on a posting nobody had tried to submit
        # (measured live 2026-08-11, Cornerstone). An empty rung asks the observer what the page
        # IS without pretending the recipe had a claim about it.
        rung=(nr.id if nr else ""),
        company=step.company or "",
        ats_lookup=ats_for_company,
        known_recipe=(step.platform or "") in DRIVEN_PLATFORMS_VIEW,
        # WHAT THE RECORD CLAIMS THE SCREEN IS, so the observation can contradict it. The two speak
        # different vocabularies — the spine walks `workday_my_information`, the observer answers
        # in generic kinds — so the translation happens here and `orientation` stays pure. "" when
        # the state cannot be placed, which reads as NO CLAIM rather than as agreement.
        recorded_kind=_ar_kind_of_state(step.platform, step.landing_state),
        # THE SPINE-PRECISE READING, when the page actually stated its own step. Gated on
        # `observed` so a URL-only default cannot promote itself into the verdict — that guess
        # wears an ordinary state's name and is exactly what stalled the ladder on 08-16.
        precise_state=_precise_state_from(step.platform, url, content.get("text") or ""),
        # THE LEARNED WITNESSES, joining the fusion at last. They claim a platform (their measured
        # strength) and abstain at the novelty ceiling, so a witness announcing "I have never seen
        # this page" is rendered without being allowed to vote.
        extra_witnesses=_learned_witnesses(bb, url, belief, content.get("text") or ""),
    )
    out = o.as_dict()
    out["url"] = url[:200]
    # TEACH WHILE DRIVING. The verdict is written to the orientation corpus when the SITUATION
    # changes — the features a perception witness will train on are exactly what was just fused,
    # and reconstructing them later is the backfill that L3/L4 already proved impossible. The log
    # dedupes by fingerprint, so a parked tab polled for an hour contributes one row, not hundreds.
    try:
        import orientation_log
        orientation_log.record(getattr(bb, "session_id", "") or "", out,
                               step_job_id=step.job_id, rung=(nr.id if nr else "submit"))
    except Exception:  # noqa: BLE001 — the corpus must never break the panel
        pass
    return out


#: Platforms with an end-to-end recipe, for the observer's plan wording.
#:
#: Was a hand-kept COPY of `apply_steps.DRIVEN_PLATFORMS`, justified as "without importing the
#: executor's policy into a read path" — but `apply_steps` is imported at the top of this module
#: anyway, so the copy bought nothing and could only drift out of step with the set that actually
#: decides. Aliased, so "have we driven this?" has one answer.
#:
#: Deliberately NOT extended with `linkedin` while adding LinkedIn parity: we have never driven an
#: Easy Apply to submission (`linkedin_recipe.EASY_APPLY` is UNVERIFIED, and the registry recipe is
#: still `seed`). This set is a claim about measurement, and the LinkedIn search side being ready
#: is not evidence about its apply side.
DRIVEN_PLATFORMS_VIEW = aps.DRIVEN_PLATFORMS


class OrientNowBody(BaseModel):
    initiator: str = "operator"


@router.post("/api/session_control/{session_id}/orient_now")
async def orient_now(session_id: int, body: OrientNowBody,
                     db: Session = Depends(get_db)) -> dict[str, Any]:
    """LOOK, deliberately: re-read the application tab with every witness and answer "where are
    we" with the scored candidates — the lost-state's primary action (operator, 2026-08-10:
    *"we don't know what's going on but we are in the application process, maybe we need to
    orient"*). Read-only on the page; each run also writes an orientation trial, so pressing it
    on confusing ground is literally the orienter practising.
    """
    _check_initiator(body.initiator)
    session, bb, ledger = _load(session_id, db)
    browser_url = _session_browser_url(session)
    obs = await _observe(browser_url, bb, session_id=session.id)
    observer = await _orient_now(bb, obs, browser_url)
    if observer is None:
        return _view(session, bb, ledger, obs, page=_current_page(obs, bb),
                     last={"ok": False, "action": "orient",
                           "detail": "No application tab to orient on — nothing was read."})
    win = observer.get("witnesses") or []
    named = ", ".join(f"{w.get('source')}: {w.get('claim') or 'abstains'}" for w in win[:4])
    return _view(session, bb, ledger, obs, page=_current_page(obs, bb), awaiting="apply",
                 observer=observer,
                 last={"ok": True, "action": "orient", "whereabouts": observer,
                       "detail": (f"Oriented: {observer.get('headline')} "
                                  f"({observer.get('confidence')} confidence)"
                                  + (f" — {named}" if named else ""))})


class OrientActionBody(BaseModel):
    action_id: str
    initiator: str = "operator"


@router.post("/api/session_control/{session_id}/orient_action")
async def orient_action(session_id: int, body: OrientActionBody,
                        db: Session = Depends(get_db)) -> dict[str, Any]:
    """Take one of the observer's OFFERED actions — the card's buttons.

    Operator, 2026-07-30: *"the observer should give us options as to hit the apply now button on
    the job landing page"*. Orientation already works out where we are and what one or two moves
    lead out; this is the half that lets the operator take the move without leaving the panel.

    It re-orients FIRST and refuses an action the current page does not offer. That is the whole
    safety property: the buttons were rendered from an observation that is now some seconds old,
    and a third-party landing can move underneath it (a redirect, a session timeout, an interstitial
    the employer serves once). Acting on a stale plan is precisely the drift this module exists to
    end, so the plan is recomputed and the request is checked against it rather than trusted.
    """
    _check_initiator(body.initiator)
    import orientation as om

    session, bb, ledger = _load(session_id, db)
    browser_url = _session_browser_url(session)
    obs = await _observe(browser_url, bb, session_id=session.id)
    before = await _orient_now(bb, obs, browser_url)
    if not before:
        raise HTTPException(status_code=409,
                            detail="No application tab is open, so there is nothing to orient in.")
    offered = {st["id"] for st in (before.get("plan") or []) if st.get("driveable")}
    if body.action_id not in offered:
        raise HTTPException(
            status_code=409,
            detail=(f"{body.action_id!r} is not on offer here. The page reads as a "
                    f"{(before.get('kind') or 'unknown').replace('_', ' ')} and what it offers is: "
                    f"{', '.join(sorted(offered)) or 'nothing driveable — this one is yours'}."))

    queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
    step = queue.current()
    style = xs.pick_style()
    detail = ""
    verification: Optional[dict[str, Any]] = None   # set when a wrapped step actually acted

    if body.action_id == om.REORIENT:
        detail = (f"Re-read the page: {(before.get('state') or 'unknown').replace('_', ' ')}"
                  f" ({before.get('confidence')} confidence).")

    elif body.action_id == om.PRESS_APPLY:
        # ADDRESS THE CONTROL BY WHERE IT GOES, not by what it is called. A careers front's apply
        # control is the link whose href is the ATS destination — that href is already the
        # `signpost` witness, so the thing that identified the platform also locates the button.
        # Names vary per employer ("APPLY NOW", "Apply", "Start your application"); the destination
        # does not.
        hrefs = [w.get("detail", "") for w in (before.get("witnesses") or [])
                 if w.get("source") == "signpost"]
        content = await _capture_post("/page_content",
                                      {"browser_url": browser_url,
                                       "tab_url": before.get("url") or ""}, timeout=12.0)
        target = (content.get("apply_hrefs") or [""])[0]
        if not target:
            raise HTTPException(status_code=409,
                                detail="The page reads as a posting but exposes no apply link to "
                                       "press. Look at it — this one is yours.")
        host = (urlparse(target).hostname or "")
        await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))

        async def _press() -> dict[str, Any]:
            res = await _capture_post("/execute", {
                "browser_url": browser_url, "tab_url": before.get("url") or "",
                "action_id": "click", "target_bbox": {},
                "selector": f'a[href*="{host}"]', "driver": "humanized"})
            await asyncio.sleep(xs.pause_for(style, xs.NAVIGATION))
            return res

        # THE STEPRUNNER WRAPS THE PRESS. The expectation is unusually strong here: the apply
        # href already NAMES the destination host, so "did it work" is "did a tab open or
        # navigate there" — the same signpost that identified the platform verifies the click.
        import step_runner as sr
        report = await sr.run_step(
            _press,
            action={"action": om.PRESS_APPLY, "target_host": host, "initiator": body.initiator},
            expect=(sr.Expectation(kind="new_tab_or_nav", hosts_hint=(host,)) if host
                    else sr.Expectation(kind="content_changed")),
            capture_post=_capture_post, browser_url=browser_url,
            tab_url=before.get("url") or "", session_id=session.id, rung_id="orient")
        res = report.result
        verification = {**report.verification(), "rung": "orient"}
        if res.get("outcome") not in _ACTED_OK:
            detail = (f"Could not press the apply link ({res.get('outcome') or 'no outcome'}). "
                      f"Nothing moved — the page is still a posting.")
            if step is not None:
                step.record("orient", aps.FAILED, detail, initiator=body.initiator)
        elif report.demotes:
            # The click dispatched; the world never gained the destination. A dispatched click on
            # a landing that swallowed it is exactly the drift this endpoint exists to catch.
            detail = (f"Pressed the apply link, but {report.evidence}. The rung stays open — "
                      f"press again, or look at the window.")
            if step is not None:
                step.record("orient", aps.MISMATCH, f"world disagrees: {report.evidence}",
                            initiator="step_runner")
        else:
            detail = f"Pressed the apply link → {host}."
            if step is not None:
                step.record("orient", aps.OK, detail, initiator=body.initiator)

    # RE-OBSERVE AFTER ACTING, ALWAYS. The action's own `ok` means CDP dispatched it; where we
    # ended up is a separate question, and it is the only one worth reporting.
    # THE OPERATOR JUST LABELLED THE VERDICT. Pressing an action the observer proposed is a
    # confirmation; anything else is a correction, and a labelled mistake is the most valuable row
    # in the corpus. This is the teacher-correction signal at the observer's altitude.
    try:
        import orientation_log
        orientation_log.resolve(session.id, action_id=body.action_id,
                                agreed=body.action_id in offered)
    except Exception:  # noqa: BLE001
        pass

    obs = await _observe(browser_url, bb, session_id=session.id)
    return await _save_queue_and_view(session, bb, ledger, queue, obs, ok=True, pace=style,
                                      detail=detail, verification=verification)


# --- the read model ------------------------------------------------------------------------------
@router.get("/api/session_control/{session_id}")
async def get_panel(session_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """The panel's view. READ-ONLY — probes the tabs and auth state (a local CDP socket, free
    even in low-data mode) and drives nothing."""
    session, bb, ledger = _load(session_id, db)
    browser_url = _session_browser_url(session)
    obs = await _observe(browser_url, bb, session_id=session.id)
    page = _current_page(obs, bb)
    return _view(session, bb, ledger, obs, page=page,
                 observer=await _orient_now(bb, obs, browser_url))


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

    obs = await _observe(browser_url, bb, session_id=session.id)
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

    # THE STEPRUNNER WRAPS THE DISPATCH (PLAN_step_runner.md): observe before → act → observe
    # after → diff → verify → record. The ladder's own evidence rules stay exactly as they are —
    # each branch still marks its rung only on proof — but the pair + diff + verdict now lands in
    # the transition corpus for every crank of the ladder, and the verdict rides back on the
    # response. NO AUTO-RELEASE on mismatch here: `query_entered` is the CONSUMING rung, and a
    # verifier false-alarm that reopened it would invite the exact double-spend the ladder
    # forbids. Disagreement is surfaced to the operator, never spent.
    import step_runner as sr
    _tab_id = ((obs.get("search_tab") or (obs.get("tabs") or [{}])[0]) or {}).get("tab_id", "")
    _report = await sr.run_step(
        lambda: _dispatch(nxt, session=session, bb=bb, ledger=ledger, obs=obs,
                          browser_url=browser_url, page=page, initiator=body.initiator, db=db),
        action={"action": nxt.checkpoint.action, "checkpoint": nxt.checkpoint.id,
                "initiator": body.initiator},
        expect=sr.expectation_for_checkpoint(nxt.checkpoint.action, query=query),
        capture_post=_capture_post, browser_url=browser_url, tab_id=_tab_id,
        session_id=session.id, rung_id=nxt.checkpoint.id)
    result = _report.result
    result["verification"] = _report.verification()
    if _report.demotes:
        result["detail"] = (str(result.get("detail") or "") +
                            f" — but the verifier disagrees: {_report.evidence}. The rung is NOT "
                            f"reopened automatically (it may be consuming); check the browser.")
    _persist(bb, ledger)

    # RE-OBSERVE AFTER ACTING. `obs` was taken BEFORE the dispatch, so reusing it renders the
    # world as it was when we decided, not as the action left it. That reads as a contradiction
    # the instant a rung succeeds: `run_query` marks `query_entered` on proof, the stale
    # observation still says False, and `next_step` puts the two together as "held but its effect
    # is gone" — telling the operator to RECOVER from a search that had just worked perfectly
    # (seen live 2026-07-24). Every other endpoint here already re-observes; step was the
    # exception, and the `observed_delta` hook it used instead was never populated by anything.
    # Observing is a local CDP socket, so this costs nothing but a round trip.
    obs_after = await _observe(browser_url, bb, session_id=session.id)
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
        if not obs["observed"].get("provisioned") and obs.get("reachable") and not tabs:
            # OPEN THE FRONT DOOR OURSELVES. A freshly provisioned session Chrome holds zero
            # page targets, and this rung used to stop there and ask a human to open a tab by
            # hand — which is the 2026-07-25 "nobody ever opened the front door" gap, fixed one
            # rung too high up: `auth_probe` learned to navigate to the engine's home page, but
            # `provisioned` gates it and never let it run (found live 2026-08-04, both session
            # browsers up with no tabs, the whole ladder unclimbable).
            #
            # Opening a site's HOME PAGE is not the URL-forcing §3 warns about — that rule is
            # about jumping into a DEEP state we should have clicked our way to. There is nothing
            # to click on an empty window, and typing indeed.com is exactly what a person does
            # first. Every deep state after this is still reached by clicking.
            #
            # ONLY when the window is EMPTY. With tabs present a non-provisioned reading means
            # something else is wrong, and opening another tab would be tab churn on a live
            # session (bot-safety).
            style = xs.pick_style()
            nav = await _capture_post("/navigate", {
                "browser_url": browser_url, "url": engine["home"], "settle_seconds": 3.0})
            await asyncio.sleep(xs.pause_for(style, xs.NAVIGATION))
            if nav.get("ok"):
                bb.log("nav", f"opened {engine['label']}'s home page in an empty window "
                              f"({style.name} pace)")
                obs = await _observe(browser_url, bb, session_id=session.id)
                tabs = obs.get("tabs") or []
        if not obs["observed"].get("provisioned"):
            detail = ("Session Chrome is up but has no tabs open, and this session could not open "
                      "one. Open the job site in that window, then step again."
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
                # A tab that is SAFE to point somewhere else — the engine's own page first, then
                # any non-apply tab. tabs[0] can be a parked application's tab, and navigating
                # that to the engine home would destroy real half-finished work.
                "tab_id": ((obs.get("search_tab") or _engine_page_tab(obs.get("tabs") or [])
                            or _navigable_tab(obs.get("tabs") or [])
                            or (obs.get("tabs") or [{}])[0]) or {}).get("tab_id", ""),
                "settle_seconds": 3.0})
            await asyncio.sleep(xs.pause_for(style, xs.NAVIGATION))
            if not nav.get("ok"):
                bb.log("handoff", f"could not open {engine['label']} — {str(nav.get('detail'))[:90]}")
                return {"ok": False, "action": action, "awaiting": "operator_open_engine",
                        "detail": f"No {engine['label']} tab was open and this session could not "
                                  f"open one ({str(nav.get('detail') or 'no detail')[:120]}). The "
                                  f"rung is left as it was rather than guessed."}
            bb.log("nav", f"opened {engine['label']}'s home page to probe sign-in ({style.name} pace)")
            obs = await _observe(browser_url, bb, session_id=session.id)

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
        # AN ENGINE WITHOUT A DISTANCE CONTROL CANNOT HOLD A DISTANCE RUNG. Operator-directed
        # 2026-07-30 after this blocked the whole preamble on LinkedIn — and the preamble gates
        # `choose`, so a rung about a control that does not exist was standing between us and
        # selecting jobs to apply to.
        #
        # It is marked SKIPPED-with-a-reason, never "set to 50mi": the floor exists so we never
        # gather a 5-mile Indeed radius by accident, and quietly recording a filter we did not
        # apply would retire that guarantee instead of scoping it. What geography LinkedIn is
        # actually searching is a real open question — its location button reads "Greater Boston" —
        # and it stays open, in writing, rather than being answered by a rung that lied.
        if not engine.get("distance_filter", True):
            ledger.mark("radius_set",
                        evidence=(f"not applicable on {engine['label']}: the results page exposes "
                                  f"no distance control. Geography comes from the location filter, "
                                  f"which is a metro area — NOT a {miles}mi radius we set."),
                        initiator=initiator)
            bb.log("checkpoint", f"radius_set — skipped: {engine['label']} has no distance filter")
            return {"ok": True, "action": action, "skipped": True,
                    "detail": (f"{engine['label']} has no distance filter, so there is nothing to "
                               f"set. Recorded as not-applicable rather than as a radius we "
                               f"applied. Its location filter carries the geography.")}
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

#: How long a committed search gets to SHOW ITS RESULTS before we conclude it did not take, and
#: how often we look while waiting. This budget buys re-READS only — a local CDP tab list, which
#: spends no query and dispatches nothing — so it is generous on purpose: the alternative is
#: concluding "nothing happened" about a search that worked, on a rung that may not be re-run.
_SUBMIT_SETTLE_S = 12.0
_SUBMIT_POLL_S = 0.75


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
    # THE ENGINE'S PAGE, never "the first tab". A session with a parked application keeps that
    # application's tab open (deliberately), and it is frequently the window's frontmost — so
    # tabs[0] pointed the query rung at a smartapply screener, which it scanned and honestly
    # reported had no search box (live, drive 2, 2026-08-10: "67 elements scanned"). And
    # `search_tab` alone is not enough: it is query-bound, so on a freshly declared NEW query it
    # is rightly None while the engine's results page (previous query) sits open — the exact tab
    # a human would type the new query into.
    tabs = obs.get("tabs") or []
    tab_id = ((obs.get("search_tab") or _engine_page_tab(tabs)
               or (tabs or [{}])[0]) or {}).get("tab_id", "")

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

    async def _submit_and_confirm(how: str = "") -> tuple[bool, Optional[dict], bool, str]:
        """Commit the search, then ask the PAGE whether it took.

        Returns (clicked, results_tab, page_moved, why). `page_moved` is the half that matters for
        deciding whether a retry is even allowed: `/execute` is a tier-1 primitive and says so in
        its own docstring — its `ok` means the node resolved and CDP dispatched without throwing,
        NOT that the page accepted the action. Confirming is this tier-2 caller's job.

        `how` overrides the engine's declared commit method — see the alternating retry below.
        """
        before = await _tab_urls()
        # COMMIT THE WAY THIS ENGINE COMMITS. `submit` on the query box dispatches Enter to the
        # focused element (the fill just focused it) — which is the only way in on an engine with
        # no submit control, and is what the operator's recording measured LinkedIn doing.
        # A COMMIT METHOD AN ENGINE HAS NO CONTROL FOR IS NOT A FALLBACK, IT IS A CRASH. The
        # alternating retry below hands us "the other method" on the theory that we do not know
        # which one an engine needs — true for Indeed, which has both a Search button and Enter.
        # LinkedIn was MEASURED to have no submit button at all (`SUBMIT_NAME_HINTS = ()`), so
        # `controls` legitimately carries only `query`, and reaching for `controls["submit"]`
        # raised KeyError mid-drive (live 2026-08-14, session #29 — the first LinkedIn run of this
        # rung). Refuse in words rather than by exception: the caller's whole contract is that a
        # commit either lands or explains itself.
        method = how or commit_by
        if method != "enter" and "submit" not in controls:
            return False, None, False, (
                f"{engine['label']} has no submit control on this page, so it cannot be committed "
                f"by button — Enter on the query box is its only commit")
        if method == "enter":
            ok, detail = await _act("submit", controls["query"])
        else:
            ok, detail = await _act("click", controls["submit"])
        if not ok:
            return False, None, False, detail
        # WAIT FOR THE NAVIGATION; DO NOT RACE IT. This used to sleep one navigation pause and
        # read the tab list ONCE — a verification that can finish before the thing it verifies.
        # Measured live 2026-08-13 (session #28, "report analyst"): the Enter commit DID submit and
        # the results page DID load, but the single read happened while it was still in flight, so
        # this returned `moved=False, tab=None`. The caller then did exactly the right thing with
        # the wrong facts — refused to mark the CONSUMING rung, refused to retry — and the run
        # stalled one observation short of a search that had worked.
        #
        # Polling is safe in the way that matters here: `/list_tabs` is a READ over a local CDP
        # socket, so it spends no query, costs no data, and cannot dispatch anything. Only the
        # commit above can act, and it has already happened. The `not moved` retry gate upstream
        # keeps its exact meaning — it just gets a fair look before it fires.
        # The first wait is the engine's own navigation pace (this drive stays human-paced); every
        # look after that is just re-reading what is already there.
        await asyncio.sleep(xs.pause_for(style, xs.NAVIGATION))
        deadline = time.monotonic() + _SUBMIT_SETTLE_S
        while True:
            res = await _capture_post("/list_tabs", {"browser_url": browser_url}, timeout=8.0)
            tabs_after = res.get("tabs") or []
            moved = [t.get("url", "") for t in tabs_after] != before
            tab_after = _find_search_tab(tabs_after, query)
            if tab_after is not None or time.monotonic() >= deadline:
                return True, tab_after, moved, ""
            await asyncio.sleep(_SUBMIT_POLL_S)

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

    if tab is None and not moved:
        # STILL NOTHING. Two identical clicks that both dispatch and both move nothing are not a
        # staged widget — they are a form whose own state disagrees with what is on screen.
        #
        # Measured live 2026-08-06, the first time a session ever ran a SECOND search: both fields
        # visibly held "data analytics" / "Boston, MA", the Search button dispatched trusted, and
        # the page did not move — because the fill sets the value authoritatively (it has to; per-
        # char typing races React) and React's controlled input never saw an onChange. So the form
        # submitted the state it still believed in, which was the query already in the URL, and
        # navigating to where you already are looks exactly like nothing happening.
        #
        # ENTER GOES TO THE ELEMENT, NOT THE FORM. A keydown on the focused input is what a person
        # does, and it is handled by the input's own listener rather than by React's submit state.
        # Alternating is honest about not knowing which engine needs which: try the engine's
        # declared method, then the other one, and stop. Still gated on `not moved`, so this can
        # never double-spend a search that actually went through.
        other = "enter" if commit_by != "enter" else "button"
        bb.log("run_query", f"still nothing after two {commit_by} commits — committing by "
                            f"{other} instead")
        await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))
        clicked, tab, moved, why = await _submit_and_confirm(other)

    # WHERE WE ARE, NOT WHAT CHANGED. This was gated on `moved`, which asks whether the last
    # commit navigated — a question that answers "no" when we are ALREADY standing on the blended
    # page from an earlier attempt, and the way on is then never taken (live 2026-08-14, session
    # #29: the run sat on `/search/results/all/` re-committing the same query into the same URL,
    # so nothing ever moved and Route B could not fire). Standing on the blended search is a FACT
    # about the world; it does not stop being true because the last action did not cause it.
    # Scoped to LinkedIn below, exactly as before, so no other engine's path changes.
    if tab is None:
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
        #
        # AND THE HREF PREFIX IS AMBIGUOUS TOO — MEASURED live on 2026-08-14 (session #29), where
        # `a[href^="/jobs/search-results/"]` matched **12** anchors on the blended page:
        #     /jobs/search-results/?origin=SWITCH_SEARCH_VERTICAL&keywords=…        the Jobs pill
        #     /jobs/search-results/?currentJobId=4451068100&…BLENDED_SEARCH…CARD    a job CARD ×10
        #     /jobs/search-results/?keywords=…&origin=…SEE_ALL&…                    "Show all"
        # So replacing the ambiguous NAME with an ambiguous HREF PREFIX only moved the ambiguity.
        # Clicking a card would be the 2026-07-26 wrong-company mistake wearing a new coat: it
        # opens ONE posting, which is not what a query rung means even though it does land on
        # `/jobs/search-results/`.
        #
        # STATED PRECISELY, because the distinction matters: this was a LATENT defect, not the
        # failure actually observed. Document order put the (correct) vertical pill first that day,
        # so the old selector would have navigated fine; what stopped Route B was its `moved` gate
        # above. Ten of twelve matches being wrong is a bug worth fixing on its own terms — but it
        # is not the cause of what we saw, and recording it as such would be the same confident
        # wrong attribution as the 08-14 truncated-dropdown note.
        #
        # The DISTINGUISHING fact, off the live page rather than from memory: a CARD always carries
        # `currentJobId`; a link meaning "show me the whole result set" never does. That exclusion
        # takes 12 matches down to 2 — the Jobs vertical pill and "Show all".
        #
        # AND TWO IS STILL ONE TOO MANY, for a reason no amount of reading hrefs could reveal.
        # Both remaining links point at the right place, so an earlier version of this fix argued
        # the choice between them had stopped mattering. It had not: HIT-TESTING the pill's own
        # centre returns a `LABEL` that overlays it. The pill is a filter control wearing an
        # anchor's href, so a trusted click at its centre toggles a radio and NAVIGATES NOWHERE —
        # which is exactly what the drive did, twice, reporting a click that landed and a page
        # that never moved. A destination is not a target; what is ON TOP at the click point is.
        # (Same family as the react-select fronting a hidden native select, and as `.click()` not
        # focusing: the thing you addressed and the thing your press reaches are two questions.)
        #
        # So the target is named exactly: the SEE_ALL affordance, which is the one the operator's
        # own measured route took. MEASURED 2026-08-14: matches exactly 1, and its hit-test
        # resolves to a SPAN *inside the anchor*, so a press at its centre reaches the link itself.
        landed = ((await _tab_urls()) or [""])[0]
        if "/search/results/" in landed and engine.get("platform") == "linkedin":
            bb.log("run_query", "landed on the blended search — taking the jobs section's "
                                "'Show all' (the SEE_ALL affordance, not the filter pill that "
                                "shares its destination)")
            res = await _capture_post("/execute", {
                "browser_url": browser_url, "tab_id": tab_id, "action_id": "click",
                "target_bbox": {},
                "selector": ('a[href*="/jobs/search-results/"][href*="SEE_ALL"]'
                             ':not([href*="currentJobId"])'),
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
            "detail": f"Ran {query!r}. This search will not run again — page forward through its "
                      f"results, or start a new search for something else."}


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
    # The search is the query, the session is the browser (2026-08-10): recording a page is the
    # moment a search becomes real, so the row is ensured here — same tuple reuses, a new query
    # in the same session mints a sibling — and every card on the page joins it.
    import searches as searches_mod
    search = searches_mod.ensure_active_search(
        db, session_id=bb.session_id, engine=engine["platform"],
        query=bb.search_state.query, location=bb.search_state.location,
        radius_miles=(bb.world or {}).get("radius_miles"))
    new_count, dup_count = upsert_observed_jobs(db, cards, engine["platform"],
                                                bb.search_state.query,
                                                search=search, page=page)
    if search is not None:
        bb.world = dict(bb.world or {})
        bb.world["search_id"] = search.id
    db.commit()

    # Give every card just written a canonical job. The other two scrape endpoints in `main.py`
    # already did this and this one never has — measured 2026-07-30: all ten sightings from session
    # 24's page carried `canonical_job_key = NULL`, so Indeed serving one Bristol County Savings
    # Bank requisition four times never even reached the matcher. A sighting with no canonical job
    # is invisible to every question asked of the `jobs` table.
    job_dedup.resolve_after_commit(db)

    # THE APPLIED CHECK, AT SCAN TIME. One query for the whole page, so every card arrives already
    # knowing whether the database has an application on file for it — by id, by requisition, or
    # (as a warning only) by employer + role. Asking here is what stops a drive from rediscovering
    # the answer six steps into an ATS, which is exactly how this page's own BIDMC pick was spent.
    applied = applied_index.check_many(db, cards, platform=engine["platform"])

    # WHAT THE DATABASE ALREADY KNOWS ABOUT EACH CARD. The applied check above answers "have we
    # applied"; these answer "have we SEEN it, and what do we hold on it" — which is the other half
    # of choosing well. Operator-directed 2026-07-30: the picker is connected to the database so a
    # choice is made against history rather than against a title. One query for the whole page.
    # Read per card via `get`: these rows were just upserted in this same session, so each is an
    # identity-map hit rather than a round trip — and `get` is the seam the whole test suite fakes.
    from models import ObservedJob as _OJ

    def _history(job_id: str, card: dict) -> dict:
        row = db.get(_OJ, job_id)
        return {
            # `apply_type` is read off the CARD at scan time — the third-party vs in-app fork the
            # operator wants visible before picking, not discovered six steps into an ATS.
            "apply_type": card.get("apply_type") or (row.apply_type if row else "") or "",
            "seen_count": (row.seen_count if row else 1) or 1,
            "first_seen_at": (row.first_seen_at.isoformat() if row and row.first_seen_at else ""),
            "status": (row.application_status if row else "") or "",
            # Whether we already hold the full posting. A card with a description behind it can be
            # judged now; one without still needs the click.
            "has_description": bool(row and (row.description or "").strip()),
            "description_chars": len((row.description or "") if row else ""),
        }

    results = []
    for c in cards:
        if not c.get("external_id"):
            continue
        job_id = f"{engine['platform']}:{c.get('external_id')}"
        results.append({
            "job_id": job_id, "external_id": c.get("external_id"),
            "title": c.get("title"), "company": c.get("company"),
            "location": c.get("location"), "salary": c.get("salary"),
            "url": c.get("url"),
            "applied": (applied.get(c.get("external_id")) or applied_index.AppliedVerdict()).as_dict(),
            **_history(job_id, c),
        })
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
        # HANDING OVER MEANS HANDING OVER SOMETHING. This path returns the loop's verdict and
        # nothing else — no state, no options — so the cockpit rendered a sentence and a single
        # "I've signed in — re-check" button while the operator sat looking at three visible ways
        # in. Surveying here costs one local AX scan (a socket, no bandwidth) and is the difference
        # between "we stopped" and "we stopped, and here is what is on the screen". The loop's own
        # `detail` is kept: it says why we stopped, which the survey cannot know.
        try:
            survey = await _login_survey(browser_url, obs, engine, bb)
            out.update({k: survey[k] for k in ("state", "options", "can_drive", "seen")
                        if k in survey})
        except Exception as exc:  # noqa: BLE001 — a survey that fails must not sink the handoff
            bb.log("login", f"could not survey the stopped sign-in screen: {exc}"[:180])
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
    elif state == "identifier_form":
        # Say what this screen IS. It used to arrive as "unknown", which told the operator nothing
        # and made an ordinary email-first page look like a fault in the page.
        detail = (f"This is the email-first step — your address now, the password or an emailed "
                  f"code on the next screen. {len(entries)} way(s) in, all of them pressable. "
                  f"Step again and I'll fill the address and continue, or pick a route.")
    return {"state": state, "url": tab.get("url", ""), "can_drive": True,
            "options": [{"name": e["name"], "role": e["role"], "why": e["why"],
                         # How this one gets pressed. A frame is clicked by point (its rect rides
                         # along); everything else by node. The cockpit shows them identically —
                         # both are things we press — because to the operator they are.
                         "by_point": bool(e.get("by_point")),
                         "bbox": e.get("bbox")} for e in entries],
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
    obs = await _observe(browser_url, bb, session_id=session.id)
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

    # THE STEPRUNNER WRAPS THE SSO STEP — with the credential-flow posture: `collect=False`
    # observes state identity only (URL, tab list, role+name), no /capture artifact and no
    # screenshot on the identity provider's screens (§4). The expectation is the one thing every
    # SSO step predicts: the screen ADVANCES. `_drive_sso_step` already re-classifies and says
    # "did not change" in prose while claiming ok — the verifier turns that into a demotion, so
    # a no-op Next stops reading as progress.
    import step_runner as sr
    report = await sr.run_step(
        lambda: _drive_sso_step(browser_url, popup, username, approved=body.approved),
        action={"action": "sso_step", "initiator": body.initiator},
        expect=sr.Expectation(kind="content_changed"),
        capture_post=_capture_post, browser_url=browser_url,
        tab_id=popup.get("tab_id", ""), session_id=session.id, rung_id="sso_step",
        collect=False)
    res = report.result
    if report.demotes:
        res["ok"] = False
        res["detail"] = (str(res.get("detail") or "") +
                         f" The verifier read the screen twice and {report.evidence} — treated "
                         f"as not advanced.")
    res["verification"] = report.verification()
    bb.log("sso", f"{res.get('state')} -> {res.get('landed') or 'no change'}"[:160])
    _persist(bb, ledger)
    obs2 = await _observe(browser_url, bb, session_id=session.id)
    return _view(session, bb, ledger, obs2, page=_current_page(obs2, bb),
                 awaiting=None if res.get("ok") else "operator_login",
                 last={**res, "action": "sso_step"})


class LoginActionBody(BaseModel):
    control_name: str                   # the accessible NAME of the control to click
    # THE SURVEY ALREADY KNOWS THE ROLE, so the default is "ask it" rather than "assume button".
    # This defaulted to "button" and role-gated the click to it, which meant every way in that the
    # survey reported as a `link` — Indeed's own "Sign in" among them — was offered and then could
    # never be clicked. Worse, the failure blamed the page ("it may have moved") for a control
    # sitting right where it was. A caller may still pin the role explicitly; the UI does.
    role: Optional[str] = None
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
    obs = await _observe(browser_url, bb, session_id=session.id)

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

    # Click it as the SURVEY saw it. Falling back to the surveyed role keeps the role gate — which
    # is what stops a name owned by two controls resolving in document order — without requiring
    # every caller to know a detail the survey just reported.
    chosen = next((o for o in before["options"] if o["name"] == body.control_name), {})
    role = body.role or chosen.get("role")
    tab = (obs.get("tabs") or [{}])[0]
    # A FRAME IS PRESSED BY POINT. Google renders its SSO button inside a cross-origin iframe, so
    # AX offers the frame and never the button: addressing by name resolves the frame node, and
    # `.click()` on that lands on nothing. Sending the frame's RECT instead makes the humanized
    # driver do what a hand does — move to the point and press — and the press lands on the button
    # inside. Named for identity, point for delivery.
    async def _click() -> dict[str, Any]:
        if chosen.get("by_point") and chosen.get("bbox"):
            r = await _capture_post("/execute", {
                "browser_url": browser_url, "tab_id": tab.get("tab_id", ""), "action_id": "click",
                "target_bbox": chosen["bbox"], "driver": "humanized"})
        else:
            r = await _capture_post("/execute", {
                "browser_url": browser_url, "tab_id": tab.get("tab_id", ""), "action_id": "click",
                "target_bbox": {},   # required by ExecuteRequest even here, where it goes unused
                "target_role": role, "target_name": body.control_name, "driver": "humanized",
            })
        await asyncio.sleep(2.0)
        return r

    # THE STEPRUNNER WRAPS THE CLICK — credential-flow posture (`collect=False`, §4): this is
    # the road TO a login screen, so identity-only looks, no artifacts. A way-in click predicts
    # the page moves (a menu opens, a login screen renders, an SSO popup appears — all visible
    # as tab or AX-content movement); a dispatched click that moved nothing gets demoted instead
    # of reading as "clicked".
    import step_runner as sr
    report = await sr.run_step(
        _click,
        action={"action": "login_action", "control": body.control_name, "role": role,
                "initiator": body.initiator},
        expect=sr.Expectation(kind="content_changed"),
        capture_post=_capture_post, browser_url=browser_url,
        tab_id=tab.get("tab_id", ""), session_id=session.id, rung_id="login_action",
        collect=False)
    res = report.result

    obs_after = await _observe(browser_url, bb, session_id=session.id)
    # SAME ARGUMENTS AS THE `before` SURVEY. This one dropped `engine` and `bb`, and it is the one
    # the operator actually reads — so the identity lookup got nothing and Google's address step
    # reported "no Google login is stored to answer with" while the account sat in the vault. A
    # survey that cannot name the identity cannot offer to fill it.
    after = await _login_survey(browser_url, obs_after,
                                engine_for(session, obs_after.get("search_tab")), bb)
    # Only a recognised outcome counts as a click. A reply with no `outcome` is an error body,
    # not a result — reading one as success is what let a whole drive report work it never did.
    ok = res.get("outcome") in _ACTED_OK and not report.demotes
    if report.demotes:
        detail = (f"Clicked {body.control_name!r}, but {report.evidence} — the click landed on "
                  f"nothing. Re-check.")
    elif ok:
        detail = f"Clicked {body.control_name!r}. " + after["detail"]
    else:
        detail = (f"Could not find {body.control_name!r} on the page any more — "
                  f"it may have moved. Re-check.")
    bb.log("login_step", f"clicked {body.control_name!r} -> {after['state']}")
    _persist(bb, ledger)
    return _view(session, bb, ledger, obs_after, page=_current_page(obs_after, bb),
                 awaiting=None if obs_after["observed"].get("authenticated") else "operator_login",
                 last={"ok": ok, "action": "login_action", "login": after,
                       "verification": report.verification(), "detail": detail})


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

    # REVEAL THIS LEG'S FORM FIRST, when the page serves both from one url. Conditional on a
    # MEASUREMENT — the submit control's own presence — because pressing the toggle on a page that
    # is already showing the right form would switch it to the wrong one. Workday's SolutionHealth
    # tenant defaults to Create Account, so the sign-in leg filled the create form's shared
    # Email/Password boxes and died looking for a submit that was one click away (live 2026-08-12).
    toggle = form.get("toggle")
    if toggle:
        toggle_field, showing_field = toggle
        probe = apply_fields.addressing_for(ats, showing_field)
        seen = await _capture_post("/locate", {"browser_url": browser_url, "tab_id": tab_id,
                                               "css": probe.get("selector") or "",
                                               "text": probe.get("name") or ""})
        # ONLY A REAL MEASUREMENT MAY TRIGGER THE TOGGLE. A missing `found` is not "the form is
        # absent", it is "we did not look" — and toggling a page that already shows the right form
        # switches it to the wrong one. So the verdict must be EXPLICIT: `found` present and false.
        # An unmeasured condition falls through to the behaviour that came before this step existed.
        measured_absent = seen.get("ok") and seen.get("found") is False
        if measured_absent:
            addr = apply_fields.addressing_for(ats, toggle_field)
            res = await _capture_post("/execute", {
                "browser_url": browser_url, "tab_id": tab_id, "action_id": "click",
                "target_bbox": {}, "driver": "humanized",
                **apply_fields.execute_addressing(addr)})
            if res.get("outcome") not in ("ok", "committed_unconfirmed"):
                return {"ok": False, "reason": "toggle_failed", "staged": staged,
                        "detail": f"This page shows the other form and {toggle_field!r} would not "
                                  f"switch it ({res.get('outcome') or res.get('detail')}). Nothing "
                                  f"was typed."}
            await asyncio.sleep(xs.pause_for(xs.pick_style(), xs.NAVIGATION))
            # VERIFIED, not assumed: a toggle that clicked and changed nothing would send every
            # credential below into the wrong form.
            again = await _capture_post("/locate", {"browser_url": browser_url, "tab_id": tab_id,
                                                   "css": probe.get("selector") or "",
                                                   "text": probe.get("name") or ""})
            if again.get("found") is not True:
                return {"ok": False, "reason": "toggle_unconfirmed", "staged": staged,
                        "detail": f"Pressed {toggle_field!r} but {showing_field!r} still is not on "
                                  f"the page, so the {leg} form never appeared. Nothing was typed."}

    async def _fill(field: str, value: str) -> dict:
        addr = apply_fields.addressing_for(ats, field)
        payload = {"browser_url": browser_url, "tab_id": tab_id, "action_id": "type",
                   "target_bbox": {}, "value": value, "driver": "humanized",
                   **apply_fields.execute_addressing(addr)}
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

    # CHECKS — required boxes that must end ON (the refusals' mirror). Label-independent by
    # design: `values: ["*"]` means every box in the addressed group ends checked, because a
    # consent's label is tenant prose and a walk that must quote it exactly breaks per tenant
    # (SolutionHealth's "I consent" vs US Bank's "I confirm…", live 2026-08-11). Converges —
    # an already-checked box is never re-clicked — and a check we cannot verify stops the
    # submit, same as a refusal: consent is not a best-effort field.
    for field in form.get("checks", ()):
        addr = apply_fields.addressing_for(ats, field)
        staged = True
        res = await _capture_post("/check_group", {
            "browser_url": browser_url, "tab_id": tab_id,
            "selector": addr["selector"], "values": ["*"]})
        # A BOX THAT IS NOT THERE IS NOT A BOX WE FAILED TO TICK. Tenants of the same ATS differ on
        # this: SolutionHealth's Workday signup requires a consent checkbox and bounces without it
        # (which is why this step exists), and C&S's Workday signup has none at all — email,
        # password, verify, submit (measured live 2026-08-13, AX scan: zero checkboxes on the
        # form). Treating every consent as mandatory for the whole ATS made a form with nothing to
        # consent to unsubmittable.
        #
        # The distinction is the whole safety property, so it rests on a MEASURED absence —
        # `not_found` from the protocol, which searches frames — and nothing else. A box that IS
        # there and will not tick still stops the submit, because a consent we cannot confirm is
        # not a consent.
        if str(res.get("outcome") or "") == "not_found":
            continue
        if not res.get("ok"):
            return {"ok": False, "reason": "check_failed", "staged": staged,
                    "detail": f"Could not check {field!r} "
                              f"({res.get('code') or res.get('outcome') or res.get('detail')}). "
                              f"The form requires it, so submitting now would bounce — nothing "
                              f"was submitted."}
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
                        "target_bbox": {}, "driver": "humanized",
                        **apply_fields.execute_addressing(open_addr)}
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
                     "target_bbox": {}, "driver": "humanized",
                     **apply_fields.execute_addressing(submit_addr)}
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
        obs = await _observe(_session_browser_url(session), bb,
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
        obs = await _observe(_session_browser_url(session), bb, session_id=session.id)
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
    obs = await _observe(browser_url, bb, session_id=session.id)
    live_url = _apply_tab_url(bb, obs) or (bb.world or {}).get("orient", {}).get("url", "")

    # Register the account record; next_account_action decides the leg (create vs sign-in) and
    # derives the credentials.
    ensure = ats_accounts.ensure_account(company, step.platform, login_url=live_url)
    if not ensure.get("ok"):
        raise HTTPException(status_code=409, detail=ensure.get("detail", "could not open account"))
    # THE ROW IS WORKING STATE UNTIL THE SIGNUP LANDS. `ensure_account` writes it on intent, so
    # every leg that ends without an account existing has to take back what it minted — otherwise
    # an abandoned attempt leaves a row claiming a login the employer has never heard of, and those
    # accumulate (three were in the store on 2026-08-13, one from a platform prediction that turned
    # out wrong). Only what THIS call minted: a row that was already there is somebody else's
    # record, and `discard_unclaimed` refuses anything active or holding a secret regardless.
    _minted_here = bool(ensure.get("created"))

    def _unclaim() -> None:
        if _minted_here:
            ats_accounts.discard_unclaimed(company, step.platform)

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
            _unclaim()   # a captcha stopped us before the form — no account was made
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
        # THE STEPRUNNER WRAPS THE DRIVE — credential-flow posture (`collect=False`, §4): a
        # password is typed in here, so the looks are identity-only and the row carries field
        # NAMES, never values. The driver's own internal proofs (dialog appeared, consent proof
        # text, submit control gone) remain the semantic verification; the wrapper adds the
        # before/after pair and diff the corpus needs. Expectation: a SUBMIT predicts the page
        # moves (these forms replace themselves on success); a fill-only leg changes values AX
        # names cannot see, so it is `unmodeled` rather than a check that would demote honest work.
        import step_runner as sr
        _submitting = body.mode == "auto"
        _report = await sr.run_step(
            lambda: _drive_account_form(browser_url, tab_id, creds, ats=step.platform,
                                        leg=action.get("leg") or "create_account",
                                        submit=_submitting, extra=extra),
            action={"action": "apply_account", "leg": action.get("leg"), "ats": step.platform,
                    "mode": body.mode, "initiator": body.initiator},
            expect=(sr.Expectation(kind="content_changed") if _submitting
                    else sr.Expectation(kind="unmodeled")),
            capture_post=_capture_post, browser_url=browser_url, tab_id=tab_id,
            session_id=session.id, rung_id=_ACCOUNT_RUNG, collect=False)
        drive = _report.result
        if not drive.get("ok"):
            step.record(_ACCOUNT_RUNG, aps.FAILED, f"create leg: {drive.get('detail', '')}"[:200],
                        initiator=body.initiator,
                        # HALF of these refusals happen before a keystroke (no credential, no form
                        # recipe, a password the site's own rules reject) and half after (a fill
                        # that errored on field four). The driver tracked which; a missing answer
                        # means an older driver, so assume it typed — over-protecting the page is
                        # the recoverable mistake.
                        staged=bool(drive.get("staged", True)))
            _unclaim()   # the drive refused or errored — nothing was submitted, so nothing exists
            _save_queue(bb, queue); _persist(bb, ledger)
            obs2 = await _observe(browser_url, bb, session_id=session.id)
            return _view(session, bb, ledger, obs2, page=_current_page(obs2, bb), awaiting="apply",
                         last={"ok": False, "action": "apply_account", "queue": queue.summary(),
                               "verification": _report.verification(),
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
            obs2 = await _observe(browser_url, bb, session_id=session.id)
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
            obs2 = await _observe(browser_url, bb, session_id=session.id)
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
        obs2 = await _observe(browser_url, bb, session_id=session.id)
        return _view(session, bb, ledger, obs2, page=_current_page(obs2, bb), awaiting="apply",
                     last={"ok": True, "action": "apply_account", "queue": queue.summary(),
                           "credentials_stored": saved,
                           "verification": _report.verification(),
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

    obs = await _observe(_session_browser_url(session), bb, session_id=session.id)
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
    import form_fill
    if body.use_source:
        # `use_source` ANSWERS ONE QUESTION, and the caller has to be asking it. Nothing checked
        # which field this was, so `use_source: true` on "Country of Residence" resolved to
        # **"Indeed"** — the search engine's name, offered as a country, on two fields of a live
        # employer's form (MAPFRE, 2026-08-15). The resolution was not wrong; it was answering a
        # question nobody had asked here. Refuse instead, and say what this flag is for: an
        # explicit `value` is always available and is what these fields wanted.
        if not form_fill.answers_how_did_you_hear(body.field_name):
            raise HTTPException(
                status_code=422,
                detail=(f"use_source answers 'how did you hear about us?', and {body.field_name!r} "
                        f"is not that question — it would offer the job board's name as the "
                        f"answer. Pass an explicit value instead."))
        source = apply_source.source_from_job_id(step.job_id)
        paths = apply_source.source_paths(source)          # [["Job Board","Indeed"], ..., ["Other"]]
    elif body.value:
        paths = [[body.value]]                              # a flat dropdown: a one-level path
    else:
        raise HTTPException(status_code=422,
                            detail="Give an explicit value, or use_source for 'how did you hear'.")

    obs = await _observe(browser_url, bb, session_id=session.id)
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
    obs2 = await _observe(browser_url, bb, session_id=session.id)
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


def _precise_state_from(platform: Optional[str], url: str, text: str) -> str:
    """The spine-precise state the PAGE stated, or "" when it only implied one.

    Workday renders its entire stepper ("current step 1 of 8 My Information"), so the screen names
    itself in the ladder's own vocabulary — better evidence than any inference, and finer than the
    generic kind the fusion would otherwise compose. Feeding it in is what lets the observation be
    as precise as the record it is meant to outrank.

    The gate is `observed`: the platform mappers fall back to a URL-only default when nothing
    matched, and that default is spelled like a real state (b33a14f). Promoting a guess here would
    hand the ladder a confident wrong position — the precise opposite of the point.
    """
    if not platform or not text.strip():
        return ""
    try:
        import apply_recipe as _ar
        readout = _ar.describe_for_ats(platform, url, text)
    except Exception:  # noqa: BLE001 — a reader that fails is silent, never wrong
        return ""
    return (readout.get("state") or "") if readout.get("observed") else ""


#: AX roles that render a form's section headings. A section bar is a heading or a button that
#: toggles one; both carry the section's name as their accessible name.
_SECTION_ROLES = ("heading", "button", "tab")

#: Every visible field's label -> its CURRENT value, for the post-fill read-back. Reads the same
#: three label sources the AX name is built from, so the keys line up with the plan's field names.
#: Values come from the light DOM, which is where a typed value lives even on forms whose widgets
#: the DOM cannot otherwise address.
_READBACK_JS = """(()=>{const o={};
document.querySelectorAll("input,textarea").forEach(i=>{
  if(i.type==="hidden"||!i.offsetParent)return;
  const l=(i.labels&&i.labels[0]&&i.labels[0].innerText)||i.getAttribute("aria-label")||
          i.getAttribute("title")||"";
  const k=l.replace(/\\s+/g," ").trim();
  if(k&&!(k in o))o[k]=i.value||"";});
return JSON.stringify(o);})()"""


def _section_at(y: float, marks: list[tuple[float, str]]) -> Optional[str]:
    """The nearest section heading ABOVE `y`, or None when the field sits above them all."""
    best: Optional[str] = None
    best_y = float("-inf")
    for my, name in marks:
        if my <= y and my > best_y:
            best_y, best = my, name
    return best


def _form_fields_from(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The {role, name} projection the fill planner reads — plus WHICH SECTION each field is in.

    The section is not decoration: it is what tells `availability_date` apart from the start date
    of a degree. Both are labelled "Start Date", only one of them is an answer to this
    application, and the page distinguishes them by putting them under different headings
    (MAPFRE/SuccessFactors, live 2026-08-15 — the planner typed today's date into the Education
    row and the form rejected it).

    Derived from GEOMETRY rather than from the per-ATS section declarations, because those name
    the bars we expected and this tenant rendered seven of its nine under different names — the
    guard has to work on a page we have never seen. A heading's own y-position orders it against
    the fields below it, which is the same reading a person does.
    """
    marks = sorted(
        (c["bbox"]["y"], (c.get("caption") or c.get("name") or "").strip())
        for c in candidates
        if (c.get("role") or "").lower() in _SECTION_ROLES
        and isinstance(c.get("bbox"), dict) and "y" in c["bbox"]
        and (c.get("caption") or c.get("name"))
    )
    out: list[dict[str, Any]] = []
    for c in candidates:
        name = c.get("caption") or c.get("name")
        if not name:
            continue
        bbox = c.get("bbox") or {}
        section = _section_at(bbox["y"], marks) if "y" in bbox else None
        out.append({"role": c.get("role"), "name": name, "section": section})
    return out


async def _scan_form_fields(browser_url: str, tab_id: str) -> list[dict[str, Any]]:
    return _form_fields_from(await _scan_ax(browser_url, tab_id))


def _fill_plan_for(bb: Any, fields: list[dict[str, Any]], db: Session) -> list[dict[str, Any]]:
    import form_fill
    from models import ApplicationAnswer
    from sqlalchemy import select as _select
    rows = db.scalars(_select(ApplicationAnswer).where(ApplicationAnswer.status == "active")).all()
    answers = {r.answer_key: r.value for r in rows}
    # THE FULL ROWS, not just key->value: the planner's second source is the stored
    # `question_patterns`, which only exist on the row. Same shape `/application-answers/match`
    # reads, so the bunch fill and the match endpoint answer the same question the same way.
    answer_rows = [{"answer_key": r.answer_key, "display_name": r.display_name,
                    "value": r.value, "input_hint": r.input_hint,
                    "question_patterns": r.question_patterns or [],
                    "options": r.options or []} for r in rows]
    return form_fill.plan(fields, answers=answers, identity=_identity_defaults(),
                          answer_rows=answer_rows)


class ApplySectionsBody(BaseModel):
    initiator: str = "operator"
    #: None = resolve from the OPEN application's platform. The old default ("successfactors")
    #: meant a press on an Indeed page read SAP's bars against a smartapply tab and rendered a
    #: phantom nine-sections-unreadable profile (the 2026-08-10 screenshot).
    ats: Optional[str] = None
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

    _q = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
    _step_now = _q.current() or _parked_step(_q)
    ats = body.ats or (_step_now.platform if _step_now else "") or ""
    if not af.has_section_bars(ats):
        raise HTTPException(status_code=400,
                            detail=f"No section bars declared for {ats!r}. Absent means the "
                                   f"form is flat or nobody has checked — not that it is flat.")

    obs = await _observe(browser_url, bb, session_id=session.id)
    tab_id = _apply_tab(bb, obs).get("tab_id", "")
    before = form_fill.section_status(ats, await _scan_ax(browser_url, tab_id))

    if not body.expand:
        return _view(session, bb, ledger, obs, page=_current_page(obs, bb), awaiting="apply",
                     last={"ok": True, "action": "apply_sections", "sections": before,
                           "detail": _sections_detail(before)})

    keys = [before["expand_all"]] if body.expand == "all" else [body.expand]
    style = xs.pick_style()
    clicked, refused = [], []
    # Resolve every address BEFORE acting — an unknown key is a caller error (400), not a step.
    addrs: dict[str, dict] = {}
    for key in keys:
        try:
            addrs[key] = af.addressing_for(ats, key)
        except af.FieldNotFound as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def _expand_bars() -> dict[str, Any]:
        for key, addr in addrs.items():
            res = await _capture_post("/execute", {
                "browser_url": browser_url, "tab_id": tab_id, "action_id": "click",
                "target_bbox": {}, "target_role": addr.get("role"),
                "target_name": addr.get("name"), "driver": "humanized"})
            (clicked if res.get("outcome") in ("ok", "committed_unconfirmed")
             else refused).append(key)
            await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))
        return {"ok": bool(clicked) and not refused, "clicked": clicked, "refused": refused}

    # THE STEPRUNNER WRAPS THE EXPAND. Opening a section pours its fields into the AX tree, so
    # the honest expectation is `content_changed`. The section re-read below stays the response's
    # authority (it names WHICH bar stayed shut); the wrapper's job is the corpus row.
    import step_runner as sr
    _report = await sr.run_step(
        _expand_bars,
        action={"action": "apply_sections", "expand": keys, "ats": ats,
                "initiator": body.initiator},
        expect=sr.Expectation(kind="content_changed"),
        capture_post=_capture_post, browser_url=browser_url, tab_id=tab_id,
        session_id=session.id, rung_id="apply_sections")

    after = form_fill.section_status(ats, await _scan_ax(browser_url, tab_id))
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
                       "opened": opened, "verification": _report.verification(),
                       "pace": xs.describe(style), "detail": detail})


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
    #: Whose accordion declaration to check the form against. None = the open application's
    #: platform; a platform with no declared bars gets sections=None, never another ATS's list.
    ats: Optional[str] = None


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

    obs = await _observe(browser_url, bb, session_id=session.id)
    block = obs.get("block")
    if block and block.get("strength") == "active":
        return await _save_queue_and_view(session, bb, ledger, queue, obs, ok=False,
                                    detail="A challenge is up — clear it yourself before filling.")
    tab_id = _apply_tab(bb, obs).get("tab_id", "")
    candidates = await _scan_ax(browser_url, tab_id)
    fields = _form_fields_from(candidates)
    # THE FORM AS IT STANDS, beside the plan. The plan only speaks to fields it recognises
    # (form_fill.plan drops the rest by design), so "0 of 0 fields" rendered alike for an empty
    # page and for a fully-answered screener the planner had no names for — the census is the
    # scanner's own truth per required control, answered rows included. Fetched BEFORE the plan
    # because it is also the plan's second field source (below).
    census = await _form_census(browser_url, tab_id)
    # THE CENSUS'S FIELDS JOIN THE PLAN. The AX scan cannot see an input with no accessible
    # name, and Cornerstone's contact block is four of those — no id, no label association, no
    # aria, no placeholder — so First/Last/Email/Phone were invisible to this plan while the
    # DOM census named them by proximity and minted structural selectors (live 2026-08-11).
    # Census rows ride in WITH their selector, and the bunch below types by selector when the
    # accessible name cannot address the node. AX names stay first: they win the dedupe.
    # Compared with the REQUIRED MARKER STRIPPED: the census reads a label as the page prints it
    # ("First Name*") while AX reports the accessible name ("First Name"), so a raw compare called
    # them different fields and planned BOTH — 14 rows for 7 boxes on Workday's My Information,
    # every value typed twice into the same node (live 2026-08-11). Same field, same box.
    def _bare(name: str) -> str:
        return re.sub(r"[\s*:]+$", "", (name or "").strip()).strip().lower()

    # "ALREADY KNOWN" MUST MEAN "ALREADY ADDRESSABLE", not "the string appears somewhere in the
    # tree". Workday wraps every question in a `role=group` whose accessible name IS the question
    # text, and those groups are in `fields`. So the dedupe saw the question as known and dropped
    # the census row — and then `form_fill.plan` discarded the group, because a group is not a
    # fillable control. The field fell through the crack between the two, and the ONLY reason any
    # of them survived was the census cutting names at ~90 chars, which made the three longest
    # questions fail to match their own group. Two of Eversource's five textareas were lost this
    # way, including "Please list your full legal name" (live 2026-08-17).
    #
    # Scoped to the roles the planner can actually use, which keeps the dedupe this comment block
    # was written for: an AX textbox "First Name" still suppresses the census's "First Name*".
    known_names = {_bare(f.get("name") or "") for f in fields
                   if (f.get("role") or "").lower() in form_fill.FILLABLE_ROLES}
    for c_row in ((census or {}).get("unanswered") or []):
        c_name = (c_row.get("field") or "").strip()
        if (c_row.get("kind") in ("input", "textarea") and c_row.get("selector")
                and c_name and _bare(c_name) not in known_names):
            # THE KIND RIDES ALONG, because the AX role cannot carry it. Both an `<input>` and a
            # `<textarea>` are role `textbox`, so flattening them here threw away the one fact
            # that separates a City box from a three-reference prose box — and the planner, given
            # only "textbox", mapped Eversource's references question to `city` = "Concord"
            # (live 2026-08-17). `form_fill._label_scope` reads this.
            fields.append({"role": "textbox", "kind": c_row["kind"], "name": c_name,
                           "selector": c_row["selector"]})
            known_names.add(_bare(c_name))
    rows = _fill_plan_for(bb, fields, db)
    summary = form_fill.summarise(rows)
    # A plan over a shut accordion is an accurate summary of a page nobody opened. Carry the
    # caveat with the plan so "0 fields" and "0 fields, nine sections closed" cannot read alike.
    # Sections are checked against the OPEN application's platform — a platform with no declared
    # bars yields None, never a phantom reading of another ATS's accordion (2026-08-10).
    ats = body.ats or (step.platform if step else "") or ""
    sections = (form_fill.section_status(ats, candidates)
                if ats and apply_fields.has_section_bars(ats) else None)
    caveat = form_fill.sections_caveat(sections, summary["total"])

    if not body.execute:
        return _view(session, bb, ledger, obs, page=_current_page(obs, bb), awaiting="apply",
                     last={"ok": True, "action": "apply_fill", "queue": queue.summary(),
                           "fill_plan": rows, "fill_summary": summary, "sections": sections,
                           "form_scan": census,
                           "detail": f"Planned {summary['fillable']} of {summary['total']} fields. "
                                     + (f"Need your data for: {', '.join(summary['missing'])}. "
                                        if summary["missing"] else "Every field has a value. ")
                                     + _census_story(census)
                                     + caveat})

    style = xs.pick_style()
    filled, failed = [], []

    async def _fill_bunch() -> dict[str, Any]:
        for r in rows:
            if not r["fillable"] or r["widget"] != "text":     # this pass does text fields only
                continue
            exe = {"browser_url": browser_url, "tab_id": tab_id, "action_id": "type",
                   "target_bbox": {}, "value": r["value"], "driver": "humanized"}
            # Selector addressing for the census-derived fields the AX name cannot reach —
            # an anonymous input's "name" is a proximity label the resolver has never heard of.
            if r.get("selector"):
                exe["selector"] = r["selector"]
            else:
                exe["target_role"], exe["target_name"] = "textbox", r["field"]
            res = await _capture_post("/execute", exe)
            (filled if res.get("outcome") in ("ok", "committed_unconfirmed")
             else failed).append(r["field"])
            await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))
        return {"ok": not failed, "filled": len(filled), "failed": len(failed)}

    # THE STEPRUNNER WRAPS THE BUNCH. One step = one bunch, not one keystroke — the rung the
    # ladder reasons about is "fill this form step". Typed VALUES live in AX value space, which
    # the role+name observation cannot see, so the expectation is `unmodeled`: the pair + diff
    # land in the corpus (this is profile data on an apply form, a capturable state), and no
    # invented check gets to demote honest work it cannot observe.
    import step_runner as sr
    _report = await sr.run_step(
        _fill_bunch,
        action={"action": "apply_fill",
                "fields": [r["field"] for r in rows if r["fillable"] and r["widget"] == "text"],
                "initiator": body.initiator},
        expect=sr.Expectation(kind="unmodeled"),
        capture_post=_capture_post, browser_url=browser_url, tab_id=tab_id,
        session_id=session.id, rung_id="form_fill")

    # READ BACK WHAT LANDED. `filled` counts /execute outcomes, and /execute's own contract says
    # `ok` means the mechanism completed — not that the value is in the field. One probe for the
    # whole bunch turns "9 dispatched" into "9 confirmed on the page", or names the ones that are
    # still empty. Failure here degrades to no read-back rather than failing the fill: the typing
    # already happened, and a probe we could not run is not evidence that it did not.
    import json as _json
    typed_rows = [r for r in rows if r["fillable"] and r["widget"] == "text"]
    readback: Optional[dict[str, Any]] = None
    if typed_rows:
        try:
            probe = await _capture_post("/probe", {
                "browser_url": browser_url, "tab_id": tab_id,
                "note": "read back the values this bunch fill just typed",
                "expression": _READBACK_JS})
            page_values = _json.loads(probe.get("value") or "{}")
            readback = form_fill.readback(typed_rows, page_values)
        except Exception as exc:                                        # noqa: BLE001
            logging.getLogger("session_control").warning("fill read-back failed: %s", exc)
            readback = None

    landed_ok = readback["ok"] if readback else not failed
    step.record("form_fill", aps.OK if landed_ok and not failed else aps.FAILED,
                f"bunch-filled {len(filled)} field(s)"
                + (f"; {form_fill.readback_detail(readback)}" if readback else "")
                + (f"; {len(failed)} failed: {', '.join(failed)}" if failed else "")
                + (f"; need operator for: {', '.join(summary['missing'])}"
                   if summary["missing"] else ""),
                initiator=body.initiator)
    _save_queue(bb, queue)
    _persist(bb, ledger)
    obs2 = await _observe(browser_url, bb, session_id=session.id)
    # Re-census AFTER the fill: the panel's form must show the page as the fill left it, not as
    # the plan found it.
    census = await _form_census(browser_url, tab_id) or census
    return _view(session, bb, ledger, obs2, page=_current_page(obs2, bb), awaiting="apply",
                 last={"ok": (not failed) and landed_ok, "action": "apply_fill",
                       "queue": queue.summary(),
                       "fill_plan": rows, "fill_summary": summary, "sections": sections,
                       "form_scan": census,
                       "readback": readback,
                       "verification": _report.verification(),
                       "pace": xs.describe(style),
                       # THE HEADLINE IS WHAT THE PAGE HOLDS, not what we dispatched. "Filled 9"
                       # was true of the keystrokes and unproven of the form.
                       "detail": f"Filled {len(filled)} field(s) at {style.name} pace."
                                 + (f" {form_fill.readback_detail(readback)}" if readback else
                                    " Read-back unavailable — values not confirmed on the page.")
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

    obs = await _observe(browser_url, bb, session_id=session.id)
    read = await _read_apply_page(bb, obs, browser_url)
    if read is None:
        raise HTTPException(status_code=409,
                            detail="No application tab is open to orient against.")
    url, apply_tab, scan, text = read["url"], read["tab"], read["scan"], read["text"]

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
    seen = _name_the_screen(step, url, text)
    platform, state, progress = seen["platform"], seen["state"], seen["progress"]
    if seen["reclassified"]:
        step.record("classify", aps.OK,
                    f"re-classified {step.platform or 'unclassified'} -> {seen['live_platform']}: "
                    f"the apply moved to {url[:70]}", initiator=body.initiator)
        step.platform = platform
        _save_queue(bb, queue)

    recognised = bool(progress.get("recognised"))
    depth = _depth_phrase(progress)
    detail = (f"On {platform}: {state}{depth}." if recognised
              else f"On {platform} but this page ({state}) is not a state we recognise — new "
                   f"territory, worth a careful look before the next move.")

    # THE ORIENTER'S VERDICT IS THE LADDER'S POSITION. Without this the tail reads a `landing_state`
    # frozen at whatever `classify` said when the apply was entered — session #25 sat on
    # `indeed_unknown` from 2026-08-04 while the live page was the resume-selection screen, so a
    # read model asking "which rung is due" got the answer for a page we left long ago. The orient
    # already knew; nothing wrote it down.
    step.landing_state = state

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

    obs2 = await _observe(browser_url, bb, session_id=session.id)
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
    obs = await _observe(browser_url, bb, session_id=session.id)
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

    obs = await _observe(browser_url, bb, session_id=session.id)
    ats_url = _apply_tab_url(bb, obs)
    if not ats_url:
        raise HTTPException(
            status_code=409,
            detail="No ATS application tab is open, so there is nothing the window can prove about "
                   "this step. Work it forward instead.")

    # ASK WITH EVERYTHING WE HOLD, exactly as the classify rung does. This called
    # `classify_landing(ats_url)` — the address ALONE — while the function's whole design is three
    # witnesses: the host, the page's content, and where the page's own APPLY control points. The
    # third is the one that reads a branded careers front: measured live 2026-08-13 on Boston
    # Children's, whose application is served from `jobs.bostonchildrens.org` (reads as
    # `company_site`) while its apply control points at BrassRing. The observer, fusing the same
    # signpost, named `brassring · application_form` correctly — and reconcile, the operator's way
    # OUT of a stale record, re-recorded the front and left the step exactly where it was stuck.
    #
    # An address is a prediction; a page is evidence. This is the same rule the re-classify branch
    # below already states, applied one level earlier so that branch gets a fact to compare.
    _content = await _capture_post("/page_content",
                                   {"browser_url": browser_url, "tab_url": ats_url}, timeout=15.0)
    disc = aps.classify_landing(ats_url,
                                page_text=_content.get("text") or "",
                                frames=_content.get("frames") or [],
                                apply_hrefs=_content.get("apply_hrefs") or [])
    # ONE RULE FOR "WALKED", THE LADDER'S. This asked its own way — any OK ever recorded — and the
    # two answers diverge exactly where reconcile is most needed: a rung recorded OK and then
    # DEMOTED by its own verification. The ladder re-offers it (latest verdict wins, by design)
    # while this skipped it as already proven, so the operator's way out reported "nothing new"
    # and left them on the same stuck rung. Measured live 2026-08-11: a stale hosts list demoted a
    # perfectly good `enter_apply` on Boston College's Cornerstone, and reconcile could not undo it.
    done = step.settled_rungs()
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
    # Re-read the settled set after each record — the rungs below gate on the ones above.
    if "verify_identity" in step.settled_rungs() and "enter_apply" not in done:
        step.record("enter_apply", aps.OK,
                    f"reconciled — an application tab is open on {disc.platform}",
                    initiator=body.initiator)
        added.append("enter_apply")

    # classify: name the platform from the live tab.
    if "enter_apply" in step.settled_rungs() and "classify" not in done:
        step.platform = disc.platform
        step.record("classify", disc.outcome, f"{ats_url[:90]} -> {disc.detail}",
                    initiator=body.initiator)
        if disc.outcome == aps.OK:
            added.append("classify")
    # A SETTLED CLASSIFY MEANS "WE NAMED IT ONCE", NOT "THE WORLD CANNOT DISAGREE". The platform is
    # first guessed on Indeed from the apply href — a PREDICTION — and a settled rung made that guess
    # permanent: Odyssey Consulting's card said `workday`, the application landed on
    # `careers-odysseyconsult.icims.com`, and the account rung was one press from driving Workday's
    # create-account recipe (Workday field names, Workday's consent box) against an iCIMS form and
    # filing the credential under `ats_odyssey_consulting_workday` (live 2026-08-12).
    #
    # This is the whole reconcile contract: when the record and the window disagree, MEMORY YIELDS.
    # A contradiction re-classifies; both entries stay on the record (§10 — keep both sides of a
    # correction), because the wrong platform is the evidence that the href tell can lie.
    elif disc.platform and step.platform and disc.platform != step.platform:
        was = step.platform
        step.platform = disc.platform
        # A state named for the OTHER platform describes nothing, so it cannot stand. It used to be
        # blanked unconditionally, which was right while `disc` came from the address alone and had
        # no state worth trusting — but an unplaced step renders as "not classified yet" and the
        # operator is back to driving blind. Now that classify_landing is asked with the page, a
        # state it read is an OBSERVATION and replaces the wrong one.
        #
        # `<platform>_unreadable` is not an observation — it is the absence of one (apply_landing:
        # "nothing to read — not the same as nothing there"). Recording it would dress a failed
        # read as a placement, so that case still erases.
        replacement = (disc.state or "")
        step.landing_state = None if replacement.endswith(f"_{al.UNREADABLE}") else (replacement or None)
        step.record("classify", disc.outcome,
                    f"RE-CLASSIFIED from the live window: recorded as {was!r}, the open tab is "
                    f"{disc.platform!r} ({ats_url[:70]}). The earlier name came from the Indeed "
                    f"card's apply href, which predicts the destination and can be wrong.",
                    initiator=body.initiator)
        added.append(f"classify:{was}->{disc.platform}")

    # THE SCREEN MOVES MORE OFTEN THAN THE PLATFORM, and only the platform was being reconciled.
    # An advance re-reads where it landed from the look it took right after acting — which can
    # finish before the navigation it is verifying, so the state lags one screen behind the world
    # (live 2026-08-13: Apply opened `cswg.wd1.myworkdayjobs.com/.../apply`, the observer read
    # `workday application_form` at HIGH confidence, and the step still said
    # `workday_job_posting`). Reconcile is the operator's remedy for exactly that, and it could
    # not fix it: the branch above only fires when the PLATFORM contradicts, so on `workday` ->
    # `workday` the stale screen survived the one control whose contract is "align the record to
    # the live window". Pressing the rung again would re-click Apply on a page that has none.
    #
    # Same guard the advance path uses: a look that read LESS does not overrule one that read more,
    # so a non-answer never overwrites a named screen.
    # NAMED IN THE PLATFORM'S OWN VOCABULARY. `classify_landing` answers in the GENERIC kinds
    # (`workday_application_form`), and a platform with a scripted recipe walks its own spine
    # (`workday_apply_method`, `workday_apply_auth`, …). Naming the screen generically therefore
    # replaced a stale-but-placeable state with a fresh-but-unplaceable one: the walk lost its
    # position and `steps_to_submit` went to None. `describe_for_ats` is the namer the advance
    # path already uses, and it routes to the platform's own mapper.
    import apply_recipe as _ar
    _text = _content.get("text") or ""
    _readout = _ar.describe_for_ats(step.platform, ats_url, _text)
    _named = _readout.get("state") or ""
    # A GUESS MUST NOT BE ABLE TO IMPERSONATE A READING. The platform mappers fall back to a
    # URL-only default when nothing on the page matched — and that default is spelled like an
    # ordinary state (`workday_job_posting`), so it is invisible to the suffix check below and to
    # the `new_state != step.landing_state` test that decides whether the screen moved.
    #
    # Live, Eversource 2026-08-16: the tab moved to Workday's SSO chooser, no marker matched, the
    # mapper answered `workday_job_posting` — the value ALREADY recorded — so reconcile concluded
    # "asked the window and it agreed with the record" and changed nothing, three presses running,
    # while the ladder kept hunting for an Apply control on a sign-in page. The `_text.strip()`
    # guard above cannot catch it: the page was read, and was full of text that matched nothing.
    #
    # `observed` is the mapper saying which of the two it did. An unobserved name is dropped here,
    # so the screen is left alone AND `added` stays empty — which the log line below turns into an
    # honest "could not name this screen" instead of a false agreement.
    _named_observed = bool(_readout.get("observed", True))
    if _named and not _named_observed:
        _named = ""
    new_state = _named or disc.state or ""
    # ONLY WHEN THE PAGE WAS ACTUALLY READ. With no text a platform mapper falls back to its
    # URL-only default — Workday answers `workday_job_posting` for any tenant URL "with no step
    # marker yet" — and that is a guess about the address, not an observation of the screen.
    # Letting it through demoted a `workday_my_information` that had been read from real content.
    # The suffix check cannot catch it, because the default wears an ordinary state's name.
    # Bound BEFORE the branch, because the journal below reads it on EVERY path — including the
    # one where nothing moved. Assigning it only inside the `if` made a reconcile that agreed with
    # the record raise NameError (live 2026-08-14, a 500 on the operator's next press).
    was_state = step.landing_state
    if (new_state and _text.strip() and new_state != step.landing_state
            and not new_state.endswith((al.UNKNOWN, al.UNREADABLE))):
        step.landing_state = new_state
        # NOT a `classify` mini. That rung's history is about naming the PLATFORM, and a screen
        # refresh is a different fact — filing it there would make every reconcile look like a
        # re-classification and bury the real ones. The move still lands on the record, in the
        # reconcile log line and in this call's own report.
        added.append(f"screen:{was_state}->{new_state}")

    bb.world = dict(bb.world or {})
    bb.world["apply_tab"] = next((t for t in (obs.get("tabs") or [])
                                  if t.get("url") == ats_url), {"url": ats_url})
    bb.world["apply_queue"] = queue.as_dict()
    # WHY THE RECORD MOVED, not just that it did. A reconcile is the system admitting the world
    # went somewhere it did not follow, and the CAUSE is the whole content of that admission: a
    # screen advancing under a drive reads identically to one the SERVER took away, and only one
    # of those means "everything you filled is gone". Live 2026-08-14: an idle BrassRing session
    # expired, the tab bounced to the careers home, and the reconcile recorded
    # `application_form -> account_gate` in exactly the same words it uses for progress.
    _back_to_wall = (new_state or "").endswith("_account_gate") and \
        (was_state or "").endswith(("_application_form", "_review", "_confirmation"))
    bb.log("reconcile_step", f"{step.job_id}: recorded {added or 'nothing new'} from the live "
                             f"window ({disc.platform})",
           why=("the session was signed out — the window has fallen BACK to the account wall from "
                "a screen further in, which is what an expiry looks like from here. Anything "
                "filled and not saved by the site is gone (optimistic UI is not a server record)."
                if _back_to_wall else
                "the window had moved on and the record had not; the browser is truth and memory "
                "yields" if added else
                # THE THIRD ANSWER, which used to be told as the second. "Agreed" and "could not
                # read it" are opposite facts about our confidence, and collapsing them is what
                # let a stuck ladder look settled for three presses.
                "the screen could not be named — the page was read but matched nothing we know, "
                "so the record was LEFT ALONE rather than confirmed. This is not agreement: the "
                "window may well have moved somewhere we cannot see."
                if not _named_observed else
                "asked the window and it agreed with the record"),
           next_up=("Sign in again with the stored credential, then re-check what the server "
                    "actually kept." if _back_to_wall else
                    "Work the rung the reconciled screen calls for." if added else
                    "Name this screen — teach it — so the ladder can place it; working the "
                    "recorded rung would drive a recipe written for a different page."
                    if not _named_observed else
                    "Nothing to catch up — carry on from the rung already recorded."))
    _persist(bb, ledger)
    obs2 = await _observe(browser_url, bb, session_id=session.id)
    stuck = step.needs_operator()
    nxt, _ruled_out = step.walk_to_next_rung()
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
    obs = await _observe(_session_browser_url(session), bb, session_id=session.id)
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
#: Moved to `apply_steps.NO_ACCOUNT_PLATFORMS` — it decides whether a RUNG EXISTS, so it belongs
#: beside the ladder, and having it here let a read model disagree with the rung. Alias kept for
#: any caller still importing it; `aps.rung_applies` is the question to ask.
_NO_ACCOUNT_PLATFORMS = aps.NO_ACCOUNT_PLATFORMS


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

    obs = await _observe(browser_url, bb, session_id=session.id)
    block = obs.get("block")
    if block and block.get("strength") == "active":
        step.record("challenge", aps.BLOCKED, f"active {block.get('provider')}",
                    initiator=body.initiator)
        return await _save_queue_and_view(session, bb, ledger, queue, obs,
                                    ok=False, detail="A challenge is up — clear it yourself. "
                                                     "We never auto-solve.")

    # WALK PAST THE RUNGS THE DISCOVERY RULED OUT, recording each as it goes. `classify` is the
    # discovery point — "the rungs after this one do not exist until this is answered" — and until
    # now the ladder answered the question and then walked the fixed tuple anyway, which is the
    # fallback-into-recipe-mode the operator named. A ruled-out rung is still WRITTEN DOWN (the
    # skip is an event, not an absence; a corpus that loses it cannot tell "not needed" from "never
    # reached"), it just is not handed to the operator as the next thing to do.
    rung, ruled_out = step.walk_to_next_rung()
    for rung_id, why_not in ruled_out:
        step.record(rung_id, aps.SKIPPED, why_not, initiator=body.initiator)

    # PAST THE PREFIX, THE PAGE DECIDES. A tail rung IS "what the live screen calls for", so it can
    # only be chosen from a fresh look — `landing_state` is a memory, and the record can be a day
    # stale (session #25 sat on `indeed_unknown` from the day it was classified while the live tab
    # was the resume-selection screen).
    #
    # THIS LOOK ACTS ON NOTHING, and that is deliberate. The panel offers this press as "Read this
    # page", and a press that re-read AND then clicked Continue would be the panel lying about what
    # its control does — the same dishonesty as a button that cannot act, pointing the other way.
    # It also matches the rule the account rung was corrected into: look at the screen before
    # deciding what it needs, because "a rung that assumes the screen it needs will always be
    # confident and will sometimes be nowhere near it".
    #
    # The cost is one extra press, once, at the only moment we genuinely did not know where we
    # were. Every press after this one is a single legible advance.
    tail_view: Optional[dict[str, Any]] = None
    # A GENERIC-CADENCE RUNG IS RE-CHOSEN FROM A FRESH LOOK, EVERY TIME. The re-read below used to
    # run only when the walk had NO rung — so a stale landing_state serving the WRONG tail rung
    # sailed straight to execution: the ladder hunted for "Apply now" on a page deep inside the
    # form, refused, and the fresh look that would have fixed it never ran (live 2026-08-11,
    # Cornerstone — the state had regressed to `_job_posting` while the real screen was the
    # completed form). The scripted flows keep their cheaper path: their states advance through
    # the recipe's own expectations. The fuzzy cadence's whole premise is that the page decides.
    if rung is not None and step.platform and rung.id.startswith(f"{step.platform}_"):
        read = await _read_apply_page(bb, obs, browser_url)
        if read is not None:
            fresh = _name_the_screen(step, read["url"], read["text"])
            f_state = fresh.get("state") or ""
            if f_state and not f_state.endswith(("unknown", "unreadable")) \
                    and f_state != step.landing_state:
                step.landing_state = f_state
                re_rung, _re_ruled = step.walk_to_next_rung()
                if re_rung is not None:
                    rung = re_rung
    if rung is None:
        read = await _read_apply_page(bb, obs, browser_url)
        if read is not None:
            was = step.landing_state or "nothing"
            tail_view = _name_the_screen(step, read["url"], read["text"])
            step.landing_state = tail_view["state"]
            if tail_view["reclassified"]:
                step.platform = tail_view["platform"]
            # ARRIVING AT THE PLATFORM'S TERMINAL STATE *IS* THE CONFIRMATION, and it is the only
            # evidence for `submitted` this system accepts. `tail_rung_for` answers None for two
            # opposite situations — a page we do not recognise, and a flow that is FINISHED — and
            # reporting the second as "genuinely new territory" is how a sent application stays
            # queued. Measured live 2026-08-06: the tab read `indeed_apply_submitted`
            # ("Your application was submitted to MFS Investment Management") and the ladder called
            # it new territory. An application recorded as unsent is one a later run applies to
            # twice.
            if (tail_view["progress"] or {}).get("done"):
                step.record("submit", aps.OK,
                            f"confirmed from outside: the page reached {tail_view['state']}",
                            initiator=body.initiator)
                step.finish(aps.SUBMITTED,
                            f"confirmed by {tail_view['state']} at {read['url'][:90]}")
                _save_queue(bb, queue)
                # THE SAME EPILOGUE AS EVERY OTHER TERMINAL, in the same order: record before
                # close, then the cleanup crew. This branch used to finish the step and walk away
                # — no durable Application row (the applied-index stayed blind to the commonest
                # success path) and the application's tabs left standing, which is precisely the
                # old-work-into-new-search mixing of 2026-08-10.
                recorded = _record_outcome(db, step, ats_url=read["url"],
                                           search_id=(bb.world or {}).get("search_id"))
                cleanup = await _apply_cleanup(bb, obs, browser_url, step)
                bb.world.pop("apply_tab", None)
                _persist(bb, ledger)
                obs2 = await _observe(browser_url, bb, session_id=session.id)
                tidied = sum(1 for c in cleanup["closed"] if c["ok"])
                return await _save_queue_and_view(
                    session, bb, ledger, queue, obs2, ok=True,
                    extra={"cleanup": cleanup, "recorded": recorded},
                    detail=(f"Confirmed sent — the page reached {tail_view['state']}. Recorded as "
                            f"submitted for {step.title or step.job_id}"
                            + (f" at {step.company}." if step.company else ".")
                            + (f" Closed {tidied} finished tab(s); back on the search."
                               if tidied else "")))

            found = aps.tail_rung_for(step.platform, tail_view["state"])
            if found is not None:
                step.record("orient", aps.OK,
                            f"{tail_view['state']} — the ladder remembered {was}",
                            initiator=body.initiator)
                return await _save_queue_and_view(
                    session, bb, ledger, queue, obs, ok=True,
                    detail=(f"We are on {aps.screen_label(tail_view['state'])} "
                            f"({tail_view['state']}). The recipe advances it with "
                            f"{found.label} — press again to work it."))
    if rung is None:
        seen_state = (tail_view or {}).get("state") or step.landing_state or "unreadable"
        return await _save_queue_and_view(
            session, bb, ledger, queue, obs, ok=False,
            detail=f"We are on {seen_state!r} ({step.platform or 'unclassified'}) and the recipe "
                   f"has no rung for it — genuinely new territory. Read the page, drive it by "
                   f"hand, and flag the result so the next application knows this screen.")

    tab_id = ((obs.get("tabs") or [{}])[0]).get("tab_id", "")
    _note_tab_drift(bb, obs, step)      # recorded on the view; never acts on its own

    # --- THE STEPRUNNER (PLAN_step_runner.md): observe before → act → observe after → verify.
    # No rung marks itself complete: the body's `step.record(...)` below is a CLAIM, and after
    # the act the deterministic diff gets to demote it. Best-effort by construction — a blind
    # observation yields `unobserved` and the ladder behaves exactly as it did before the
    # StepRunner existed. The pair + diff + verdict is appended to the transition corpus either
    # way, because that row IS the training data this system runs on.
    import step_runner as sr
    _ext = step.job_id.split(":", 1)[-1]
    # The job_id's own prefix names the engine this posting was found on, which is what decides
    # how its results page reports the open pane (`vjk` vs `currentJobId`). Falls back to the
    # step's recorded platform, then to the engine default inside `expectation_for`.
    _job_platform = (step.job_id.split(":", 1)[0] if ":" in step.job_id else "") or ""
    _expect = sr.expectation_for(rung.id, external_id=_ext, platform=_job_platform)
    async def _observe_tab_now() -> Optional[str]:
        """THE TAB THE WORK IS ON *NOW*, re-resolved at each look. Before the apply is entered
        that is the search tab (the card and its pane live there); the instant Apply opens the
        ATS it is the application tab. Pinning it once meant every apply-path row recorded the
        SERP's AX tree and a belief about the search page while the application form sat open
        in the next tab (measured live 2026-08-04). The diff still caught the tab opening, so
        the verdicts were right and the perception half of the corpus was about the wrong page —
        the quiet kind of wrong."""
        live = await _capture_post("/list_tabs", {"browser_url": browser_url}, timeout=8.0)
        tabs = [{"tab_id": t.get("tab_id", ""), "url": t.get("url", "")}
                for t in (live.get("tabs") or [])]
        if tabs:
            apply_tab = _apply_tab(bb, {"tabs": tabs})
            if apply_tab.get("tab_id"):
                return apply_tab["tab_id"]
        return (obs.get("search_tab") or {}).get("tab_id") or tab_id

    _observe_tab = await _observe_tab_now()
    _before = await sr.observe(_capture_post, browser_url=browser_url, tab_id=_observe_tab,
                               session_id=session.id)
    # Hard-stop ONLY before the irreversible: acting on top of an unresolved mismatch there is
    # the one place a verification failure blocks rather than retries.
    if rung.id in sr.IRREVERSIBLE_RUNGS and step.last_flag == aps.MISMATCH:
        return await _save_queue_and_view(session, bb, ledger, queue, obs, ok=False,
                                    detail=f"{rung.label}: the previous step's verification is "
                                           f"unresolved — not acting irreversibly on top of a "
                                           f"mismatch. Re-run the step or resolve it first.")
    style = xs.pick_style()
    await asyncio.sleep(xs.pause_for(style, xs.BETWEEN))

    #: What this crank ACTUALLY did, filled in by whichever branch acts — the teacher half of the
    #: shadow pair. Reported by the branch rather than parsed back out of its prose: a metric that
    #: depends on a detail string's wording breaks silently the first time somebody rewords it.
    _acted: dict[str, Any] = {}
    #: Structured facts a rung wants the PANEL to carry beside its prose (`last.form_scan` from a
    #: required-fields refusal). Reported, never parsed back out of the detail string.
    _rung_extra: dict[str, Any] = {}

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
                                   # THIS engine's search tab. Naming Indeed's here sent a
                                   # LinkedIn card lookup at an Indeed page, where it could only
                                   # ever report the card missing.
                                   "tab_url": None if search_tab.get("tab_id")
                                   else _search_focus_url(bb, obs)})
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
            # STAMPED WITH ITS SUBJECT. The verdict's own `job_id` is the row that MATCHED, not the
            # job being asked about, so the stored answer had no owner — and the panel rendered it
            # beside whatever step happened to be in focus. Every sibling field on this panel
            # (`proposal`, `account_handoff`, `account_state`) is already scoped by `job_id ===
            # step.job_id` in `executeFocus`; this one was the fourth and was missing it, which is
            # the stale-source shape the 08-16 audit found four times in a day. A wrong "Already
            # applied" beside the wrong job is worse than none: it is the sentence the operator is
            # being asked to trust when they decide not to apply.
            bb.world["applied_check"] = {**verdict.as_dict(), "for_job_id": step.job_id}
            if verdict.applied:
                step.record("open_pane", aps.OK,
                            f"pane switched to {res.get('title', '')!r} — but we have already "
                            f"applied ({verdict.matched_on}: {'; '.join(verdict.evidence)})",
                            initiator=body.initiator)
                _save_queue(bb, queue); _persist(bb, ledger)
                return await _save_queue_and_view(
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
                # DID WE ACTUALLY LEAVE THE ENGINE? This asked only about Indeed, so the same
                # miss on LinkedIn — a click that matched a card or a filter and never entered
                # anything — was journaled as OK. That is the corpus row the comment above calls
                # worse than a failure, at the fourth engine-blind site in this file.
                strayed_engine = _engine_of_landed(landed_url)
                if strayed_engine and not new:
                    label = strayed_engine["label"]
                    step.record("enter_apply", aps.FAILED,
                                f"clicked {ctrl.get('name')!r} and stayed on {label} "
                                f"({landed_url[:80]}) — that was not this job's apply control",
                                initiator=body.initiator)
                    detail = (f"That click did not enter an application — we are still on {label}. "
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

    elif rung.id == "account":  # the wall most ATS put in front of an application
        import ats_accounts

        platform = step.platform or ""
        company = step.company or ""
        applies, why_not = aps.rung_applies("account", platform=platform,
                                            state=step.landing_state)
        if not applies:
            step.record("account", aps.SKIPPED, why_not, initiator=body.initiator)
            detail = f"No account needed on {platform} — skipped, not skipped over."
        elif not company:
            step.record("account", aps.UNKNOWN,
                        "no company on the step, so no account can be identified",
                        initiator=body.initiator)
            detail = ("I cannot tell whose account this would be — the step has no company. "
                      "Name it before creating credentials anywhere.")
        else:
            # LOOK BEFORE OFFERING A CREDENTIAL. This rung used to go straight to ensure_account +
            # "Sign in", asserting the wall rather than observing it. Live 2026-07-30: classify had
            # correctly named appvault (from the careers front's APPLY NOW href) and this then
            # offered "Sign in — Ahold Delhaize USA (appvault)" while the browser was still sitting
            # on the careers-front JOB POSTING with APPLY NOW un-clicked. There was no account wall
            # on screen, no account to sign into yet, and the panel said the account existed.
            #
            # The operator's word for it was "brainless", and that is the right diagnosis: the
            # ladder was walking a recipe instead of reading a page. A rung that assumes the screen
            # it needs will always be confident and will sometimes be nowhere near it.
            here = await _capture_post("/page_content",
                                       {"browser_url": browser_url,
                                        "tab_url": _apply_tab_url(bb, obs)}, timeout=15.0)
            seen = aps.classify_landing(_apply_tab_url(bb, obs),
                                        page_text=here.get("text") or "",
                                        frames=here.get("frames") or [],
                                        apply_hrefs=here.get("apply_hrefs") or [])
            if seen.kind in (al.JOB_POSTING, al.JOB_LIST):
                # The way on is the posting's own apply control, not a credential.
                step.record("account", aps.UNKNOWN,
                            f"not at an account wall — the page is a {seen.kind.replace('_', ' ')} "
                            f"({seen.state}). The way in from here is the posting's own apply "
                            f"control, not a sign-in.", initiator=body.initiator)
                bb.world = dict(bb.world or {})
                bb.world.pop("account_handoff", None)
                return await _save_queue_and_view(
                    session, bb, ledger, queue, obs, ok=False, pace=style,
                    detail=(f"We are not at {company}'s account wall — the tab is still a "
                            f"{seen.kind.replace('_', ' ')} on {seen.platform}. Nothing to sign "
                            f"into yet: press its apply control to enter the application, then "
                            f"work this rung again."))

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

    elif rung.id == aps.SUBMIT_RUNG.id:
        # THE GATE. Everything above this line is reversible; this line sends an application to a
        # real employer under the operator's name, and it is the one rung that exists to be pressed
        # BY THEM. `_check_initiator` admits several initiators; this admits exactly one.
        if body.initiator != "operator":
            step.record("submit", aps.HUMAN_REQUIRED,
                        f"refused: {body.initiator!r} may not submit an application",
                        initiator=body.initiator)
            detail = ("Submitting is the operator's, on every platform, always. Nothing was sent.")
        else:
            _ok, detail = await _work_submit_rung(step, bb, obs, browser_url, style,
                                                  initiator=body.initiator, acted=_acted)
            if _ok and step.terminal == aps.SUBMITTED:
                # THE SAME EPILOGUE AS EVERY OTHER TERMINAL — record before close, then the
                # cleanup crew. The gate's own success path was the third seam that finished a
                # step and walked away (found live minutes after wiring the second: application
                # #1 submitted and its post-app tab stood open, still claimed, while the queue
                # moved on — the exact leftover the operator's tab-cleanup mandate names).
                obs = await _observe(browser_url, bb, session_id=session.id)
                _rung_extra["recorded"] = _record_outcome(
                    db, step, ats_url=_apply_tab(bb, obs).get("url", ""),
                    search_id=(bb.world or {}).get("search_id"))
                _rung_extra["cleanup"] = await _apply_cleanup(bb, obs, browser_url, step)
                bb.world.pop("apply_tab", None)
                tidied = sum(1 for c in _rung_extra["cleanup"]["closed"] if c["ok"])
                if tidied:
                    detail += f" Closed {tidied} finished tab(s); back on the search."

    else:
        # A COMPLETE SINGLE-PAGE FORM IS ITS OWN REVIEW SCREEN. The generic cadence puts the gate
        # at `<platform>_review`, but some ATS have no review page at all — Cornerstone's whole
        # application is one form with Submit at the bottom (live 2026-08-11). On that shape the
        # form rung dead-ends by design: the census is satisfied, and the only control left is the
        # one the advance lexicon must never reach. That is not a stall — it IS the review moment:
        # the form, read back, one irreversible press from sent. So when the census says complete
        # and the page's own submit control is the only move, the ladder serves the GATE — same
        # operator-only rung, same confirm affordance, nothing new invented.
        if rung.id == f"{step.platform}_application_form":
            _pending = await _unanswered_required(browser_url,
                                                  (await _observe_tab_now()) or tab_id)
            # DEFINITELY NOTHING PENDING — not "nothing pending as far as we could tell". This is
            # the branch that promotes a step to the SUBMIT gate, so a census we could not take
            # must never reach it: `value_or(None)` makes unmeasured fall through as None rather
            # than as the empty list that means "complete".
            if _pending.value_or(None) == []:
                _scan_now = await _capture_post("/ax_scan", {
                    "browser_url": browser_url,
                    "tab_id": (await _observe_tab_now()) or tab_id}, timeout=25.0)
                import apply_recipe as _ar
                if _ar.submit_control(_ax_identities(_scan_now)):
                    # The STATE is the lever, not a prose note: `<platform>_review` is what the
                    # tail serves the SUBMIT rung for, what the panel's flow counts as 0-from-
                    # Submit, and what the gate's consequential styling keys off. One state
                    # change, every surface follows.
                    step.landing_state = f"{step.platform}_review"
                    step.record("orient", aps.OK,
                                "the form is complete and the page's only remaining control "
                                "sends it — a single-page apply's form IS its review screen",
                                initiator=body.initiator)
                    _save_queue(bb, queue)
                    return await _save_queue_and_view(
                        session, bb, ledger, queue, obs, ok=True,
                        detail=("Every required field is answered and the only control left is "
                                "Submit — the operator's gate, on every platform, always. "
                                "Review the form, then press Submit."))
        # A TAIL RUNG: advance this screen by one. Reversible by construction — `advance_control`
        # cannot reach a submit control (the lexicons are deliberately separate), so the worst this
        # can do is move the application forward a page it was always going to move forward.
        detail = await _work_advance_rung(rung, step, bb, obs, browser_url, style,
                                          initiator=body.initiator, acted=_acted,
                                          out=_rung_extra)

    # --- STEPRUNNER, the after half: observe → diff → verify → settle. The rung's record above
    # is a claim; this is the world's answer. The verifier only DEMOTES a claimed ok — a rung
    # that already reported failure needs no second witness, and a blind observation challenges
    # nothing. Every pair lands in the transition corpus regardless of verdict, because rows
    # where claim and world agree are training data too (they are the easy half the verifier
    # model learns first).
    _after = await sr.observe(_capture_post, browser_url=browser_url,
                              tab_id=await _observe_tab_now(),
                              session_id=session.id)
    _changes = sr.diff(_before, _after, expect_new_tab=(_expect.kind == "new_tab_or_nav"))
    _verdict, _evidence = sr.verify(_expect, _changes, _after)
    _claimed = step.last_flag or "none"
    # A RUNG THAT REFUSED TO ACT MUST NOT BE SCORED AS ONE THAT ACTED AND FAILED.
    #
    # `content_changed` is the right expectation for an advance that CLICKS. When the rung declined
    # — an unanswered required field, a challenge, a screen it did not recognise — nothing was
    # supposed to move, and grading that "mismatch" writes a disagreement into the transition
    # corpus that never happened. Measured live 2026-08-06 on NH Ball Bearings: the required-fields
    # guard correctly refused Continue over two unanswered screener questions, and the row it left
    # said the world disagreed with us.
    #
    # The step itself was never at risk (the demotion below only fires on a claimed OK). This is
    # about the CORPUS, which is the thing being trained.
    if _claimed in aps.NEEDS_OPERATOR:
        _verdict, _evidence = sr.READ_ONLY, (
            f"the {rung.id} rung declined to act ({_claimed}) — nothing was expected to change")
    if _verdict == sr.MISMATCH and _claimed == aps.OK:
        step.record(rung.id, aps.MISMATCH, f"world disagrees: {_evidence}",
                    initiator="step_runner")
        detail = (f"{rung.label}: the action reported ok, but {_evidence}. The rung stays open — "
                  f"press again to retry, and if it keeps happening the recipe is wrong about "
                  f"this page.")
    sr.record_transition(session_id=session.id, rung_id=rung.id,
                         action={"rung": rung.id, "job_id": step.job_id,
                                 "initiator": body.initiator},
                         expect=_expect, before=_before, after=_after, changes=_changes,
                         verdict=_verdict, evidence=_evidence, claimed=_claimed)

    # THE LADDER'S POSITION, FROM THE LOOK WE JUST TOOK. Free — `_after` already holds the url and
    # the control names. Without it the panel rendered the screen we advanced AWAY from for a whole
    # poll cycle after the act: "at most 5 screens from Submit" beside a detail line saying we had
    # just left that screen. Measured live 2026-08-06 on the first advance.
    if _after is not None and getattr(_after, "ok", False):
        _moved_to = _state_from_observation(step, _after)
        # A NON-ANSWER MUST NOT OVERWRITE AN ANSWER, whatever prefix it wears. The generic
        # describer renders its non-answers platform-prefixed (`cornerstone_unknown`), so the
        # bare-string check walked right past them — and the after-look, reading only the sparse
        # AX names, demoted a landing the classify rung had just named from the page's full text
        # (measured live 2026-08-11: `cornerstone_job_posting` → `cornerstone_unknown` one act
        # later). The look that read less does not get to overrule the look that read more.
        if _moved_to and not _moved_to.endswith(("unknown", "unreadable")):
            step.landing_state = _moved_to

    # SHADOW: what the controller WOULD have decided on the page we decided on. Taken from
    # `_before` — the look the decision was actually made against — because scoring the local
    # system against the page it would have seen AFTER the act would flatter it.
    _shadow_the_crank(rung, step, _before, _acted, step.last_flag or "none",
                      session_id=session.id)
    # And the orienter's own practice score, settled the same way: a prediction, then what happened.
    _score_the_orienter(step, rung, _before, _after, session_id=session.id)

    return await _save_queue_and_view(session, bb, ledger, queue, obs,
                                ok=step.last_flag == aps.OK, detail=detail, pace=style,
                                extra=_rung_extra or None,
                                # The same look that judged the rung also feeds the orienter, so
                                # the card the operator reads and the row the corpus keeps are
                                # two renderings of ONE observation rather than two guesses.
                                belief=_cache_belief(bb, _after),
                                verification={"rung": rung.id, "verdict": _verdict,
                                              "evidence": _evidence, "claimed": _claimed,
                                              "expected": _expect.as_row(),
                                              # The rest of the window, when it did something
                                              # this rung did not account for. Raises a hand;
                                              # never acts (closing a tab on our own initiative
                                              # is how a half-finished application dies).
                                              "window_alert": (_changes or {}).get("window_alert")})


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
    obs = await _observe(_session_browser_url(session), bb, session_id=session.id)
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
        obs = await _observe(_session_browser_url(session), bb, session_id=session.id)
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
                                                         bb))
                                         .get("tabs") or [{}])[0].get("tab_id", "")

    from routers import controller as controller_router
    commit_body = controller_router.TeachCommitBody(
        browser_url=_session_browser_url(session), tab_id=tab_id,
        # THE TASK NAMES THE TRAINING BUCKET. Hardcoded, every LinkedIn correction the teacher
        # wrote landed in Indeed's — the one place where "share what generalizes, separate what
        # doesn't" has to be got right, because a mislabelled golden row teaches the wrong engine.
        task=_apply_task_name(bb, step), goal_text=f"apply to {step.title or step.job_id}",
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
    obs = await _observe(_session_browser_url(session), bb, session_id=session.id)
    view = await _save_queue_and_view(session, bb, ledger, queue, obs,
                                ok=outcome == aps.OK,
                                detail=f"Taught {body.intent!r}: {detail}")
    view["last_step"]["taught"] = {"journaled": res.get("journaled"), "held": res.get("held"),
                                   "outcome": res.get("outcome"),
                                   "landed_state": res.get("landed_state"),
                                   "verified": res.get("verified"),
                                   # The act's own account — a `no_option` select carries the
                                   # option list it enumerated, a `describe` its reading. Dropping
                                   # it forced the teacher to re-probe by hand for evidence the
                                   # drive already fetched (2026-08-10 audit).
                                   "detail": res.get("detail", ""),
                                   "intent": body.intent, "field": (body.params or {}).get("field")}
    # The form AS THE ACT LEFT IT. Without this the census vanished at the exact moment the
    # operator most wants to re-check it (the response's last_step replaces the one that carried
    # the scan) — and "did my answer take?" is the question a correction exists to answer.
    view["last_step"]["form_scan"] = await _form_census(_session_browser_url(session), tab_id)
    return view


def _ats_brief_for_view(bb: Any, observer: Any) -> Optional[dict[str, Any]]:
    """The pre-flight brief for whatever tab the application is on, or None off-application.

    Opens its own short-lived session rather than threading `db` through `_view` and its callers:
    this is a read-only hint on a view that already does several reads, and widening a signature
    used in a dozen places to carry it would be the larger change.

    Best-effort by design: a view that 500s because a lookup table was empty would be a worse
    surface than one that omits a hint, and this is a hint.
    """
    from db import SessionLocal
    db = None
    try:
        url = (((bb.world or {}).get("apply_tab") or {}).get("url") or "")
        if not url and isinstance(observer, dict):
            url = observer.get("url") or ""
        if not url:
            return None
        import ats_brief
        db = SessionLocal()
        return ats_brief.brief(url, db)
    except Exception:  # noqa: BLE001 — a hint must never take the cockpit down
        logger.exception("ats_brief failed for the session view")
        return None
    finally:
        if db is not None:
            db.close()


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


async def _read_apply_page(bb: Any, obs: dict[str, Any],
                           browser_url: str) -> Optional[dict[str, Any]]:
    """Scan the live application tab: url, tab, AX scan, and the recognition text. None if no tab.

    The recognition text is the page's richest readable document AND the control names together —
    the apply modal's buttons are where "Use My Last Application" lives, and the body text never
    carries it.

    /page_content is fetched alongside the AX scan because the scan alone CANNOT name a screen:
    its `page_text` is empty on this server (the scan returns candidates, not prose), so the
    recognition text was really just the control names — and a marker classifier fed 26 button
    names calls a plainly readable job posting `unknown` (measured live 2026-08-11 on Cornerstone:
    the crank's own "fresh look" kept answering `cornerstone_unknown` while the classify rung,
    which reads /page_content, had already said `cornerstone_job_posting`). Two looks at one page
    must read the same page. `pick_content` also reaches into frames — the iCIMS lesson: the
    content is usually not in the document you are looking at.
    """
    url = _apply_tab_url(bb, obs)
    if not url:
        return None
    tab = next((t for t in (obs.get("tabs") or []) if t.get("url") == url), {})
    scan = await _capture_post("/ax_scan", {"browser_url": browser_url,
                                            "tab_id": tab.get("tab_id", "")}, timeout=25.0)
    try:
        content = await _capture_post("/page_content", {"browser_url": browser_url,
                                                        "tab_url": url}, timeout=15.0)
    except Exception:  # noqa: BLE001 — an unreadable body leaves the names, same as before
        content = {}
    import apply_landing as al
    body_text, _src = al.pick_content(content.get("text") or "", content.get("frames") or [])
    names = " ".join((c.get("name") or "") for c in (scan.get("candidates") or []))
    return {"url": url, "tab": tab, "scan": scan,
            "text": f"{body_text} {scan.get('page_text') or ''} {names}"}


def _name_the_screen(step: Any, url: str, text: str) -> dict[str, Any]:
    """Which platform, which screen, and how far from Submit — the orienter's verdict, in one place.

    Extracted because the TAIL needs the same answer the orient endpoint produces: a tail rung is
    "what the live page calls for", so the crank that walks it has to name the page first. Two
    copies of this would be two orienters, and the codebase has already paid for that mistake at
    the account wall (2026-07-30: "our ui is on the wrong step").

    Depth now comes from `flow_progress` for EVERY platform rather than `workday_progress` for one.
    The Indeed recipe has held a full ordered spine since it was written and nothing ever counted
    along it, so an Indeed application could not say how far it was from Submit while a Workday one
    could — the same asymmetry that left the tail unbuilt.
    """
    import apply_recipe as ar
    live = aps.classify_landing(url)
    platform = step.platform or live.platform
    named = live.platform not in ("", "unknown", "company_site")
    reclassified = named and live.platform != step.platform
    if reclassified:
        platform = live.platform
    if platform == "workday":
        state = ar.map_workday_state(url, text)
    else:
        state = ar.describe_for_ats(platform, url, text).get("state", "unknown")
    progress = ar.flow_progress(state, platform=platform)
    if not progress.get("recognised"):
        # The flow does not place it, but the describer may still have named it. Those are two
        # different questions — "is this a screen we know" vs "is it on the spine we can count
        # along" — and collapsing them would report a recognised page as new territory.
        progress = {**progress, "recognised": state not in ("unknown", "", None)}
    return {"platform": platform, "state": state, "progress": progress,
            "reclassified": reclassified, "live_platform": live.platform}


def _ax_identities(scan: dict[str, Any]) -> list[str]:
    """`role|name` for each addressable control — the form both lexicons match against."""
    return [f"{c.get('role') or ''}|{c.get('name') or ''}"
            for c in (scan.get("candidates") or []) if c.get("name")]


def _control_by_name(scan: dict[str, Any], name: str) -> dict[str, Any]:
    """The scanned candidate whose name is `name`, so the click is addressed by the role we
    ACTUALLY found. Hard-coding "button" is what returned NOT_FOUND on a link (2026-07-26)."""
    for c in (scan.get("candidates") or []):
        if (c.get("name") or "") == name:
            return c
    return {}


#: AX signals that the page is SHOWING AN ERROR. A control offering to dismiss one is the most
#: reliable tell there is: it only exists when there is an error to dismiss.
_ERROR_CONTROLS = ("dismiss error", "close error", "dismiss alert")


def _page_is_refusing(scan: dict[str, Any]) -> bool:
    """Is the page displaying an error right now? Read off the AX, not the pixels."""
    for c in (scan.get("candidates") or []):
        name = str(c.get("name") or "").lower()
        role = str(c.get("role") or "").lower()
        if role in ("alert", "alertdialog") or any(e in name for e in _ERROR_CONTROLS):
            return True
    return False


async def _refusal_text(browser_url: str, tab_url: str) -> str:
    """What the page says it is refusing about, in its own words. "" when it will not say.

    Deliberately the page's OWN sentence rather than a diagnosis of ours: the operator is being
    asked to fix something, and a paraphrase of an error is a worse instruction than the error.
    """
    try:
        res = await _capture_post("/page_content",
                                  {"browser_url": browser_url, "tab_url": tab_url}, timeout=15.0)
    except Exception:  # noqa: BLE001
        return ""
    text = " ".join((res.get("text") or "").split())
    for marker in ("We couldn't", "We could not", "Please ", "Required", "This field"):
        i = text.find(marker)
        if i >= 0:
            return text[i:i + 240].strip()
    return ""


async def _form_census(browser_url: str, tab_id: str) -> Optional[dict[str, Any]]:
    """The page's required form AS IT STANDS — unanswered AND answered — or None if we could
    not look.

    This is the cockpit's "observe the form": the scanner has always computed every required
    control's truth and then thrown the satisfied rows away, so the surface could render a count
    but never the form — and an ANSWERED-but-wrong field (the "Are you an Active Employee → Yes"
    near-self-withdrawal, 2026-08-10) was invisible by construction. Previews pass through the
    journal's own field-aware redaction: a secret-named field's value never reaches the panel
    (§4), a screener answer stays readable for the human judging it.
    """
    from interaction.contract import redact
    try:
        scan = await _capture_post("/scan_required",
                                   {"browser_url": browser_url, "tab_id": tab_id}, timeout=25.0)
    except Exception:  # noqa: BLE001
        return None
    if not scan or scan.get("ok") is False:
        return None

    def _rows(key: str) -> list[dict[str, Any]]:
        out = []
        for row in (scan.get(key) or []):
            field = str(row.get("field") or "").strip()
            if not field:
                continue
            out.append({"field": field[:90], "kind": row.get("kind") or "",
                        "required_via": row.get("required_via") or "",
                        "answered": bool(row.get("answered")), "valid": row.get("valid", True),
                        "value_preview": redact(str(row.get("value_preview") or ""), field=field),
                        # The scanner's address for this control — an anonymous input's ONLY
                        # address (structural css path), and what lets the fill and the teach
                        # seam act on a row the AX tree cannot name. An address is not a secret.
                        "selector": row.get("selector") or None,
                        "options": list(row.get("options") or []) or None,
                        # HOW MUCH OF THE OPTION LIST THIS IS. The scanner caps at 24 and says so;
                        # this projection dropped the saying-so, so a ~250-entry Country list
                        # arrived at the cockpit indistinguishable from a complete one. A tell
                        # that exists at the mcp boundary and is erased before the surface is no
                        # tell at all — the seam is exactly where a measurement gets turned back
                        # into a bare value (see `interaction.measured`).
                        "option_count": row.get("option_count"),
                        "options_truncated": bool(row.get("options_truncated"))})
        return out

    # AN ALLOW-LIST PROJECTION DROPS WHAT IT WAS NOT TOLD ABOUT, SILENTLY.
    #
    # `field_errors` was added to the scanner on 2026-08-14 so a page complaining about an
    # OPTIONAL field could block the advance, and `_unanswered_required` reads it — but this
    # function never carried it, so the gate was inert in production while its test (which
    # supplies a census directly) passed. A fix that cannot fail its own test and cannot fire in
    # the world is worse than no fix: it closes the ticket.
    #
    # `page_errors` had the same hole. `optional` too — the census's own "filed for ADDRESSING,
    # never for the gate" rows, which the teach seam needs to reach a field the required walk
    # skipped.
    #
    # And `url` — the census's PROOF OF LIFE, which 2026-08-12 made an error when absent — was
    # read from `steps[0].url` while the scanner returns it at the top level. That is why a scan
    # that plainly ran reported `url: None` on 08-14. Top level first, the old path as fallback.
    return {"unanswered": _rows("unanswered"), "answered": _rows("answered"),
            "optional": _rows("optional"),
            "field_errors": list(scan.get("field_errors") or []),
            "page_errors": list(scan.get("page_errors") or []),
            "url": scan.get("url") or (scan.get("steps") or [{}])[0].get("url") or ""}


def _census_story(census: Optional[dict[str, Any]]) -> str:
    """One honest sentence about the form as scanned — "" when we could not look.

    Exists so an empty fill PLAN cannot masquerade as an empty PAGE: "Planned 0 of 0" beside
    "8 required fields, all answered" are two different mornings, and the panel showed them
    as one (2026-08-10)."""
    if not census:
        return ""
    answered, unanswered = len(census["answered"]), len(census["unanswered"])
    if not answered and not unanswered:
        return "The page shows no required form fields. "
    if not unanswered:
        return (f"The page's own required form: all {answered} field(s) answered — "
                f"review them below before advancing. ")
    return (f"The page's own required form: {unanswered} unanswered, {answered} answered. ")


async def _unanswered_required(browser_url: str, tab_id: str,
                               census_out: Optional[dict[str, Any]] = None) -> Reading:
    """What the page still wants, as a READING — the answer plus whether we managed to take it.

    "The form is complete" licenses an advance and "we could not check" does not, and this
    function has always known the difference: it returned `None` for the second and `[]` for the
    first, with a docstring telling every caller not to merge them. That is a hand-rolled
    tri-state whose correctness lives in each caller's memory — and one of those callers promotes
    the step to the SUBMIT gate on `_pending == []`, which is the highest-stakes place in the
    system for somebody to later "simplify" into `if not _pending`.

    So it returns a `Reading` (interaction.measured) instead. `bool()` on one raises, which means
    the simplification cannot be written; the caller has to say which of the three cases it means.
    Same distinction as before, now enforced rather than remembered.

    `census_out`, when given, receives the full form census from the SAME look — the refusal this
    feeds must carry the form it refused over, not a count of it (a refusal that names the fix in
    prose is the cockpit gap of 2026-08-10).
    """
    census = await _form_census(browser_url, tab_id)
    if census is None:
        return Reading.unmeasured(
            "the form census did not come back — we cannot tell a complete form from an "
            "unreadable one")
    if census_out is not None:
        census_out["form_scan"] = census
    out: list[str] = []
    for row in census["unanswered"]:
        # VOLUNTARY groups (required_via 'none' — the unstarred EEO radios) are in the census so
        # they can be SEEN and TAUGHT, but they must not block an advance: the gate's contract
        # stays "required fields only".
        if row.get("required_via") == "none":
            continue
        label = row["field"][:60] if len(row["field"]) <= 60 else ""
        if label:
            out.append(label)
    # A FIELD THE PAGE IS COMPLAINING ABOUT BLOCKS THE ADVANCE TOO, whatever its requiredness.
    #
    # This gate's whole question was "which REQUIRED fields are UNANSWERED", and a page can refuse
    # for a reason that answers neither half. Live 2026-08-14 on Boston Children's: the resume
    # parser filled the OPTIONAL Job Description past the form's 500-char limit, the page printed
    # the complaint in red under the control, and the census reported zero unanswered — so the rung
    # clicked Save & Continue over a form the page had already refused, twice, and only the
    # StepRunner's "nothing observable changed" caught it. The operator's rule from 2026-08-12,
    # one axis over: *"regardless of whether it's required or not"* — an optional field filled with
    # the wrong answer is the same error.
    for err in (census.get("field_errors") or []):
        label = str(err.get("field") or "")[:60]
        if label and label not in out:
            out.append(label)
    return Reading.measured(
        out, how=f"census of {census.get('url') or 'the open form'}: "
                 f"{len(census.get('unanswered') or [])} unanswered, "
                 f"{len(census.get('field_errors') or [])} complained about")


def _refuse(out: Optional[dict[str, Any]], r: "refusal.Refusal") -> str:
    """Record a refusal so the cockpit can render its EXIT, and return the prose the caller
    already returned.

    The whole migration in one function: a rung handler still `return`s a sentence and nothing
    downstream changes shape, while `out` — which `_save_queue_and_view` folds into `last` —
    gains the structured way out. That is what lets the surface put a button under the sentence
    instead of leaving the operator to work out which endpoint the prose was pointing at.
    """
    if out is not None:
        out["refusal"] = r.as_dict()
    return str(r)


async def _work_advance_rung(rung: Any, step: Any, bb: Any, obs: dict[str, Any],
                             browser_url: str, style: Any, *, initiator: str,
                             acted: Optional[dict[str, Any]] = None,
                             out: Optional[dict[str, Any]] = None) -> str:
    """Advance the application ONE SCREEN with the page's own control. Reversible, always.

    Two refusals, both deliberate:

    * **An unanswered required field stops the advance.** The recipe words several of these screens
      "autofill + Continue", and clicking Continue over a half-filled form is how an application
      arrives at Submit missing answers nobody chose to leave out. The fill is a separate,
      already-built action (`/apply_fill`) with its own answer store and its own refusals — this
      rung points at it rather than reaching past it.
    * **No advance control means stop, not guess.** `advance_control` cannot reach a submit
      control, so the failure mode here is a rung that does nothing, which is the safe one.
    """
    import apply_recipe as ar
    from controller.decide import advance_control

    read = await _read_apply_page(bb, obs, browser_url)
    if read is None:
        step.record(rung.id, aps.UNKNOWN, "no application tab to advance", initiator=initiator)
        # THE EXIT EXISTED AND HAD NO BUTTON. "Reopen the application first" named the remedy in
        # prose and left the operator to find it — and on 2026-08-14 the step whose tab had been
        # closed by another step's cleanup sat here with the cockpit still offering "Continue".
        return _refuse(out, refusal.Refusal(
            what="There is no application tab open to advance.",
            why="this step's page is gone — it was closed, or the browser was relaunched.",
            exit=refusal.Exit(
                label="Start it again", endpoint="/apply_reopen",
                body={"job_id": step.job_id,
                      "reason": "its page was gone when the ladder tried to advance it"},
                why="Re-walks this application from the posting; the rungs already walked are "
                    "archived on the step, not lost.")))

    scan, tab = read["scan"], read["tab"]
    tab_id = tab.get("tab_id", "")
    action = ar.advance_action(step.platform, rung.id)

    # EVERY ADVANCE IS CHECKED, not just the ones the recipe words with "fill".
    #
    # The first version keyed on the verb in the recipe's action text, which meant the guard's
    # coverage depended on prose — and the very next screen added was worded "highlight resume
    # details + Continue" and would have walked straight past it (2026-08-06). A safety check whose
    # scope is a substring of a description is one nobody can reason about.
    #
    # The cost is one scan per advance; the benefit is that no screen on any platform gets a
    # Continue clicked over an answer nobody chose to leave out. And a screen with nothing required
    # returns `[]` immediately, which is the common case and cheap.
    reading = await _unanswered_required(browser_url, tab_id, census_out=out)
    if reading.is_unmeasured():
        step.record(rung.id, aps.UNKNOWN,
                    f"could not scan the form for required fields — {reading.why}",
                    initiator=initiator)
        return _refuse(out, refusal.Refusal(
            what="I could not read this form's required fields.",
            why=f"not clicking Continue blind — {reading.why}",
            exit=refusal.Exit(
                label="Re-read the form", endpoint="/apply_fill", body={"execute": False},
                why="Reads the open form again and shows it as it stands. Types nothing.")))
    pending = reading.require("the form census")
    if pending:
        step.record(rung.id, aps.HUMAN_REQUIRED,
                    f"{len(pending)} required field(s) unanswered: {', '.join(pending[:6])}",
                    initiator=initiator)
        # The census this refusal was made FROM rides `out` into the panel (`last.form_scan`),
        # so the cockpit renders the fields as pressable work instead of prose pointing at an
        # endpoint it never shows (the 2026-08-10 audit's core finding).
        return _refuse(out, refusal.Refusal(
            what=(f"This screen still wants {len(pending)} answer(s) — "
                  f"{', '.join(pending[:6])}" + ("…" if len(pending) > 6 else "") + "."),
            why=("an application must not reach Submit with answers nobody chose to leave out"),
            # The census this refusal was made FROM is already riding `out` as `form_scan`; the
            # exit points at the surface that renders it as pressable work.
            exit=refusal.Exit(
                label="Fill what the profile knows", endpoint="/apply_fill",
                body={"execute": False},
                why="Plans every field we hold an answer for, and shows the rest to answer by "
                    "hand. Types nothing until you press Fill.")))

    # THE FORM MAY BE REFUSING US, not failing us. A Continue that no-ops beside a visible error is
    # a rejected form, and the ladder's own message for a mismatch — "if it keeps happening the
    # recipe is wrong about this page" — is exactly the wrong diagnosis there: the recipe is right,
    # the page is saying no. Live 2026-08-06, Indeed's resume-review screen: "We couldn't pull any
    # work experience or education from your resume", Continue inert, and the drive would have
    # retried it forever while blaming its own map.
    #
    # Gated on the PREVIOUS attempt having mismatched, so a stale banner from something else does
    # not stop a first try. Two facts together — we pressed it and nothing moved, and the page is
    # showing an error — are what mean refusal; either alone does not.
    if step.last_flag == aps.MISMATCH and _page_is_refusing(scan):
        says = await _refusal_text(browser_url, read["url"])
        step.record(rung.id, aps.HUMAN_REQUIRED,
                    f"the form is refusing: {says or 'an error is displayed we could not read'}",
                    initiator=initiator)
        return ("This screen is refusing to advance, and it is showing an error rather than "
                "ignoring the click"
                + (f' — it says: "{says}"' if says else " that we could not read")
                + ". The recipe is right about the page; the page wants something. This one is "
                  "yours — nothing here can be answered on your behalf.")

    # THE RECIPE FIRST, the generic lexicon second. Where we have actually stood on a screen and
    # read its buttons, that observation beats a substring guess — and on Indeed's highlights
    # screen the lexicon cannot reach the real control ("Review details") while the only thing it
    # CAN match is the exit.
    identities = _ax_identities(scan)
    control = ar.named_control(step.platform, rung.id, identities) or advance_control(identities)
    if not control:
        step.record(rung.id, aps.UNKNOWN,
                    f"no advance control among {len(scan.get('candidates') or [])} elements "
                    f"(recipe expects {action or 'Continue'!r})", initiator=initiator)
        return (f"I cannot see the control that advances this screen — the recipe expects "
                f"{action or 'Continue'}. Scroll it into view, or drive it by hand and flag it.")

    ctrl = _control_by_name(scan, control)
    if acted is not None:
        acted.update({"intent": "click", "params": {"control": control},
                      "rationale": f"the recipe advances {rung.id} with {action or 'Continue'!r} "
                                   f"and {control!r} is the control on the page",
                      "evidence": ("recipe.action", "ax_identities")})
    res = await _capture_post("/execute", {
        "browser_url": browser_url, "tab_id": tab_id, "action_id": "click", "target_bbox": {},
        "target_role": ctrl.get("role") or "button", "target_name": control,
        "driver": "humanized"})
    await asyncio.sleep(xs.pause_for(style, xs.NAVIGATION))
    if res.get("outcome") not in ("ok", "committed_unconfirmed"):
        step.record(rung.id, aps.FAILED,
                    f"click on {control!r} returned {res.get('outcome') or 'nothing'}",
                    initiator=initiator)
        return f"Could not click {control!r} — the screen has not moved."

    step.record(rung.id, aps.OK, f"clicked {control!r} to advance {rung.id}", initiator=initiator)
    return (f"Clicked {control!r}. Step again to see which screen that landed on — the recipe "
            f"expects one of {', '.join(ar.expected_after(step.platform, rung.id)) or 'unknown'}.")


#: How long to wait for a submit to LAND before concluding it did not. Bounded: an application
#: that has not confirmed in ~6s gets reported honestly as unconfirmed rather than waited on
#: forever, and the operator looks at the tab. Erring long is safe here — the click has already
#: happened and nothing else is racing us.
_SUBMIT_SETTLE_TRIES = 6
_SUBMIT_SETTLE_WAIT = 1.0


async def _work_submit_rung(step: Any, bb: Any, obs: dict[str, Any], browser_url: str,
                            style: Any, *, initiator: str,
                            acted: Optional[dict[str, Any]] = None) -> tuple[bool, str]:
    """Send the application. The operator has already pressed the button that got us here.

    **The captcha race is why this is one function and not two.** `apply_recipe.APPLY_BRANCHES`
    records it from a live loss (Purple Carrot, 2026-07-17): a solved reCAPTCHA token expires in
    1–2 minutes and expires SILENTLY — Submit just goes disabled, no error text, no alert, page
    looks identical. So the gate is checked and the submit fires in the SAME pass, with no human
    round-trip and no re-probe in between. Anything that asks a question here loses the race.

    An ACTIVE challenge escalates and never gets solved for the operator — that rule does not bend
    at the gate, it matters more here.
    """
    import apply_recipe as ar

    vis = await _capture_post("/challenge_visibility", {"browser_url": browser_url}, timeout=8.0)
    if vis and vis.get("ok") is not False and vis.get("blocking"):
        step.record("submit", aps.BLOCKED,
                    f"challenge up at the gate: {vis}", initiator=initiator)
        return False, ("A challenge is up on the submit screen. Clear it yourself — we never "
                       "auto-solve — and press Submit again the moment it clears. The token "
                       "expires in about a minute, so do not wait.")

    read = await _read_apply_page(bb, obs, browser_url)
    if read is None:
        step.record("submit", aps.UNKNOWN, "no application tab at the gate", initiator=initiator)
        return False, "There is no application tab open. Nothing was sent."

    # ARE WE STILL AT THE GATE? The rung comes from `landing_state`, which is a memory, and the one
    # way it can be wrong here is the expensive way: a submit that LANDED but was not recorded
    # leaves the rung saying `submit`, and pressing it again would send a second application.
    #
    # So the live page is consulted before the click, not only after it. A page that has already
    # reached the platform's terminal state is not a gate to press — it is a confirmation to
    # record, and recording it is what stops the retry becoming a duplicate.
    here = _name_the_screen(step, read["url"], read["text"])
    if (here["progress"] or {}).get("done"):
        step.record("submit", aps.OK,
                    f"already sent — the page is on {here['state']}", initiator=initiator)
        step.finish(aps.SUBMITTED, f"confirmed by {here['state']} at {read['url'][:90]}")
        step.landing_state = here["state"]
        return True, (f"This application was already sent — the page is on {here['state']}. "
                      f"Recorded as submitted; nothing was pressed again.")

    scan, tab = read["scan"], read["tab"]
    control = ar.submit_control(_ax_identities(scan))
    if not control:
        step.record("submit", aps.UNKNOWN,
                    f"no submit control among {len(scan.get('candidates') or [])} elements",
                    initiator=initiator)
        return False, ("I cannot see a submit control on this screen. Nothing was sent — scroll it "
                       "into view and press again, or check we are really at the review step.")

    ctrl = _control_by_name(scan, control)
    if acted is not None:
        acted.update({"intent": "click", "params": {"control": control},
                      "rationale": f"the operator pressed the gate and {control!r} is this page's "
                                   f"submit control",
                      "evidence": ("operator", "ax_identities")})
    res = await _capture_post("/execute", {
        "browser_url": browser_url, "tab_id": tab.get("tab_id", ""), "action_id": "click",
        "target_bbox": {}, "target_role": ctrl.get("role") or "button",
        "target_name": control, "driver": "humanized"})
    await asyncio.sleep(xs.pause_for(style, xs.NAVIGATION))

    if res.get("outcome") not in ("ok", "committed_unconfirmed"):
        # A DISABLED SUBMIT IS A CAPTCHA SUSPECT FIRST. The token expiring is silent and looks
        # exactly like a field problem; diagnosing fields first is how the 2026-07-17 loss was
        # misread for an hour.
        step.record("submit", aps.FAILED,
                    f"click on {control!r} returned {res.get('outcome') or 'nothing'}",
                    initiator=initiator)
        return False, (f"{control!r} did not take. If it looked enabled, suspect an expired "
                       f"captcha token before the fields — re-check the challenge and press again "
                       f"immediately. Nothing was sent.")

    # CONFIRM FROM OUTSIDE — AND WAIT FOR THE NAVIGATION. `/execute` returning ok means the click
    # dispatched, and this codebase has twice recorded a dispatched click as an accomplished act.
    # `submitted` is the one flag that means success, so it is the last one allowed to be claimed
    # on a dispatch alone.
    #
    # But a single look after the click RACES the navigation, and losing that race is expensive in
    # the one direction that matters: the application really was sent, the page had not moved yet,
    # and the step is filed `unknown`. Measured live 2026-08-06 on MFS Investment Management —
    # Submit worked, the tab reached `/form/post-apply` ("Your application was submitted to…"),
    # and the record said it had not. An application recorded as unsent is one a later run will
    # apply to a second time.
    #
    # Same shape as `run_query`'s confirm, which already re-checks for exactly this reason. Poll a
    # few short beats rather than sleeping once: a fast navigation is confirmed immediately and a
    # slow one is still caught.
    state, landed, after = "", None, None
    for _ in range(_SUBMIT_SETTLE_TRIES):
        # RE-LIST THE TABS EVERY PASS. `obs` was taken BEFORE the click, so resolving the apply tab
        # from it returns the url we submitted FROM — and a settle that re-reads a cached address
        # is six looks at the same stale page wearing the costume of patience. Measured live
        # 2026-08-06 on Bristol County Savings Bank: the tab was on `/form/post-apply` the whole
        # time and the loop reported `indeed_apply_review` for its full six seconds.
        #
        # Same rule the crank's `_observe_tab_now` already states: the tab the work is on NOW,
        # re-resolved at each look.
        fresh = await _capture_post("/list_tabs", {"browser_url": browser_url}, timeout=8.0)
        after = await _read_apply_page(bb, {"tabs": fresh.get("tabs") or []}, browser_url)
        landed = _name_the_screen(step, after["url"], after["text"]) if after else None
        state = (landed or {}).get("state") or ""
        if landed and (landed["progress"] or {}).get("done"):
            break
        await asyncio.sleep(_SUBMIT_SETTLE_WAIT)
    step.landing_state = state or step.landing_state
    if landed and (landed["progress"] or {}).get("done"):
        step.record("submit", aps.OK, f"clicked {control!r}; the page reached {state}",
                    initiator=initiator)
        step.finish(aps.SUBMITTED, f"submitted via {control!r}; confirmed by {state}")
        return True, (f"Sent. {control!r} was pressed and the page reached {state} — recorded as "
                      f"submitted.")

    step.record("submit", aps.UNKNOWN,
                f"clicked {control!r} but the page reads {state or 'unreadable'}, not a "
                f"submitted state", initiator=initiator)
    return False, (f"{control!r} was pressed but the page reads {state or 'unreadable'} rather "
                   f"than a confirmation, so this is NOT recorded as submitted. It may be a "
                   f"post-submit branch (an AI-recruiter gate, a survey) or the click may not have "
                   f"taken — look at the tab before pressing anything again.")


#: What the local system is asked to have an opinion about, per rung. Shadow rows are only worth
#: journaling where the controller COULD have a view — the mapping is the honest statement of which
#: half of the ladder the inner layers are being scored on.
_RUNG_INTENT: dict[str, str] = {
    "open_pane": "click", "verify_identity": "observe", "enter_apply": "click",
    "classify": "observe", "account": "observe", "submit": "click",
}


def _shadow_the_crank(rung: Any, step: Any, before: Any, acted: dict[str, Any],
                      outcome: str, *, session_id: int) -> None:
    """Journal what the CONTROLLER would have decided here, beside what we actually did.

    Shadow mode has existed since the controller was built and had **never run outside a test** —
    `shadow_step` was imported by `test_controller_evals` and by nothing else, so
    `metrics.shadow_agreement` was scoring an empty set and the controller's read of a live page
    was never once compared to a real one. The module is one call from being live; this is the call.

    FREE by construction: `model=None`, so only the deterministic rungs run. `shadow.py`'s own rule
    is that a shadow drive must not spend unless the caller opts in, and a crank the operator
    presses is not a place to start spending on their behalf.

    Never raises. A shadow row is an observation about ourselves; failing to take one must not cost
    the operator the step they asked for.
    """
    try:
        from controller.bundle import build_bundle
        from controller.shadow import shadow_step
        from interaction.decision import Decision

        teacher = Decision(
            intent=acted.get("intent") or _RUNG_INTENT.get(rung.id, "observe"),
            params=acted.get("params") or {},
            confidence=1.0, rung="teacher",
            rationale=acted.get("rationale") or f"the operator worked the {rung.id} rung",
            evidence=tuple(acted.get("evidence") or ("operator",)))
        bundle = build_bundle(
            task="apply", url=getattr(before, "url", "") or "",
            goal_text=f"{step.title or step.job_id} at {step.company or 'unknown'}",
            ats=step.platform or None,
            ax_candidates=list(getattr(before, "candidates", None) or []),
            belief=getattr(before, "belief", None),
            window=getattr(before, "window", None))
        shadow_step(teacher, bundle, session_id=str(session_id), outcome=outcome)
    except Exception:  # noqa: BLE001 — measuring ourselves must never break the drive
        pass


def _state_from_observation(step: Any, o: Any) -> str:
    """Name the screen an observation was taken on, from the observation alone.

    Free — no extra scan. `Observation` already carries the url and the AX control names, which is
    exactly what `describe_for_ats` reads.
    """
    import apply_recipe as ar
    names = " ".join(str(c.get("name") or "") for c in (getattr(o, "candidates", None) or []))
    return ar.describe_for_ats(step.platform, getattr(o, "url", "") or "",
                               names).get("state", "unknown")


def _score_the_orienter(step: Any, rung: Any, before: Any, after: Any, *,
                        session_id: int) -> None:
    """Settle the orienter's prediction for this crank: did the page go where the recipe said?

    THE ORIENTER WAS BEING USED AND NEVER PRACTISED. It names a state on every look, the recipe
    names the states that one may lead to, and one action later the answer is sitting in the
    StepRunner's `after` observation. Nobody was closing that loop, so the orienter could not get
    better at the one thing it does — and "practise" was a word about it rather than a mechanism.

    Read off observations already taken: no extra scan, no extra cost. `after.url` is enough for
    the Indeed spine (the recipe's URL patterns name every screen in it); a platform whose states
    are only distinguishable by text scores `unscored` rather than guessing, which keeps the
    accuracy number honest about what it covers.

    Never raises — see `_shadow_the_crank`.
    """
    try:
        import apply_recipe as ar
        import orientation_log

        # FROM THE BEFORE-OBSERVATION, not from `step.landing_state` — the act has already run by
        # the time this is called and several rungs update the record on their way out, so reading
        # the record here would score the prediction against the state it predicted.
        state_before = _state_from_observation(step, before)
        predicted = ar.expected_after(step.platform, state_before)
        if not predicted:
            return
        state_after = _state_from_observation(step, after)
        orientation_log.record_prediction(
            session_id, platform=step.platform or "", state_before=state_before or "",
            predicted=predicted, state_after=state_after, rung=rung.id,
            step_job_id=step.job_id)
    except Exception:  # noqa: BLE001 — a scorecard must not break the thing it scores
        pass


def _depth_phrase(progress: dict[str, Any]) -> str:
    """" · at most 4 screens from Submit", or "" when the flow cannot place this page.

    Worded as a CEILING because that is what it is: platforms skip screens whose answers the
    profile already holds, and skipping only ever shortens the path. "4 screens from Submit" would
    claim a precision about this particular application that the spine cannot give.
    """
    left = progress.get("steps_to_submit")
    if left is None:
        return ""
    if left == 0:
        return " · at the Submit gate"
    return f" · at most {left} screen{'s' if left != 1 else ''} from Submit"


async def _save_queue_and_view(session, bb, ledger, queue: aps.Queue, obs, *, ok: bool,
                               detail: str, pace=None,
                               verification: Optional[dict[str, Any]] = None,
                               belief: Optional[dict[str, Any]] = None,
                               extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Persist, then render — WITH a fresh observation. Async so the observer fires on every
    apply-step render, not only on the poll: the moment after an action is exactly when the world
    is most likely to have moved (operator-directed 2026-07-30)."""
    bb.world = dict(bb.world or {})
    bb.world["apply_queue"] = queue.as_dict()
    _persist(bb, ledger)
    last: dict[str, Any] = {"ok": ok, "action": "apply_step", "detail": detail,
                            "queue": queue.summary()}
    if verification is not None:
        # The StepRunner's verdict, beside the claim it judged — so the cockpit can render
        # "the action said ok and the page carries vjk=…" instead of a bare flag.
        last["verification"] = verification
    if pace is not None:
        last["pace"] = xs.describe(pace)
    if extra:
        # A rung's structured facts (a refusal's `form_scan`), carried beside the prose so the
        # panel can render the work instead of describing it. Reserved keys stay the panel's own.
        last.update({k: v for k, v in extra.items()
                     if k not in ("ok", "action", "detail", "queue")})
    observer = await _orient_now(bb, obs, _session_browser_url(session), belief=belief)
    return _view(session, bb, ledger, obs, page=_current_page(obs, bb),
                 awaiting="apply", last=last, observer=observer)



class RunBody(BaseModel):
    initiator: str = "operator"
    #: Hard ceiling on cranks. Not a target — the loop stops the moment anything wants a human,
    #: and this only bounds the case where nothing does and nothing finishes either.
    max_steps: int = 12
    #: Assert which application is being driven, same contract as `apply_step`: name it and we
    #: check it, omit it and the queue decides.
    job_id: Optional[str] = None


#: Why the drive stopped. Each is a FACT about the world rather than a policy knob, which is what
#: keeps the loop honest: it does not decide when to hand over, it notices.
STOP_GATE = "gate"                  # the next act is the irreversible one — always the operator's
STOP_NEEDS_OPERATOR = "needs_operator"   # a rung recorded blocked/human_required/unknown
STOP_REFUSED = "refused"            # a rung declined and named what it wants
STOP_DONE = "done"                  # the step reached a terminal flag
STOP_NO_PROGRESS = "no_progress"    # two cranks, same rung, nothing moved
STOP_BUDGET = "budget"              # max_steps reached with work still to do
STOP_NO_STEP = "no_step"            # nothing in the queue to drive


@router.post("/api/session_control/{session_id}/run")
async def run(session_id: int, body: RunBody,
              db: Session = Depends(get_db)) -> dict[str, Any]:
    """Crank until something actually needs a human — the composition, not a new capability.

    WHY THIS EXISTS. Driving one application from the cockpit on 2026-08-14 took about fifteen
    presses, every one of them "yes, continue", through gates that already exist: each advance
    runs the required-field census, the StepRunner verify, and the operator-only Submit gate. The
    rails were built and the composition was missing, so the operator was the loop — and an
    operator who is the loop is not supervising it, they are executing it.

    IT ADDS NO AUTHORITY. Every iteration is the same `apply_step` the button calls, with the same
    initiator, the same journalling, the same refusals. Nothing here can reach a control
    `apply_step` could not, and the Submit gate is unreachable by construction: `advance_control`
    and `submit_control` are deliberately separate lexicons, and this loop STOPS at a consequential
    action rather than pressing it. That is the whole safety argument — it is not "we trust the
    loop", it is "the loop cannot do anything the single press could not".

    IT STOPS ON FACTS, NOT ON A BUDGET. `max_steps` is a backstop for the case where nothing wants
    a human and nothing finishes; the real stops are the world's: a gate, a refusal, a rung that
    recorded blocked/human_required/unknown, a terminal flag, or a rung that ran twice and moved
    nothing. Every stop names itself, so "why did it hand back" is answered before it is asked.
    """
    _check_initiator(body.initiator)
    steps_run: list[dict[str, Any]] = []
    view: dict[str, Any] = {}
    stop, stop_detail = STOP_BUDGET, ""
    last_rung: Optional[str] = None
    repeats = 0
    #: The apply tab's url at the end of the previous crank. None on the first pass, because "we
    #: have not looked yet" is not "it has not moved" — the same distinction `interaction.measured`
    #: exists to keep, applied to a loop variable.
    last_url: Optional[str] = None

    for _ in range(max(1, min(int(body.max_steps or 12), 40))):
        session, bb, ledger = _load(session_id, db)
        queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
        step = queue.current()
        if step is None:
            stop, stop_detail = STOP_NO_STEP, "nothing in the queue is unfinished."
            break

        # THE GATE, CHECKED BEFORE THE CRANK RATHER THAN AFTER. `_resolve_next_action` already
        # marks the irreversible act `consequential`; asking it here means the loop never dispatches
        # the press it is not allowed to make, instead of dispatching and hoping something downstream
        # refuses. The operator's gate is the one rung this never touches, on every platform, always.
        obs_now = await _observe(_session_browser_url(session), bb,
                                 session_id=session.id)
        nxt = _resolve_next_action(step, await _orient_now(bb, obs_now,
                                                           _session_browser_url(session)))
        if (nxt or {}).get("consequential"):
            stop = STOP_GATE
            stop_detail = (f"{step.title or step.job_id} is at the gate: "
                           f"{(nxt or {}).get('label') or 'Submit'}. This one is yours — nothing "
                           f"here presses it.")
            view = _view(session, bb, ledger, obs_now, page=_current_page(obs_now, bb),
                         awaiting="apply")
            break

        # THE RECORD MUST CATCH UP BEFORE THE NEXT CRANK, or the loop acts on a stale rung.
        #
        # Measured on the loop's first real drive (2026-08-14): Save & Continue took Boston
        # Children's from its careers front onto the BrassRing tenant — a different page entirely
        # — and the step's `landing_state` still said `company_site_application_form`, so the next
        # iteration resolved the SAME rung and clicked whatever matched on the new screen. It
        # found "Save" (save this job to a list) and pressed it. Harmless here; not harmless in
        # general, and it is precisely the wrong-target class this system keeps paying for.
        #
        # `reconcile_step` is the remedy and it already exists — "the browser is truth, the record
        # is memory, so when they disagree memory yields". The loop's job is to COMPOSE it, not to
        # re-derive it: when the application tab has moved since the last crank, catch the record
        # up first. A single press never hit this because a human looks at the screen between
        # presses; the loop is exactly what makes it reachable.
        apply_url = (_apply_tab(bb, obs_now) or {}).get("url") or ""
        if last_url is not None and apply_url and apply_url != last_url:
            try:
                await reconcile_step(session_id, ReconcileStepBody(initiator=body.initiator), db)
            except HTTPException:
                pass          # nothing to reconcile is not a reason to stop driving
            session, bb, ledger = _load(session_id, db)
            queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
            step = queue.current() or step
            obs_now = await _observe(_session_browser_url(session), bb,
                                     session_id=session.id)
            nxt = _resolve_next_action(step, await _orient_now(bb, obs_now,
                                                               _session_browser_url(session)))
            if (nxt or {}).get("consequential"):
                stop = STOP_GATE
                stop_detail = (f"{step.title or step.job_id} reached the gate: "
                               f"{(nxt or {}).get('label') or 'Submit'}. This one is yours.")
                view = _view(session, bb, ledger, obs_now, page=_current_page(obs_now, bb),
                             awaiting="apply")
                break
            # A MOVED PAGE IS PROGRESS, whatever the rung ends up being called. Counting a repeat
            # across a real transition would fire the no-progress guard on the very move it should
            # be celebrating — which is exactly what happened on the first live drive, where Save
            # & Continue crossed from the careers front onto the BrassRing tenant and the guard
            # read the unchanged rung id as a stall.
            repeats, last_rung = 0, None
        last_url = apply_url

        before_flag, before_terminal = step.last_flag, step.terminal
        rung_id = (nxt or {}).get("id") or ""
        view = await apply_step(session_id, ApplyStepBody(initiator=body.initiator,
                                                          job_id=body.job_id), db)
        last = view.get("last_step") or {}
        steps_run.append({"rung": rung_id, "ok": last.get("ok"),
                          "detail": (last.get("detail") or "")[:200]})

        # Re-read rather than trusting the view: the step object above is a copy from before the
        # crank, and what matters is what the crank RECORDED.
        _s, bb_after, _l = _load(session_id, db)
        after = aps.Queue.from_dict((bb_after.world or {}).get("apply_queue")).current()

        if last.get("refusal"):
            stop, stop_detail = STOP_REFUSED, last.get("detail") or "a rung declined."
            break
        if after is None or (after.job_id == step.job_id and after.done):
            stop, stop_detail = STOP_DONE, f"{step.title or step.job_id} reached a terminal flag."
            break
        if (after.last_flag or "") in aps.NEEDS_OPERATOR:
            stop = STOP_NEEDS_OPERATOR
            stop_detail = last.get("detail") or f"the {rung_id or 'last'} rung wants a human."
            break

        # NOTHING MOVED, TWICE. A rung that reports ok and leaves the world where it was is the
        # loop's own version of the mismatch the StepRunner catches per-step — and left unchecked
        # it is how a drive spends its whole budget re-clicking one control.
        if rung_id and rung_id == last_rung and \
                (after.last_flag, after.terminal) == (before_flag, before_terminal):
            repeats += 1
            if repeats >= 1:
                stop = STOP_NO_PROGRESS
                stop_detail = (f"the {rung_id} rung ran twice and nothing changed — stopping "
                               f"rather than spending the budget on it.")
                break
        else:
            repeats = 0
        last_rung = rung_id

    if not view:
        session, bb, ledger = _load(session_id, db)
        obs_now = await _observe(_session_browser_url(session), bb,
                                 session_id=session.id)
        view = _view(session, bb, ledger, obs_now, page=_current_page(obs_now, bb), awaiting="apply")

    # THE HANDBACK, ON THE RECORD. A loop that stops without saying why is the same dead end as a
    # refusal without an exit — and `next_up` is what the operator reads to know whether the stop
    # was the expected one.
    session, bb, ledger = _load(session_id, db)
    bb.log("run", f"{len(steps_run)} rung(s) driven, stopped: {stop}",
           why=stop_detail,
           next_up=("The operator's press — nothing here may make it."
                    if stop in (STOP_GATE, STOP_NEEDS_OPERATOR, STOP_REFUSED)
                    else "The queue moves on." if stop == STOP_DONE
                    else "Press again to continue driving."))
    _persist(bb, ledger)

    view = dict(view)
    view["run"] = {"steps": steps_run, "count": len(steps_run),
                   "stopped": stop, "detail": stop_detail}
    return view


class ApplyFlagBody(BaseModel):
    job_id: str
    flag: str                      # a terminal flag from apply_steps
    detail: str = ""
    initiator: str = "operator"
    #: Record `submitted` even though the page does not confirm it. The escape hatch for a
    #: confirmation we could not read (an emailed receipt, a tab already closed) — never the
    #: default, and it is written into the record so the claim is marked as unverified forever.
    override_verifier: bool = False


class VerifySubmissionBody(BaseModel):
    """Anything the caller happens to have seen. All optional — less input, less evidence."""
    url: str = ""
    title: str = ""
    text: str = ""
    platform: str = ""
    tabs: list[dict[str, Any]] = []
    extra_hints: Optional[dict[str, Any]] = None


@router.post("/api/verify/submission")
async def verify_submission(body: VerifySubmissionBody) -> dict[str, Any]:
    """Was an application actually sent? The shareable form of the question.

    Deliberately session-free and browser-free: it takes what you saw and answers with a verdict
    plus the signals behind it. That is what lets the same question be asked by the cockpit, the
    runtime loop, a capture replay, or a person with a URL in their hand and get the same answer —
    the property the `__kindOf` lesson says to build for, applied to "is it done?".

    Unknown platforms are supported and say so; `extra_hints` teaches it one at the call site
    without editing the module.
    """
    import submission_verifier as sv
    if body.tabs:
        verdict = sv.verify_tabs(body.tabs, platform=body.platform)
    else:
        verdict = sv.verify(body.url, body.title, body.text,
                            platform=body.platform, extra_hints=body.extra_hints)
    return verdict.as_dict()


@router.post("/api/session_control/{session_id}/apply_flag")
async def apply_flag(session_id: int, body: ApplyFlagBody,
                     db: Session = Depends(get_db)) -> dict[str, Any]:
    """End one apply step with a terminal flag, so the page can eventually move on.

    Every step must reach one of these — that is what stops a queue from either blocking forever
    on an account wall or quietly losing an application nobody finished. `submitted` is the only
    flag that means success; the rest record honestly why this one stopped.

    `submitted` is the claim that a real application was sent, so it is gated — but on EVIDENCE,
    not on provenance. That changed 2026-08-19 after the guard cost us the thing it exists to
    protect: a Paylocity application reached `Jobs/Success/4382310` reading "Your application has
    been received!", every organ of the cockpit agreed, and the ledger still said `now` because a
    button did not land. Operator's ruling: *"don't let that guard ruin data if i become lazy or
    miss that step ... if there is an application sent confirmation or anything of that nature,
    you ... will always have the right to set something as applied/done, especially if we have a
    verifier."*

    So `submission_verifier` reads the live window and its verdict decides. A confirmed page
    records `submitted` **with the evidence line attached to the detail**, so the claim can always
    be checked against the page it came from. An unconfirmed page is refused with what was
    actually seen — which is a better guard than a provenance check, because a human pressing a
    button is not evidence that an application was sent either. `override_verifier` remains for a
    receipt we cannot read, and marks the record UNVERIFIED rather than hiding the gap.
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

    # WHERE IT WAS STANDING. `parked` promises the operator is coming back to this page, and a
    # promise about a tab is only true while the tab exists — a session shutdown closes it and
    # takes anything typed-but-not-saved with it. Recorded for every terminal (an abandoned step's
    # last page is just as much a fact) and read back by `_parked_all`, which compares it to the
    # live window so the cockpit can say whether stepping back in resumes or starts over.
    # THE EVIDENCE GATE. Only `submitted` is checked: every other flag records why we stopped,
    # and being wrong about "account wall" costs a re-visit, while being wrong about "submitted"
    # costs a job the operator never applied to and will never chase.
    detail = body.detail
    if body.flag == "submitted":
        import submission_verifier as _sv
        # OBSERVE FIRST. The recorded `apply_tab` is a hint and a treacherous one — `_apply_tab`'s
        # own docstring says so, and here it was fatally stale: the tab had navigated from
        # /Jobs/Apply/... to /Jobs/Success/... on the very act we are recording, so the gate read
        # the pre-submit URL and refused a real submission. The whole point of this check is to
        # look at the window, so it has to look at the window NOW.
        _obs = await _observe(_session_browser_url(session), bb, session_id=session.id)
        _tabs = [{"url": t.get("url", ""), "title": t.get("title", "")}
                 for t in (_obs.get("tabs") or [])]
        _live = _apply_tab(bb, _obs)
        if _live.get("url"):
            _tabs.append({"url": _live.get("url", ""), "title": _live.get("title", "")})
        _verdict = _sv.verify_tabs(_tabs, platform=(step.platform or ""))
        if _verdict.submitted:
            detail = f"{detail} [verified: {_verdict.evidence_line()}]".strip()
        elif body.override_verifier:
            detail = (f"{detail} [UNVERIFIED — operator override; the window did not confirm: "
                      f"{_verdict.evidence_line()}]").strip()
        else:
            raise HTTPException(
                status_code=409,
                detail=(f"the window does not confirm this application was sent — "
                        f"{_verdict.evidence_line()}. Open the confirmation page and flag it "
                        f"again, or pass override_verifier=true to record it as UNVERIFIED."))

    step.tab_url = (((bb.world or {}).get("apply_tab") or {}).get("url") or "")
    step.finish(body.flag, detail)


    # THE ATTEMPT IS OVER — take back an account row whose signup never happened. The account rung
    # writes the row on intent and it legitimately outlives a single crank (a filled form waiting on
    # the operator's click is still live work), but a step reaching a TERMINAL flag is the moment
    # that stops being true. Without this, every abandoned or parked application left a row behind
    # claiming a login the employer never issued. `discard_unclaimed` refuses anything active or
    # holding a secret, so a real account is never touched by a tidy-up.
    if step.company and step.platform:
        import ats_accounts as _ats_accounts
        _gone = _ats_accounts.discard_unclaimed(step.company, step.platform)
        if _gone.get("discarded"):
            bb.log("account_discard",
                   f"{step.company} {step.platform}: pending account row taken back — the step "
                   f"ended {body.flag!r} and no signup ever completed")
    bb.world = dict(bb.world or {})
    bb.world["apply_queue"] = queue.as_dict()
    # A finished step must not leave its handoff or proposal lingering onto the next job.
    if (bb.world.get("account_handoff") or {}).get("job_id") == step.job_id:
        bb.world.pop("account_handoff", None)
    if (bb.world.get("apply_proposal") or {}).get("job_id") == step.job_id:
        bb.world.pop("apply_proposal", None)
    bb.log("apply_flag", f"{body.job_id} -> {body.flag}"
                         + (f" ({detail})" if detail else ""))

    # THE CLEANUP CREW RUNS ON EVERY TERMINAL, not just on success. An application abandoned at a
    # wall leaves exactly the same orphan tab as one that was submitted, and the next prospect has
    # to start from a window that means something.
    obs = await _observe(_session_browser_url(session), bb, session_id=session.id)
    # RECORD BEFORE CLOSE — the epilogue's own rule. A closed tab with no record is unrecoverable,
    # and the record is what the NEXT session gets to ask (applied_index).
    _live_tab_url = _apply_tab(bb, obs).get("url", "")
    recorded = _record_outcome(db, step, ats_url=_live_tab_url,
                               search_id=(bb.world or {}).get("search_id"))

    # THE JOIN — job, terminal, session and the tab it ended on, written where all four are known.
    # Reconstructing it afterwards failed on the same missing column every time: the transition
    # corpus records states without job identity, which is why 63 backfilled flows carry no outcome
    # and the pre-flight brief could not say whether anyone had ever finished on a given ATS.
    #
    # It reads the LIVE tab (`_apply_tab`), not `step.tab_url`. The recorded hint is empty whenever
    # a previous terminal popped `apply_tab` — a re-flag after a reopen writes nothing at all, which
    # is exactly what happened the first time this ran and is the same staleness `_apply_tab`'s own
    # docstring warns about. Same source `_record_outcome` uses, one line above, on purpose.
    import ats_backfill as _ats_backfill
    if _ats_backfill.record_flow(
            db, url=_live_tab_url, job_key=step.job_id, terminal=body.flag,
            session_id=session.id, platform=(step.platform or ""),
            states=[m.get("rung") for m in (step.minis or ()) if isinstance(m, dict)
                    and m.get("rung")]):
        # COMMITTED HERE, EXPLICITLY. `record_flow` only flushes — it is a helper and must not
        # decide when a request's transaction ends. But this is the last write in the handler, and
        # `_record_outcome` above has already committed, so a flush with nothing after it is rolled
        # back at request teardown. That is exactly how the first live run wrote nothing while the
        # same call succeeded when driven directly against the session.
        db.commit()
    # DOES THE TAB HAVE TO SURVIVE? Terminal for the ladder is not finished in the world.
    # Measured live 2026-08-04: an application sitting on smartapply's review step — complete,
    # one click from sent — was parked because Submit is the operator's gate, and the cleanup
    # crew closed the tab underneath it. `leaves_work_open` owns the rule (who acts next, and
    # where); this only supplies the staged-input fact, which lives on the mini-steps.
    _staged = any(_mini_typed(m) for m in (step.minis or ()))
    if aps.leaves_work_open(body.flag, staged=_staged):
        cleanup = {"closed": [], "preserved": True}
    else:
        cleanup = await _apply_cleanup(bb, obs, _session_browser_url(session), step)
        bb.world.pop("apply_tab", None)      # the record dies with the tab it pointed at
    _persist(bb, ledger)
    if cleanup["closed"]:
        obs = await _observe(_session_browser_url(session), bb, session_id=session.id)

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
                                  + ("Its tab is LEFT OPEN — parked means you are coming back "
                                     "to it, and closing it would throw away whatever is filled "
                                     "in. " if cleanup.get("preserved") else "")
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


def _record_outcome(db: Session, step: aps.ApplyStep, *, ats_url: str = "",
                    search_id: Optional[int] = None) -> dict[str, Any]:
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
        # The job_id's own prefix names the platform (`linkedin:4123…`). When it carries none,
        # prefer what the STEP recorded over a hardcoded "indeed" — filing a LinkedIn application
        # under Indeed in the canonical job table is the kind of wrong that survives forever.
        row = ObservedJob(job_id=step.job_id,
                          platform=platform or step.platform or DEFAULT_ENGINE["platform"],
                          external_id=ext,
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
    # The canonical half of the record. The sighting write above answered "did the drive finish
    # this step"; the Application row answers "did we apply to this JOB" — and only the second is
    # what the dashboard and the next drive's applied-check read. The manual mark endpoint has
    # mirrored since 07-30; the LIVE submit seam never did (found 2026-08-10), which is how a
    # confirmed submit could still read as never-applied. `search_id` carries which query led here.
    if status == "applied":
        from application_events import mirror_application
        mirror_application(db, row, search_id=search_id)
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

    # THE DURABLE CLAIM: which APPLICATION each tab belongs to, surviving queue turnover. The
    # census above is keyed to the CURRENT job — the moment the queue moves on, a leftover tab
    # loses its association and nobody can say whose it was (operator, 2026-08-10: tabs must be
    # associated with the task so a finished application knows exactly what to clean). Claims
    # accrue when a step's own drift watches a tab appear; they drop when the tab vanishes,
    # whoever closed it.
    claims = dict(bb.world.get("tab_claims") or {})
    for a in appeared:
        claims.setdefault(a["tab_id"], {"job_id": step.job_id, "url": a["url"],
                                        "title": step.title or ""})
    for tid in [k for k in claims if k not in live]:
        claims.pop(tid, None)
    bb.world["tab_claims"] = claims

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
    search_url = _search_focus_url(bb, obs)
    apply_tab = _apply_tab(bb, obs)
    closed: list[dict[str, Any]] = []

    async def _close(tab_id: str, url: str, why: str) -> None:
        res = await _capture_post("/close_tab", {"browser_url": browser_url, "tab_id": tab_id,
                                                 "focus_tab_url": search_url})
        closed.append({"tab_id": tab_id, "url": url[:90], "why": why,
                       "ok": bool(res.get("ok")), "detail": res.get("detail", "")})

    # WHOSE APPLY TAB? `_apply_tab` answers "the session's apply tab", and this line called that
    # "the application tab for {step.job_id}" — a sentence that is simply false when the tab
    # belongs to a different job. Measured live 2026-08-14: flagging the C&S duplicate as
    # already-applied closed Boston Children's BrassRing tab, an application mid-flight on another
    # step, one screen from Submit. The flag was right; the tidying reached past its own step and
    # then described what it had done in the wrong step's name.
    #
    # `tab_claims` is the durable record of whose tab is whose, written so "the window stops being
    # anonymous". An unclaimed apply tab is still fair game (that is the ordinary case — the step
    # just finishing is the one that opened it); a tab claimed by a job that has NOT finished is
    # live work and survives every cleanup but its own.
    _claims = (bb.world or {}).get("tab_claims") or {}
    _open_jobs = {s.job_id for s in aps.Queue.from_dict(
        (bb.world or {}).get("apply_queue")).steps if not s.done and s.job_id != step.job_id}
    _owner = (_claims.get(apply_tab.get("tab_id")) or {}).get("job_id")
    if (apply_tab.get("tab_id")
            and apply_tab.get("tab_id") != (obs.get("search_tab") or {}).get("tab_id")
            and _owner not in _open_jobs):
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

    # THE DURABLE CLAIMS, same warrant across time: a tab this application was WATCHED opening is
    # its to close even if the queue has since moved on and back (the census above dies with the
    # step; claims survive). Every claim this job holds is closed here and dropped either way —
    # closed tabs are gone, and a claim on a vanished tab is bookkeeping debt.
    claims = dict((bb.world or {}).get("tab_claims") or {})
    by_id_live = {t.get("tab_id"): t.get("url", "") for t in tabs}
    for tid, claim in list(claims.items()):
        if claim.get("job_id") != step.job_id:
            continue
        if tid in by_id_live and tid not in {c["tab_id"] for c in closed} \
                and tid != (obs.get("search_tab") or {}).get("tab_id"):
            await _close(tid, by_id_live[tid],
                         f"claimed by {step.job_id} while it was being worked")
        claims.pop(tid, None)
    bb.world["tab_claims"] = claims

    # Whatever else the window manager would retire anyway — blanks, exact duplicates, orphaned
    # duplicate apply flows. Its four rails (never the active tab, the last tab, an UNKNOWN role,
    # or the only search tab) are the reason this is a survey rather than a loop over `tabs`.
    #
    # ANOTHER APPLICATION'S LIVE TAB IS NOT DEBRIS, AND THIS BLOCK HAD NO OWNER CHECK. Every block
    # above scopes to `step.job_id`; this one asked the window manager "what looks retirable" and
    # the window manager cannot know that an ATS tab belongs to a DIFFERENT, still-open step —
    # from outside it is exactly the shape of an orphaned apply flow. Measured live 2026-08-14:
    # flagging the C&S duplicate as already-applied closed Boston Children's BrassRing tab, an
    # application mid-flight on another step, one screen from Submit. The flag was correct; the
    # tidying reached past its own step.
    #
    # `tab_claims` is the mechanism that already exists for this — the durable record of whose tab
    # is whose, written precisely so "the window stops being anonymous". A claim held by a step
    # that has NOT finished is a live application, and its tab survives every cleanup but its own.
    live_jobs = {s.job_id for s in aps.Queue.from_dict(
        (bb.world or {}).get("apply_queue")).steps if not s.done and s.job_id != step.job_id}
    spoken_for = {tid for tid, claim in claims.items() if claim.get("job_id") in live_jobs}
    remaining = [t for t in tabs if t.get("tab_id") not in {c["tab_id"] for c in closed}
                 and t.get("tab_id") not in spoken_for]
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
        # A SURVIVOR OF A FINISHED SEARCH. Its search's queue is gone (`_reset_for_new_search`
        # harvested it into `parked_apps`), but parked means "not now", never "not reachable" —
        # stepping back in resurrects it into whatever queue is current, where the ladder
        # re-walks it exactly like a same-search reopen.
        world = dict(bb.world or {})
        survivors = list(world.get("parked_apps") or [])
        held = next((p for p in survivors if p.get("job_id") == body.job_id), None)
        if held is None:
            raise HTTPException(
                status_code=404,
                detail=f"{body.job_id} is not in this page's apply queue, nor parked from an "
                       f"earlier search in this session.")
        step = aps.ApplyStep.from_dict(
            {k: v for k, v in held.items() if k not in ("from_search", "from_page",
                                                        "in_current_queue")})
        queue.steps.append(step)
        world["parked_apps"] = [p for p in survivors if p.get("job_id") != body.job_id]
        bb.world = world
    try:
        step.reopen(body.reason, initiator=body.initiator)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    _save_queue(bb, queue)
    bb.log("apply_reopen", f"{body.job_id} reopened — {body.reason[:100]}")
    _persist(bb, ledger)
    summary = queue.summary()
    nxt = queue.current()
    obs = await _observe(_session_browser_url(session), bb, session_id=session.id)
    return _view(session, bb, ledger, obs, page=_current_page(obs, bb), awaiting="apply",
                 last={"ok": True, "action": "apply_reopen", "queue": summary,
                       "detail": (f"{step.title or body.job_id} is back in the queue and the "
                                  f"ladder restarts at the top. "
                                  + (f"Now working: {nxt.title or nxt.job_id}." if nxt else ""))})


class CloseTabBody(BaseModel):
    tab_id: str
    initiator: str = "operator"
    #: Required when the tab is claimed by an UNFINISHED application, or is the apply tab of the
    #: step being worked — closing live work needs saying so.
    confirm_discards_work: bool = False
    reason: str = ""


@router.post("/api/session_control/{session_id}/close_tab")
async def close_tab(session_id: int, body: CloseTabBody,
                    db: Session = Depends(get_db)) -> dict[str, Any]:
    """Close ONE tab from the cockpit's window panel — guarded, recorded, never the search tab.

    The window panel's per-tab verb (operator, 2026-08-10: the cockpit must surface the tab
    manager, especially on out-of-Indeed applications). Guards, in order: the search tab is never
    closable from here (it is the session's spine — walking away from a search is `initialize` or
    `close_out`, both on the record); a tab claimed by an application still OPEN in the queue
    needs `confirm_discards_work`; everything else closes with the reason logged.
    """
    _check_initiator(body.initiator)
    session, bb, ledger = _load(session_id, db)
    browser_url = _session_browser_url(session)
    obs = await _observe(browser_url, bb, session_id=session.id)

    if body.tab_id == (obs.get("search_tab") or {}).get("tab_id"):
        raise HTTPException(status_code=409,
                            detail="That is the search tab — the session's spine. End the search "
                                   "with a new declare or the session with close-out; neither "
                                   "happens by closing its tab.")
    live = {t.get("tab_id"): t.get("url", "") for t in (obs.get("tabs") or [])}
    if body.tab_id not in live:
        raise HTTPException(status_code=404, detail="That tab is no longer open.")

    claims = dict((bb.world or {}).get("tab_claims") or {})
    claim = claims.get(body.tab_id)
    queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
    holder = next((s for s in queue.steps if claim and s.job_id == claim.get("job_id")), None)
    apply_tab_id = _apply_tab(bb, obs).get("tab_id")
    holds_live_work = (holder is not None and not holder.done) \
        or (body.tab_id == apply_tab_id and queue.current() is not None)
    if holds_live_work and not body.confirm_discards_work:
        who = (holder.title or holder.job_id) if holder else "the application being worked"
        raise HTTPException(
            status_code=409,
            detail=f"That tab belongs to {who}, which is still open — closing it can lose "
                   f"filled-in work. Say so explicitly (confirm_discards_work), or flag the "
                   f"application first.")

    res = await _capture_post("/close_tab", {
        "browser_url": browser_url, "tab_id": body.tab_id,
        "focus_tab_url": _search_focus_url(bb, obs)})
    if not res.get("ok"):
        raise HTTPException(status_code=502,
                            detail=f"The tab did not close: {res.get('detail', 'no reason given')}")
    claims.pop(body.tab_id, None)
    bb.world = dict(bb.world or {})
    bb.world["tab_claims"] = claims
    bb.log("close_tab", f"operator closed tab {body.tab_id[:8]} ({live[body.tab_id][:80]})"
                        + (f" — {body.reason}" if body.reason else ""))
    _persist(bb, ledger)
    obs2 = await _observe(browser_url, bb, session_id=session.id)
    return _view(session, bb, ledger, obs2, page=_current_page(obs2, bb),
                 last={"ok": True, "action": "close_tab",
                       "detail": f"Closed {live[body.tab_id][:80]}"
                                 + (f" (was claimed by {claim.get('title') or claim.get('job_id')})"
                                    if claim else "")})


class CloseOutBody(BaseModel):
    initiator: str = "operator"
    #: Required when closing would discard real half-finished work (in-flight or parked
    #: applications) — the clean-start pattern: nothing that may hold somebody's application
    #: dies silently.
    confirm_discards_work: bool = False
    reason: str = ""
    #: TIDY THE MACHINE, KEEP THE WORK. This protocol welds together two jobs: shutting the session
    #: down (Chrome stopped, drive latch released, searches closed) and DECIDING that its
    #: unfinished applications are over. Only the first is what "close down at the end of a
    #: sitting" means, and welding the second to it made the protocol too destructive to run
    #: habitually — so it did not get run, which is the opposite of its intent (operator,
    #: 2026-08-13: "make sure that always gets done when closing").
    #:
    #: With `keep_work` the session shuts down and its in-flight and parked applications survive
    #: on the ledger, resumable — the same distinction the start-fresh handoff draws between
    #: RETIRE and CLOSE OUT. The default stays the full close-out, because "I am done with these"
    #: must remain sayable in one press.
    keep_work: bool = False


@router.post("/api/session_control/{session_id}/close_out")
async def close_out(session_id: int, body: CloseOutBody,
                    db: Session = Depends(get_db)) -> dict[str, Any]:
    """THE CLEANUP PROTOCOL — end a session completely, on the record, in one press.

    Born from the 2026-08-10 tab mess: a parked application's tab deliberately outlived its
    search, got stepped back into a NEW search's queue, and old work bled into new. The missing
    verb was a truthful CLOSE: before this, "ending a session" meant a Chrome stop that left the
    ledger holding half-finished applications, open Search rows, a stale drive latch — state that
    ambushed the next session.

    What it does, in order, all reported:
      1. Surveys the window (what tabs were open rides the report).
      2. Refuses without `confirm_discards_work` when in-flight/parked applications would die —
         listing them by name, the same rule `clean_start` enforces.
      3. Flags every unfinished application `abandoned:operator` with the close-out reason —
         parked means "not now", and closing the session is the operator saying "not ever, not
         here"; the detail string keeps the truth distinguishable from a seen-and-rejected.
         SKIPPED under `keep_work` — see below.
      4. Closes the session's ACTIVE Search rows (`abandoned`) — findings keep their provenance.
      5. Releases the drive latch when this session holds it.
      6. Stops the session's Chrome through the training-session stop (protected sessions still
         refuse without force there).
      7. KEEPS the persistent profile — the sign-in is the session's whole savings account, and
         cleanup must never log us out.

    TWO JOBS, AND ONLY ONE OF THEM IS "CLOSING DOWN" (2026-08-13). Steps 4–7 shut the session
    down; step 3 decides its applications are over. Welding them together made the one press an
    operator needs at the end of every sitting also the press that discards a week of half-finished
    applications — so it was too dangerous to make a habit of, and the tidy-up it exists to
    guarantee stopped happening. `keep_work=True` performs the shutdown and leaves in-flight and
    parked applications on the ledger, resumable: the same distinction the start-fresh handoff
    draws between RETIRE and CLOSE OUT. The default is unchanged.
    """
    from models import Search as SearchRow
    from sqlalchemy import select

    _check_initiator(body.initiator)
    session, bb, ledger = _load(session_id, db)
    browser_url = _session_browser_url(session)
    obs = await _observe(browser_url, bb, session_id=session.id)
    queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))

    dying: list[dict[str, Any]] = []
    for s in queue.steps:
        if not s.done or (s.terminal or "").startswith("parked:"):
            dying.append({"job_id": s.job_id, "title": s.title, "company": s.company,
                          "state": "in flight" if not s.done else s.terminal})
    seen_dying = {d["job_id"] for d in dying}
    for p in ((bb.world or {}).get("parked_apps") or []):
        if p.get("job_id") and p["job_id"] not in seen_dying:
            dying.append({"job_id": p["job_id"], "title": p.get("title"),
                          "company": p.get("company"), "state": p.get("terminal") or "parked"})

    if dying and not body.confirm_discards_work and not body.keep_work:
        raise HTTPException(
            status_code=409,
            detail=("Closing this session discards "
                    + ", ".join(f"{d['title'] or d['job_id']} ({d['state']})" for d in dying)
                    + ". Say so explicitly (confirm_discards_work) — half-finished applications "
                      "do not die silently. Or pass keep_work to shut the session down and leave "
                      "them resumable."))

    why = body.reason.strip() or "session closed out"
    world = dict(bb.world or {})
    if not body.keep_work:
        for s in queue.steps:
            if not s.done or (s.terminal or "").startswith("parked:"):
                s.finish(aps.ABANDONED_OPERATOR, f"closed out with the session — {why}")
        world["apply_queue"] = queue.as_dict()
        world.pop("parked_apps", None)
    bb.world = world

    searches_closed = 0
    for row in db.scalars(select(SearchRow).where(SearchRow.session_id == session_id,
                                                  SearchRow.status == "active")).all():
        row.status = "abandoned"
        searches_closed += 1

    import drive_lock as drive_lock_mod
    lock = drive_lock_mod.state()
    lock_released = False
    if lock.get("locked") and str(session_id) in str(lock.get("holder") or ""):
        drive_lock_mod.release()
        lock_released = True

    bb.log("close_out", f"session closed out by {body.initiator} — {why}; "
                        + (f"{len(dying)} application(s) KEPT (resumable)" if body.keep_work
                           else f"{len(dying)} application(s) discarded")
                        + f", {searches_closed} search(es) closed")
    _persist(bb, ledger)
    db.commit()

    # The Chrome stop, through the one seam that already knows how (protected checks included).
    # `import main` at call time resolves the loaded module — main imports this router at boot,
    # so a top-level import would be circular.
    import main as main_mod
    chrome = {"stopped": False, "detail": ""}
    try:
        main_mod.stop_training_session(session_id, force=False, db=db)
        chrome = {"stopped": True, "detail": "session Chrome stopped"}
    except HTTPException as exc:
        chrome = {"stopped": False, "detail": str(exc.detail)}
    except Exception as exc:  # noqa: BLE001
        # A REPORT IS THE POINT OF A CLEANUP. Everything above is already committed by here, so
        # letting an unexpected failure in the stop seam raise turns a partial cleanup into a 500
        # with no account of what did happen — the operator cannot tell whether their applications
        # were kept, whether the searches closed, or whether the browser is still up. A refused
        # stop was already a reportable outcome; an unexpected one is too.
        chrome = {"stopped": False, "detail": f"{type(exc).__name__}: {exc}"}

    return {"ok": True, "closed": True, "session_id": session_id,
            "kept_work": bool(body.keep_work),
            # Named by what actually happened to them, so a report can never read as a discard that
            # did not occur (or, worse, a keep that did not).
            "discarded": [] if body.keep_work else dying,
            "kept": dying if body.keep_work else [],
            "searches_closed": searches_closed,
            "lock_released": lock_released, "chrome": chrome,
            "profile_kept": getattr(session, "persistent_profile", None),
            "tabs_at_close": [(t.get("url") or "")[:120] for t in (obs.get("tabs") or [])],
            "detail": (("Closed down. " + (f"{len(dying)} application(s) KEPT on the ledger, "
                                           f"resumable. " if dying else "Nothing was half-finished. "))
                       if body.keep_work else
                       f"Closed out. {len(dying)} application(s) discarded on the record, ")
                      + f"{searches_closed} search(es) closed, Chrome "
                      + ("stopped" if chrome["stopped"] else f"NOT stopped — {chrome['detail']}")
                      + ". The signed-in profile is kept."}


class ResumeBody(BaseModel):
    initiator: str = "operator"


@router.post("/api/session_control/{session_id}/resume")
async def resume(session_id: int, body: ResumeBody,
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    """PICK THE WORK BACK UP — relaunch a shut-down session's browser and carry on.

    `close_out(keep_work=True)` made "shut down and keep the work" the normal way to end a sitting
    (2026-08-13), which makes "pick it back up" the normal way to start one — and there was nothing
    to press. The session's ledger survives the shutdown intact: the query stays SPENT, the page's
    results stay cached, the queue keeps its picks in order. Only `provisioned` regresses, because
    only the browser actually went away.

    That asymmetry was costing real work. A stopped session still holding a queue rendered its
    apply step as the current moment and offered "Work this" over a browser that did not exist,
    while the only reachable alternative — starting fresh — would spend a SECOND query against
    Indeed for a search that had already been run and picked from. The operator's words for it:
    "we wasted a good search and actual candidates."

    So this is deliberately NOT a new session. Same row, same persistent profile (the sign-in comes
    back with it), same blackboard; `_launch_training_chrome` attaches if the browser is somehow
    still alive and relaunches otherwise, and refuses honestly if another live Chrome holds the
    profile. What it restores is reported, so the operator can see the search was not re-spent.
    """
    _check_initiator(body.initiator)
    session, bb, ledger = _load(session_id, db)

    import main as main_mod
    # Raises HTTPException on a real conflict (another live Chrome holding this profile), which is
    # the honest answer and should reach the operator unchanged. It commits and refreshes the very
    # session object `_load` handed us — same identity map — so nothing needs re-reading here.
    main_mod.start_training_session(session_id, db=db)

    # The browser is back, so the rung that regressed with it is re-established on EVIDENCE, not
    # on the launch call returning: `_observe` is the same probe every other rung is judged by.
    browser_url = _session_browser_url(session)
    obs = await _observe(browser_url, bb, session_id=session.id)
    if obs.get("observed", {}).get("provisioned"):
        profile = getattr(session, "persistent_profile", None) or "throwaway"
        ledger.mark("provisioned",
                    evidence=f"resumed — browser relaunched on the {profile} profile",
                    initiator=body.initiator)

    # REOPEN THE RESULTS, DO NOT RE-RUN THEM. A relaunched Chrome lands on about:blank, so the
    # consuming rung's EFFECT is gone even though the rung is still held — the ladder says LAPSED,
    # "recover, never re-run", and correctly refuses to dispatch. Recovering is this endpoint's
    # job: without it the operator resumes onto an empty browser whose only offered move is the
    # one the rung forbids, which is how a good search got spent twice.
    ss = bb.search_state
    reopened = ""
    if ledger.holds("query_entered") and (ss.query or "").strip():
        engine = engine_for(session, obs.get("search_tab"))
        url = _results_url(engine, query=ss.query, location=ss.location or "",
                           radius=(bb.world or {}).get("radius_miles"), page=ss.page or 1)
        nav = await _capture_post("/navigate", {"browser_url": browser_url, "url": url,
                                                "driver": "humanized"}, timeout=30.0)
        if nav.get("ok"):
            reopened = url
            bb.log("resume", f"reopened the results page without re-running the query ({url[:90]})")
        else:
            bb.log("resume", f"could not reopen the results page — {nav.get('detail', '')[:120]}")
        obs = await _observe(browser_url, bb, session_id=session.id)

    queue = aps.Queue.from_dict((bb.world or {}).get("apply_queue"))
    held = [s for s in queue.steps
            if not s.done or (s.terminal or "").startswith("parked:")]
    bb.log("resume", f"session resumed by {body.initiator}; {len(held)} application(s) carried over")
    _persist(bb, ledger)
    db.commit()

    spent = ledger.holds("query_entered")
    return _view(session, bb, ledger, obs, page=_current_page(obs, bb),
                 last={"ok": True, "action": "resume", "reopened": reopened,
                       "detail": (f"Resumed session #{session_id} on its own profile. "
                                  + (f"{len(held)} application(s) carried over: "
                                     + ", ".join(s.title or s.job_id for s in held[:4])
                                     + (" …" if len(held) > 4 else "") + ". " if held else "")
                                  + (("The search "
                                      f"{ss.query!r} is still spent for this session — its results "
                                      "page was REOPENED, not re-run." if reopened else
                                      f"The search {ss.query!r} is still spent for this session — "
                                      "it was not re-run, but its results page could not be "
                                      "reopened; recover to it before working the queue.")
                                     if spent else
                                     "No search has been run in this session yet."))})


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
    obs = await _observe(browser_url, bb, session_id=session.id)
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
    obs_after = await _observe(browser_url, bb, session_id=session.id)
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
    #: Per-job WHY, keyed by job_id — for picks and passes alike. Optional and usually sparse:
    #: an unexplained decision is still worth recording, and an invented reason would be worse
    #: than none, so nothing fabricates these.
    reasons: dict[str, str] = {}
    #: Job ids the operator means to apply to AGAIN despite a CERTAIN match on file. Named one by
    #: one rather than a blanket boolean, because "yes, re-apply to this one" is a judgement about
    #: a specific job (the req was reposted, the first attempt died at a wall) and a flag that
    #: waves through everything is not that judgement. The fuzzy tier never reaches here — it
    #: warns and enqueues, per `applied_index`: a near-miss that silently skips a job the operator
    #: picked is worse than one that asks.
    confirm_reapply: list[str] = []


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
    obs = await _observe(browser_url, bb, session_id=session.id)
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

    # ASK THE DATABASE BEFORE SPENDING A DRIVE. The applied check already ran at scan time and
    # every card carries its verdict, but a stored verdict is a MEMORY: this session's own earlier
    # submissions, and any other session's, land in `ObservedJob` after the page was read. So the
    # question is asked again HERE, where it changes what happens, against the live table.
    #
    # Operator, 2026-08-17, having just picked two jobs already applied to through Indeed:
    # *"when we use the picker we need to start involving the applied database here and in
    # decision making … so we don't waste any time."* The verdict existed on every row and no
    # surface rendered it and no decision consulted it.
    #
    # THE TWO TIERS ARE ANSWERED DIFFERENTLY, which is the whole point of `applied_index` having
    # tiers at all (its own docstring): `applied` is certain — same job id, or a shared requisition
    # — so enqueuing it is waste and it REFUSES, naming the match and how it was made. `likely`
    # is company+title, right far more often than wrong and exactly the match that would wrongly
    # skip "Data Analyst II" for "Data Analyst I", so it never blocks: it enqueues and says so.
    # A refusal the operator cannot act on is a dead end, so the refusal carries the override.
    picked_cards = [by_id_pre for by_id_pre in ((bb.world or {}).get("page_results") or [])
                    if by_id_pre.get("job_id") in set(body.picks)]
    verdicts = applied_index.check_many(db, picked_cards, platform=engine["platform"])
    certain, likely = [], []
    for card in picked_cards:
        verdict = verdicts.get(str(card.get("external_id") or ""))
        if verdict is None:
            continue
        if verdict.applied and card.get("job_id") not in set(body.confirm_reapply):
            certain.append((card, verdict))
        elif verdict.worth_asking:
            likely.append((card, verdict))
    if certain:
        named = "; ".join(
            f"{c.get('title') or c.get('job_id')} at {c.get('company') or 'unknown employer'} "
            f"(applied {str(v.applied_at)[:10]} via {v.platform or 'unknown'}, matched on "
            f"{v.matched_on})" for c, v in certain)
        raise HTTPException(
            status_code=409,
            detail=(f"Already applied to {len(certain)} of these: {named}. Entering an application "
                    f"that exists spends real traffic against a real account for nothing. Drop "
                    f"them from the picks, or name them in `confirm_reapply` if you mean to apply "
                    f"again."))
    if likely:
        bb.log("applied_warning",
               f"page {page}: {len(likely)} pick(s) look already-applied and were queued anyway: "
               + "; ".join(f"{c.get('title') or c.get('job_id')} ({'; '.join(v.evidence)})"
                           for c, v in likely),
               why="The fuzzy tier matches on employer and role words, which is right far more "
                   "often than it is wrong but would also skip 'Analyst II' for having applied to "
                   "'Analyst I'. It warns and never decides.",
               next_up="Check the posting against the earlier application before driving it; if it "
                       "is the same requisition, flag the step already-applied rather than filling "
                       "the form twice.")

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

    # PICKING A JOB THAT IS ALREADY PARKED RESURRECTS IT — it does not start a blank one.
    #
    # `enqueue` is idempotent by job_id, which protects a job already in THIS queue. A parked
    # survivor is not in it: `_reset_for_new_search` harvested it to session level precisely so it
    # would outlive its search. So re-running the same terms and picking the same job — the exact
    # shape of a repick, and live on 2026-08-14 with Boston Children's sitting one screen from
    # Submit with five fields filled — would have queued a FRESH step beside the parked one. Two
    # records for one application, the visible one empty, and the real progress reachable only from
    # a strip the operator had no reason to look at.
    #
    # The saved step is restored INTO the queue instead, still carrying its parked flag and its
    # walked rungs, so the surface offers "Step back in" (which is what resuming is) rather than
    # "Open the posting" (which would re-walk it from the top). Reopening stays the operator's
    # press: this only makes sure they are pressing it on the real step.
    restored: list[str] = []
    world = dict(bb.world or {})
    survivors = list(world.get("parked_apps") or [])
    known_ids = {s.job_id for s in queue.steps}
    for job_id in body.picks:
        held = next((p for p in survivors if p.get("job_id") == job_id), None)
        if held is None or job_id in known_ids:
            continue
        queue.steps.append(aps.ApplyStep.from_dict(
            {k: v for k, v in held.items()
             if k not in ("from_search", "from_page", "in_current_queue")}))
        survivors = [p for p in survivors if p.get("job_id") != job_id]
        known_ids.add(job_id)
        restored.append(job_id)
    if restored:
        world["parked_apps"] = survivors
        bb.world = world
        bb.log("choose_restored",
               f"page {page}: {len(restored)} pick(s) were already parked in this session and came "
               f"back with their progress: "
               + ", ".join((by_id.get(j) or {}).get("title") or j for j in restored),
               why="A parked application picked again is the same application, not a new one — "
                   "queueing a blank step beside it would duplicate the record and hide the work "
                   "already done.",
               next_up="Step back in to resume where it stopped; its walked rungs are archived on "
                       "the step, so the retry can be read against the first attempt.")

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
    # THE DECISION LEDGER — every card under review, PICKED AND PASSED (operator, 2026-08-04:
    # "the actual decisions should be saved"). The passes are the perishable half: the twenty
    # cards you did not choose exist nowhere once the page moves, and a corpus of picks alone
    # teaches "apply to everything". Best-effort by construction — a ledger failure must never
    # cost the operator their selection, which is the thing that actually took judgement.
    try:
        import job_decisions
        counts = job_decisions.record_page_decisions(
            db, cards=list((bb.world or {}).get("page_results", [])), picked=set(body.picks),
            decided_by=body.decided_by, session_id=session.id, page=page,
            query=bb.search_state.query or "", reasons=body.reasons,
            search_id=(bb.world or {}).get("search_id"))
        db.commit()
        bb.log("decisions", f"page {page}: recorded {counts['picked']} picked / "
                            f"{counts['passed']} passed by {body.decided_by}")
    except Exception:  # noqa: BLE001
        logging.getLogger("session_control").exception(
            "could not record the decision ledger for page %s", page)

    # THE DECISION, WITH ITS REASONING — the one event on this timeline a human actually authored.
    # `note` is the operator's own words and belongs in `why`, not appended to a count; `next_up`
    # states what these picks are about to become, which is the difference between "7 picked" and
    # "7 applications will be walked one at a time, and this page will not turn until each reaches
    # a terminal flag". A reader who does not already know that rule cannot follow what comes next.
    queued_names = ", ".join(
        (by_id.get(j) or {}).get("title") or j for j in body.picks[:4])
    bb.log("choose", f"page {page}: picked {len(body.picks)} of {len(known)} "
                     f"by {body.decided_by} ({added} queued to apply)"
                     + (f" — {body.note}" if body.note else ""),
           why=body.note or (f"{body.decided_by} reviewed all {len(known)} cards on page {page} "
                             f"and chose {len(body.picks)}"
                             + (f": {queued_names}" if queued_names else "")
                             + ("…" if len(body.picks) > 4 else "")),
           next_up=(f"Work {added} queued application(s) one at a time; page {page} does not turn "
                    f"until every one reaches a terminal flag."
                    if added else
                    f"Nothing queued from page {page} — the page is reviewed and the ladder may "
                    f"turn to the next one."))

    if queue.blocks_page():
        summary = queue.summary()
        _persist(bb, ledger)
        obs_now = await _observe(browser_url, bb, session_id=session.id)
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
                obs_now = await _observe(browser_url, bb, session_id=session.id)
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
    obs_after = await _observe(browser_url, bb, session_id=session.id)
    return _view(session, bb, ledger, obs_after, page=advanced.get("page", page),
                 awaiting=advanced.get("awaiting"), last=advanced)
