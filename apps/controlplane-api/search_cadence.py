"""Search cadence — the bounded, safe LOGIC for the job-search domain (planner seed).

Search is two DISTINCT tasks, and conflating them is what leads to "searching a million
different ways" (ad-hoc + bot-risky). This module names them, gives each an ordered recipe
with explicit BOUNDS and SAFETY rules, and classifies where an application actually routes
(so apply generalizes across sites instead of being siloed to Indeed/Workday).

This is the structured substrate a real planner will sequence; today it's the documented
cadence we follow by hand. Exposed at GET /api/search/cadence.
"""

from __future__ import annotations

from typing import Optional

# Bounds keep the cadence SAFE + human-paced (see feedback_bot_safety_live_sessions):
# don't sweep endlessly, don't churn tabs, reach apply pages like a human.
BOUNDS = {
    "max_queries_per_session": 12,
    "max_pages_per_query": 5,
    "min_seconds_between_navigations": 3,
    # Distance floor: every search runs at >= this radius. Enforced by /api/search/sweep clicking
    # the Indeed distance filter (not a radius= URL param) up to this value before extracting.
    "min_radius_miles": 50,
    "navigate_by": "search results only — NEVER job-detail URLs or bot-like tab churn",
    # SCROLLING IS AN INPUT, NOT A LAYOUT OPERATION. Operator-directed 2026-07-30, after LinkedIn's
    # list would not move: a wheel with the cursor over the thing being scrolled, in the driver's
    # eased notches — never `scrollTop = …`, never `scrollIntoView` as a way of getting somewhere,
    # never a wheel at mid-viewport on a two-column app (it lands on whichever column is there).
    # Same reasoning as trusted clicks: an assignment is not a motion a hand could make, and on an
    # inner scroller it is not even the right element.
    "interact_by": "CLICK like a human — click cards to open the in-page detail pane, click "
                   "pagination numbers to page forward; SCROLL BY WHEEL with the cursor over the "
                   "list being scrolled; never URL-jump",
    "scroll_by": "wheel, cursor over the target scroller, humanized notches (never scrollTop)",
    "apply_requires": "explicit user approval per job before the final Submit",
    # Tab hygiene: Indeed opens the apply flow (smartapply) OR a cross-site ATS (Workday/…) in a
    # NEW tab. The "no tab churn" rule forbids scraper-like opening/closing of many tabs — it does
    # NOT forbid the human-natural epilogue: once ONE application is finished (submitted, or
    # abandoned at a human-required wall), CLOSE that single apply tab and return to the search
    # tab, then continue. That cleanup is driven via mcp /close_tab (focus_tab_url = the search).
    "tab_hygiene": "close the ONE finished apply tab and refocus the search tab before the next "
                   "prospect; never open/close tabs to browse — that single close is not churn",
}

# Structured SEARCH/triage spine — the search-phase analogue of apply_recipe.INDEED_APPLY_RECIPE.
# The blackboard's search plan is built from this. State ids are the LIVE states the tab classifier
# emits (apply_recipe.map_url_to_state), NOT the registry's short ids — they must match exactly or
# the plan can't advance. Step 2 is the handoff: opening a posting / clicking Apply leaves the
# search phase and the apply spine (INDEED_APPLY_RECIPE) takes over.
SEARCH_RECIPE = [
    {"step": 0, "state": "indeed_home",           "action": "enter query + location, run the search",
     "expect": ["indeed_search_results"]},
    {"step": 1, "state": "indeed_search_results", "action": "set distance >= min_radius_miles (click filter); "
     "extract cards; click shortlisted cards to read the detail pane; click pagination to page forward (bounded)",
     "expect": ["indeed_search_results", "indeed_job_posting"]},
    {"step": 2, "state": "indeed_job_posting",    "action": "open posting / click Apply (handoff to the apply flow)",
     "expect": ["indeed_job_posting", "indeed_apply_resume_selection", "indeed_apply_questions"]},
]


def search_recipe_states() -> list[str]:
    """The state ids the search spine advances through (for validation + the planner)."""
    return [entry["state"] for entry in SEARCH_RECIPE]


# --- Finding the search box, by looking rather than by assuming --------------------------------
#: Name fragments that identify each search control, best-match first. These are FRAGMENTS matched
#: against the live accessible name — never the whole name, because the whole name is not stable.
#:
#: Written after getting it wrong live (2026-07-24, session 19). The first version hard-coded
#: `("combobox", "What")`, `("combobox", "Where")` and `("button", "Find jobs")` from general
#: knowledge of Indeed. All three were wrong on the real page, which offers:
#:     combobox 'search: Job title, keywords, or company'
#:     combobox 'Edit location'
#:     button   'Search'
#: Nothing typed, nothing clicked. The lesson is not "use these names instead" — these will drift
#: too, and they differ between the logged-out home, the logged-in feed and the results page. It is
#: that the names must be DISCOVERED from a scan every time, which is what this function does.
_QUERY_HINTS = ("job title", "keywords", "what", "search:", "find jobs", "job search")
_LOCATION_HINTS = ("location", "where", "city, state", "postal")
_SUBMIT_HINTS = ("find jobs", "search", "find job")

_TEXT_ROLES = ("combobox", "textbox", "searchbox")


def find_search_controls(candidates: list[dict]) -> dict:
    """Locate the query box, location box and submit button on whatever search page we are on.

    Returns {query, location, submit} -> {"role", "name"}, omitting anything not found. Addressed
    by role + accessible NAME (never a selector or a node id) so the caller drives through the AX
    layer and survives node-id churn between scan and act.
    """
    out: dict[str, dict] = {}

    def _pick(roles: tuple[str, ...], hints: tuple[str, ...]) -> Optional[dict]:
        # Hints are ordered, so a page offering several matches yields the most specific one.
        for hint in hints:
            for c in candidates:
                role = (c.get("role") or "").lower()
                name = (c.get("name") or "").strip()
                if role in roles and name and hint in name.lower():
                    return {"role": role, "name": name}
        return None

    q = _pick(_TEXT_ROLES, _QUERY_HINTS)
    if q:
        out["query"] = q
    loc = _pick(_TEXT_ROLES, _LOCATION_HINTS)
    # A single box that matched both lists is the query box, not the location box — Indeed's
    # results page names its keyword field "search: Job title, keywords, or company", and calling
    # that the location field would type a city into the keyword box.
    if loc and (not q or loc["name"] != q["name"]):
        out["location"] = loc
    sub = _pick(("button",), _SUBMIT_HINTS)
    if sub:
        out["submit"] = sub
    return out


# --- HOW a results list is walked, per engine ---------------------------------------------------
# The cadence is the same everywhere because it is about how we BEHAVE. The traversal is not: a
# virtualised inner-scrolling list has to be wheeled through before its results exist at all, and a
# paginated page of static cards does not. That difference lived nowhere, so every caller improvised
# it — which is how LinkedIn ended up being read seven cards at a time (see linkedin_recipe's
# RESULTS_TRAVERSAL for the full account).
#
# Indeed's is the DEFAULT because it is the simple case, not because it is the norm: cards are all in
# the DOM, the window scrolls, and we click into the ones worth reading. LinkedIn declares its own.
_DEFAULT_TRAVERSAL = {
    "virtualised": False,
    "scroll_container": "window",
    "scroll_by": "wheel",
    "scroll_pointer": "over the results column",
    "located_by": "cards are all in the DOM — one read is the whole page",
    "click_into": "shortlist",          # only the query-matching cards earn a detail read
    "click_by": "humanized trusted click at the card's measured centre",
    "click_evidence": "the detail pane's title/description CHANGED (`switched`)",
    "paginate_by": "scroll to the bottom, then click the page number (never a ?start= jump)",
}


def traversal_for(platform: str) -> dict:
    """How to walk THIS engine's results list — the per-domain half of the sweep.

    Returns at minimum {virtualised, scroll_by, scroll_pointer, click_into, paginate_by}. Callers
    read `click_into` to decide whether every card gets opened or only the shortlist, and
    `virtualised` to decide whether the list must be scrolled before it can be trusted as complete.
    An unknown platform gets the default, which is the safe direction: it under-scrolls a list that
    needed wheeling rather than over-clicking a page that did not.
    """
    if (platform or "").strip().lower() == "linkedin":
        import linkedin_recipe
        return linkedin_recipe.results_traversal()
    return dict(_DEFAULT_TRAVERSAL)


CADENCE_MODES = {
    # ---- TASK 1: pure data gathering ----------------------------------------
    "extraction_sweep": {
        "goal": "Breadth — run through options and record EVERYTHING found. No applying. This is the "
                "bounded auto-sweep behind POST /api/search/sweep.",
        "steps": [
            "Pick a query from the target preferences (job_preference profile).",
            "Run the search on the existing tab (enter query + location), human-paced.",
            "Set the DISTANCE filter to >= min_radius_miles by CLICKING the filter pill and picking "
            "the option (never a radius= URL param). Refuse to gather sub-floor results.",
            "Capture + classify the results page (trains L3 along the way).",
            "WALK THE LIST per traversal_for(platform): on a VIRTUALISED list (LinkedIn) wheel "
            "through it with the cursor over the list column until a batch lands nothing — the "
            "results do not exist in the DOM until then, so extracting first records a partial "
            "page as a whole one.",
            "Extract all job cards → observed_jobs (deduped by platform:external_id).",
            "CLICK INTO the cards the traversal says to — every card where click_into is "
            "'every_card' (LinkedIn), otherwise the query-matching shortlist — to read the full "
            "description from the in-page pane, like a human, no viewjob URL-jump. Confirm the pane "
            "SWITCHED before recording it: both engines auto-open the first result.",
            "Scroll to the bottom and CLICK the pagination number to page forward within bounds; "
            "else next query.",
        ],
        "records": ["observed_jobs (deduped)", "job descriptions (shortlisted)", "search_query", "page"],
        "stops_when": "queries/pages exhausted, bounds hit, a live captcha, or logout",
        "does_not": ["apply", "open job-detail URLs", "open/close tabs", "gather below min_radius_miles"],
    },
    # ---- TASK 1.5: the operator's day-to-day loop — TARGETED search-and-apply ----
    # Neither pure search (scrape everything, apply to nothing) nor pure apply (apply to every
    # result). A page-by-page HUMAN-IN-THE-LOOP loop: the teacher shortlists per page, the operator
    # handpicks, we apply to the picks, THEN advance to the next page. Defined live with the
    # operator 2026-07-01 (see user_job_application_preferences: handpick = approval for that job).
    "targeted_search_and_apply": {
        "goal": "Page-by-page: shortlist per page → operator handpicks → apply to picks → next page.",
        "steps": [
            "Enter the query + location on the existing tab and run the search (human-paced).",
            "Set the DISTANCE filter to the target radius (>= min_radius_miles) before anything else.",
            "READ THE SEARCH META off page 1: total result count + visible page numbers — both are "
            "findable on the first results page and are recorded with the query (sizing the loop).",
            "Per page: extract all cards → observed_jobs; the teacher builds the page's shortlist "
            "(good matches AND semi-relevant, ~10 results/page) and SENDS it to the operator.",
            "The operator handpicks which (if any) of that page's shortlist to apply to — final say.",
            "For each pick: verify the pane shows the INTENDED job, then run the apply cadence "
            "(quick-apply drive, or cross-site recipe) — captcha/submit gates per the apply rules.",
            "APPLY EPILOGUE (every pick): the apply opens in a NEW tab (smartapply or the ATS). "
            "When it finishes — submitted, OR abandoned at a human-required wall (e.g. a Workday "
            "account gate we can't create) — record the outcome, then CLOSE that apply tab and "
            "refocus the search tab (mcp /close_tab, focus_tab_url=the search). Don't leave orphan "
            "apply tabs; return to exactly where triage left off.",
            "Only after the page's picks are handled: CLICK pagination to the next page. Repeat "
            "until the query's pages are exhausted, then record_outcome on the target.",
        ],
        "records": ["observed_jobs", "search meta (total results + page count) per query",
                    "per-page shortlist + operator picks", "application_status + provenance"],
        "stops_when": "all pages done, bounds hit, a live captcha gate, or the operator pauses",
        "does_not": ["apply without the operator's per-job pick", "skip the distance filter",
                     "advance a page while its picks are unfinished", "auto-solve captchas",
                     "leave the finished apply tab open (close it + refocus search before the next pick)"],
    },
    # ---- TASK 2: act on good fits -------------------------------------------
    "apply_triage": {
        "goal": "Find good fits on a page, get user approval, apply, record provenance.",
        "steps": [
            "On the current results page, shortlist candidates that fit the preferences.",
            "SEND the shortlist to the user (do not auto-pick).",
            "For each user-APPROVED job: reach its apply page like a human (user clicks in, "
            "or click the on-page Apply button — never URL-jump).",
            "Detect the application platform (classify_apply_platform) and route to the "
            "right apply recipe (Indeed quick-apply | Workday | Greenhouse | ...).",
            "Drive the apply cadence; PAUSE at the final Submit for explicit user approval.",
            "On submit: mark observed_jobs.applied + record which page + which search it came from.",
            "Close the finished apply tab and refocus the search tab (mcp /close_tab), then "
            "continue (next approved job / next page).",
        ],
        "records": ["application_status (applied/skipped)", "source page", "search_query",
                    "application_platform"],
        "stops_when": "shortlist handled or user pauses",
        # "churn tabs" = scraper-like open/close of many tabs. Closing the ONE finished apply tab
        # to return to search is the expected epilogue (see BOUNDS.tab_hygiene), not churn.
        "does_not": ["auto-submit without approval", "URL-jump to jobs", "churn tabs to browse"],
    },
    # ---- TASK 3: the TEACHING sweep — apply to EVERYTHING, end to end -----------
    # The controller's training mode (operator-directed 2026-07-17). NOT the day-to-day loop:
    # here we deliberately DON'T handpick. The goal is fixed and known — apply to every result of
    # one query — so decide() executes the WHOLE cadence and every step becomes training data
    # (right calls verify; wrong calls get corrected at the point of disagreement = golden rows).
    # "You must do everything in order to learn" — breadth of extraction_sweep, but each result is
    # driven end to end like apply_triage, minus the per-job handpick (the operator batch-approves
    # the sweep up front instead). This is a decide()-owned flow: it must know the cadence ALWAYS,
    # because the north star is outlined, not reasoned per step (that high-level reasoning is v2 —
    # see docs/PLAN_cadence_northstar.md). The HARD gates do NOT relax: the final Submit is still a
    # per-application operator confirm (consequential gate), account walls / captcha / AI-recruiter
    # branches still hand to the human (classify_apply_outcome human_required=True), navigation is
    # still human-like. Batch-approving the sweep authorizes ENTERING each apply, never the Submit.
    "apply_sweep": {
        "goal": "Breadth + depth for TEACHING: apply to EVERY result of one query, end to end, "
                "recording found + applied. No handpick — the operator batch-approves the sweep; "
                "decide() drives every step so the whole cadence is exercised and journaled.",
        "steps": [
            "STATE CHECK: confirm we're logged in and on a fresh Indeed page. Not logged in / a "
            "challenge → ESCALATE to the operator (never type a password, never auto-solve). This "
            "is cadence step 0 and it gates everything below.",
            "Enter the query + location on the existing tab and run the search (human-paced typing).",
            "Set the DISTANCE filter to >= min_radius_miles by CLICKING the pill (never a radius= "
            "URL param). Refuse to proceed below the floor.",
            "READ SEARCH META off page 1 (total results + page count) and record it with the query.",
            "Per page: extract ALL cards -> observed_jobs (deduped by platform:external_id).",
            "For EVERY card on the page (not a shortlist): click it to open the in-page pane, VERIFY "
            "the pane is that job (verify_job_identity — near-miss guard), then run the apply "
            "cadence for its platform (classify_apply_platform -> the right recipe).",
            "Drive each apply END TO END through decide(): fill every field (autofill + answer "
            "store), route the random-event branches (classify_apply_outcome), PAUSE at the final "
            "Submit for the operator's per-application approval, then record applied + provenance.",
            "APPLY EPILOGUE per result: close the ONE finished apply tab (submitted OR abandoned at "
            "a human-required wall) and refocus the search tab (mcp /close_tab). No orphan tabs.",
            "Only after the whole page is handled: CLICK pagination to the next page. Repeat until "
            "the query's pages are exhausted (bounds), then record_outcome on the target.",
        ],
        "records": ["observed_jobs (ALL, deduped)", "search meta (total + page count)",
                    "application_status + provenance (per result)", "the decision journal (every "
                    "decide() step — the whole point)"],
        "stops_when": "pages exhausted, bounds hit, a live captcha/challenge, logout, the weekly "
                      "budget cap, or the operator pauses",
        "does_not": ["type passwords or create accounts (operator-owned; escalate at the wall)",
                     "auto-solve captchas/2FA", "auto-submit without the per-application approval",
                     "open job-detail URLs / churn tabs to browse", "gather below min_radius_miles",
                     "handpick or skip results — the sweep applies to everything it can reach"],
    },
}

# Where an application actually routes — apply is CROSS-SITE, not Indeed-only.
# (project_application_is_cross_site: Workday majority, but many others.) The ATS host map and the
# per-ATS structure now live in ats_registry.py (each ATS is domain-like, with company→ATS
# generalization); this function delegates so there's ONE source of truth for "which ATS is this."
def classify_apply_platform(url: str) -> str:
    """Map an apply destination URL to its ATS platform id (see ats_registry.classify_ats).
    Unknown external host = 'company_site'; empty = 'unknown'. Drives which per-platform apply
    recipe to run; keeps the apply task generalized across companies sharing an ATS, not siloed."""
    from ats_registry import classify_ats
    return classify_ats(url)


# Apply OUTCOME branches — the "random events" an Indeed application can hit after the form.
# Each tuple: (outcome, human_required, regex). First match wins; order = most-specific first.
# Lets the apply loop RECOGNIZE the branch and route (autofill vs escalate to human) instead
# of blindly clicking. See project_apply_random_events.
_APPLY_OUTCOMES = [
    ("submitted",          False, r"application (has been |was |successfully )?submitted|your application was sent|application sent|application received|thanks for applying|successfully applied"),
    # ATS job no longer available: the Indeed listing outlived the source posting, so the company
    # ATS 404s / says the req is gone. NON-human — just skip this prospect and move on (don't
    # escalate, don't retry). Observed live on State Street Workday (req R-788153) 2026-06-30.
    ("ats_unavailable",    False, r"page you are looking for doesn'?t exist|no longer (accepting|available|posted)|this (job|posting|requisition|position) (is no longer|has expired|has been filled|could not be found)|job not found"),
    # reCAPTCHA gate at submit (often EXPIRES if the form sat too long) — HUMAN must (re)check
    # the box; never auto-solve. Submit button stays disabled until then.
    ("captcha",            True,  r"i'm not a robot|verification expired|recaptcha challenge|check the checkbox again|select all images"),
    # Post-submit AI-assistant satisfaction survey — OPTIONAL, app already submitted; skippable.
    ("post_submit_feedback", False, r"satisfied were you with the ai|improving the ai assistant|rate your experience"),
    # Interview-review step (after the AI interview): user-owned 'Submit all' to finalize.
    ("interview_review",   True,  r"interview review|review and edit responses|review your responses"),
    # AI-recruiter mini-interview gate (video/audio/text) — REQUIRES the human.
    ("ai_recruiter_gate",  True,  r"ai recruiter|respond using video|video,? audio,? or text|complete these steps before your application"),
    ("survey_assessment",  True,  r"\bsurvey\b|\bassessment\b|skills? test|questionnaire|personality"),
    ("account_creation",   True,  r"create (an )?account|set a password|sign up to continue"),
    ("company_site",       True,  r"apply on company site|continue to (the )?employer|you are leaving indeed"),
    ("additional_questions", False, r"answer these questions from the employer|questions from the employer"),
]


def classify_apply_outcome(page_text: str, url: str = "") -> dict:
    """Detect which apply-outcome branch the current page is — the 'check' for random events
    like the AI-recruiter interview. Returns {outcome, human_required, matched}. 'unknown'
    when nothing matches (treat as continue/inspect)."""
    import re
    text = (page_text or "")[:4000].lower()
    for outcome, human, pattern in _APPLY_OUTCOMES:
        if re.search(pattern, text):
            return {"outcome": outcome, "human_required": human, "matched": True}
    return {"outcome": "unknown", "human_required": False, "matched": False}


def cadence_spec() -> dict:
    """The full cadence definition — what GET /api/search/cadence returns."""
    from ats_registry import ATS_PLATFORMS
    return {
        "bounds": BOUNDS,
        "modes": CADENCE_MODES,
        "search_recipe": SEARCH_RECIPE,
        # The per-engine half: same behaviour everywhere, different list to walk.
        "traversals": {"indeed": traversal_for("indeed"), "linkedin": traversal_for("linkedin")},
        "known_platforms": sorted({a["ats_id"] for a in ATS_PLATFORMS} | {"company_site"}),
        "apply_outcomes": [{"outcome": o, "human_required": h} for o, h, _ in _APPLY_OUTCOMES],
        "note": "Two search tasks: extraction_sweep (record everything) vs apply_triage "
                "(triage→approve→apply→record). Apply routes by platform; not Indeed-only. "
                "After the form, classify_apply_outcome() detects random-event branches "
                "(ai_recruiter_gate, survey, account_creation, ...) and routes human vs auto.",
    }
