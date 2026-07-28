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
* **The jobs home has one search box and NO location box and NO submit button.** The box's
  accessible name is `I'm looking for...` (a curly apostrophe, ellipsis as three dots). Its visible
  PLACEHOLDER changes to "Describe the job you want" on focus — so the placeholder is not an
  identifier; the AX name is.
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
QUERY_NAME_HINTS: tuple[str, ...] = ("looking for", "describe the job", "search by title")
QUERY_ROLES: tuple[str, ...] = ("combobox", "textbox", "searchbox")

#: MEASURED: there is no separate location field on the jobs home. LinkedIn takes location either
#: from the typeahead ("<role> in <city>") or from a second box that only appears on the RESULTS
#: page. Declared absent rather than guessed at, so the cadence skips it instead of typing a city
#: into the keyword box.
LOCATION_NAME_HINTS: tuple[str, ...] = ("city, state", "location")

#: MEASURED: no submit button exists on the jobs home, and the generic "search" hint matches the
#: `Skip to search` SKIP-LINK. So the engine declares that it has none, and anything looking for a
#: submit here must not fall back to a name match.
SUBMIT_NAME_HINTS: tuple[str, ...] = ()
FORBIDDEN_SUBMIT_NAMES: tuple[str, ...] = ("skip to", "keyboard shortcut", "close jump menu")

#: The blocker above. Flip to True only when a drive has typed into the box AND read the value back.
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
        "location": None,          # MEASURED absent on the jobs home
        "submit": None,            # MEASURED absent; Enter/typeahead is the commit
        "ready": bool(query) and SEARCH_SUBMIT_READY,
        "why": ("" if SEARCH_SUBMIT_READY else
                "The query box is found but the humanized `type` does not fill it — it reports ok, "
                "blurs the field and inserts nothing (measured session #22). Needs a "
                "focus-preserving fill in the driver before a LinkedIn query can be claimed."),
    }


def spec() -> dict[str, Any]:
    return {
        "host": HOST,
        "states": [HOME, SEARCH_RESULTS, JOB_DETAIL, LOGIN_WALL, EASY_APPLY],
        "search": {"query_hints": list(QUERY_NAME_HINTS), "has_location_box": False,
                   "has_submit_button": False, "ready": SEARCH_SUBMIT_READY},
        "measured": ["classes are build-hashed — never a class selector",
                     "one search box, no location box, no submit button on the jobs home",
                     "the placeholder changes on focus; the AX name does not",
                     "'Skip to search' matches a generic submit hint and must be excluded"],
        "blocked_on": ("humanized `type` does not fill the search combobox (ok + blur + empty); "
                       "driver-level fix, tracked in the module docstring"),
    }
