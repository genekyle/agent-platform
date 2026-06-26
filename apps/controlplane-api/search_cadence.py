"""Search cadence — the bounded, safe LOGIC for the job-search domain (planner seed).

Search is two DISTINCT tasks, and conflating them is what leads to "searching a million
different ways" (ad-hoc + bot-risky). This module names them, gives each an ordered recipe
with explicit BOUNDS and SAFETY rules, and classifies where an application actually routes
(so apply generalizes across sites instead of being siloed to Indeed/Workday).

This is the structured substrate a real planner will sequence; today it's the documented
cadence we follow by hand. Exposed at GET /api/search/cadence.
"""

from __future__ import annotations

from urllib.parse import urlparse

# Bounds keep the cadence SAFE + human-paced (see feedback_bot_safety_live_sessions):
# don't sweep endlessly, don't churn tabs, reach apply pages like a human.
BOUNDS = {
    "max_queries_per_session": 12,
    "max_pages_per_query": 5,
    "min_seconds_between_navigations": 3,
    "navigate_by": "search results only — NEVER job-detail URLs or new/closed tabs",
    "apply_requires": "explicit user approval per job before the final Submit",
}

CADENCE_MODES = {
    # ---- TASK 1: pure data gathering ----------------------------------------
    "extraction_sweep": {
        "goal": "Breadth — run through options and record EVERYTHING found. No applying.",
        "steps": [
            "Pick a query from the target preferences (job_preference profile).",
            "Navigate the existing tab to the results page (human-paced, single nav).",
            "Capture + classify the results page (trains L3 along the way).",
            "Extract all job cards → observed_jobs (deduped by platform:external_id).",
            "If more pages add value (new>0), page forward within bounds; else next query.",
        ],
        "records": ["observed_jobs (deduped)", "search_query", "page"],
        "stops_when": "queries exhausted or max_queries_per_session hit",
        "does_not": ["apply", "open job-detail URLs", "open/close tabs"],
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
            "Then continue (next approved job / next page).",
        ],
        "records": ["application_status (applied/skipped)", "source page", "search_query",
                    "application_platform"],
        "stops_when": "shortlist handled or user pauses",
        "does_not": ["auto-submit without approval", "URL-jump to jobs", "churn tabs"],
    },
}

# Where an application actually routes — apply is CROSS-SITE, not Indeed-only.
# (project_application_is_cross_site: Workday majority, but many others.)
_PLATFORM_HOSTS = {
    "smartapply.indeed.com": "indeed_quick_apply",
    "myworkdayjobs.com": "workday",
    "myworkday.com": "workday",
    "greenhouse.io": "greenhouse",
    "lever.co": "lever",
    "icims.com": "icims",
    "ashbyhq.com": "ashby",
    "taleo.net": "taleo",
    "successfactors.com": "successfactors",
    "smartrecruiters.com": "smartrecruiters",
    "workable.com": "workable",
}


def classify_apply_platform(url: str) -> str:
    """Map an apply destination URL to its ATS platform. Unknown external host =
    'company_site' (still handled — just not a recognized ATS yet). Drives which
    per-platform apply recipe to run; keeps the apply task generalized, not siloed."""
    host = (urlparse(url or "").hostname or "").lower()
    if not host:
        return "unknown"
    for needle, platform in _PLATFORM_HOSTS.items():
        if needle in host:
            return platform
    if "indeed.com" in host:
        return "indeed_quick_apply"
    return "company_site"


def cadence_spec() -> dict:
    """The full cadence definition — what GET /api/search/cadence returns."""
    return {
        "bounds": BOUNDS,
        "modes": CADENCE_MODES,
        "known_platforms": sorted(set(_PLATFORM_HOSTS.values()) | {"company_site"}),
        "note": "Two search tasks: extraction_sweep (record everything) vs apply_triage "
                "(triage→approve→apply→record). Apply routes by platform; not Indeed-only.",
    }
