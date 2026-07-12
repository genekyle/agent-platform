"""Provider groups — the bucket ABOVE domains.

A *provider* is a company whose many surfaces we drive as separate domains but which share ONE
identity and login. Google is the first: gmail, google_calendar, google_docs and google_sheets
are distinct domains, but a single Google sign-in (one persistent, pre-authed Chrome profile)
authenticates all of them — and that shared SSO flow is exactly what other domains hand off to
(e.g. Indeed's "sign in with a code" errand fetches the code from Gmail).

Why this is the right shape:
  * The provider OWNS the shared auth. Log in once to the provider's profile and every member
    domain is authenticated — we never re-solve Google's login per surface.
  * The provider is the errand HAND-OFF target. "Get the Indeed login code" resolves to a
    capability a member domain offers (gmail ▸ fetch_login_code), addressed through the group.
  * The provider is the UI bucket — one "Google" card that expands to its member workspaces.

Deliberately a small backend-owned CONSTANT (like command_center.DOMAINS), not a DB table: it's
config, not training data, and membership is checked against the live DomainRegistry so there's a
single source of truth for "does this domain exist yet." Promote to a table only if operators ever
need to edit groups at runtime.
"""

from __future__ import annotations

from typing import Any, Optional

# provider_id -> group definition. `member_domains` lists every domain_id that belongs to the
# group whether or not it's seeded yet; resolve_members() splits it into live vs. planned against
# the actual registry. `auth` describes the ONE shared sign-in every member reuses.
PROVIDERS: list[dict[str, Any]] = [
    {
        "provider_id": "google",
        "display_name": "Google",
        "icon": "🌐",
        # Shared persistent Chrome profile: log in ONCE here and every member domain is authed.
        "profile": "google",
        "member_domains": ["gmail", "google_calendar", "google_docs", "google_sheets"],
        "auth": {
            "kind": "sso",  # Google single-sign-on / OAuth
            "host_patterns": ["accounts.google.com"],
            # The shared SSO screens. Captured under whichever member domain triggered login
            # (today: gmail) — this is where "Google single sign-on" training data lives.
            "login_states": [
                "google_signin_email",
                "google_signin_password",
                "google_signin_2fa",
                "google_signin_consent",
            ],
            # Cross-domain errand hook: another domain's "sign in with a code" flow hands off to
            # the gmail domain's fetch_login_code goal to read the one-time code from email.
            "code_delivery": {"via_domain": "gmail", "goal": "fetch_login_code"},
        },
    },
]

_BY_ID = {p["provider_id"]: p for p in PROVIDERS}


def list_providers() -> list[dict[str, Any]]:
    """All provider groups (the raw constants)."""
    return PROVIDERS


def get_provider(provider_id: str) -> Optional[dict[str, Any]]:
    return _BY_ID.get(provider_id)


def provider_for_domain(domain_id: str) -> Optional[str]:
    """Which provider group a domain belongs to, or None if it's standalone. Lets any consumer
    (training rollups, session launch, errand routing) group by provider without a DB column."""
    for p in PROVIDERS:
        if domain_id in p["member_domains"]:
            return p["provider_id"]
    return None


def resolve_members(provider: dict[str, Any], live_domain_ids: set[str]) -> dict[str, list[str]]:
    """Split a provider's member_domains into those already in the registry vs. still planned."""
    members = provider.get("member_domains", [])
    return {
        "live": [d for d in members if d in live_domain_ids],
        "planned": [d for d in members if d not in live_domain_ids],
    }
