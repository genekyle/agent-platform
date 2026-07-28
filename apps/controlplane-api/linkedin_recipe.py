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
* **THE BOX'S ACCESSIBLE NAME IS ITS PLACEHOLDER, AND THE PLACEHOLDER CHANGES ON FOCUS.** Corrected
  2026-07-28, having first been recorded here BACKWARDS ("the placeholder is not an identifier; the
  AX name is"). There is exactly one real input — `<input>`, 280x34, no aria-label — so AX derives
  its name from the placeholder:

      unfocused -> "I'm looking for…"        focused -> "Describe the job you want"

  Which means addressing this control by accessible name is unstable BY CONSTRUCTION: the act of
  focusing it renames it. Resolving the stale name afterwards finds a boxless node — `/execute`
  reported `css_point: [0.0, 0.0]`, drove nothing, and returned ok. That, not the React write, is
  why the field stayed empty.
  The honest consequence: this control needs an addressing scheme that is not the name. It has no
  aria-label and no data-* to use, so the candidate is the node id from a scan taken IMMEDIATELY
  before the act (the tight scan->act window `project_fb_listing_schema` already describes), or a
  driver that holds the node it focused instead of re-resolving.
* **`_SUBMIT_HINTS` matching "search" picks `Skip to search`** — a skip-link, not a submit.
  Clicking it would jump the caret to a landmark and report a submitted query. This is why the
  engine declares its own control profile instead of inheriting Indeed's matcher.
* **The results list is virtualised and the whole app is a SPA** — see `_LINKEDIN_JOBS_JS` and
  `/await_results`; nothing here may treat a navigation (or its absence) as proof.

--------------------------------------------------------------------------------------
THE OPEN BLOCKER (measured, unsolved)
--------------------------------------------------------------------------------------
The humanized `type` DOES NOT FILL this combobox, and says `ok` while doing nothing:

    click  -> outcome ok, focused: True,  value ""
    type   -> outcome ok, focused: FALSE, value ""

So `type` blurs the element and inserts nothing. `/execute` ok means "the node resolved and CDP
dispatched", never "the page accepted it" — the tier-1/tier-2 split its own docstring describes —
and here the gap is total. Until a focus-preserving fill exists, `SEARCH_SUBMIT_READY` is False and
the cadence must not claim it can run a LinkedIn query.

The likely fix is the one the body driver already documents for React inputs: type for timing but
set the value authoritatively, then dispatch input/change so the framework's model updates. That is
a DRIVER change (`apps/mcp`), not a recipe change, which is exactly why it is named here and not
worked around with a bespoke selector.
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
#: BOTH spellings, because the name flips on focus (see the module note). A matcher that knows only
#: one of them finds the box exactly once and then loses it.
QUERY_NAME_HINTS: tuple[str, ...] = ("looking for", "describe the job", "search by title")
QUERY_ROLES: tuple[str, ...] = ("combobox", "textbox", "searchbox")

#: The location box is not ABSENT from the jobs home — it does not exist YET. Operator, 2026-07-28:
#: LinkedIn's search is STAGED, and that is the real difference from Indeed, not a missing field.
#: I had recorded the measurement ("no location box here") and drawn the wrong conclusion from it.
LOCATION_NAME_HINTS: tuple[str, ...] = ("city", "location", "where")

#: MEASURED: no submit button exists on the jobs home, and the generic "search" hint matches the
#: `Skip to search` SKIP-LINK. So the engine declares that it has none, and anything looking for a
#: submit here must not fall back to a name match.
SUBMIT_NAME_HINTS: tuple[str, ...] = ()
FORBIDDEN_SUBMIT_NAMES: tuple[str, ...] = ("skip to", "keyboard shortcut", "close jump menu")

#: The blocker above. Flip to True only when a drive has typed into the box AND read the value back.
#: Still False: the cause is now known (name-based addressing resolves a boxless node once focus
#: renames the control) but the fix — hold the focused node, or re-scan immediately before acting —
#: is not built.
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
        "value_from": "query",              # SearchState.query — the job title only
        "control": "query",                 # find_query_box
        "commits": True,                    # this is the CONSUMING act
        "lands_on": SEARCH_RESULTS,
        "why": "LinkedIn asks for the job title by itself. The city is not on this page and cannot "
               "be typed here — putting it in the title box searches for a place, not a role.",
    },
    {
        "stage": STAGE_LOCATION,
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
