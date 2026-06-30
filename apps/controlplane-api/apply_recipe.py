"""Indeed apply-flow state manager — the recipe + live state observer for the apply task.

Stops the "making it up as I go" problem: instead of holding tab/step state in my head, this
gives a STRUCTURED recipe (the expected state machine for an Indeed quick-apply) plus a tracker
that maps a live tab's URL/page-state onto the recipe — so at any moment we know which tab is
what, which step it's on, and the expected next state.

Teachable by design: every `state` here is a `page_state_registry` id (indeed_apply_*), and the
expected transitions are exactly what the `state_transition` model learns from captured
(observed_page_state -> post_action_state) data. The recipe is SEEDED here from the flows we've
observed live, and refines as the teacher (Haiku page-state classifier) + captures grow — the
same teacher→distill loop used everywhere else.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# --- The recipe: expected linear spine of an Indeed quick-apply -------------------
# Each step: state id, the action that advances it, and the state(s) it may lead to.
# Indeed skips steps when the profile is already saved, so `expect` lists alternatives.
INDEED_APPLY_RECIPE = [
    {"step": 0, "state": "indeed_job_posting",            "action": "click Apply with Indeed",
     "expect": ["indeed_apply_resume_selection", "indeed_apply_questions", "indeed_apply_review"]},
    {"step": 1, "state": "indeed_apply_resume_selection", "action": "Continue",
     "expect": ["indeed_apply_questions", "indeed_apply_contact_info", "indeed_apply_demographics", "indeed_apply_review"]},
    {"step": 2, "state": "indeed_apply_questions",        "action": "autofill + Continue",
     "expect": ["indeed_apply_questions", "indeed_apply_contact_info", "indeed_apply_demographics", "indeed_apply_review"]},
    {"step": 3, "state": "indeed_apply_contact_info",     "action": "autofill (atomic) + Continue",
     "expect": ["indeed_apply_questions", "indeed_apply_demographics", "indeed_apply_review"]},
    {"step": 4, "state": "indeed_apply_resume_review",    "action": "Continue",
     "expect": ["indeed_apply_demographics", "indeed_apply_review"]},
    {"step": 5, "state": "indeed_apply_demographics",     "action": "fill gender/ethnicity/etc + Review",
     "expect": ["indeed_apply_review", "indeed_apply_demographics"]},
    {"step": 6, "state": "indeed_apply_review",           "action": "Submit (human at captcha)",
     "expect": ["indeed_apply_submitted", "indeed_apply_ai_recruiter_gate", "captcha"]},
    {"step": 7, "state": "indeed_apply_submitted",        "action": "done", "expect": []},
]

# --- Branches: the "random events" off the spine (project_apply_random_events) -----
# human_required => STOP and hand to the operator; never auto-handle.
APPLY_BRANCHES = {
    "captcha":                     {"human_required": True,  "note": "reCAPTCHA box (often expired) — human checks it, then submit promptly"},
    "indeed_apply_ai_recruiter_gate": {"human_required": True, "note": "AI recruiter interview (video/audio/text) — human does it"},
    "interview_review":            {"human_required": True,  "note": "review AI-interview answers, 'Submit all'"},
    "survey_assessment":           {"human_required": True,  "note": "survey / skills assessment"},
    "account_creation":            {"human_required": True,  "note": "company-site account signup"},
    "company_site":                {"human_required": True,  "note": "redirect off Indeed to an ATS (Workday/Greenhouse/...)"},
    "post_submit_feedback":        {"human_required": False, "note": "optional AI-tool rating — skippable; app already in"},
}

# --- URL -> apply state (cheap, no model) ------------------------------------------
# The smartapply/dashboard URL reliably encodes the module, so we read state from it
# first; the page-state classifier / classify_apply_outcome refine + catch branches.
_URL_STATES = [
    (r"/post-apply",                              "indeed_apply_submitted"),
    (r"/dashboard/feedback",                      "post_submit_feedback"),
    (r"/dashboard/verify-interview",              "interview_review"),
    (r"/dashboard.*workflowExecutionId",          "indeed_apply_ai_recruiter_gate"),
    (r"resume-selection",                          "indeed_apply_resume_selection"),
    (r"structured-data-review|resume-module",      "indeed_apply_resume_review"),
    (r"demographic-questions",                     "indeed_apply_demographics"),
    (r"questions-module|/questions/",              "indeed_apply_questions"),
    (r"review-module",                             "indeed_apply_review"),
    (r"smartapply\.indeed\.com",                   "indeed_apply_resume_selection"),  # apply just opened
    (r"indeed\.com/viewjob|indeed\.com/jobs.*vjk", "indeed_job_posting"),
    (r"indeed\.com/jobs",                          "indeed_search_results"),
    (r"indeed\.com/?$|indeed\.com/\?",             "indeed_home"),
]


def map_url_to_state(url: str) -> str:
    u = url or ""
    for pattern, state in _URL_STATES:
        if re.search(pattern, u):
            return state
    return "unknown"


def _recipe_entry(state: str) -> Optional[dict]:
    return next((s for s in INDEED_APPLY_RECIPE if s["state"] == state), None)


def describe_tab(url: str, page_text: str = "") -> dict[str, Any]:
    """Map ONE live tab onto the recipe: its role, current state, recipe step, expected next,
    and whether it's a human-required branch. This is the per-tab 'where are we' readout."""
    state = map_url_to_state(url)
    branch = APPLY_BRANCHES.get(state)
    entry = _recipe_entry(state)
    role = ("apply" if "apply" in state or state in ("interview_review", "post_submit_feedback", "captcha")
            else "search" if state in ("indeed_search_results", "indeed_job_posting", "indeed_home")
            else "other")
    return {
        "url": (url or "")[:90],
        "state": state,
        "role": role,
        "recipe_step": entry["step"] if entry else None,
        "next_action": entry["action"] if entry else None,
        "expected_next": entry["expect"] if entry else [],
        "is_branch": branch is not None,
        "human_required": bool(branch and branch["human_required"]),
        "branch_note": branch["note"] if branch else None,
    }


# --- CROSS-SITE: Workday apply recipe seed (project_application_is_cross_site) -----------------
# Seeded from a live teacher probe (2026-06-30, "data analytics"/Lowell): clicking Indeed's
# "Apply on company site" opens indeed.com/applystart → the employer ATS in a NEW TAB. Two shapes
# observed for Workday:
#   * DIRECT: lands on *.myworkdayjobs.com/.../job/... (State Street).
#   * BRANDED WRAPPER: lands on the employer's careers site (e.g. jobs.takeda.com) whose "APPLY NOW"
#     href IS the Workday URL (takeda.wd3.myworkdayjobs.com/.../apply) — so detect the wrapper by the
#     APPLY-button href, not the visible host.
# The Workday apply itself gates on Sign In / Create Account — HUMAN-required, and needs a PERSISTENT
# pre-authed Workday profile per employer (a fresh profile hits the account wall). So the autonomous
# spine ends at the auth gate; everything past it is captured-and-taught once an authed profile exists.
WORKDAY_APPLY_RECIPE = [
    {"step": 0, "state": "ats_landing",          "action": "detect Workday (host *.myworkdayjobs.com OR an APPLY-NOW href to it); accept cookies if shown",
     "expect": ["ats_landing", "workday_apply_auth"]},
    {"step": 1, "state": "workday_apply_auth",   "action": "Sign In with the employer-specific Workday account (HUMAN / pre-authed profile)",
     "expect": ["workday_apply_form", "account_creation"]},
    {"step": 2, "state": "workday_apply_form",   "action": "autofill (My Information / Experience / Questions); often 'Autofill with Resume'",
     "expect": ["workday_apply_form", "workday_apply_review"]},
    {"step": 3, "state": "workday_apply_review",  "action": "Review + Submit (HUMAN approval at the final Submit)",
     "expect": ["submitted"]},
]

WORKDAY_APPLY_BRANCHES = {
    "ats_unavailable":  {"human_required": False, "note": "req 404'd on the ATS (Indeed listing outlived it) — skip, next prospect"},
    "account_creation": {"human_required": True,  "note": "Workday account signup — needs a persistent pre-authed profile per employer"},
    "captcha":          {"human_required": True,  "note": "anti-bot challenge — human clears it"},
}


def recipe_spec() -> dict[str, Any]:
    return {
        "domain": "indeed",
        "recipe": INDEED_APPLY_RECIPE,
        "branches": APPLY_BRANCHES,
        "cross_site": {
            "workday": {"recipe": WORKDAY_APPLY_RECIPE, "branches": WORKDAY_APPLY_BRANCHES,
                        "detect": "host matches *.myworkdayjobs.com, OR a branded careers wrapper whose "
                                  "APPLY-NOW href targets *.myworkdayjobs.com (e.g. Takeda)"},
        },
        "teachable": "states = page_state_registry indeed_apply_* ; transitions = the "
                     "state_transition model learns from captured observed->post_action data. "
                     "Seeded from observed live flows; refines as captures + the teacher grow. "
                     "Cross-site recipes (Workday/...) are seeded from teacher probes and graduate "
                     "to full spines once a pre-authed per-employer profile exists.",
    }
