"""Gmail recipes — the Google sign-in spine (owned on behalf of the whole provider) and the
fetch-login-code errand.

Mirrors `facebook_recipe.py` / `apply_recipe.py`: each recipe is the expected linear spine of a
task as page-state → action → the state(s) it may lead to, plus the BRANCHES that peel off and
need a human.

Two things make Gmail different from every other domain here, and both are deliberate:

**1. The login spine ends at a wall we do not climb.** Everything up to Google's password field we
drive on the CDP-AX layer; the password and 2FA keystrokes are a hand-off to the operator, in the
same class as never auto-solving a captcha. Three reasons, all still true: it is the operator's
crown-jewel credential and a locked Google account cascades into every other domain;
`accounts.google.com` is the most bot-fingerprinted page on the web; and the training value is in
CAPTURING the states, not in who typed. So `google_signin_password` and `google_signin_2fa` are
human-required branches, not steps — the recipe's job is to reach them and stop.

**2. These states belong to the PROVIDER, not to Gmail.** `google_signin_*` is the ONE sign-in that
Docs, Sheets and Drive will reuse; it is homed here because Gmail is the surface that triggers
login today. When those members arrive they inherit this map rather than re-learning it — which is
the entire argument for the provider group existing. `providers.google.auth.login_states` is the
declaration; this module is the reader, and a test pins the two together.

**Do not reuse the Indeed auth detector here.** On 2026-07-10 `/auth_state` reported
`logged_in: false` on `myaccount.google.com` — meaningless there, and confidently wrong. Google
gives no "Sign out" button to probe; the real signal is being on a URL that only renders when
signed in. `signed_in_signal()` is that check.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# --- LOGIN spine ------------------------------------------------------------------------
# Terminal state = inbox. The password/2FA middle is intentionally absent from the spine: we drive
# to the wall and the operator authenticates. Passkey is the common case on this account and is
# cleaner than a password — it still surfaces as google_signin_2fa.
GMAIL_LOGIN_RECIPE = [
    {"step": 0, "state": "google_signin_email", "action": "type the account email, click Next",
     "selectors": {"email": {"roles": ["textbox"], "name_any": ["email", "phone"]},
                   "next": {"roles": ["button"], "name_any": ["next"]}},
     "expect": ["google_signin_password", "google_signin_2fa", "captcha"]},
    # No step for password/2FA — see the module docstring. They are branches, and they are the
    # operator's to clear.
    {"step": 1, "state": "inbox", "action": "done — signed in, inbox rendered", "expect": []},
]

GMAIL_LOGIN_BRANCHES = {
    "google_signin_password": {
        "human_required": True,
        "note": "Google password — operator types it, never the agent. The crown-jewel credential; "
                "a lock-out cascades into every domain that signs in with Google."},
    "google_signin_2fa": {
        "human_required": True,
        "note": "2-step verification (passkey / prompt / TOTP / SMS) — operator clears it. Same "
                "class as a captcha: classify and escalate, never solve."},
    "google_signin_consent": {
        "human_required": True,
        "note": "OAuth consent — granting an app access to the account is the operator's decision, "
                "not ours."},
    "captcha": {
        "human_required": True,
        "note": "Google anti-bot challenge — operator solves, never auto-solve."},
    "google_signin_account_picker": {
        "human_required": False,
        "note": "'Choose an account' — pick the operator's account; not blocking."},
}

# --- FETCH-LOGIN-CODE errand spine --------------------------------------------------------
# The cheapest possible read, and short on purpose. The code lives in the SUBJECT line, so the
# inbox LIST is the whole surface — we never open the thread. That is fewer steps, less churn, and
# no read-receipt on a mail we only needed to glance at (LEARNINGS 2026-07-10). `errands.py` holds
# the resolution rules; this is only the navigation.
GMAIL_FETCH_CODE_RECIPE = [
    {"step": 0, "state": "inbox",
     "action": "read the inbox LIST rows (sender, subject, age) — do not open the thread",
     "expect": ["inbox"]},
    {"step": 1, "state": "inbox",
     "action": "hand the rows to errands.resolve_login_code; return the code or escalate",
     "expect": []},
]

GMAIL_FETCH_CODE_BRANCHES = {
    "login_wall": {
        "human_required": True,
        "note": "the shared Google profile is signed out — the errand cannot run until the "
                "operator signs in once; every provider member is blocked with it."},
    "google_signin_2fa": {
        "human_required": True,
        "note": "re-authentication demanded mid-errand — operator clears it, then retry."},
}

# --- URL -> state (cheap, no model) --------------------------------------------------------
# Order is load-bearing: the challenge paths are all UNDER /signin, so a bare /signin rule placed
# first would swallow the password and 2FA screens and report every one of them as "email".
_URL_STATES = [
    # OAuth consent — check before the generic signin rules; it lives on both hosts.
    (r"/signin/oauth|/o/oauth2/auth|/oauth/consent",              "google_signin_consent"),
    # 2FA, in all the shapes this account has actually served: passkey/prompt (dp, ipp), TOTP,
    # SMS, and the legacy two-step URL.
    (r"/challenge/(totp|ipp|dp|sms|az|iap)|two[-_]?step|/webauthn", "google_signin_2fa"),
    (r"/challenge/pwd|/signin/v2/challenge/password",             "google_signin_password"),
    (r"/signin/identifier|/signin/v2/identifier|/AccountChooser", "google_signin_email"),
    (r"accounts\.google\.com/(v3/)?signin",                       "google_signin_email"),
    # A thread is the inbox hash plus a message id — must precede the bare inbox rule.
    (r"mail\.google\.com/mail/u/\d+/#\w+/\w+",                    "email_thread"),
    (r"mail\.google\.com/mail/u/\d+",                             "inbox"),
    (r"mail\.google\.com/mail/?$",                                "inbox"),
    # Signed out, Gmail bounces to its marketing/sign-in front door.
    (r"mail\.google\.com/(mail/)?(about|signin)|workspace\.google\.com/.*gmail", "login_wall"),
]


def map_url_to_state(url: str) -> str:
    u = url or ""
    for pattern, state in _URL_STATES:
        if re.search(pattern, u, re.I):
            return state
    return "unknown"


#: Only these URLs render when signed in. This is the Google auth signal — presence of a
#: signed-in-only surface — because Google offers nothing to probe for the way Indeed does.
_SIGNED_IN_STATES = frozenset({"inbox", "email_thread"})


def signed_in_signal(url: str) -> Optional[bool]:
    """Is the shared Google profile signed in, judged from the URL alone?

    `True` on a signed-in-only surface, `False` at a sign-in wall, and **None when we cannot tell** —
    which is the honest answer for `myaccount.google.com` and every other page Google serves to both
    states. None means "ask something else" (a screenshot, an AX scan); it does not mean False. The
    Indeed detector's mistake was returning a confident False here.
    """
    state = map_url_to_state(url)
    if state in _SIGNED_IN_STATES:
        return True
    if state in {"login_wall", "google_signin_email", "google_signin_password",
                 "google_signin_2fa", "google_signin_account_picker"}:
        return False
    return None


def _all_branches() -> dict:
    return {**GMAIL_LOGIN_BRANCHES, **GMAIL_FETCH_CODE_BRANCHES}


def _recipe_entry(state: str) -> Optional[dict]:
    for recipe in (GMAIL_LOGIN_RECIPE, GMAIL_FETCH_CODE_RECIPE):
        entry = next((s for s in recipe if s["state"] == state), None)
        if entry is not None:
            return entry
    return None


def describe_tab(url: str, page_text: str = "") -> dict[str, Any]:
    """Map ONE live tab onto the Gmail recipes — the per-tab 'where are we' readout, same shape as
    the Indeed and Facebook `describe_tab`s so a caller can treat them interchangeably."""
    state = map_url_to_state(url)
    branch = _all_branches().get(state)
    entry = _recipe_entry(state)
    return {
        "url": (url or "")[:90],
        "state": state,
        "recipe_step": entry["step"] if entry else None,
        "next_action": entry["action"] if entry else None,
        "expected_next": entry["expect"] if entry else [],
        "is_branch": branch is not None,
        "human_required": bool(branch and branch["human_required"]),
        "branch_note": branch["note"] if branch else None,
        # Unlike the other domains', this readout carries the auth verdict, because for Gmail
        # "are we signed in" is the question every errand asks first.
        "signed_in": signed_in_signal(url),
    }


def recipe_spec() -> dict[str, Any]:
    return {
        "domain": "gmail",
        "provider": "google",
        "login": {"recipe": GMAIL_LOGIN_RECIPE, "branches": GMAIL_LOGIN_BRANCHES,
                  "terminal_state": "inbox"},
        "fetch_login_code": {"recipe": GMAIL_FETCH_CODE_RECIPE,
                             "branches": GMAIL_FETCH_CODE_BRANCHES,
                             "terminal_state": "inbox"},
        "status": "The login spine is live-verified as far as we drive it (2026-07-10, passkey "
                  "sign-in on the shared google profile); password/2FA are human-required by "
                  "policy and will stay that way. The errand's navigation is one read of the inbox "
                  "list — the rules live in errands.py. The google_signin_* states are the "
                  "provider's, homed here for the members that follow.",
    }
