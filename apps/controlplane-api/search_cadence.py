"""Search cadence — the bounded, safe LOGIC for the job-search domain (planner seed).

Search is two DISTINCT tasks, and conflating them is what leads to "searching a million
different ways" (ad-hoc + bot-risky). This module names them, gives each an ordered recipe
with explicit BOUNDS and SAFETY rules, and classifies where an application actually routes
(so apply generalizes across sites instead of being siloed to Indeed/Workday).

This is the structured substrate a real planner will sequence; today it's the documented
cadence we follow by hand. Exposed at GET /api/search/cadence.
"""

from __future__ import annotations

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
    "interact_by": "CLICK like a human — click cards to open the in-page detail pane, click "
                   "pagination numbers to page forward; scroll the page; never URL-jump",
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
            "Extract all job cards → observed_jobs (deduped by platform:external_id).",
            "Shortlist the cards that match the query, and CLICK INTO each (opens the in-page detail "
            "pane) to read the full description — like a human, no viewjob URL-jump.",
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
        "known_platforms": sorted({a["ats_id"] for a in ATS_PLATFORMS} | {"company_site"}),
        "apply_outcomes": [{"outcome": o, "human_required": h} for o, h, _ in _APPLY_OUTCOMES],
        "note": "Two search tasks: extraction_sweep (record everything) vs apply_triage "
                "(triage→approve→apply→record). Apply routes by platform; not Indeed-only. "
                "After the form, classify_apply_outcome() detects random-event branches "
                "(ai_recruiter_gate, survey, account_creation, ...) and routes human vs auto.",
    }
