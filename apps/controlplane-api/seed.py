"""Registry seed data + idempotent bootstrap seeders/backfills.

Extracted from main.py (bootstrap layer — docs/TARGET_ARCHITECTURE.md Layer 2). REGISTRY_SEED is
the initial domains/goals/tasks/scenarios; the seed_* functions populate an empty registry, the
backfill_* functions top up rows added after an earlier seed. All idempotent; main.py calls them
from the startup hook. Self-contained: models + select + _slugify, no route/app imports.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from deps import _slugify
from models import (
    ActionRegistry,
    ApplicationAnswer,
    DomainRegistry,
    GoalRegistry,
    PageStateRegistry,
    ScenarioRegistry,
    TaskRegistry,
    TrainingCapture,
)


REGISTRY_SEED = {
    "domains": [
        {
            "domain_id": "facebook_marketplace",
            "display_name": "Facebook Marketplace",
            "host_patterns": ["facebook.com", "www.facebook.com"],
            "page_states": [
                {"page_state_id": "listing_feed", "display_name": "Listing Feed"},
                {"page_state_id": "buyer_inbox", "display_name": "Buyer Inbox"},
                {"page_state_id": "login_wall", "display_name": "Login Wall"},
            ],
            "capture_defaults": {"profile": "viewport", "shot_types": ["viewport", "fullpage"]},
            "validation_expectations": [{"kind": "host_match", "value": "facebook.com"}],
            "config_version": "v1",
        },
        {
            "domain_id": "indeed_jobs",
            "display_name": "Indeed Jobs",
            "host_patterns": ["indeed.com", "www.indeed.com"],
            "page_states": [
                {"page_state_id": "search_results", "display_name": "Search Results"},
                {"page_state_id": "company_page", "display_name": "Company Page"},
                {"page_state_id": "email_alert", "display_name": "Email Alert"},
                {"page_state_id": "job_detail", "display_name": "Job Detail"},
                {"page_state_id": "login_wall", "display_name": "Login Wall"},
            ],
            "capture_defaults": {"profile": "viewport", "shot_types": ["viewport", "fullpage", "sweep_1", "sweep_2"]},
            "validation_expectations": [{"kind": "host_match", "value": "indeed.com"}],
            "config_version": "v1",
        },
        {
            "domain_id": "linkedin_jobs",
            "display_name": "LinkedIn Jobs",
            "host_patterns": ["linkedin.com", "www.linkedin.com"],
            "page_states": [
                {"page_state_id": "job_search", "display_name": "Job Search"},
                {"page_state_id": "job_detail", "display_name": "Job Detail"},
                {"page_state_id": "login_wall", "display_name": "Login Wall"},
            ],
            "capture_defaults": {"profile": "viewport", "shot_types": ["viewport", "fullpage"]},
            "validation_expectations": [{"kind": "host_match", "value": "linkedin.com"}],
            "config_version": "v1",
        },
        {
            # Gmail — first member of the "google" PROVIDER group (see providers.py). It is BOTH a
            # real domain (its own inbox/thread surfaces) and the home for shared Google SSO
            # training data: the google_signin_* states below are the ONE sign-in every Google
            # domain reuses, captured here because Gmail is the surface that triggers login today.
            # Also the target of cross-domain "sign in with a code" errands (goal fetch_login_code).
            "domain_id": "gmail",
            "display_name": "Gmail",
            "host_patterns": ["mail.google.com", "accounts.google.com"],
            "page_states": [
                # Shared Google single-sign-on flow (provider-level; homed here for now).
                {"page_state_id": "google_signin_email", "display_name": "Google Sign-in — Email"},
                {"page_state_id": "google_signin_password", "display_name": "Google Sign-in — Password"},
                {"page_state_id": "google_signin_2fa", "display_name": "Google Sign-in — 2-Step Verification"},
                {"page_state_id": "google_signin_consent", "display_name": "Google OAuth Consent"},
                # Gmail's own surfaces.
                {"page_state_id": "inbox", "display_name": "Inbox"},
                {"page_state_id": "email_thread", "display_name": "Email Thread"},
                {"page_state_id": "login_wall", "display_name": "Login Wall"},
            ],
            "capture_defaults": {"profile": "viewport", "shot_types": ["viewport", "fullpage"]},
            "validation_expectations": [{"kind": "host_match", "value": "google.com"}],
            "config_version": "v1",
        },
    ],
    "goals": [
        {"goal_id": "log_in", "domain_id": None, "display_name": "Log In", "action_type_hints": ["type", "click"]},
        {"goal_id": "review_posted_items", "domain_id": "facebook_marketplace", "display_name": "Review Posted Items", "action_type_hints": ["click"]},
        {"goal_id": "reply_to_buyer", "domain_id": "facebook_marketplace", "display_name": "Reply to Buyer", "action_type_hints": ["click", "type"]},
        {"goal_id": "create_listing", "domain_id": "facebook_marketplace", "display_name": "Create Marketplace Listing", "action_type_hints": ["click", "type", "select"]},
        {"goal_id": "search_jobs", "domain_id": "indeed_jobs", "display_name": "Search Jobs", "action_type_hints": ["type", "click"]},
        {"goal_id": "open_job_posting", "domain_id": "indeed_jobs", "display_name": "Open Job Posting", "action_type_hints": ["click"]},
        {"goal_id": "apply_to_job", "domain_id": "indeed_jobs", "display_name": "Apply to Job", "action_type_hints": ["click", "type", "select"]},
        {"goal_id": "search_linkedin_jobs", "domain_id": "linkedin_jobs", "display_name": "Search LinkedIn Jobs", "action_type_hints": ["type", "click"]},
        {"goal_id": "open_linkedin_job", "domain_id": "linkedin_jobs", "display_name": "Open LinkedIn Job", "action_type_hints": ["click"]},
        # Apply is the point of the domain, and it forks: Easy Apply completes on LinkedIn, anything
        # else hands off to a real ATS on that ATS's own host (where the existing ATS recipes take over).
        {"goal_id": "apply_to_linkedin_job", "domain_id": "linkedin_jobs", "display_name": "Apply to LinkedIn Job", "action_type_hints": ["click", "type", "select"]},
        # Gmail (google provider). fetch_login_code is the cross-domain errand hand-off — read a
        # one-time sign-in code out of the inbox for another domain's "sign in with a code" flow.
        {"goal_id": "fetch_login_code", "domain_id": "gmail", "display_name": "Fetch Login Code from Email", "action_type_hints": ["click", "type"]},
        {"goal_id": "read_email", "domain_id": "gmail", "display_name": "Read Email", "action_type_hints": ["click"]},
    ],
    "tasks": [
        {"task_id": "browser_open_tab", "scope_level": "browser", "domain_id": None, "goal_id": None, "display_name": "Open target tab"},
        {"task_id": "marketplace_open_inbox", "scope_level": "domain", "domain_id": "facebook_marketplace", "goal_id": None, "display_name": "Open marketplace inbox"},
        {"task_id": "facebook_create_listing_flow", "scope_level": "goal", "domain_id": "facebook_marketplace", "goal_id": "create_listing", "display_name": "Create a Marketplace listing"},
        {"task_id": "indeed_apply_flow", "scope_level": "goal", "domain_id": "indeed_jobs", "goal_id": "apply_to_job", "display_name": "Complete Indeed apply flow"},
        {"task_id": "linkedin_apply_flow", "scope_level": "goal", "domain_id": "linkedin_jobs", "goal_id": "apply_to_linkedin_job", "display_name": "Complete LinkedIn apply flow"},
        # The errand, as a task another domain's flow detours into and returns from.
        {"task_id": "gmail_fetch_code_flow", "scope_level": "goal", "domain_id": "gmail", "goal_id": "fetch_login_code", "display_name": "Read a one-time login code from the inbox"},
    ],
    "scenarios": [
        {
            # Supervised one-time Google sign-in for the shared `google` profile (gmail domain).
            # Start state is the Google email screen; the flow walks email → password → 2FA → inbox.
            "scenario_id": "gmail_login_google_signin",
            "domain_id": "gmail",
            "goal_id": "log_in",
            "task_id": None,
            "display_name": "Google Sign-in -> Gmail Inbox",
            "start_page_state": "google_signin_email",
            "description": "Supervised one-time Google sign-in for the shared google profile (email -> password -> 2FA -> inbox).",
            "capture_profile_override": None,
        },
        {
            # The ERRAND, as a startable scenario. Without this row the fetch_login_code goal is
            # registered and unusable: a training session is created from (domain, scenario) and
            # the endpoint 404s when the scenario is missing, so the goal shows up in every picker
            # and nothing can ever run it. Exactly the hole LinkedIn sat in until 642c8dd.
            # Starts at `inbox` because the shared google profile is already signed in — the errand
            # is a tab hop, not a login. If it isn't signed in, the recipe's login_wall branch says
            # so and the operator signs in once for every provider member at once.
            "scenario_id": "gmail_inbox_fetch_login_code",
            "domain_id": "gmail",
            "goal_id": "fetch_login_code",
            "task_id": "gmail_fetch_code_flow",
            "display_name": "Inbox -> Read a One-Time Login Code",
            "start_page_state": "inbox",
            "description": "Cross-domain errand: read a one-time sign-in code out of the inbox LIST (the subject line carries it) for another domain's 'sign in with a code' flow, then return.",
            "capture_profile_override": None,
        },
        {
            "scenario_id": "indeed_search_results_open_job_posting",
            "domain_id": "indeed_jobs",
            "goal_id": "open_job_posting",
            "task_id": None,
            "display_name": "Search Results -> Open Job Posting",
            "start_page_state": "search_results",
            "description": "Start from Indeed search results and open a job posting.",
            "capture_profile_override": None,
        },
        {
            "scenario_id": "indeed_company_page_open_job_posting",
            "domain_id": "indeed_jobs",
            "goal_id": "open_job_posting",
            "task_id": None,
            "display_name": "Company Page -> Open Job Posting",
            "start_page_state": "company_page",
            "description": "Start from a company page and open a job posting.",
            "capture_profile_override": None,
        },
        {
            "scenario_id": "indeed_email_alert_open_job_posting",
            "domain_id": "indeed_jobs",
            "goal_id": "open_job_posting",
            "task_id": None,
            "display_name": "Email Alert -> Open Job Posting",
            "start_page_state": "email_alert",
            "description": "Start from an email-alert landing page and open a posting.",
            "capture_profile_override": None,
        },
        {
            "scenario_id": "indeed_login_wall_log_in",
            "domain_id": "indeed_jobs",
            "goal_id": "log_in",
            "task_id": None,
            "display_name": "Login Wall -> Log In",
            "start_page_state": "login_wall",
            "description": "Start at the Indeed login wall and authenticate.",
            "capture_profile_override": None,
        },
        # LinkedIn. A scenario names WHERE a session starts, and the login-wall one is the entry
        # point that matters: a fresh LinkedIn profile has no cookies, so the first session begins
        # signed out and the ladder's auth rung drives from there.
        {
            "scenario_id": "linkedin_login_wall_log_in",
            "domain_id": "linkedin_jobs",
            "goal_id": "log_in",
            "task_id": None,
            "display_name": "Login Wall -> Log In",
            "start_page_state": "login_wall",
            "description": "Start at the LinkedIn sign-in wall and authenticate with the stored login.",
            "capture_profile_override": None,
        },
        {
            "scenario_id": "linkedin_job_search_open_job",
            "domain_id": "linkedin_jobs",
            "goal_id": "open_linkedin_job",
            "task_id": None,
            "display_name": "Job Search -> Open Job",
            "start_page_state": "job_search",
            "description": "Start from LinkedIn job search results and open a posting in the detail pane.",
            "capture_profile_override": None,
        },
        {
            "scenario_id": "linkedin_job_detail_apply",
            "domain_id": "linkedin_jobs",
            "goal_id": "apply_to_linkedin_job",
            "task_id": "linkedin_apply_flow",
            "display_name": "Job Detail -> Apply",
            "start_page_state": "job_detail",
            "description": "Start from an open LinkedIn posting and enter the apply flow — Easy Apply "
                           "on-engine, or the hand-off to the employer's ATS.",
            "capture_profile_override": "fullpage",
        },
        {
            "scenario_id": "indeed_job_detail_apply_to_job",
            "domain_id": "indeed_jobs",
            "goal_id": "apply_to_job",
            "task_id": "indeed_apply_flow",
            "display_name": "Job Detail -> Apply to Job",
            "start_page_state": "job_detail",
            "description": "Start from a job detail page and enter the apply flow.",
            "capture_profile_override": "fullpage",
        },
    ],
}


def seed_training_registry(db: Session) -> None:
    if db.scalar(select(DomainRegistry.domain_id).limit(1)):
        return

    for payload in REGISTRY_SEED["domains"]:
        db.add(DomainRegistry(status="active", **payload))
    for payload in REGISTRY_SEED["goals"]:
        db.add(GoalRegistry(status="active", **payload))
    for payload in REGISTRY_SEED["tasks"]:
        db.add(TaskRegistry(status="active", **payload))
    for payload in REGISTRY_SEED["scenarios"]:
        db.add(ScenarioRegistry(status="active", **payload))
    db.commit()


def seed_facebook_extras(db: Session) -> None:
    """Idempotent top-up for the Facebook create-listing goal/task on an ALREADY-seeded DB.
    `seed_training_registry` only runs on an empty registry, so a DB seeded before the
    create-listing flow existed would miss these rows — add just the missing ones."""
    want_goals = [g for g in REGISTRY_SEED["goals"] if g["goal_id"] == "create_listing"]
    want_tasks = [t for t in REGISTRY_SEED["tasks"] if t["task_id"] == "facebook_create_listing_flow"]
    if not db.scalar(select(DomainRegistry.domain_id).where(
            DomainRegistry.domain_id == "facebook_marketplace")):
        return  # domain not seeded at all → the full seeder will handle it on a fresh DB
    changed = False
    for payload in want_goals:
        if not db.scalar(select(GoalRegistry.goal_id).where(GoalRegistry.goal_id == payload["goal_id"])):
            db.add(GoalRegistry(status="active", **payload))
            changed = True
    for payload in want_tasks:
        if not db.scalar(select(TaskRegistry.task_id).where(TaskRegistry.task_id == payload["task_id"])):
            db.add(TaskRegistry(status="active", **payload))
            changed = True
    if changed:
        db.commit()


def seed_gmail_domain(db: Session) -> None:
    """Idempotent top-up + RECONCILE for the Gmail domain + its goals on an ALREADY-seeded DB —
    the base seeder only runs on an empty registry, so a DB seeded before Gmail existed would miss
    it. A barebones `gmail` row may also have been added by hand/UI; if so we merge in the canonical
    Google-SSO page_states (the home for single-sign-on training data) and host patterns without
    removing anything another session may own. See providers.py for the google provider group."""
    want = next((d for d in REGISTRY_SEED["domains"] if d["domain_id"] == "gmail"), None)
    if want is None:
        return
    changed = False
    row = db.get(DomainRegistry, "gmail")
    if row is None:
        db.add(DomainRegistry(status="active", **want))
        changed = True
    else:
        # Merge (reassign so SQLAlchemy tracks the JSON change), never remove.
        have_states = {s.get("page_state_id") for s in (row.page_states or [])}
        missing_states = [s for s in want["page_states"] if s["page_state_id"] not in have_states]
        if missing_states:
            row.page_states = (row.page_states or []) + missing_states
            changed = True
        have_hosts = set(row.host_patterns or [])
        missing_hosts = [h for h in want["host_patterns"] if h not in have_hosts]
        if missing_hosts:
            row.host_patterns = (row.host_patterns or []) + missing_hosts
            changed = True
    for payload in [g for g in REGISTRY_SEED["goals"] if g.get("domain_id") == "gmail"]:
        if not db.scalar(select(GoalRegistry.goal_id).where(GoalRegistry.goal_id == payload["goal_id"])):
            db.add(GoalRegistry(status="active", **payload))
            changed = True
    # TASKS before scenarios — a scenario carries a task_id foreign key, so seeding them the other
    # way round leaves the errand scenario pointing at a task that does not exist yet. (This loop
    # was missing entirely until the errand needed it; the same omission is what left LinkedIn
    # registered-and-unusable, one row further down the same chain.)
    for payload in [t for t in REGISTRY_SEED["tasks"] if t.get("domain_id") == "gmail"]:
        if not db.scalar(select(TaskRegistry.task_id).where(TaskRegistry.task_id == payload["task_id"])):
            db.add(TaskRegistry(status="active", **payload))
            changed = True
    for payload in [s for s in REGISTRY_SEED["scenarios"] if s.get("domain_id") == "gmail"]:
        if not db.scalar(select(ScenarioRegistry.scenario_id).where(ScenarioRegistry.scenario_id == payload["scenario_id"])):
            db.add(ScenarioRegistry(status="active", **payload))
            changed = True
    if changed:
        db.commit()


def seed_linkedin_domain(db: Session) -> None:
    """Idempotent top-up for the LinkedIn Jobs domain + its goals on an ALREADY-seeded DB.

    `linkedin_jobs` has been in REGISTRY_SEED since the registry was written, but the base seeder
    only runs on an EMPTY registry — so every DB seeded before LinkedIn was promoted to a real
    workspace has no row for it, and captures tagged `linkedin_jobs` would have no domain to hang
    off. Same merge discipline as `seed_gmail_domain`: add what's missing, never remove, so a
    concurrent session's edits survive."""
    want = next((d for d in REGISTRY_SEED["domains"] if d["domain_id"] == "linkedin_jobs"), None)
    if want is None:
        return
    changed = False
    row = db.get(DomainRegistry, "linkedin_jobs")
    if row is None:
        db.add(DomainRegistry(status="active", **want))
        changed = True
    else:
        have_states = {s.get("page_state_id") for s in (row.page_states or [])}
        missing_states = [s for s in want["page_states"] if s["page_state_id"] not in have_states]
        if missing_states:
            row.page_states = (row.page_states or []) + missing_states
            changed = True
        have_hosts = set(row.host_patterns or [])
        missing_hosts = [h for h in want["host_patterns"] if h not in have_hosts]
        if missing_hosts:
            row.host_patterns = (row.host_patterns or []) + missing_hosts
            changed = True
    for payload in [g for g in REGISTRY_SEED["goals"] if g.get("domain_id") == "linkedin_jobs"]:
        if not db.scalar(select(GoalRegistry.goal_id).where(GoalRegistry.goal_id == payload["goal_id"])):
            db.add(GoalRegistry(status="active", **payload))
            changed = True
    # TASKS + SCENARIOS. A training session is (domain, scenario) and the endpoint 404s when the
    # scenario is missing — so a domain with goals but no scenario is registered and unusable, which
    # is precisely where LinkedIn sat: `linkedin_jobs` showed up in every picker and no session
    # could be created for it.
    for payload in [t for t in REGISTRY_SEED["tasks"] if t.get("domain_id") == "linkedin_jobs"]:
        if not db.scalar(select(TaskRegistry.task_id).where(TaskRegistry.task_id == payload["task_id"])):
            db.add(TaskRegistry(status="active", **payload))
            changed = True
    for payload in [sc for sc in REGISTRY_SEED["scenarios"] if sc.get("domain_id") == "linkedin_jobs"]:
        if not db.scalar(select(ScenarioRegistry.scenario_id)
                         .where(ScenarioRegistry.scenario_id == payload["scenario_id"])):
            db.add(ScenarioRegistry(status="active", **payload))
            changed = True
    if changed:
        db.commit()


def seed_application_answers(db: Session) -> None:
    """Seed the operator's repeatable application answers once (idempotent: skip if any
    exist). Values are operator-provided; everything is editable in the Indeed workspace."""
    import application_answers as aa
    if db.scalar(select(ApplicationAnswer.answer_key).limit(1)):
        return
    for payload in aa.SEED_ANSWERS:
        db.add(ApplicationAnswer(**payload))
    db.commit()




# Global states that apply to any capture regardless of domain/scenario.
_GLOBAL_PAGE_STATES = [
    {"state_id": "out_of_domain", "display_name": "Out of Domain", "category": "navigation"},
    {"state_id": "unknown", "display_name": "Unknown / Unclassified", "category": "navigation"},
    {"state_id": "blocked", "display_name": "Blocked / Needs Human", "category": "error"},
]


# Built-in action vocabulary. action_id matches what TrainingCapture.action_type_hint
# already stores (keep ids stable!). "clear" is new. value_label names the payload field.
_BUILTIN_ACTIONS = [
    {"action_id": "click", "label": "Click", "value_label": "Optional Payload", "sort_order": 10},
    {"action_id": "type", "label": "Type", "value_label": "Text to Type", "sort_order": 20},
    {"action_id": "clear", "label": "Clear", "value_label": None, "sort_order": 30},
    {"action_id": "select", "label": "Select", "value_label": "Option to Select", "sort_order": 40},
    {"action_id": "scroll", "label": "Scroll", "value_label": "Scroll Direction / Amount", "sort_order": 50},
    {"action_id": "navigate", "label": "Navigate", "value_label": "URL or Destination", "sort_order": 60},
    {"action_id": "wait", "label": "Wait", "value_label": "Wait Condition", "sort_order": 70},
    {"action_id": "press", "label": "Key", "value_label": "Key / Shortcut", "sort_order": 80},
    {"action_id": "any", "label": "Any", "value_label": "Optional Payload", "sort_order": 90},
]


def seed_actions(db: Session) -> None:
    """Idempotently ensure built-in actions exist (incl. the new 'clear')."""
    existing = set(db.scalars(select(ActionRegistry.action_id)).all())
    for payload in _BUILTIN_ACTIONS:
        if payload["action_id"] not in existing:
            db.add(ActionRegistry(is_builtin=True, status="active", **payload))
    db.commit()


# Heuristic backfill for the new goal.stage: login/signup/auth objectives are the
# unauthenticated bridge; everything else is authenticated. Annotators can override.
_UNAUTH_GOAL_HINTS = ("log_in", "login", "sign_in", "signin", "sign_up", "signup", "register", "reset_password", "forgot")


def backfill_goal_stages(db: Session) -> None:
    """Set stage on goals that are still 'neutral' (one-time, non-destructive)."""
    for goal in db.scalars(select(GoalRegistry).where(GoalRegistry.stage == "neutral")).all():
        gid = (goal.goal_id or "").lower()
        goal.stage = "unauthenticated" if any(h in gid for h in _UNAUTH_GOAL_HINTS) else "authenticated"
    db.commit()


def backfill_page_state_stages(db: Session) -> None:
    """Goal/scenario-scoped states inherit their goal's stage when unset
    (one-time, non-destructive — only fills NULL stage)."""
    goals = {g.goal_id: g for g in db.scalars(select(GoalRegistry)).all()}
    changed = False
    for s in db.scalars(select(PageStateRegistry).where(PageStateRegistry.stage.is_(None))).all():
        if s.scope in ("goal", "scenario") and s.goal_id in goals:
            s.stage = goals[s.goal_id].stage or "neutral"
            changed = True
    if changed:
        db.commit()


def seed_page_states(db: Session) -> None:
    """Idempotently ensure the global states exist, and migrate any legacy
    per-domain page_states (the old JSON blob) into the registry as scope=domain
    rows. Safe to run on every startup — only inserts what's missing."""
    existing_ids = set(db.scalars(select(PageStateRegistry.state_id)).all())

    for payload in _GLOBAL_PAGE_STATES:
        if payload["state_id"] not in existing_ids:
            db.add(PageStateRegistry(scope="global", status="active", **payload))
            existing_ids.add(payload["state_id"])

    # Migrate legacy domain.page_states JSON → scope=domain registry rows.
    for domain in db.scalars(select(DomainRegistry)).all():
        for ps in (domain.page_states or []):
            sid = ps.get("page_state_id") or _slugify(ps.get("display_name", ""))
            if not sid or sid in existing_ids:
                continue
            db.add(PageStateRegistry(
                state_id=sid,
                display_name=ps.get("display_name") or sid,
                scope="domain",
                domain_id=domain.domain_id,
                category=ps.get("category") or "general",
                status="active",
            ))
            existing_ids.add(sid)
    db.commit()
