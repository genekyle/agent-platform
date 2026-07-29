"""LinkedIn's own recipe — its states, its search controls, and what its stack does differently.

Sibling of `indeed`'s cadence and of `google_recipe`: LinkedIn is an ENGINE (search + triage +
hand-off), and this is the per-domain knowledge that the shared cadence reads. The cadence itself —
one query per session, floor the radius, one page at a time, click into what you shortlist — lives
in `session_control`/`search_cadence` and is the same everywhere, because it is about how we behave.
What differs is whose markup we are reading, and that is all that belongs in here.

STATUS, stated plainly so nobody mistakes a stub for a finding. Every fact marked MEASURED was read
off the live page in session #22 on 2026-07-28. Everything marked UNVERIFIED is a scaffold to be
replaced by the next drive, not knowledge — the last two times a per-stack claim went in unmeasured
("Enter is steadier than the button", "the identifier is the credential screen") both were wrong and
cost a live drive to find out.

--------------------------------------------------------------------------------------
MEASURED
--------------------------------------------------------------------------------------
* **Classes are build-hashed** (`_5b06c34f cfc88646 _7a48f6fa`). No class selector survives a
  deploy. Address by accessible NAME, by href, or by a data-* the site's own tests use.
* **The search is STAGED** (operator, 2026-07-28) — title alone on the home page, then location,
  then radius on the RESULTS page. Indeed asks for what+where together and then the radius; that
  ordering difference is the cadence, see SEARCH_CADENCE.
* **The jobs home has one search box and NO submit button.**
* **THE SEARCH BOX IS A WIDGET, AND FOCUSING IT OPENS A PANEL** (operator screenshot, 2026-07-28).
  A real click focuses the field, changes the visible placeholder to "Describe the job you want",
  and opens a suggestion overlay ("Search for more than just job titles"). So it is a staged
  control, not a plain input — the widget-protocol shape this codebase already knows.
* **AX exposes TWO nodes for it**: `textbox "I'm looking for…"` and `combobox "I'm looking for..."`
  (curly apostrophe). They do NOT change name on focus — a re-scan after focusing returns both,
  unchanged.

* **`_SUBMIT_HINTS` matching "search" picks `Skip to search`** — a skip-link, not a submit.
  Clicking it would jump the caret to a landmark and report a submitted query. This is why the
  engine declares its own control profile instead of inheriting Indeed's matcher.
* **The results list is virtualised and the whole app is a SPA** — see `_LINKEDIN_JOBS_JS` and
  `/await_results`; nothing here may treat a navigation (or its absence) as proof.

--------------------------------------------------------------------------------------
THE FULL TITLE-STAGE CADENCE — MEASURED, from the operator's own recording
--------------------------------------------------------------------------------------
Recorded end to end by `/observe` on 2026-07-28 while the operator drove it by hand:

    3082ms  focus    "Describe the job you want"
    3255ms  click    trusted, at [261,29]
    4611ms  keydown  'R'  -> input value='R'   ... 17 keys ... value='Reporting Analyst'
    7684ms  keydown  'Enter'            <-- THE COMMIT
    7685ms  change   value='Reporting Analyst'
    7686ms  blur

So: **click to open, type, and press ENTER.** There is no submit button and no suggestion tile to
click — Enter is the commit, and the signature of a successful one is `Enter -> change -> blur`.

**THERE ARE TWO ROUTES TO THE JOBS RESULTS, AND BOTH WORK.** Measured 2026-07-28:

    A. jobs-home box -> Enter                       -> /jobs/search-results/?...&origin=JOBS_HOME_SEARCH
    B. global header box -> Enter -> blended page
       -> click the JOBS section's "Show all"       -> /jobs/search-results/?...&origin=BLENDED_SEARCH

Route B is one step longer and is what the system drove first; the operator confirmed it is a
legitimate way through, and it lands on the same page. There is no single golden path here — the
golden path is the least-steps route actually OBSERVED to work, and both of these have been.

**Assert the PATH, not the origin.** `origin=` is useful provenance (it names which affordance was
used: JOBS_HOME_SEARCH, BLENDED_SEARCH, GLOBAL_SEARCH_HEADER) but it is not the success condition.
The success condition is landing on `/jobs/search-results/`. An earlier version asserted
`origin=JOBS_HOME_SEARCH` and would have failed route B, which works.

**The trap on the blended page:** FIVE links share the accessible name "Show all" — one per section
(jobs, posts, courses, people, groups). Picking by document order happens to be right today and is
the exact mistake that clicked the wrong company on 2026-07-26. Choose by HREF: the jobs one is the
only one pointing at `/jobs/`.

THE ONE REMAINING GAP, now precisely defined: **the executor cannot send Enter.** The interaction
vocabulary (`interaction/contract.py`) has no `press`/key intent — an earlier attempt dispatched
`action_id="press"` and it went nowhere, silently. Filling the box is solved; COMMITTING it needs a
key-press capability in the driver. That is a driver change, justified now by a measurement rather
than by the guess that preceded it.
"""

from __future__ import annotations

import re
from typing import Any, Optional

HOST = "linkedin.com"

# --- states -----------------------------------------------------------------------------------
HOME = "linkedin_home"                  # signed-in Jobs home. MEASURED (capture #342).
SEARCH_RESULTS = "linkedin_job_search"  # /jobs/search/?keywords=…
JOB_DETAIL = "linkedin_job_detail"      # a posting open in the right-hand pane
LOGIN_WALL = "login_wall"               # shared id: the logged-out wall
EASY_APPLY = "linkedin_easy_apply"      # the on-engine apply modal. UNVERIFIED.
UNKNOWN = "unknown"

_URL_STATES: list[tuple[str, str]] = [
    (r"/jobs/search", SEARCH_RESULTS),
    (r"/jobs/view/", JOB_DETAIL),
    (r"/jobs/?$|/jobs/collections|/jobs/tracker", HOME),
    (r"/login|/uas/login|/checkpoint", LOGIN_WALL),
]


def map_url_to_state(url: str) -> str:
    for pattern, state in _URL_STATES:
        if re.search(pattern, url or "", re.I):
            return state
    return UNKNOWN


def classify(url: str = "", page_text: str = "") -> str:
    """State from the URL, with the logged-out wall promotable by text.

    The signed-in/out distinction is NOT made here — that is `/auth_state`'s job, and it reads the
    profile href rather than anything on this page (the classes are hashed; see the module note).
    """
    state = map_url_to_state(url)
    text = (page_text or "").lower()
    if state in (HOME, UNKNOWN) and ("join now" in text or "sign in to continue" in text):
        return LOGIN_WALL
    return state


# --- the search controls ----------------------------------------------------------------------
#: MEASURED. The query box's ACCESSIBLE NAME — matched as a substring because the apostrophe is
#: curly and the ellipsis is three dots, and neither survives being retyped by hand.
#: Both spellings, because AX exposes the box twice (textbox + combobox, curly vs straight
#: apostrophe). The names do NOT change on focus — that was a wrong diagnosis, now retracted.
QUERY_NAME_HINTS: tuple[str, ...] = ("looking for", "describe the job", "search by title")
QUERY_ROLES: tuple[str, ...] = ("combobox", "textbox", "searchbox")

#: The location box is not ABSENT from the jobs home — it does not exist YET. Operator, 2026-07-28:
#: LinkedIn's search is STAGED, and that is the real difference from Indeed, not a missing field.
#: I had recorded the measurement ("no location box here") and drawn the wrong conclusion from it.
#: MEASURED on the results page 2026-07-28: the control is a BUTTON, not a textbox —
#: `button "Location Greater Boston"`, i.e. the accessible name CARRIES THE CURRENT VALUE. So a
#: matcher must key on the "location" stem and read the rest as state, and the control is opened
#: (a filter popup) rather than typed into. It was already set to Greater Boston without us doing
#: anything, so the stage must CHECK before it acts — re-applying a filter re-queries for nothing.
LOCATION_NAME_HINTS: tuple[str, ...] = ("location", "city", "where")
LOCATION_IS_BUTTON = True

#: MEASURED: no submit button exists on the jobs home, and the generic "search" hint matches the
#: `Skip to search` SKIP-LINK. So the engine declares that it has none, and anything looking for a
#: submit here must not fall back to a name match.
SUBMIT_NAME_HINTS: tuple[str, ...] = ()
FORBIDDEN_SUBMIT_NAMES: tuple[str, ...] = ("skip to", "keyboard shortcut", "close jump menu")

#: FILLING the box is now MEASURED to work (click to open, then type — see the module note and the
#: `/observe` trace that produced it). SUBMITTING it is not: nothing has yet committed the query and
#: read back a results page. Two flags, because "we can type" is not "we can search".
SEARCH_FILL_READY = True
SEARCH_SUBMIT_READY = False


def find_query_box(candidates: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    for hint in QUERY_NAME_HINTS:
        for c in candidates or []:
            if (c.get("role") or "").lower() not in QUERY_ROLES:
                continue
            if hint in (c.get("name") or "").strip().lower():
                return {"role": c.get("role"), "name": c.get("name"),
                        "backend_node_id": c.get("backend_node_id")}
    return None


def is_forbidden_submit(name: str) -> bool:
    """Is this 'submit' actually a skip-link or a shortcut? MEASURED: `Skip to search` matched the
    shared submit hint, and clicking it would report a query that never ran."""
    n = (name or "").strip().lower()
    return any(bad in n for bad in FORBIDDEN_SUBMIT_NAMES)


def search_controls(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """{query?, submit?, ready, why} — LinkedIn's own answer, not Indeed's matcher.

    `ready` is the honest bit: it is False while the box cannot actually be filled, so a caller
    cannot read "we found a query box" as "we can run a query".
    """
    query = find_query_box(candidates)
    return {
        **({"query": query} if query else {}),
        # Location is STAGED, not absent — it lives on the results page. Reporting it as missing
        # here would send a caller hunting for a control that cannot exist yet.
        "location": None,
        "location_stage": SEARCH_RESULTS,
        "submit": None,            # MEASURED absent; the typeahead/Enter is the commit
        "ready": bool(query) and SEARCH_SUBMIT_READY,
        "why": ("" if SEARCH_SUBMIT_READY else
                "The query box is found but the humanized `type` does not fill it — it reports ok, "
                "blurs the field and inserts nothing (measured session #22). Needs a "
                "focus-preserving fill in the driver before a LinkedIn query can be claimed."),
    }


# --- the cadence: LinkedIn stages what Indeed asks for at once --------------------------------
# Operator-described, 2026-07-28, and the single most important structural difference between the
# two engines:
#
#   INDEED    one form, both fields:  [what] + [where]  -> submit -> results -> set radius
#   LINKEDIN  title ALONE             [describe the job] -> submit -> RESULTS PAGE
#                                     ...then on the results page: location, THEN radius
#
# So the location box being missing from the jobs home is not an absence to route around — it is
# stage 2, and it appears only after the first submit. A cadence that tried to fill location on the
# home page would hunt for a control that cannot exist yet and report the page as broken.
#
# This also means LinkedIn spends its CONSUMING query on the title alone. Location and radius are
# refinements applied to an existing result set, which is why they are separate rungs rather than
# inputs to the search: changing them re-queries, but it does not spend a NEW search.
STAGE_TITLE = "title"
STAGE_LOCATION = "location"
STAGE_RADIUS = "radius"

SEARCH_CADENCE: tuple[dict[str, Any], ...] = (
    {
        "stage": STAGE_TITLE,
        "on_state": HOME,
        # MEASURED: the box must be CLICKED OPEN before it accepts text — it is a staged widget,
        # not a plain input. A bare type filled nothing four separate times; click-then-type filled
        # it first try, and `/observe` recorded the whole chain.
        "open_first": True,
        "value_from": "query",              # SearchState.query — the job title only
        "control": "query",                 # find_query_box
        "commits": True,                    # this is the CONSUMING act
        # MEASURED: Enter. No submit button, no suggestion tile — and the commit is confirmable
        # from `change` + `blur` without waiting on a navigation there is none of.
        "commit_key": "Enter",
        "commit_signature": ("change", "blur"),
        # MEASURED: the landed URL must carry this, or we committed the GLOBAL search instead of
        # the jobs one — same-looking control, different destination, and nothing errors.
        # MEASURED: the success condition is the PATH. `origin=` names WHICH route was taken
        # (JOBS_HOME_SEARCH direct, or BLENDED_SEARCH via the global box + "Show all") and both
        # land here — asserting one origin would have failed a route that works.
        "lands_on_path": "/jobs/search-results/",
        "lands_on": SEARCH_RESULTS,
        "why": "LinkedIn asks for the job title by itself. The city is not on this page and cannot "
               "be typed here — putting it in the title box searches for a place, not a role.",
    },
    {
        "stage": STAGE_LOCATION,
        # MEASURED: a filter BUTTON whose name carries the value ("Location Greater Boston"), not
        # a text field. Read it first — it may already hold what we want.
        "control_kind": "filter_button",
        "on_state": SEARCH_RESULTS,
        "value_from": "location",           # e.g. "Greater Boston"
        "control": "location",
        "commits": False,                   # refines an existing result set
        "lands_on": SEARCH_RESULTS,
        "why": "The location box exists only once results are on screen. Applying it re-queries "
               "but does not spend a new search.",
    },
    {
        "stage": STAGE_RADIUS,
        "on_state": SEARCH_RESULTS,
        "value_from": "radius_miles",       # ~100mi, or the nearest offered stop
        "control": "distance",              # the slider — /set_distance's linkedin branch
        "commits": False,
        "lands_on": SEARCH_RESULTS,
        "why": "Radius is a slider on the results page and is meaningless before there is a "
               "location to be radial about — which is why it follows location, not precedes it.",
    },
)


def stage_for_state(state: str, done: tuple[str, ...] = ()) -> Optional[dict[str, Any]]:
    """The next search stage to work, given where we are and what has already been applied.

    Returns None when this state has no outstanding stage — which is the honest answer on a page
    the cadence does not drive, and keeps a caller from forcing a stage onto the wrong screen.
    """
    for step in SEARCH_CADENCE:
        if step["stage"] in done:
            continue
        if step["on_state"] == state:
            return step
    return None


def location_box_expected_on(state: str) -> bool:
    """Should a location field exist here at all? False on the home page — so 'not found' there is
    the expected answer and never an error worth reporting."""
    return state == SEARCH_RESULTS


def spec() -> dict[str, Any]:
    return {
        "host": HOST,
        "states": [HOME, SEARCH_RESULTS, JOB_DETAIL, LOGIN_WALL, EASY_APPLY],
        "search": {"query_hints": list(QUERY_NAME_HINTS),
                   "cadence": [st["stage"] for st in SEARCH_CADENCE],
                   "location_box_on": SEARCH_RESULTS,   # staged, not absent
                   "has_submit_button": False, "ready": SEARCH_SUBMIT_READY},
        "measured": ["classes are build-hashed — never a class selector",
                     "the search is STAGED: title alone, then location, then radius",
                     "one search box and no submit button on the jobs home",
                     "the placeholder changes on focus; the AX name does not",
                     "'Skip to search' matches a generic submit hint and must be excluded"],
        "blocked_on": ("humanized `type` does not fill the search combobox (ok + blur + empty); "
                       "driver-level fix, tracked in the module docstring"),
    }
