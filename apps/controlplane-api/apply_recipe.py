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
    # `indeed_apply_resume_highlights` added 2026-08-06 from a MEASURED miss, not a guess: the
    # orienter's practice loop predicted this list, Continue was pressed on MFS Investment
    # Management's application, and the tab went `/resume-selection-module/resume-selection` ->
    # `/resume-module/structured-data-intro`. First live trial of the scorer, first correction it
    # bought (orientation_log: miss, 0/1).
    {"step": 1, "state": "indeed_apply_resume_selection", "action": "Continue",
     "expect": ["indeed_apply_resume_highlights", "indeed_apply_resume_review", "indeed_apply_questions", "indeed_apply_contact_info", "indeed_apply_demographics", "indeed_apply_review"]},
    # THE HIGHLIGHTS SCREEN — Indeed's structured-data capture, operator-identified live
    # 2026-08-06. Not a page to click past: the applicant marks up the parts of their resume the
    # employer's screen reads, and skipping it forfeits the thing it exists for. Every advance is
    # gated on the unanswered-required-fields scan, so a bare Continue cannot walk over it.
    #
    # Its own hazard, met on the first encounter: the editor's "Save and close" opens a
    # {Save, Don't save} modal, and the advance lexicon's longest-match tie-break picked the
    # negation (fixed in `decide._is_negated`). A screen whose controls include a discard is one
    # to be careful on.
    {"step": 2, "state": "indeed_apply_resume_highlights", "action": "highlight resume details",
     "control": "Review details",
     "expect": ["indeed_apply_resume_review", "indeed_apply_questions", "indeed_apply_contact_info", "indeed_apply_demographics", "indeed_apply_review"]},
    {"step": 3, "state": "indeed_apply_questions",        "action": "autofill + Continue",
     "expect": ["indeed_apply_questions", "indeed_apply_contact_info", "indeed_apply_demographics", "indeed_apply_review"]},
    {"step": 4, "state": "indeed_apply_contact_info",     "action": "autofill (atomic) + Continue",
     "expect": ["indeed_apply_questions", "indeed_apply_demographics", "indeed_apply_review"]},
    {"step": 5, "state": "indeed_apply_resume_review",    "action": "Continue",
     "expect": ["indeed_apply_demographics", "indeed_apply_review"]},
    {"step": 6, "state": "indeed_apply_demographics",     "action": "fill gender/ethnicity/etc + Review",
     "expect": ["indeed_apply_review", "indeed_apply_demographics"]},
    {"step": 7, "state": "indeed_apply_review",           "action": "Submit (human at captcha)",
     "expect": ["indeed_apply_submitted", "indeed_apply_ai_recruiter_gate", "captcha"]},
    {"step": 8, "state": "indeed_apply_submitted",        "action": "record applied + provenance, "
     "then EPILOGUE: close this apply tab and refocus the search tab (mcp /close_tab, "
     "focus_tab_url=the search) so the next prospect starts where triage left off", "expect": []},
]

# The apply flow opens in a NEW tab (smartapply for quick-apply, the ATS host for cross-site). The
# EPILOGUE is the same for every terminal — whether we submitted or bailed at a human-required wall
# (Workday account gate, survey, ai_recruiter): record the outcome, then close that one apply tab
# and return to the search tab. This is what closes the loop back to the search cadence.
APPLY_EPILOGUE = {
    "when": "any apply terminal — submitted OR abandoned at a human-required branch",
    "do": "POST /api/career_search/apply/epilogue {external_id, status, ats_id, tenant_id, "
          "apply_tab_url, search_tab_url} — records the outcome AND closes the apply tab AND "
          "refocuses the search tab, in that order",
    "why": "no orphan apply tabs; the next prospect resumes exactly where triage left off",
    # This was prose + a bare /close_tab primitive until 2026-07-15 — nothing wired them, so every
    # finished apply left an orphan ATS tab and an unrecorded outcome. It is now ONE endpoint, and
    # it is a REQUIRED step of the loop, not a manual tidy-up.
    "endpoint": "POST /api/career_search/apply/epilogue",
    "order_matters": "RECORD before CLOSE — a failed close still leaves the outcome known, but a "
                     "closed tab with no record is unrecoverable",
    "status_values": {"applied": "CONFIRMED submitted — only this stamps applied_at",
                      "abandoned": "stopped at a human-required wall (account gate, survey, "
                                   "assessment); the prospect stays resumable",
                      "skipped": "triaged out without applying"},
    "verified": "Wellington Management · Financial Reporting Analyst US Funds · req R94007 · "
                "2026-07-15: recorded applied + closed the Workday tab + refocused search (1 tab left)",
}

# --- Branches: the "random events" off the spine (project_apply_random_events) -----
# human_required => STOP and hand to the operator; never auto-handle.
APPLY_BRANCHES = {
    "captcha":                     {"human_required": True,
                                    "note": "reCAPTCHA box — human checks it, then SUBMIT WITHIN SECONDS. "
                                            "THE TOKEN HAS A SHORT TTL (~1-2 min) and its expiry is SILENT: "
                                            "'Submit your application' simply goes disabled=true with NO error "
                                            "text, no aria-describedby, no alert — the page looks identical to a "
                                            "ready one. Proven live 2026-07-17 (Purple Carrot): solved -> gate "
                                            "read cleared -> our submit fired and no-op'd; by the next poll the "
                                            "checkbox was unsolved again. A human round-trip between the solve "
                                            "and the submit ALWAYS loses this race. So: never ask the operator to "
                                            "solve and then go do something else. Poll the gate and fire submit in "
                                            "the SAME loop iteration it clears (no re-probe, no confirmation "
                                            "question in between), and if submit is disabled with no error, "
                                            "suspect an expired token FIRST — re-check /challenge_visibility "
                                            "before diagnosing fields (extends the captcha-first rule)."},
    "indeed_apply_ai_recruiter_gate": {"human_required": True, "note": "AI recruiter interview (video/audio/text) — human does it"},
    "interview_review":            {"human_required": True,  "note": "review AI-interview answers, 'Submit all'"},
    "survey_assessment":           {"human_required": True,  "note": "survey / skills assessment"},
    "account_creation":            {"human_required": True,  "note": "company-site account signup"},
    "company_site":                {"human_required": True,  "note": "redirect off Indeed to an ATS (Workday/Greenhouse/...)"},
    "post_submit_feedback":        {"human_required": False, "note": "optional AI-tool rating — skippable; app already in"},
    "ai_use_attestation":          {"human_required": False,
                                    "note": "a field asking the applicant to attest whether their materials were "
                                            "generated/edited/supplemented by AI tools (KKR's Greenhouse form names "
                                            "ChatGPT/Gemini/Claude explicitly). Answered from the answer store key "
                                            "`ai_use_attestation` like any other question. Detected (not string-matched) "
                                            "because the wording varies per employer and can INVERT — see "
                                            "GREENHOUSE_LESSONS.ai_use_attestation."},
}

# Detects the attestation by question TEXT, so it's recognised on any ATS rather than only where we've
# seen the exact wording. Biased toward detecting: a miss means the field gets blind-filled from a
# stored value whose polarity may not match the question (these invert — see GREENHOUSE_LESSONS).
# STRONG — any one of these is enough on its own.
_AI_ATTEST_STRONG = [
    r"(chatgpt|gemini|\bclaude\b|copilot|\bllm\b)",                      # names a tool
    r"without\s+(the\s+)?(use\s+of\s+)?(any\s+)?(ai|artificial intelligence)\b",
    r"not\s+(been\s+)?(generated|written|created|produced|edited|supplemented)\b.{0,80}\b(ai|artificial intelligence)\b",
    r"\b(ai|artificial intelligence)\b.{0,60}(tools?|assistant|assistance).{0,90}\b(not|without|no)\b",
    r"\b(no|not)\s+(ai|artificial intelligence)\s+(was\s+)?(used|involved)",
]
# WEAK — need two together (each is innocuous alone).
_AI_ATTEST_WEAK = [
    r"reflects?\s+my\s+own\s+work",
    r"\bmy\s+own\s+(work|words|writing)\b",
    r"\b(certify|attest|confirm|declare)\b",
    r"\b(ai|artificial intelligence)\b",
]
AI_USE_ATTESTATION_PATTERNS = _AI_ATTEST_STRONG + _AI_ATTEST_WEAK   # kept for introspection/tests


def is_ai_use_attestation(question_text: str) -> bool:
    """True when a form field asks the applicant to attest about AI-tool use in their application.

    Detection matters because the wording varies a lot per employer and can INVERT (KKR: "I confirm ...
    NOT generated by AI" -> Yes = confirming; elsewhere "did you use AI?" -> Yes = the opposite), so a
    fixed-string match either misses the field or fills it with the wrong polarity. Once detected, the
    value comes from the answer store key `ai_use_attestation`. Exposed as a function (not just a regex
    list) so the scanner, the recipe and any future L3 classifier all ask the same question the same way.
    """
    t = (question_text or "").strip().lower()
    if not t:
        return False
    if any(re.search(p, t, re.I) for p in _AI_ATTEST_STRONG):
        return True
    return sum(1 for p in _AI_ATTEST_WEAK if re.search(p, t, re.I)) >= 2

# --- URL -> apply state (cheap, no model) ------------------------------------------
# The smartapply/dashboard URL reliably encodes the module, so we read state from it
# first; the page-state classifier / classify_apply_outcome refine + catch branches.
_URL_STATES = [
    (r"/post-apply",                              "indeed_apply_submitted"),
    (r"/dashboard/feedback",                      "post_submit_feedback"),
    (r"/dashboard/verify-interview",              "interview_review"),
    (r"/dashboard.*workflowExecutionId",          "indeed_apply_ai_recruiter_gate"),
    (r"resume-selection",                          "indeed_apply_resume_selection"),
    # BEFORE the `resume-module` catch-all, which would otherwise swallow it. Indeed's structured-
    # data capture: the applicant marks up the details of their resume that the employer's screen
    # reads, so the application is less likely to be filtered out. Operator, live 2026-08-06:
    # "we will be highlighting details of our resume and application to send to the employer so we
    # have a better chance of not getting screened out." Its own screen, its own state — folding it
    # into `resume_review` would tell the ladder a form to fill in is a page to click past.
    (r"structured-data-intro|structured-data/",    "indeed_apply_resume_highlights"),
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


def expected_next_for(state: str) -> tuple[str, ...]:
    """The states the recipe says a step in `state` should land on — the ground-truth edges used
    wherever an expectation is missing. Empty when the recipe has no entry (nothing to inherit;
    an absent expectation must never be fabricated)."""
    entry = _recipe_entry(state)
    return tuple(entry.get("expect") or ()) if entry else ()


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


# --- CROSS-SITE: Workday apply recipe (project_application_is_cross_site) ----------------------
# Upgraded 2026-07-01 from a probe-seed to a LIVE-VERIFIED driving recipe: driven end-to-end on
# State Street (statestreet.wd1.myworkdayjobs.com) with the operator's real candidate account,
# through sign-in and a fully prefilled My Information step. Two landing shapes:
#   * DIRECT: *.myworkdayjobs.com/.../job/... (State Street).
#   * BRANDED WRAPPER: employer careers site (jobs.takeda.com) whose APPLY-NOW href IS the Workday
#     URL — detect by the APPLY-button href, not the visible host.
# Selectors are Workday `data-automation-id`s — STABLE across tenants (Workday renders them from the
# same component library), which is what makes this recipe transferable, not State-Street-specific.
WORKDAY_APPLY_RECIPE = [
    {"step": 0, "state": "ats_landing",
     "action": "detect Workday; accept cookies if shown. If the deep-linked req 404s "
               "(\"doesn't exist\"), DON'T trust ats_unavailable yet: search the TENANT "
               "(?q=<title>) — Indeed links go stale while the re-posted req is live "
               "(saw R-791270 dead → R-791273 live, same title/location).",
     "selectors": {"job_title_link": "a[data-automation-id=jobTitle]",
                   "not_found_text": r"doesn't exist|no longer"},
     "expect": ["ats_landing", "workday_job_posting"]},
    {"step": 1, "state": "workday_job_posting",
     "action": "click Apply → choose the apply method. 'Use My Last Application' is the BEST path "
               "when the candidate account exists (prefills everything); else 'Autofill with Resume' "
               "(needs the resume file); 'Apply Manually' is the fallback.",
     "selectors": {"apply": "[data-automation-id=adventureButton]",
                   "use_last": "[data-automation-id=useMyLastApplication]",
                   "autofill_resume": "[data-automation-id=autofillWithResume]",
                   "apply_manually": "[data-automation-id=applyManually]"},
     "expect": ["workday_apply_auth"]},
    {"step": 2, "state": "workday_apply_auth",
     "action": "the flow shows Create Account even when tenant-nav is signed in — click its Sign In "
               "link and authenticate with the per-employer candidate account. NEVER fill "
               "'beecatcher' (bot honeypot). Verify advance: progress bar re-numbers (8→7 steps).",
     "selectors": {"sign_in_link": "[data-automation-id=signInLink]",
                   "email": "[data-automation-id=email]",
                   "password": "[data-automation-id=password]",
                   "submit": "[data-automation-id=signInSubmitButton]",
                   "honeypot_do_not_fill": "[data-automation-id=beecatcher]",
                   "signed_in_marker": "[data-automation-id=utilityButtonAccount]"},
     "expect": ["workday_my_information", "account_creation"]},
    {"step": 3, "state": "workday_my_information",
     "action": "mostly PREFILLED by Use-My-Last-Application (legal name, address, phone, country). "
               "Usually one required gap: 'How Did You Hear About Us' (formField-source) — a nested "
               "prompt (Online Source → Indeed). KNOWN AUTOMATION GAP: see WORKDAY_LESSONS.",
     "selectors": {"source": "[data-automation-id=formField-source]",
                   "first_name": "[data-automation-id='legalName--firstName']",
                   "previous_worker": "[data-automation-id=candidateIsPreviousWorker]",
                   "next": "button[data-automation-id=bottom-navigation-next-button]"},
     "expect": ["workday_my_experience"]},
    {"step": 4, "state": "workday_my_experience",
     "action": "work history / resume section — verify prefill, fill gaps, Save and Continue",
     "expect": ["workday_questions"]},
    {"step": 5, "state": "workday_questions",
     "action": "Application Questions (State Street: 2 pages) — autofill from the answer profile; "
               "ASK THE OPERATOR for unmatched/sensitive ones (expected branch, not a failure)",
     "expect": ["workday_questions", "workday_voluntary_disclosures"]},
    {"step": 6, "state": "workday_voluntary_disclosures",
     "action": "Voluntary Disclosures + Self Identify — decline/leave blank per operator preference",
     "expect": ["workday_review"]},
    {"step": 7, "state": "workday_review",
     "action": "Review — PAUSE: run the captcha gate check, then the OPERATOR approves Submit",
     "expect": ["submitted"]},
]

WORKDAY_APPLY_BRANCHES = {
    "ats_unavailable":  {"human_required": False, "note": "req 404'd on the ATS — but if AUTHED, tenant-search the title first (stale-re-post pattern); only then skip"},
    "account_creation": {"human_required": True,  "note": "no candidate account for this employer — needs the operator (persistent per-employer profile). ABANDON this prospect (don't create an account): record blocked, then run APPLY_EPILOGUE — close the ATS tab + refocus search"},
    "captcha":          {"human_required": True,  "note": "anti-bot challenge — human clears it (captcha-first check on any blocked action)"},
    "nested_prompt_gap": {"human_required": True, "note": "Workday nested-prompt multiselect resists CDP input — hand the single field to the operator, continue after"},
}

# What the live teacher LEARNED driving Workday — the planner's seed knowledge. `works` are proven
# paths to prefer; `gaps` are steps automation cannot yet do (route to operator, or build the tool).
WORKDAY_LESSONS = {
    "works": [
        "trusted CDP mouse clicks (Input.dispatchMouseEvent) drive Workday buttons/links reliably",
        "Input.insertText typing works for text/password fields (email, password)",
        "data-automation-id selectors are the stable handle — prefer them over classes/text",
        "'Use My Last Application' prefills the whole My Information step from the candidate profile",
        "stale Indeed deep-link ≠ dead req: tenant ?q= search finds the live re-post",
        "page navigations kill the CDP websocket — reconnect and re-discover the target (expected)",
    ],
    "prompt_action": [  # reusable atomic action for Workday hierarchical prompts + listboxes
        "SOLVED nested-prompt / listbox selection via the reusable MCP action `POST /select_prompt` "
        "(field_name, value): opens the field with a NATIVE node-click, then — if the popup has a "
        "searchBox — types the value with TRUSTED per-char key events (Workday fetches results "
        "server-side on real keystrokes; value-set/insertText do NOT trigger it), and finally "
        "NATIVE-clicks the matched option by accessible name (coordinate clicks mis-fire on long/"
        "virtualized lists — picked 'American Samoa' for 'New Hampshire'). Validated live on State. "
        "A stale session silently returns NO options → refresh first. Pass a PRECISE field_name; the "
        "field's accessible name embeds its current value ('State New Hampshire Required'), and short "
        "names collide ('State' matches 'United States').",
    ],
    "gaps": [
        "(was: nested-prompt multiselect — NOW handled by /select_prompt, see works above)",
        "3-spinner DATE widget (dateSectionMonth/Day/Year-input): inputs are linked and auto-advance; "
        "CDP click+type+backspace scrambles across sub-fields (got '12//', '//2012'). Operator fills it",
        "flow-level auth is separate from tenant-nav auth (sign-in may be needed twice)",
    ],
    # PROVEN full-drive (State Street BA, submitted 2026-07-02): single-select listbox dropdowns,
    # checkboxes, and TEXT fields all work with trusted-click + Input.insertText (NOT the React
    # value-setter — that leaves aria-invalid=true; must TYPE). Clear text fields with Backspace*N
    # before typing (value-setter left stale values). 2 of ~20 fields needed the operator (above).
    "field_types_that_work": ["single-select listbox dropdown", "checkbox", "typed text (insertText after clearing)"],
    "field_types_route_to_operator": ["nested/hierarchical multiselect prompt", "3-spinner date widget"],
}


# --- Workday ACCOUNT lifecycle: create-account leg + sign-in leg (the Account Manager's loop) -----
# A per-employer Workday login is CREATED before it can sign in. The account record's status drives
# WHICH leg runs — this is the "state as an account" the operator described:
#   needs_creation  → WORKDAY_CREATE_ACCOUNT_RECIPE  (the button says "Create Account")
#   created/active  → WORKDAY_SIGN_IN_RECIPE          (the button says "Sign In")
# then either leg hands off to WORKDAY_APPLY_RECIPE (My Information → … → Review). All three are ONE
# loop the (future, operator-run) Account Manager executes end-to-end so the operator doesn't manage it.
#
# Fields are matched by accessible NAME (role + name → backend_node_id at act time), the churn-immune
# AX layer. Verified live on U.S. Bank's Workday tenant (usbank.wd1.myworkdayjobs.com) 2026-07-12.
#
# BOUNDARY: these are DATA recipes describing the flow. They are executed by the operator-triggered
# Account Manager / the operator — NEVER by the agent's own tool-loop (the agent does not type
# passwords into a site or submit an account creation/sign-in). See docs/PLAN_account_manager_and_l3.md.
WORKDAY_CREATE_ACCOUNT_RECIPE = [
    {"step": 0, "state": "workday_create_account",
     "action": "fill Email Address (username) + Password + Verify New Password (the generated "
               "credential), CHECK the acknowledge checkbox, click Create Account. NEVER fill the "
               "honeypot. May then require email verification (errand → gmail fetch_login_code).",
     "fields": {
         "email": {"role": "textbox", "name": "Email Address"},
         "password": {"role": "textbox", "name": "Password"},
         "verify_password": {"role": "textbox", "name": "Verify New Password"},
         "acknowledge": {"role": "checkbox", "name": "I confirm that I have read and acknowledge"},
     },
     "submit": {"role": "button", "name": "Create Account"},
     "toggle_to_sign_in": {"role": "button", "name": "Sign In"},
     "honeypot_do_not_fill": {"role": "textbox", "name": "Enter website. This input is for robots only"},
     # Creating the account does NOT land in one place — it BRANCHES, and which branch you get
     # varies by tenant (and, for the same tenant, by how much Workday trusts the session). Classify
     # the landing state, don't assume. Observed live 2026-07-15 (Wellington): straight in, no wall.
     "expect": ["workday_my_information", "workday_sign_in", "workday_verify_email"],
     "branches": {
         "workday_my_information": {
             "meaning": "signed in automatically — creation flowed STRAIGHT into the application",
             "detect": "account email in the header (Settings <email> / Candidate Home) AND the "
                       "stepper reads 'current step 1 of 6' with heading 'My Information'",
             "next": "drive WORKDAY_APPLY_RECIPE from My Information",
             "observed": "Wellington 2026-07-15"},
         "workday_sign_in": {
             "meaning": "account created but dropped at a LOGIN WALL — must sign in with the creds",
             "detect": "Email/Password fields + a 'Sign In' submit, no account email in the header",
             "next": "WORKDAY_SIGN_IN_RECIPE (operator-triggered ▶ Login), then the apply spine"},
         "workday_verify_email": {
             "meaning": "account created but GATED on email verification before it can be used",
             "detect": "'verify'/'check your email'/'code sent' copy; no application stepper",
             "next": "ERRAND → gmail fetch_login_code, then resume. Human-gated; never guess a code."},
     },
     # The stepper itself disambiguates: with the account step pending it reads 'step 1 of 7'
     # (Create Account/Sign In); once the account exists that step DISAPPEARS and My Information
     # becomes 'step 1 of 6'. Cheap, deterministic signal — prefer it over guessing from the URL.
     "step_count_tell": {"7": "account step still pending", "6": "account done; apply spine only"},
     "signed_in_tell": "the ACCOUNT EMAIL in the header (Settings <email> / Candidate Home). NOT a "
                       "'Sign Out' button — a /sign out/i probe returns false while signed in."},
]

WORKDAY_SIGN_IN_RECIPE = [
    {"step": 0, "state": "workday_sign_in",
     "action": "fill Email Address + Password (resolved from the account's stored/derived creds), "
               "click Sign In. NEVER fill the honeypot. 2FA/verification → escalate.",
     "fields": {"email": {"role": "textbox", "name": "Email Address"},
                "password": {"role": "textbox", "name": "Password"}},
     "submit": {"role": "button", "name": "Sign In"},
     "honeypot_do_not_fill": {"role": "textbox", "name": "Enter website. This input is for robots only"},
     "expect": ["workday_my_information"]},
]

WORKDAY_ACCOUNT_LOOP = {
    "needs_creation": {"state": "workday_create_account", "recipe": "WORKDAY_CREATE_ACCOUNT_RECIPE",
                       "button": "Create Account"},
    "created": {"state": "workday_sign_in", "recipe": "WORKDAY_SIGN_IN_RECIPE", "button": "Sign In"},
    "then": "hand to WORKDAY_APPLY_RECIPE (My Information → … → Review → operator Submit)",
    "runs_as": "ONE loop executed by the operator-run Account Manager (never the agent's own loop)",
}


# --- AppVault ACCOUNT lifecycle (Ahold Delhaize et al.) --------------------------------------------
# Reached via a careers FRONT (careerswithus.com) → 'APPLY NOW' → <employer>apply.appvault.com. The
# apply is account-gated behind a Material-UI login. Mapped live 2026-07-14 on
# aholddelhaizeapply.appvault.com. KEY QUIRK: MUI inputs carry NO stable accessible name/id EXCEPT the
# two password fields (#outlined-adornment-password / #outlined-adornment-re-password) — match the
# email/name fields by their floating-LABEL text or DOM order, not by role+name (the AX name is empty).
# Password RULES (enforced by AppVault): 8–18 chars, NO whitespace, ≥1 upper + ≥1 lower + ≥1 non-alpha
# — the generated credential MUST satisfy this. No bot-honeypot observed. Terms must be accepted (a
# link, not a checkbox) before Continue enables.
#
# BOUNDARY (same as Workday): DATA recipes. Executed by the operator-triggered Account Manager / the
# operator — NEVER the agent's own tool-loop. The agent never types a password into the site.
APPVAULT_CREATE_ACCOUNT_RECIPE = [
    {"step": 0, "state": "appvault_create_account",
     "action": "fill Email + Password + confirm-Password (the generated credential; must meet the "
               "8-18/upper/lower/non-alpha rule) + First Name + Last Name; Country of Residence and "
               "Profile Visibility default (United States / Any company recruiter); click 'Click Here "
               "to Accept Terms of Use'; then click Continue. May then require email verification "
               "(errand → gmail fetch_login_code).",
     "fields": {
         "email": {"label": "Email", "role": "textbox", "match": "label_or_first_text",
                   "note": "MUI floating label 'Email *'; input has no name/id — match by label or 1st text input"},
         "password": {"label": "Password", "selector": "#outlined-adornment-password"},
         "verify_password": {"label": "Enter password again", "selector": "#outlined-adornment-re-password"},
         "first_name": {"label": "First Name", "role": "textbox", "match": "label_proximity"},
         "last_name": {"label": "Last Name", "role": "textbox", "match": "label_proximity"},
     },
     "defaults_ok": {"country_of_residence": "United States", "profile_visibility": "Any company recruiter"},
     "accept_terms": {"open_link": {"role": "link", "name": "Click Here to Accept Terms of Use"},
                      "then_modal": {"role": "button", "name": "Agree"},
                      "note": "the link opens a 'Term of Use Policy' MODAL (buttons Print/Disagree/Agree); "
                              "click Agree — Continue stays DISABLED until terms are agreed (live 2026-07-14)"},
     "submit": {"role": "button", "name": "Continue"},
     "password_rules": "8-18 chars, no whitespace, ≥1 upper, ≥1 lower, ≥1 non-alpha",
     "honeypot_do_not_fill": None,
     "expect": ["appvault_apply", "appvault_verify_email", "account_creation"]},
]

APPVAULT_SIGN_IN_RECIPE = [
    {"step": 0, "state": "appvault_login",
     "action": "fill Email + Password (resolved from the account's stored/derived creds), click "
               "Sign In. MUI inputs — match Password by #outlined-adornment-password, Email by the "
               "1st text input. 2FA/verification → escalate.",
     "fields": {"email": {"label": "Email Address", "role": "textbox", "match": "first_text"},
                "password": {"label": "Password", "selector": "#outlined-adornment-password"}},
     "submit": {"role": "button", "name": "Sign In"},
     "honeypot_do_not_fill": None,
     "expect": ["appvault_apply"]},
]

APPVAULT_ACCOUNT_LOOP = {
    "needs_creation": {"state": "appvault_create_account", "recipe": "APPVAULT_CREATE_ACCOUNT_RECIPE",
                       "button": "Create an Account"},
    "created": {"state": "appvault_login", "recipe": "APPVAULT_SIGN_IN_RECIPE", "button": "Sign In"},
    "then": "hand to APPVAULT_APPLY_RECIPE (the post-auth apply spine — captured once authed)",
    "runs_as": "ONE loop executed by the operator-run Account Manager (never the agent's own loop)",
}

# Post-auth apply spine — TBD (behind the account wall; seed it from the first authed capture, the
# way WORKDAY_APPLY_RECIPE was seeded from teacher probes).
APPVAULT_APPLY_RECIPE: list[dict[str, Any]] = []


# --- iCIMS (Joslin Diabetes Center et al.) ---------------------------------------------------------
# Mapped live 2026-07-26 on jobs-joslin.icims.com, reached from Indeed's "Apply on company site".
# Per-employer subdomain (jobs-<tenant>.icims.com / careers-<tenant>.icims.com), often behind the
# employer's own branded wrapper.
#
# THE ONE THING TO KNOW ABOUT iCIMS: THE PAGE IS TWO DOCUMENTS. The employer's wrapper carries the
# nav, the footer and a newsletter box; `#icims_content_iframe` carries the job and the ENTIRE apply
# flow. Same-origin, so role+name addressing crosses it — but anything that reads, measures or
# matches on the TOP document alone is looking at the hospital's homepage (this cost us three
# separate bugs on the first drive; see docs/LEARNINGS.md 2026-07-26).
#
# AND: THE ACCOUNT IS NOT A SEPARATE WALL. Step 1 of 4 creates the profile and starts the
# application on one form, behind one "Submit Profile" — so a generic "get past the account gate,
# then begin the application" shape does not fit iCIMS at all.
ICIMS_CREATE_PROFILE_RECIPE = [
    {"step": 0, "state": "icims_email_gate",
     "action": "the job's Apply asks for an email address first; submitting it issues an `eem` "
               "token and lands on Basic Information (URL gains ?from=login&eem=...).",
     "expect": ["icims_create_account"]},
    {"step": 1, "state": "icims_create_account",
     "action": "Basic Information (step 1 of 4) — fill First Name, Last Name, Email, Login "
               "(same address as Email), Password and Password (Re-enter), then Submit Profile. "
               "The resume upload and the social-SSO buttons are ALTERNATIVE profile PREFILLS, not "
               "required fields — none of them is starred. hCaptcha sits on this form: check it "
               "before submitting and escalate if it is live. NEVER auto-solve.",
     "fields": "apply_fields.ICIMS_FIELDS (addressed by role+name — a selector cannot reach into "
               "#icims_content_iframe)",
     "password_rules": "min 8 chars, ≥1 alphabetic, ≥1 lower, ≥1 upper, ≥1 numeric, ≥1 special",
     "submit": {"role": "button", "name": "Submit Profile"},
     "expect": ["icims_candidate_profile", "icims_create_account", "icims_verify_email"]},
]

#: The application's own spine. DRIVEN END TO END 2026-07-26/27 (Joslin, Healthcare Data Analyst):
#: account created, five steps completed, confirmation observed.
#:
#: THE STEPPER GROWS ONCE YOU ARE AUTHENTICATED — 4 steps before, 5 after: a "Job Specific
#: Questions" step appears between EEO and Portal Specific Forms. Same tell as Workday's 7→6, in
#: the opposite direction, and for the same reason: the stepper describes the work REMAINING for
#: whoever is asking. Never treat the pre-auth count as the shape of the application.
ICIMS_APPLY_RECIPE = [
    {"step": 1, "state": "icims_create_account",
     "action": "Basic Information — see ICIMS_CREATE_PROFILE_RECIPE (account + application, one "
               "form, one 'Submit Profile')"},
    {"step": 2, "state": "icims_candidate_profile",
     "action": "Candidate Profile — upload the resume (REQUIRED here, unlike step 1; there is an "
               "'Upload Resume at a later time' checkbox if none is held), then phone, address, "
               "and 'How did you hear about us?' (pick the source we actually came from).",
     "fields": "apply_fields.ICIMS_FIELDS (profile_* entries)",
     "gotchas": [
         "TWO comboboxes are named 'Type' — phone and address. Exact-name matching takes the "
         "first (phone) every time, so the ADDRESS one needs a fresh backend_node_id from a "
         "scan taken immediately before the act.",
         "Country and State are searchable custom widgets: their option lists exist only while "
         "open, render a windowed subset (25 of 50 states), and filter on REAL keystrokes. "
         "Open → type the value → click the option by accessible name. State stays empty until "
         "Country is set.",
     ],
     "expect": ["icims_eeo"]},
    {"step": 3, "state": "icims_eeo",
     "action": "Gender/Race/Veteran are starred BUT an 'I do not wish to self-identify' checkbox "
               "satisfies all three — tick it and submit, leaving the three unselected. Matches "
               "the operator's standing 'decline demographics/EEO' preference.",
     "expect": ["icims_job_specific_questions"]},
    {"step": 4, "state": "icims_job_specific_questions",
     "action": "Employer-authored free-text questions — the SET VARIES BY REQUISITION, so read "
               "them. At Joslin: salary expectations, commute acceptance, availability date, visa "
               "sponsorship. Sponsorship answers from stored answers; salary/commute/start date "
               "are the operator's call (a comp figure and a commute judgement are not ours to "
               "invent). Answer salary WITHIN the posted range from the observed job record.",
     "expect": ["icims_portal_forms_cc305"]},
    {"step": 5, "state": "icims_portal_forms_cc305",
     "action": "Portal Specific Forms, served in TWO sub-forms (1/2 then 2/2) as their own "
               "?form=OFCCP pages: CC-305 disability self-identification, then VEVRAA protected "
               "veteran. Each has three radios with EMPTY accessible names (address them by node "
               "id from a fresh scan; document order is Yes / No / decline) and a Signature "
               "checkbox that the form states is equivalent to a handwritten signature — ASK "
               "before ticking it, every time.",
     "gotchas": [
         "A TRUSTED COORDINATE CLICK DOES NOT SET THESE RADIOS; the native node click "
         "(driver='direct') does. They sit under a highlight overlay that takes the hit. This is "
         "the one place we knowingly step off the humanized default — and it is worth a probe "
         "before assuming the same of any other control on the page.",
     ],
     "expect": ["icims_application_confirmed"]},
    {"step": 6, "state": "icims_application_confirmed",
     "action": "TERMINAL. 'Your application was submitted successfully... You are currently being "
               "considered for this job.' above the job overview. Nothing short of this text "
               "justifies the submitted flag."},
]

ICIMS_ACCOUNT_LOOP = {
    "needs_creation": {"state": "icims_create_account", "recipe": "ICIMS_CREATE_PROFILE_RECIPE",
                       "button": "Submit Profile"},
    "created": {"state": "icims_login", "recipe": None,
                "button": "Log back in!",
                "note": "the returning-candidate link sits in the FRAME's header on every page of "
                        "the flow — its presence is NOT evidence of an account wall (it read as "
                        "one to a strict-precedence classifier)"},
    "then": "continue the same form's flow: Candidate Profile → EEO → Portal Specific Forms",
    "runs_as": "the apply ladder's `account` rung, automated by default (mode=auto); a captcha or "
               "an email/2FA prompt escalates",
}




# --- SAP SUCCESSFACTORS (Teradyne et al.) ----------------------------------------------------------
# First encountered 2026-07-27 on jobs.teradyne.com, reached from Indeed's "Apply on company site".
# Room made for it at the operator's request after it stopped a drive dead.
#
# ==================================================================================================
# THE BLOCKER, FIRST, BECAUSE IT STOPS EVERYTHING: SAP THROWS A JAVASCRIPT alert().
# ==================================================================================================
# On the job page, jobs.<tenant>.com raises:
#
#     jobs.teradyne.com says
#     Join our talent community, receive job alerts, and start the apply process.   [ OK ]
#
# It is a plain `alert()` — page-owned, one button. But it BLOCKS THAT TAB'S RENDERER, and our whole
# observation stack reads the page: no DOM node, no AX node, and `Page.captureScreenshot` hangs
# along with everything else. So the drive goes blind in the most misleading way possible —
# `/execute` re-resolves its target, dispatches, returns `ok`, and nothing moves.
#
# MEASURED SIGNATURE (2026-07-27): the blocked tab answered NO CDP command while the Indeed tab
# beside it returned 8731 characters in the same second. "This tab stopped talking and its sibling
# did not" is the tell, and `/native_dialog` reports it.
#
# YOU CANNOT DISMISS IT AFTER THE FACT. Chrome hands a dialog to a CDP client only if that client
# had `Page.enable` ACTIVE WHEN THE DIALOG OPENED. Connect afterwards — as every per-request probe
# does — and `Page.handleJavaScriptDialog` answers "No dialog is showing" about a dialog that is
# plainly on screen; even `Page.enable` times out, queued behind the block. All three recovery
# strategies in `/dismiss_dialog` were tried live and all three failed.
#
# SO THE ANSWER IS PREVENTION: start `/dialog_guard` on the tab BEFORE driving SAP. It holds a
# Page-enabled socket open, answers `Page.javascriptDialogOpening` the instant it fires, and records
# the message. Verified end to end on a live alert: dismissed_count 1, message captured verbatim,
# renderer never blocked.
#
# THE GENERAL LESSON: a dialog is the one blocker that makes the page unreadable INSTEAD of wrong,
# and it must be prevented rather than detected — by the time you can see the problem, you have
# already lost the ability to act on it.
SUCCESSFACTORS_LESSONS = {
    "alert_blocks_the_tab": "The job page raises a JS alert() — 'Join our talent community, "
        "receive job alerts, and start the apply process.' It blocks the tab's renderer, so every "
        "probe hangs and a click returns ok while nothing moves. It CANNOT be dismissed after the "
        "fact (CDP only owns dialogs that open while Page.enable is already active). START "
        "/dialog_guard ON THE TAB BEFORE DRIVING. Not a notification prompt — the profile already "
        "blocks those, and that guess cost an hour.",
    "branded_by_default": "The career site runs on the EMPLOYER's domain (jobs.teradyne.com) with no "
        "SAP string in the url. Recognised by path shape — /<Tenant>/job/<Location>-<Title>-<id>/ "
        "and /<Tenant>/search/ (ats_registry._SUCCESSFACTORS_PATH_TELLS).",
    "apply_is_a_staged_menu": "'Apply now' does NOT navigate — it opens a small menu offering "
        "'Apply Now' and 'Start applying with LinkedIn'. The button STAGES; the menu item acts. A "
        "click on the button alone reports ok and goes nowhere, which reads exactly like the "
        "notification blocker and is a different fault.",
    "cookie_banner": "Two buttons only: 'Accept Use Of Cookies' and 'Close Cookies Notice'. There is "
        "no decline — Close dismisses without consenting, so Close is the privacy-preserving choice.",
    "linkedin_path_is_a_detour": "'Start applying with LinkedIn' hands off to LinkedIn auth — a "
        "different flow with its own account wall. Prefer the plain 'Apply Now'.",
    "the_account_is_on_a_different_host": "The job page is on the EMPLOYER's domain "
        "(jobs.teradyne.com); the account and the application are on career<N>.sapsf.com. Two hosts, "
        "one application. So an account's login_url must come from the LIVE TAB at the account rung, "
        "never from orient.url or a blackboard apply_tab.url — both of those still said "
        "jobs.teradyne.com while the tab was already on sapsf.com (2026-07-28), and a wrong "
        "login_url fails nothing now and opens a job ad at a sign-in weeks later.",
    "password_rules": "STATED ON THE FORM and tighter than most: 8-18 chars, >=1 upper, >=1 lower, "
        ">=1 number or punctuation, no space or unicode. Encoded in "
        "apply_fields.PASSWORD_POLICIES['successfactors'] and checked BEFORE typing. This bites the "
        "derived credential specifically: the password is company INITIALS + a shared suffix, so a "
        "one-word employer ('Teradyne' -> 'T') lands at suffix+1 characters — exactly 8, on SAP's "
        "floor. A rejected password is not a free retry; it costs a submit and leaves a half-made "
        "account that looks exactly like a made one.",
    "consent_is_a_button_and_its_name_drifts": "The required data-privacy acceptance renders as a "
        "BUTTON, not a checkbox. Its live accessible name carries the required-marker text — "
        "'Terms of Use Read and accept the data privacy statement. Required' — while the field "
        "table stores it without the trailing ' Required'. It resolves because _resolve_ax_node "
        "falls back from exact match to substring; that fallback is load-bearing here, not "
        "decorative.",
    "the_apply_form_is_collapsed_sections": "The profile is not one flat form — it is a stack of "
        "collapsible SECTION BARS, and a section's fields are unreachable until its bar is open "
        "(operator, 2026-07-30; MEASURED live the same day on career41.sapsf.com/portalcareer). "
        "Nine bars: My Documents, Profile Information, Search Options and Privacy, Jobs Applied "
        "(N), Saved Applications, Employment History, Formal Education, Language Skills, "
        "Geographic Mobility. So every field has a PRECONDITION no field-level recipe can "
        "express: open the bar that owns it. CORRECTED — the predicted failure was 'resolves a "
        "node that is in the DOM and cannot be typed into'; it is not. A collapsed section's "
        "fields are ABSENT FROM THE AX TREE ENTIRELY (collapsed scan: 25 candidates, zero "
        "textboxes; after opening one bar: 41, with all thirteen), so the real signature is "
        "/execute returning NOT_FOUND — indistinguishable from a stale recipe, and the reason to "
        "check openness before believing the recipe rotted. This is the widget-protocol shape one "
        "level up: the container is the widget, not the input. A scan of a closed form "
        "under-reports required fields and makes the page look simpler than it is.",
    "the_section_bars_need_no_selector_treatment": "The open question after the consent link — "
        "whether these bars would need the same #dataPrivacyId escape hatch — is answered NO, "
        "measured 2026-07-30. Each bar is a real <button class='rcmFormSectionTopBar'> whose "
        "accessible name IS its visible label, and each carries aria-expanded, so open/closed is "
        "READ rather than inferred from geometry or chevron pixels. Clicking one flipped "
        "aria-expanded false->true and pushed the bars below it ~514px down (both signals, per "
        "the scroll lesson — the check is about the world, not about our call returning). "
        "'Expand all sections' / 'Collapse all sections' sit above them as <a role=button>. TWO "
        "TRAPS REMAIN, though, and neither is the one we were watching for: (1) 'Jobs Applied "
        "(2)' carries a live COUNT in its accessible name, so it must be matched by PREFIX — the "
        "same shape as LinkedIn's `button \"Location Greater Boston\"`, a control whose name "
        "carries its value; (2) the bars expose NO aria-controls, so a bar cannot name its own "
        "content region and scoping a scan to one section is still unsolved. The element ids "
        "(`142:topBar`, `30:_expandAllSections`) are framework counters that shift with the "
        "component tree — they are not identity, the name is.",
    "required_markers_bleed_into_the_accessible_name": "On the candidate profile every REQUIRED "
        "label wraps its asterisk in <span class='requiredField' aria-hidden='true'>. aria-hidden "
        "removes the '*' from the accessible name but NOT the whitespace around it, so AX reports "
        "'\\xa0First Name' while optional 'Middle Name' is clean. The character is a NON-BREAKING "
        "SPACE (U+00A0), not an ASCII space — which matters, because it survives only where the "
        "normaliser treats \\xa0 as whitespace. Python's str.strip() does, so _resolve_ax_node is "
        "fine (main_server.py:190, verified rather than assumed); a matcher trimming an explicit "
        "' \\t\\n' set, or an ASCII-only \\s regex, would miss EVERY required field on this form "
        "while still finding the optional ones — a half-failure that looks like a partial page. "
        "DO NOT read the space as a required-detector either: "
        "Country and State / Province are required and have no leading space, because they take "
        "their name from aria-label instead of the <label>. aria-required is the honest signal. "
        "Generalisable: an aria-hidden marker inside a label changes the accessible name without "
        "changing anything a human can see, so a name copied off the screen will not match.",
    "country_and_state_answer_to_two_controls_at_once": "On the profile, Country and State / "
        "Province EACH appear twice under one accessible name: the input[role=combobox] that "
        "holds the value (aria-label'd, editable, .value read 'United States' / 'New Hampshire' "
        "live) and a sibling <button id='81:_selectButton'> that opens the picker. "
        "_resolve_ax_node takes exact[0] in DOCUMENT ORDER when no role is given — a coin flip "
        "between typing the value and opening a dropdown. Role gating is what disambiguates it, "
        "so the role is load-bearing on these two entries and must never be dropped. The shape is "
        "neither a native select nor a react-select; apply_fields leaves it UNKNOWN so it routes "
        "to /describe_widget rather than being dispatched as something it is not.",
    "the_policy_gate_comes_back_after_sign_in": "A successful sign-in does NOT land on the career "
        "site — SAP raises the Data Privacy Consent dialog again, unprompted, over the sign-in "
        "page (state successfactors_policy_gate, 2026-07-29). Same dialog as the signup's, same "
        "Accept/Decline/Print, but no opener precedes it, so a recipe waiting for a click it must "
        "make first will wait forever. LEAVING IT COSTS THE SESSION, not the rung: observed with "
        "the dialog gone unaccepted and the tab back at the sign-in wall, logged_in false, "
        "loginFlowRequired=true. Clear it in the same breath as the sign-in submit "
        "(_ACCOUNT_FORMS 'interstitials'). It is CONDITIONAL — a session SAP does not re-ask must "
        "not be treated as a failure.",
    "an_existing_account_makes_create_look_broken": "Submitting Create Account for an email that "
        "already has an account bounces to the sign-in page — no error naming the cause. Every "
        "symptom points at the last thing you did instead: on 2026-07-28 the whole create form "
        "filled cleanly, the consent click was followed by the sign-in gate, and the drive read it "
        "as a broken consent widget. It was a registered email. Before diagnosing a create flow "
        "that keeps returning to sign-in, TRY THE CREDENTIALS.",
    "create_form_is_its_own_state": "The gate and the signup form are two states, not one, and they "
        "are confusable: both are SAP-chrome 'Career Opportunities' pages with an email and a "
        "password box. The form's tells are the DOUBLED fields (Retype Email Address, Retype "
        "Password), the First/Last Name pair, the Country/Region dropdown, and the password-rules "
        "callout. The gate has none of those and offers a 'Please sign in' link instead.",
}

SUCCESSFACTORS_APPLY_RECIPE = [
    {"step": 0, "state": "successfactors_job_posting",
     "action": "FIRST: POST /dialog_guard {action:'start'} on this tab — SAP raises a blocking "
               "alert() here and it cannot be cleared once open. Then dismiss the cookie notice "
               "with 'Close Cookies Notice' (never Accept), and click 'Apply now' to OPEN THE MENU. "
               "If a click reports ok and the page does not move, run /native_dialog: an "
               "unresponsive renderer beside a healthy sibling tab means a dialog has it.",
     "controls": {"cookie_close": {"role": "button", "name": "Close Cookies Notice"},
                  "apply_menu": {"role": "button", "name": "Apply now"}},
     "expect": ["successfactors_apply_menu"]},
    {"step": 1, "state": "successfactors_apply_menu",
     "action": "The menu is open. Click the 'Apply Now' ITEM (a link, not the button that opened "
               "the menu). 'Start applying with LinkedIn' is a detour into LinkedIn auth.",
     "controls": {"apply_now": {"role": "link", "name": "Apply now"},
                  "apply_linkedin": {"role": "link", "name": "Apply with LinkedIn"}},
     "expect": ["successfactors_account_gate", "successfactors_apply_form"]},
    {"step": 2, "state": "successfactors_account_gate",
     "action": "The sign-in wall, on career<N>.sapsf.com — a DIFFERENT HOST from the job page. "
               "Email Address / Password, plus a create-an-account path. With an active account, "
               "the `account` rung's sign_in leg drives it (apply_fields signin_email / "
               "signin_password / sign_in_submit). Without one, take the create path to step 3.",
     "controls": {"sign_in": {"role": "button", "name": "Sign In"}},
     "expect": ["successfactors_create_account", "successfactors_apply_form"]},
    {"step": 3, "state": "successfactors_create_account",
     "action": "The signup form. Driven by the `account` rung's create_account leg, which resolves "
               "every field from apply_fields.SUCCESSFACTORS_FIELDS — both email boxes, both "
               "password boxes, first/last name, the Country/Region dropdown (required before the "
               "form will take anything), and the data-privacy consent, which is a BUTTON, not a "
               "checkbox. The two marketing opt-ins are named in the field table for one reason: so "
               "they are refused BY NAME rather than skipped by luck. Check the password against "
               "apply_fields.check_password FIRST — see the password_rules lesson.",
     "controls": {"submit": {"role": "button", "name": "Create Account"}},
     "expect": ["successfactors_apply_form", "successfactors_account_verify"]},
    {"step": 4, "state": "successfactors_policy_gate",
     "action": "INTERSTITIAL, not a step you navigate to — it arrives on its own the moment a "
               "sign-in lands, over the sign-in page, with no opener. Click Accept immediately. "
               "Leaving it costs the SESSION: the dialog goes, the tab returns to the sign-in wall "
               "and logged_in reads false. Conditional — a session it does not interrupt is normal.",
     "controls": {"accept": {"role": "button", "name": "Accept"}},
     "expect": ["successfactors_apply_form", "successfactors_account_gate"]},
    {"step": 5, "state": "successfactors_apply_form",
     "action": "UNDRIVEN as a drive, but its SHAPE is known: a stack of collapsible section bars "
               "(see the_apply_form_is_collapsed_sections). Open a bar, scan what it contains, "
               "fill it, then move to the next — a field is not addressable while its section is "
               "shut, and a scan of the closed form under-reports what the application asks for. "
               "Map the bars first; do not transcribe fields off a closed page.",
     "expect": ["successfactors_submitted"]},
]

SUCCESSFACTORS_ACCOUNT_LOOP = {
    # Driven live to the form on Teradyne 2026-07-28: every field resolves, the country dropdown
    # takes "United States", the consent resolves despite the AX-name drift below. What has NOT
    # been walked is the far side of the Create Account click — whether SAP mails a verification
    # code, and what state it lands on. Left as an `expect` on step 3, not asserted here: a leg we
    # have not walked reads as unknown, not as covered.
    "needs_creation": {"state": "successfactors_create_account",
                       "recipe": "apply_fields.SUCCESSFACTORS_FIELDS (create_account leg)",
                       "button": "Create Account"},
    "created": {"state": "successfactors_account_gate",
                "recipe": "apply_fields.SUCCESSFACTORS_FIELDS (sign_in leg)",
                "button": "Sign In",
                "note": "MAPPED FROM THE GATE, NOT YET DRIVEN — the returning-candidate leg has "
                        "never run."},
    "then": "hand to SUCCESSFACTORS_APPLY_RECIPE step 4",
    "runs_as": "the apply ladder's `account` rung, automated by default; captcha / email code escalate",
}

# --- GREENHOUSE (KKR et al.) -----------------------------------------------------------------------
# The second ATS we drive, and the FIRST with NO ACCOUNT WALL — a Greenhouse application is one
# embedded form, submitted anonymously. Mapped live 2026-07-15 on KKR (Analyst - Actuarial Financial
# Reporting). Everything here is keyed to greenhouse_* page states so it generalizes across EVERY
# employer on Greenhouse — never key a recipe to the employer.
#
# REACHED TWO WAYS (ats_registry.classify_ats handles both):
#   * DIRECT   — job-boards.greenhouse.io / boards.greenhouse.io (host match).
#   * WRAPPER  — the employer's own domain with the form in a cross-origin IFRAME, e.g.
#                www.kkr.com/careers/career-opportunities/post?gh_jid=<id>. The host says
#                "company_site"; the `gh_jid` QUERY PARAM is the tell. NEVER grow a per-employer path.
#
# THE IFRAME IS ITS OWN CDP TARGET (type=iframe, has a webSocketDebuggerUrl). The wrapper page's
# Runtime CANNOT see inside it — address it by tab_id (or a tab_url specific enough to match exactly
# one target; "job-boards.greenhouse.io" alone matched a stray googleapis proxy iframe too).
GREENHOUSE_APPLY_RECIPE = [
    {"step": 0, "state": "greenhouse_apply_form",
     "action": "Fill the embedded job_app form and submit. NO login, no account — this is the whole "
               "application. Fields carry clean semantic ids; a per-employer CUSTOM QUESTIONS block "
               "is appended below the standard fields and varies by employer — read it, never assume.",
     "fields": {
         "first_name": {"selector": "#first_name"},
         "last_name": {"selector": "#last_name"},
         "email": {"selector": "#email"},
         "phone": {"selector": "#phone"},
         "country": {"selector": "#country", "note": "autocomplete combobox"},
         "location_city": {"selector": "#candidate-location", "note": "autocomplete — pick the suggestion"},
         "resume": {"selector": "#resume", "action": "upload", "note": "input[type=file]; setFileInputFiles"},
         "cover_letter": {"selector": "#cover_letter", "action": "upload", "optional": True},
         "company_name": {"selector": "#company-name-0", "note": "current/most-recent employer"},
         "title": {"selector": "#title-0"},
         "start_date": {"selector": ["#start-date-month-0", "#start-date-year-0"], "note": "plain text MM/YYYY inputs — NOT a Workday segmented spinbutton; typing works"},
         "end_date": {"selector": ["#end-date-month-0", "#end-date-year-0"]},
     },
     "submit": {"role": "button", "name": "APPLY"},
     "captcha": "reCAPTCHA Enterprise lives in the IFRAME's frame tree — invisible/score-based, not "
                "blocking on load. /challenge_visibility run against the PAGE reports anchor_count:0 "
                "because of that; check the iframe target. Humanize input to keep the score healthy. "
                "If a challenge appears -> greenhouse_captcha -> ESCALATE, never auto-solve.",
     "expect": ["greenhouse_apply_submitted", "greenhouse_apply_error", "greenhouse_captcha"]},
]

GREENHOUSE_ACCOUNT_LOOP = {
    "needs_creation": None,
    "created": None,
    "why": "Greenhouse embedded applications require NO account (needs_account:false observed on KKR). "
           "There is no create/sign-in leg — do NOT invent one. 'Quick Apply with MyGreenhouse' is an "
           "OPTIONAL convenience login; the anonymous form is the path we drive.",
}

GREENHOUSE_LESSONS = {
    "wrapper_detection": "gh_jid / gh_src query param => greenhouse, even on the employer's host.",
    "iframe": "The form is a cross-origin OOPIF: address it as its own CDP target by tab_id.",
    "no_account": "No wall — the whole application is one anonymous form.",
    "cookie_banner": "The WRAPPER (not Greenhouse) may show a consent banner — KKR uses OneTrust "
                     "(#onetrust-banner-sdk). Decline non-essential: MANAGE PREFERENCES "
                     "(#onetrust-pc-btn-handler) -> 'Reject All' (.ot-pc-refuse-all-handler). The "
                     "banner itself offers only ACCEPT, so the reject lives one level in.",
    "required_detection": "Do NOT trust the label asterisk or aria-required — both go STALE. KKR's "
                          "work End date keeps '*' and aria-required='true' after 'Current role' is "
                          "ticked, but the input is `disabled` => not required. Check `disabled` first. "
                          "Conversely the conditional 'If yes, provide details' fields keep "
                          "aria-required='true' even when the parent answer is No: they still need "
                          "filling — 'N/A' is the form's own stated convention for not-applicable.",
    "checkbox_groups": "Some REQUIRED questions are checkbox groups, not comboboxes, and a scan that "
                       "only looks at inputs/selects MISSES them entirely (we missed restrictions + "
                       "languages on the first pass). Group them by the id prefix before '[]' "
                       "(question_<id>[]_<optid>) and treat 0-checked as unanswered. Match option "
                       "labels EXACTLY — 'No' must not match 'Yes, non-compete'.",
    "custom_questions": "Employer-specific questions are appended per posting — they are the part that "
                        "does NOT generalize. Read them live; the rest of the form does generalize. "
                        "They render as #question_<id> (comboboxes carry aria-expanded; each has a "
                        "hidden required twin, so a duplicate empty-id field is NOT a second question).",
    "education_may_be_required": "KKR requires School*/Degree*/Discipline* + dates (#school--0, "
                                 "#degree--0, #discipline--0, #start-month--0, #start-year--0, ...). "
                                 "Wellington's Workday allowed blank; Greenhouse employers often don't. "
                                 "Answers live in the answer store (education_*) — never invent credentials.",
    "ai_use_attestation": "KKR asks a REQUIRED 'my materials were not generated/edited/supplemented by "
                          "AI tools (ChatGPT, Gemini, Claude...)' confirmation, rendered as a Yes/No "
                          "react-select where the QUESTION carries the confirmation wording (so Yes = "
                          "confirming). Wording varies per employer and can INVERT, so detect it with "
                          "is_ai_use_attestation(question_text) rather than matching a fixed string, "
                          "then answer from the answer-store key `ai_use_attestation`. Read the "
                          "question's polarity before filling — never blind-fill the stored value.",
    "react_select_widgets": "Country / Location / every Yes-No custom question is a REACT-SELECT "
                            "combobox. It fetches + opens ONLY on real per-char keystrokes: a "
                            "react-safe value-set or insertText leaves aria-expanded=false and no "
                            "listbox (same lesson as Workday's prompt searchBox). Use driver="
                            "'humanized' to type, THEN click the option. Two gotchas: (1) aria-controls "
                            "is ABSENT until it expands, so resolve the popup AFTER typing; (2) after "
                            "picking, the input's .value goes EMPTY — the choice renders in a sibling "
                            "[class*=singleValue], so verify there, not on .value.",
    "option_matching": "Match options EXACTLY. /Concord/ picked 'Concordia, Entre Rios, Argentina' "
                       "over 'Concord, New Hampshire' — the same substring pitfall as Workday's "
                       "'State' matching 'United States'. Anchor it (^Concord,\\s*New Hampshire).",
    "date_inputs": "MONTH and YEAR are DIFFERENT widgets, and the month is NOT a text input: month is a "
                   "react-select combobox wanting the NAME ('Aug' -> 'August'; typing '08' yields NO "
                   "options), year is a plain number input that accepts typing. No calendar picker "
                   "(unlike Workday's segmented spinbuttons). CAUTION: typing into the month input "
                   "leaves transient text that READS BACK like a value and then clears on blur — a "
                   "verify against .value reports a false success. Verify the month at its sibling "
                   "[class*=singleValue]; verify the year at .value.",
    "phone_country_field": "#country is the PHONE country code (renders '+1'), NOT the address "
                           "country — the address lives in #candidate-location.",
    "stale_state": "Greenhouse is ANONYMOUS — no session, no account. So unlike Workday (where any "
                   "refresh DROPS the session and costs you the whole fill), a stale Greenhouse form is "
                   "cheap: just reload the iframe and re-fill. Refresh freely here; never on Workday.",
}

# --- Cross-ATS state readout: describe_tab, one altitude up (the controller Bundle) --------------
# The controller's Bundle needs the SAME "where are we" readout for every Career Search ATS, not
# just Indeed. Indeed reads state from the URL (map_url_to_state); Workday and Greenhouse are
# single-origin SPAs whose step is in the PAGE, not the URL, so they are classified from page_text
# markers here. These are SEED heuristics — the cheap deterministic tool before L3 (the page-state
# model) graduates — and they fail HONESTLY: no marker => state "unknown", which makes the bundle
# escalate rather than guess. Site knowledge lives HERE with the recipe, never in the bundle builder.

# page_text substring (lowercased) -> state, checked in order (most specific first). Ordering
# matters: "create account"/"sign in" are checked before the generic apply steps, and the weak
# "review" marker is phrased specifically so it doesn't fire on an incidental "review" elsewhere.
_WORKDAY_STATE_MARKERS: list[tuple[str, str]] = [
    # The apply-METHOD chooser — the "Start Your Application" modal that Apply opens. Added
    # 2026-07-24 after driving it live: the click worked, the modal opened, but state detection was
    # URL-only and a modal does not change the URL, so verification called a perfectly good landing
    # "unexpected". These three lines are the content signal the URL could never carry. Placed
    # first because the modal overlays the job posting and its buttons are the most specific thing
    # on the page at that moment.
    ("use my last application", "workday_apply_method"),
    ("autofill with resume", "workday_apply_method"),
    ("start your application", "workday_apply_method"),
    ("verify new password", "workday_create_account"),
    ("create account", "workday_create_account"),
    ("my information", "workday_my_information"),
    ("my experience", "workday_my_experience"),
    ("voluntary disclosures", "workday_voluntary_disclosures"),
    ("self identify", "workday_voluntary_disclosures"),
    ("application questions", "workday_questions"),
    ("review and submit", "workday_review"),
    ("please review your application", "workday_review"),
    ("password", "workday_sign_in"),   # a password field with none of the above => the login wall
]

# The Workday flow in order, from arrival to submitted — the "how far am I" spine. Operator, live
# 2026-07-24: a third-party apply is not always one click from the form. Sometimes Indeed lands on
# a company careers page, THEN Apply, THEN the tenant Workday app, THEN the real application. So
# the flow has a PRE-WORKDAY approach (we can be on the company site before we are in Workday at
# all) and then the Workday-internal steps. `workday_progress` locates where we are and how many
# steps remain, so a proposal can say "you are 4 steps from Submit" instead of guessing depth.
WORKDAY_FLOW_ORDER: list[str] = [
    "company_careers_landing",   # pre-Workday: a branded careers page, not yet myworkdayjobs
    "workday_job_posting",       # the posting, Apply not yet clicked
    "workday_apply_method",      # the Start-Your-Application modal (choose how to apply)
    "workday_apply_auth",        # sign in / create account gate
    "workday_create_account",    # the account-creation leg (operator-owned)
    "workday_sign_in",           # the sign-in leg
    "workday_my_information",    # the form begins
    "workday_my_experience",
    "workday_questions",
    "workday_voluntary_disclosures",
    "workday_review",            # review & submit — the consequential gate
    "workday_submitted",         # done
]


# Indeed's flow order is DERIVED from the recipe rather than restated. `INDEED_APPLY_RECIPE` is
# already an ordered spine — a second hand-written copy is a second thing to keep in sync, and the
# one that drifts is always the copy nothing executes.
INDEED_FLOW_ORDER: list[str] = [str(s["state"]) for s in INDEED_APPLY_RECIPE]

#: One platform, several names. `classify_landing` answers `indeed` where `_TERMINAL_STATES` says
#: `indeed_quick_apply`, and callers pass through whichever the live page resolved to. Resolved
#: ONCE, here, rather than by adding the alias to each table — the table that gets forgotten is
#: always the third one, and the symptom is a submitted application that does not read as done.
_PLATFORM_ALIASES: dict[str, str] = {"indeed": "indeed_quick_apply"}


def _canon(platform: Optional[str]) -> str:
    p = platform or ""
    return _PLATFORM_ALIASES.get(p, p)


#: Flow order per platform — the "how far am I" spine, canonical names only.
_FLOW_ORDERS: dict[str, list[str]] = {
    "indeed_quick_apply": INDEED_FLOW_ORDER,
    "workday": WORKDAY_FLOW_ORDER,
}

#: The state where the next action is the IRREVERSIBLE one. `steps_to_submit` counts TO here and
#: never past it: the gate is the furthest the drive travels on its own, and the step beyond it
#: belongs to the operator on every platform, always.
_GATE_STATES: dict[str, str] = {
    "indeed_quick_apply": "indeed_apply_review",
    "workday": "workday_review",
}


def flow_progress(state: Optional[str], *, platform: str = "workday") -> dict[str, Any]:
    """Where `state` sits in this platform's flow, and how far from Submit — the depth awareness
    the operator asked for, for every platform rather than only Workday.

    `steps_to_submit` counts to the review gate (the last thing before the irreversible Submit).
    An unrecognised state is reported as such rather than defaulted to a position: "we do not know
    where we are" is a real answer and the one the tail must not paper over.

    **The count is an UPPER BOUND, not an estimate.** Indeed skips steps whose answers the profile
    already holds (the recipe says so at the top of this module), and skipping can only SHORTEN the
    path — never lengthen it. So "at most N screens from Submit" is a fact about the spine, where
    "N screens from Submit" would be a guess about this particular application.
    """
    canon = _canon(platform)
    order = _FLOW_ORDERS.get(canon)
    gate = _GATE_STATES.get(canon)
    if not order or not gate:
        return {"state": state, "position": None, "total": 0, "steps_to_submit": None,
                "recognised": False, "platform": canon}
    try:
        i = order.index(state or "")
    except ValueError:
        return {"state": state, "position": None, "total": len(order),
                "steps_to_submit": None, "recognised": False, "platform": canon}
    gate_i = order.index(gate)
    return {"state": state, "position": i, "total": len(order), "platform": canon,
            "steps_to_submit": max(0, gate_i - i), "recognised": True,
            "at_review_gate": state == gate,
            "done": state in _TERMINAL_STATES.get(canon, frozenset())}


def flow_order(platform: Optional[str]) -> list[str]:
    """This platform's screens in order, or []. The spine, for anything that wants to RENDER it
    rather than count along it."""
    return list(_FLOW_ORDERS.get(_canon(platform), []))


def gate_state(platform: Optional[str]) -> str:
    """The screen whose next action is the irreversible one, or ""."""
    return _GATE_STATES.get(_canon(platform), "")


def workday_progress(state: Optional[str]) -> dict[str, Any]:
    """Workday's flow depth — `flow_progress` with the platform already answered. Kept as its own
    name because callers and tests read better for it, and because Workday is the flow whose depth
    problem motivated the whole idea."""
    return flow_progress(state, platform="workday")


#: The controls that SEND an application, most specific first. Deliberately a separate lexicon from
#: `controller.decide._ADVANCE_CONTROLS`, which excludes every one of these on purpose: a control
#: that advances a form may be reached for by a lexicon match, and the one that sends an
#: application may not. Keeping them in one list is how "Submit" ends up one substring match away
#: from being pressed by a guess.
SUBMIT_CONTROLS: tuple[str, ...] = (
    "Submit your application",
    "Submit application",
    "Submit",
)


def submit_control(ax_identities) -> str:
    """The control that SENDS this application, as the page renders it — or "".

    Same match rules as `decide.advance_control` (render-label, longest wins) against a lexicon
    that only the operator's own gate is ever allowed to consult.
    """
    names = [i.partition("|")[2].strip() if "|" in i else str(i).strip()
             for i in (ax_identities or ())]
    names = [n for n in names if n]
    for control in SUBMIT_CONTROLS:
        matches = [n for n in names if control.lower() in n.lower()]
        if matches:
            return max(matches, key=len)
    return ""


def named_control(platform: str, state: Optional[str], ax_identities) -> str:
    """The control THIS RECIPE names for this screen, matched against the page — or "".

    THE RECIPE OUTRANKS THE GENERIC LEXICON on a screen it knows by name. `advance_control`'s
    lexicon is a fallback for pages nobody has driven; where we have actually stood on a screen and
    read its buttons, that observation is better than a guess and should not have to be re-derived
    by substring every time.

    Measured live 2026-08-06 on Indeed's highlights screen: the advance is **"Review details"**,
    which the generic lexicon cannot reach — its nearest entry is "Review your application", and
    the only thing it *could* match was "Save and close", the exit. A recipe that has seen the
    screen should simply say so.

    Still matched AS RENDERED rather than returned verbatim, and still refuses when the named
    control is absent: a recipe naming a button the page does not have is a stale recipe, and the
    honest answer there is "" — not a click on whatever else happened to be lying around.
    """
    if _canon(platform) != "indeed_quick_apply":
        return ""
    wanted = ""
    for entry in INDEED_APPLY_RECIPE:
        if entry.get("state") == state:
            wanted = str(entry.get("control") or "")
            break
    if not wanted:
        return ""
    names = [i.partition("|")[2].strip() if "|" in i else str(i).strip()
             for i in (ax_identities or ())]
    matches = [n for n in names if n and wanted.lower() in n.lower()]
    return max(matches, key=len) if matches else ""


def advance_action(platform: str, state: Optional[str]) -> str:
    """The action THIS RECIPE names for `state` — "Continue", "autofill + Continue", "Submit …".

    Returned verbatim so the router never restates it. The recipe is the authority on what advances
    a screen; a second description of the same move in the executor is how the two come to disagree.
    """
    if _canon(platform) != "indeed_quick_apply":
        return ""
    for entry in INDEED_APPLY_RECIPE:
        if entry.get("state") == state:
            return str(entry.get("action") or "")
    return ""


def expected_after(platform: str, state: Optional[str]) -> tuple[str, ...]:
    """The states this recipe says `state` may lead to — the orienter's prediction, and the thing
    a practice score is scored AGAINST. Empty when the recipe has nothing to say."""
    if _canon(platform) != "indeed_quick_apply":
        return ()
    for entry in INDEED_APPLY_RECIPE:
        if entry.get("state") == state:
            return tuple(entry.get("expect") or ())
    return ()


_GREENHOUSE_STATE_MARKERS: list[tuple[str, str]] = [
    ("thank you for applying", "greenhouse_apply_submitted"),
    ("application submitted", "greenhouse_apply_submitted"),
    ("your application has been submitted", "greenhouse_apply_submitted"),
]

# The credential boundary as STATE: at any of these the agent stops — it never types a password or
# creates an account (WORKDAY_ACCOUNT_LOOP runs_as the operator). Marking them human_required here
# is what makes the bundle refuse to drive them, per PRINCIPLES and the ATS-accounts boundary.
_CREDENTIAL_STATES = frozenset({"workday_sign_in", "workday_create_account",
                                "appvault_login", "appvault_create_account"})

# Anti-bot / challenge markers — classify -> escalate, NEVER auto-solve (same rule everywhere).
_CHALLENGE_MARKERS = ("verify you are human", "i'm not a robot", "recaptcha",
                      "complete the captcha", "select all images")

# Terminal states per ATS — the recipe's own "arrived" set. Used as a done fallback when no
# TaskSpec matches (task_spec.py is the primary source; this covers ATSs without a spec yet).
_TERMINAL_STATES: dict[str, frozenset[str]] = {
    "indeed_quick_apply": frozenset({"indeed_apply_submitted"}),
    "workday": frozenset({"submitted", "workday_submitted"}),
    "greenhouse": frozenset({"greenhouse_apply_submitted"}),
}

_LESSONS_BY_ATS: dict[str, dict[str, Any]] = {
    "workday": WORKDAY_LESSONS,
    "greenhouse": GREENHOUSE_LESSONS,
}


def _classify_from_markers(page_text: str, markers: list[tuple[str, str]]) -> Optional[str]:
    t = (page_text or "").lower()
    if any(m in t for m in _CHALLENGE_MARKERS):
        return "captcha"
    for needle, state in markers:
        if needle in t:
            return state
    return None


def map_workday_state(url: str, page_text: str = "") -> str:
    hit = _classify_from_markers(page_text, _WORKDAY_STATE_MARKERS)
    if hit:
        return hit
    # On a Workday origin with no step marker yet, we're at the job posting; otherwise unknown.
    if "myworkdayjobs" in (url or ""):
        return "workday_job_posting"
    return "unknown"


def map_greenhouse_state(url: str, page_text: str = "") -> str:
    hit = _classify_from_markers(page_text, _GREENHOUSE_STATE_MARKERS)
    if hit:
        return hit
    # The whole Greenhouse application is one form; if we're on it at all, that's the state.
    return "greenhouse_apply_form" if ("greenhouse" in (url or "") or "gh_jid" in (url or "")
                                       or page_text) else "unknown"


def _describe_from_recipe(url: str, state: str, recipe: list[dict], branches: dict) -> dict[str, Any]:
    """Shared 'where are we' builder — the Workday/Greenhouse twin of Indeed's describe_tab."""
    entry = next((s for s in recipe if s["state"] == state), None)
    branch = branches.get(state)
    human = bool(branch and branch.get("human_required")) or state in _CREDENTIAL_STATES
    note = (branch.get("note") if branch else None) or (
        "credential step — the operator signs in / creates the account; the agent never types "
        "a password" if state in _CREDENTIAL_STATES else None)
    return {
        "url": (url or "")[:90],
        "state": state,
        "role": "apply",
        "recipe_step": entry["step"] if entry else None,
        "next_action": entry["action"] if entry else None,
        "expected_next": entry["expect"] if entry else [],
        "is_branch": (branch is not None) or state in _CREDENTIAL_STATES,
        "human_required": human,
        "branch_note": note,
    }


def describe_workday_tab(url: str, page_text: str = "") -> dict[str, Any]:
    return _describe_from_recipe(url, map_workday_state(url, page_text),
                                 WORKDAY_APPLY_RECIPE, WORKDAY_APPLY_BRANCHES)


def describe_greenhouse_tab(url: str, page_text: str = "") -> dict[str, Any]:
    branches = {"captcha": {"human_required": True, "note": GREENHOUSE_APPLY_RECIPE[0]["captcha"]}}
    return _describe_from_recipe(url, map_greenhouse_state(url, page_text),
                                 GREENHOUSE_APPLY_RECIPE, branches)


def lessons_for(ats: str) -> dict[str, Any]:
    """The ATS's LESSONS dict (the teacher's seed knowledge) — {} for Indeed (no LESSONS dict;
    its branch notes carry the equivalent). Serialised into the Bundle's `lessons` field."""
    return _LESSONS_BY_ATS.get(ats, {})


def is_terminal_state(ats: str, state: Optional[str]) -> bool:
    """Whether `state` is a recipe-known 'arrived' state for `ats` — the done fallback when no
    TaskSpec matches. Accepts the ats_registry id or the short group name."""
    if not state:
        return False
    key = "workday" if "workday" in (ats or "") else "greenhouse" if "greenhouse" in (ats or "") \
        else ats
    return state in _TERMINAL_STATES.get(key, frozenset())


def describe_for_ats(ats: Optional[str], url: str, page_text: str = "") -> dict[str, Any]:
    """Dispatch the 'where are we' readout to the recipe that owns this ATS. Career Search only;
    an unrecognised ATS falls back to the Indeed reader (the URL-state path degrades gracefully)."""
    a = ats or ""
    if "workday" in a:
        return describe_workday_tab(url, page_text)
    if "greenhouse" in a:
        return describe_greenhouse_tab(url, page_text)
    return describe_tab(url, page_text)   # indeed_quick_apply + graceful default


def recipe_spec() -> dict[str, Any]:
    import apply_fields
    return {
        "domain": "indeed",
        "recipe": INDEED_APPLY_RECIPE,
        # The EXECUTABLE half of the recipe. The `fields`/`selectors` entries in the step
        # dicts below are documentation — no code reads them (that is what made the recipe
        # inert). `apply_fields.resolve(ats, field)` is what code calls. Add a new field
        # THERE, not as a seventh addressing shape in a step dict.
        "fields": {ats: apply_fields.known_fields(ats) for ats in apply_fields.known_ats()},
        "branches": APPLY_BRANCHES,
        "epilogue": APPLY_EPILOGUE,
        "cross_site": {
            "workday": {"recipe": WORKDAY_APPLY_RECIPE, "branches": WORKDAY_APPLY_BRANCHES,
                        "lessons": WORKDAY_LESSONS,
                        "account_loop": WORKDAY_ACCOUNT_LOOP,
                        "create_account_recipe": WORKDAY_CREATE_ACCOUNT_RECIPE,
                        "sign_in_recipe": WORKDAY_SIGN_IN_RECIPE,
                        "detect": "host matches *.myworkdayjobs.com, OR a branded careers wrapper whose "
                                  "APPLY-NOW href targets *.myworkdayjobs.com (e.g. Takeda)"},
            "greenhouse": {"recipe": GREENHOUSE_APPLY_RECIPE,
                           "account_loop": GREENHOUSE_ACCOUNT_LOOP,
                           "lessons": GREENHOUSE_LESSONS,
                           "detect": "host job-boards/boards.greenhouse.io, OR a branded wrapper whose "
                                     "URL carries gh_jid/gh_src (form in an embedded greenhouse iframe)"},
            "appvault": {"recipe": APPVAULT_APPLY_RECIPE,
                         "account_loop": APPVAULT_ACCOUNT_LOOP,
                         "create_account_recipe": APPVAULT_CREATE_ACCOUNT_RECIPE,
                         "sign_in_recipe": APPVAULT_SIGN_IN_RECIPE,
                         "detect": "apply-destination host matches *apply.appvault.com, reached via a "
                                   "careers front (careerswithus.com) 'APPLY NOW' link; record the "
                                   "company→appvault mapping from the applystart feed"},
            "successfactors": {"recipe": SUCCESSFACTORS_APPLY_RECIPE,
                               "account_loop": SUCCESSFACTORS_ACCOUNT_LOOP,
                               "lessons": SUCCESSFACTORS_LESSONS,
                               "detect": "SAP on the EMPLOYER's own domain: path shape "
                                         "/<Tenant>/job/<Location>-<Title>-<id>/ or /<Tenant>/search/. "
                                         "ASKS FOR NOTIFICATION PERMISSION — deny it on the profile "
                                         "before driving or a native prompt blocks the window"},
            "icims": {"recipe": ICIMS_APPLY_RECIPE,
                      "account_loop": ICIMS_ACCOUNT_LOOP,
                      "create_account_recipe": ICIMS_CREATE_PROFILE_RECIPE,
                      "detect": "host matches jobs-/careers-<tenant>.icims.com, OR an employer "
                                "wrapper embedding #icims_content_iframe. READ THE FRAME, not the "
                                "top document — the wrapper is the employer's marketing site"},
        },
        "teachable": "states = page_state_registry indeed_apply_* ; transitions = the "
                     "state_transition model learns from captured observed->post_action data. "
                     "Seeded from observed live flows; refines as captures + the teacher grow. "
                     "Cross-site recipes (Workday/...) are seeded from teacher probes and graduate "
                     "to full spines once a pre-authed per-employer profile exists.",
    }
