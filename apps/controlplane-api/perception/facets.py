"""State facets — compositional identity for a page state (PLAN_perception_v1 §3.1).

A flat id is a cold start for every new tenant: `workday_my_information` tells a fresh Acme
Workday nothing, even though we have driven the identical page at six other tenants. Facets fix
that without touching anything that already works:

    state_id  workday_my_information        <- STILL the primary key, NEVER renamed
    domain    career_search
    platform  workday                       <- what a new tenant preserves
    phase     personal_information          <- what generalizes ACROSS platforms
    condition form_ready | error | ...       <- what varies turn to turn
    variant   generic | tenant:acme

The payoff is `phase`. `workday_my_information` and `indeed_apply_contact_info` are the same
question wearing two vendors' chrome, and a recipe that binds to (platform, phase) can say so.
The payoff is NOT a new taxonomy to maintain: facets are DERIVED, so a state added tomorrow gets
them for free, and a derivation we get wrong is a one-line fix rather than a data migration.

Precedence for `platform`, strongest evidence first: the live URL (`ats_registry.classify_ats`,
which already sees through branded wrappers) -> the registry's `domain_id` -> the state id's own
prefix. The url wins because a state id is a label we chose and a host is a fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

#: The one facet vocabulary. CLOSED in the same sense `Outcome` is closed: extend by earning a
#: member (a state we actually met that no member describes), never by intuition, never rename.
DOMAINS = ("career_search", "marketplace", "comms", "generic")

PLATFORMS = (
    "indeed", "linkedin", "workday", "greenhouse", "appvault", "icims", "lever", "taleo",
    "facebook", "google", "company_site", "generic",
)

#: Cross-platform phase vocabulary — the generalization surface. Two states with the same phase
#: are the same question in different chrome, and a recipe may bind to (platform, phase).
PHASES = (
    "sign_in", "sign_in_method_choice", "account_creation", "verification", "challenge",
    "home", "search_results", "job_posting", "apply_start", "resume",
    "personal_information", "work_experience", "questions", "demographics", "review",
    "submitted", "apply_form", "navigation", "listing_form", "listing_picker",
    "listing_publish", "messaging", "content", "error", "unknown",
)

CONDITIONS = ("form_ready", "picker_open", "blocked", "error", "challenge", "complete", "unknown")

# --- platform derivation -------------------------------------------------------------------
_PREFIX_PLATFORM = {
    "indeed": "indeed", "linkedin": "linkedin",
    "workday": "workday", "greenhouse": "greenhouse", "appvault": "appvault",
    "icims": "icims", "lever": "lever", "taleo": "taleo",
    "fb": "facebook", "facebook": "facebook", "mkt": "facebook", "listing": "facebook",
    "buyer": "facebook", "notifications": "facebook", "marketplace": "facebook",
    "gmail": "google", "google": "google",
}

#: registry `domain_id` -> platform. The registry predates facets and its domain_id mixes
#: engines with vendors (`indeed_jobs` vs `indeed`), so normalise rather than trust.
_DOMAIN_PLATFORM = {
    "indeed": "indeed", "indeed_jobs": "indeed",
    "linkedin": "linkedin", "linkedin_jobs": "linkedin", "workday": "workday",
    "greenhouse": "greenhouse", "appvault": "appvault", "gmail": "google", "google": "google",
    "facebook_marketplace": "facebook", "facebook": "facebook",
}

#: ats_registry ids that are not their own platform in facet terms — the ENGINE's on-page apply is
#: the engine, not a platform of its own.
_ATS_PLATFORM = {"indeed_quick_apply": "indeed", "linkedin_easy_apply": "linkedin",
                 "company_site": "company_site", "unknown": ""}

_CAREER_PLATFORMS = {"indeed", "linkedin", "workday", "greenhouse", "appvault", "icims", "lever",
                     "taleo", "company_site"}

# --- phase derivation ----------------------------------------------------------------------
# Exact ids first (unambiguous, mined from the 102 registry rows + the 59 observed states), then
# suffix patterns for ids we have not met yet. An id that matches nothing lands on `unknown` —
# the honest bucket, same discipline as FailureClass.UNKNOWN.
_PHASE_EXACT = {
    # auth
    "workday_sign_in": "sign_in", "indeed_login_email": "sign_in", "appvault_login": "sign_in",
    "login_form_full": "sign_in", "login_modal": "sign_in", "login_wall": "sign_in",
    "saved_account_login": "sign_in", "gmail_signin_identifier": "sign_in",
    "google_signin_email": "sign_in", "google_signin_password": "sign_in",
    "password_only": "sign_in", "gmail_password": "sign_in",
    "email_sso_or_code_choice": "sign_in_method_choice",
    "workday_create_account": "account_creation", "account_creation": "account_creation",
    "indeed_passcode_entry": "verification", "google_signin_2fa": "verification",
    "indeed_postauth_passkey": "verification", "gmail_verification_email": "verification",
    "google_signin_consent": "verification",
    # stop states
    "captcha": "challenge", "captcha_image_challenge": "challenge", "captcha_solved": "challenge",
    "security_checkpoint_captcha": "challenge", "greenhouse_captcha": "challenge",
    "account_locked": "error", "blocked": "error",
    # career search
    "indeed_home": "home", "indeed_home_logged_out": "home", "gmail_inbox": "home",
    "inbox": "home", "facebook_home_logged_in": "home", "listing_feed": "home",
    "indeed_search_results": "search_results", "search_results": "search_results",
    "job_search": "search_results",
    # LinkedIn's three seeded states (seed.py: job_search / job_detail / login_wall) under their
    # domain-prefixed ids. Everything else LinkedIn grows is caught by the suffix rules below.
    "linkedin_home": "home", "linkedin_job_search": "search_results",
    "linkedin_job_detail": "job_posting",
    "indeed_job_posting": "job_posting", "job_detail": "job_posting",
    "company_careers_job_posting": "job_posting", "ats_job_posting": "job_posting",
    "company_page": "job_posting", "ats_careers_index": "job_posting",
    "ats_apply_start": "apply_start", "ats_landing": "apply_start",
    "indeed_apply_ai_recruiter_gate": "apply_start", "indeed_did_you_apply": "apply_start",
    "indeed_apply_resume_selection": "resume", "indeed_apply_resume_review": "resume",
    "workday_my_information": "personal_information",
    "indeed_apply_contact_info": "personal_information",
    "workday_my_experience": "work_experience",
    "workday_questions": "questions", "indeed_apply_questions": "questions",
    "workday_voluntary_disclosures": "demographics", "workday_self_identify": "demographics",
    "indeed_apply_demographics": "demographics",
    "workday_review": "review", "indeed_apply_review": "review",
    "workday_application_submitted": "submitted", "indeed_apply_submitted": "submitted",
    "greenhouse_apply_submitted": "submitted", "task_complete": "submitted",
    "logged_in": "submitted",
    "greenhouse_apply_form": "apply_form", "greenhouse_apply_error": "apply_form",
    "workday_error_retry": "error",
    # marketplace
    "fb_create_listing_form": "listing_form", "fb_create_listing_form_complete": "listing_form",
    "mkt_create_item_form": "listing_form",
    "fb_listing_condition_picker": "listing_picker", "fb_listing_delivery_method": "listing_picker",
    "fb_listing_type_picker": "listing_picker", "mkt_create_chooser": "listing_picker",
    "fb_listing_publish": "listing_publish",
    "fb_listing_publish_blocked_new_account_limit": "listing_publish",
    "fb_marketplace_seller_dashboard": "home",
    "buyer_inbox": "messaging", "notifications_messages": "messaging",
    "email_thread": "content", "gmail_email_open": "content",
    "account_menu": "navigation", "indeed_account_menu_open": "navigation",
    "facebook_navigation": "navigation",
    "remove_profile_confirm": "navigation", "remove_profiles_dialog": "navigation",
    "out_of_domain": "unknown",
}

_PHASE_SUFFIX = (
    ("_sign_in", "sign_in"), ("_login", "sign_in"), ("_signin", "sign_in"),
    ("_2fa", "verification"), ("_passcode_entry", "verification"), ("_otp", "verification"),
    ("_captcha", "challenge"), ("_checkpoint", "challenge"),
    ("_search_results", "search_results"), ("_job_posting", "job_posting"),
    ("_resume_selection", "resume"), ("_resume_review", "resume"),
    ("_my_information", "personal_information"), ("_contact_info", "personal_information"),
    ("_my_experience", "work_experience"), ("_questions", "questions"),
    ("_voluntary_disclosures", "demographics"), ("_self_identify", "demographics"),
    ("_demographics", "demographics"), ("_review", "review"),
    ("_submitted", "submitted"), ("_complete", "submitted"),
    ("_error", "error"), ("_error_retry", "error"), ("_apply_form", "apply_form"),
    ("_picker", "listing_picker"), ("_chooser", "listing_picker"), ("_dialog", "navigation"),
    ("_menu_open", "navigation"),
)

# --- condition derivation ------------------------------------------------------------------
_CONDITION_RULES = (
    ("blocked", "blocked"), ("captcha", "challenge"), ("checkpoint", "challenge"),
    ("challenge", "challenge"), ("locked", "blocked"),
    ("error", "error"), ("retry", "error"), ("incorrect", "error"),
    ("submitted", "complete"), ("complete", "complete"), ("solved", "complete"),
    ("picker", "picker_open"), ("chooser", "picker_open"), ("dialog", "picker_open"),
    ("modal", "picker_open"), ("menu_open", "picker_open"), ("confirm", "picker_open"),
)

_MARKETPLACE_PHASES = {"listing_form", "listing_picker", "listing_publish", "messaging"}

#: `acme.wd5.myworkdayjobs.com` -> acme ; `boards.greenhouse.io/acme` handled by path below.
_WD_HOST = re.compile(r"^([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com$", re.I)
_WD_HOST_PLAIN = re.compile(r"^([a-z0-9-]+)\.myworkdayjobs\.com$", re.I)


@dataclass(frozen=True)
class StateFacets:
    """The compositional identity of one page state. `state_id` remains the primary key."""
    state_id: str
    domain: str
    platform: str
    phase: str
    condition: str
    variant: str

    @property
    def binding(self) -> str:
        """The key a recipe/program binds to when it wants to generalize across tenants."""
        return f"{self.platform}/{self.phase}"

    def as_dict(self) -> dict[str, str]:
        return {"state_id": self.state_id, "domain": self.domain, "platform": self.platform,
                "phase": self.phase, "condition": self.condition, "variant": self.variant}


def family_of(claim: str) -> str:
    """The coarse platform a platform CLAIM belongs to — the bridge between `ats_registry`'s ids
    and this module's `PLATFORMS`, so that two witnesses naming the same owner at different
    granularity can be recognised as agreeing rather than disagreeing.

    `indeed_quick_apply` and `indeed` are one owner said two ways: the url witness reads the host
    through the registry, a learned witness reads its own label through `platform_for`, and until
    2026-08-05 the fusion compared the two as raw strings and scored substantive agreement as
    dissent. Measured on the nine distinct live situations in the transition corpus, six of them
    were that exact false dissent and NOTHING ever reached `high` confidence.

    Identity is the right answer for everything else, and deliberately so:

      * already coarse (`workday`, `appvault`) — its own family
      * outside this vocabulary (`smartrecruiters`, `brassring`, …) — its own family too. Those
        ids only ever come from the url/signpost/memory witnesses, which speak registry ids; a
        learned witness cannot emit one, so there is no granularity gap to bridge.
      * `company_site` — the url witness SHRUGGING, and a shrug must never be collapsed into a
        named vendor. Its own family, which is what keeps `company_site` vs `appvault` scored as
        the dissent that IS the branded-wrapper diagnosis.

    The only true bridges are `ats_registry._ON_ENGINE_APPLY`: an engine's on-page apply is the
    engine, not a platform of its own. That is `_ATS_PLATFORM`, which already existed for exactly
    this reason.
    """
    return _ATS_PLATFORM.get((claim or "").strip().lower(), (claim or "").strip().lower())


def platform_for(state_id: str = "", *, url: str = "", domain_id: str = "") -> str:
    """Strongest evidence first: the live host, then the registry's domain_id, then the prefix."""
    if url:
        # Non-ATS hosts FIRST: `classify_ats` answers `company_site` for anything it does not
        # recognize, and `company_site` is a real platform in this vocabulary — so asking it
        # first made every facebook.com page a "company site". Found 2026-07-22 running the
        # observer on a real captcha capture. A confident wrong answer beats no answer only when
        # it is actually right.
        host = (urlparse(url).hostname or "").lower()
        if "facebook.com" in host or "messenger.com" in host:
            return "facebook"
        if "google.com" in host or "gmail.com" in host or "googleusercontent.com" in host:
            return "google"
        try:
            import ats_registry
            ats = ats_registry.classify_ats(url)
            mapped = _ATS_PLATFORM.get(ats, ats)
            if mapped in PLATFORMS:
                return mapped
        except Exception:  # ats_registry is optional for a pure-facets consumer
            pass
    if domain_id and _DOMAIN_PLATFORM.get(domain_id) in PLATFORMS:
        return _DOMAIN_PLATFORM[domain_id]
    prefix = (state_id or "").split("_")[0].lower()
    return _PREFIX_PLATFORM.get(prefix, "generic")


def phase_for(state_id: str) -> str:
    sid = (state_id or "").lower()
    if sid in _PHASE_EXACT:
        return _PHASE_EXACT[sid]
    for suffix, phase in _PHASE_SUFFIX:
        if sid.endswith(suffix):
            return phase
    return "unknown"


def condition_for(state_id: str) -> str:
    sid = (state_id or "").lower()
    for needle, cond in _CONDITION_RULES:
        if needle in sid:
            return cond
    return "form_ready"


def tenant_for(url: str) -> str:
    """The tenant slug from a live URL, or "" — the `variant` facet's only real source.

    A tenant is an INSTANCE of a platform (Acme's Workday), and it is knowable from the host for
    every ATS that subdomains per customer. Never guessed from a state id: the id is ours, the
    host is theirs.
    """
    host = (urlparse(url or "").hostname or "").lower()
    if not host:
        return ""
    for pattern in (_WD_HOST, _WD_HOST_PLAIN):
        m = pattern.match(host)
        if m and m.group(1) not in {"www", "impl"}:
            return m.group(1)
    if "greenhouse.io" in host:
        path = [p for p in urlparse(url).path.split("/") if p]
        if path and path[0] not in {"embed"}:
            return path[0]
    return ""


def facets_for(state_id: str, *, url: str = "", domain_id: str = "") -> StateFacets:
    """Derive all five facets. Pure, cheap, no IO — safe to call on every observation."""
    platform = platform_for(state_id, url=url, domain_id=domain_id)
    phase = phase_for(state_id)
    tenant = tenant_for(url)
    if platform in _CAREER_PLATFORMS:
        domain = "career_search"
    elif platform == "facebook" or phase in _MARKETPLACE_PHASES:
        domain = "marketplace"
    elif platform == "google":
        domain = "comms"
    else:
        domain = "generic"
    return StateFacets(
        state_id=state_id or "",
        domain=domain,
        platform=platform,
        phase=phase,
        condition=condition_for(state_id),
        variant=f"tenant:{tenant}" if tenant else "generic",
    )
