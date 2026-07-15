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
    {"step": 7, "state": "indeed_apply_submitted",        "action": "record applied + provenance, "
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
    "custom_questions": "Employer-specific questions are appended per posting — they are the part that "
                        "does NOT generalize. Read them live; the rest of the form does generalize.",
}

def recipe_spec() -> dict[str, Any]:
    return {
        "domain": "indeed",
        "recipe": INDEED_APPLY_RECIPE,
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
        },
        "teachable": "states = page_state_registry indeed_apply_* ; transitions = the "
                     "state_transition model learns from captured observed->post_action data. "
                     "Seeded from observed live flows; refines as captures + the teacher grow. "
                     "Cross-site recipes (Workday/...) are seeded from teacher probes and graduate "
                     "to full spines once a pre-authed per-employer profile exists.",
    }
