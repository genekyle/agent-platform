"""Tests for the reasoning login loop — the classifier and the HARD SAFETY RAILS (MFA/captcha/
wrong-password always escalate and can't be reasoned around), which is the whole point of adding
reasoning to a sensitive flow.

Plus the STALE TAB path (2026-07-19): the pinned CDP target can vanish mid-drive, and the capture
server reports that as a *successful* empty scan — so it must be detected explicitly, recovered
once, and otherwise escalated honestly."""

from __future__ import annotations

import asyncio

import login_reasoner as lr


def _tb(name, nid):
    return {"role": "textbox", "name": name, "backend_node_id": nid}


def _btn(name, nid):
    return {"role": "button", "name": name, "backend_node_id": nid}


SIGNIN = [_tb("Email Address", 1), _tb("Password", 2), _btn("Sign In", 3)]
CREATE = [_tb("Email Address", 1), _tb("Password", 2), _tb("Verify New Password", 3), _btn("Create Account", 4)]


def test_classify_distinguishes_the_gray_areas():
    assert lr.classify_login_state([], "Candidate Home — My Applications", logged_in=True) == "authenticated"
    assert lr.classify_login_state(SIGNIN, "Sign out", None) == "authenticated"          # text wins
    assert lr.classify_login_state(SIGNIN, "Please enter the verification code we sent", None) == "mfa"
    assert lr.classify_login_state(SIGNIN, "Please complete the reCAPTCHA", None) == "captcha"
    assert lr.classify_login_state(SIGNIN, "An account already exists for this email address", None) == "account_exists"
    assert lr.classify_login_state(SIGNIN, "The password you entered is incorrect", None) == "login_error"
    assert lr.classify_login_state(SIGNIN, "Sign in to your account", None) == "signin_form"
    assert lr.classify_login_state(CREATE, "Create your account", None) == "create_form"
    assert lr.classify_login_state([_btn("Apply Manually", 9)], "Start your application", None) == "unknown"


def test_safety_rails_cannot_be_reasoned_around():
    # A reasoner that WANTS to push forward is ignored for captcha / MFA / errors.
    yes = lambda obs: {"action": "fill_credentials", "rationale": "let's go"}
    for state, status in [("captcha", "captcha"), ("mfa", "mfa"), ("login_error", "bad_credentials")]:
        step = lr.reason_step(state, SIGNIN, "x", has_creds=True, attempted_creds=False, reasoner=yes)
        assert step.escalate and step.escalate_status == status

    # Authenticated is always 'done'.
    assert lr.reason_step("authenticated", [], "Sign out", has_creds=True, attempted_creds=False).action == "done"


def test_never_retries_a_rejected_password():
    # Once creds were attempted and we're not authenticated, escalate — never a second attempt.
    step = lr.reason_step("signin_form", SIGNIN, "Sign in", has_creds=True, attempted_creds=True)
    assert step.escalate and step.escalate_status == "bad_credentials"


def test_deterministic_policy_handles_signin_and_account_exists():
    assert lr.reason_step("signin_form", SIGNIN, "Sign in", has_creds=True, attempted_creds=False).action == "fill_credentials"

    # account_exists WITH a Sign In control -> click it (switch to sign-in), don't try to create.
    cands = [_btn("Sign In", 7)]
    step = lr.reason_step("account_exists", cands, "already have an account", has_creds=True, attempted_creds=False)
    assert step.action == "click" and step.control["backend_node_id"] == 7

    # account_exists WITHOUT a Sign In control -> escalate cleanly.
    step = lr.reason_step("account_exists", [], "already have an account", has_creds=True, attempted_creds=False)
    assert step.escalate and step.escalate_status == "account_exists"


def test_find_login_fields_submit_is_robust():
    # named "Sign In" — found.
    assert lr.find_login_fields(SIGNIN) == {"email": 1, "password": 2, "submit": 3}
    # submit named "Continue" (not "Sign In") — the rigid matcher missed this on Workday; now found.
    assert lr.find_login_fields([_tb("Email", 1), _tb("Password", 2), _btn("Continue", 5)])["submit"] == 5
    # a generically-named primary — falls back to the LAST button on the form.
    fields = lr.find_login_fields([_tb("Email", 1), _tb("Password", 2), _btn("Show password", 8), _btn("Foobar", 9)])
    assert fields["submit"] == 9


def test_reasoner_click_resolves_and_bad_output_falls_back():
    # A reasoner naming a real control -> click it.
    r = lambda obs: {"action": "click", "control": "sign in", "rationale": "switch to sign-in"}
    step = lr.reason_step("account_exists", [_btn("Sign In", 5)], "already exists", has_creds=True,
                          attempted_creds=False, reasoner=r)
    assert step.action == "click" and step.control["backend_node_id"] == 5

    # A reasoner asking to fill where there's NO password field -> falls back (can't fill thin air).
    r2 = lambda obs: {"action": "fill_credentials", "rationale": "fill it"}
    step = lr.reason_step("unknown", [_btn("Apply Manually", 1)], "start", has_creds=True,
                          attempted_creds=False, reasoner=r2)
    assert step.action == "escalate"           # deterministic fallback for unknown


# --- the stale tab ------------------------------------------------------------------------------
#: What /ax_scan ACTUALLY returns when the pinned tab is gone: ok:true, zero candidates, and the
#: real reason buried in errors[]. Indistinguishable from "no form here" unless you look.
STALE_SCAN = {"ok": True, "count": 0, "candidates": [],
              "errors": ["target_discovery: No target with id 'DEAD1' — refusing to fall back to "
                         "another tab."]}
LIVE_SCAN = {"ok": True, "count": 3, "candidates": SIGNIN, "errors": []}


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeClient:
    """Stands in for httpx.AsyncClient. Canned per-path replies (queues that repeat their last
    entry once exhausted) plus a call log, so we can assert WHICH tab each call addressed."""

    def __init__(self, *, scans, probes=None, auths=None, executes=None):
        self.calls = []                       # (path, payload)
        self._q = {
            "/ax_scan": list(scans),
            "/probe": list(probes or [{"ok": True, "value": "Sign in to your account"}]),
            "/auth_state": list(auths or [{"ok": True, "logged_in": None}]),
            "/execute": list(executes or [{"outcome": "ok"}]),
        }

    def _next(self, path):
        q = self._q.get(path) or [{}]
        return q.pop(0) if len(q) > 1 else q[0]

    async def post(self, url, json=None):
        path = url[url.rfind("/"):]
        self.calls.append((path, dict(json or {})))
        return _Resp(self._next(path))

    def tabs_used(self, path):
        return [p.get("tab_id") for c, p in self.calls if c == path]


def _run(client, *, re_resolve=None, max_steps=3):
    return asyncio.run(lr.run_login(
        client=client, capture_url="http://cap", browser_url="http://127.0.0.1:9328",
        tab_id="DEAD1", username="u@example.com", password="pw",
        re_resolve=re_resolve, max_steps=max_steps))


def test_mentions_stale_tab_reads_every_shape_the_server_uses():
    assert lr._mentions_stale_tab(STALE_SCAN)                                     # ax_scan errors[]
    assert lr._mentions_stale_tab({"ok": False, "detail": "No target with id 'X'"})  # auth_state
    assert lr._mentions_stale_tab({"outcome": "error", "detail": "no attachable page target"})  # execute
    assert not lr._mentions_stale_tab({"ok": True, "candidates": [], "errors": []})  # genuinely no form
    assert not lr._mentions_stale_tab(None)


def test_stale_tab_is_not_mistaken_for_a_missing_form():
    """The bug: a dead tab read as 'no form here' and died as no_login_form/max_steps, telling the
    operator nothing useful. It must now be named."""
    client = FakeClient(scans=[STALE_SCAN])
    result = _run(client, re_resolve=lambda: None)
    assert result.ok is False
    assert result.status == "stale_tab"          # NOT no_login_form, NOT max_steps
    assert "no longer exists" in result.detail   # a real, actionable message
    assert result.trail[-1]["state"] == "stale_tab"


def test_stale_tab_re_resolves_once_and_carries_on():
    """The backup plan: re-find the live tab and keep going, addressing the FRESH tab id."""
    calls = []

    def re_resolve():
        calls.append(1)
        return {"browser_url": "http://127.0.0.1:9400", "tab_id": "FRESH"}

    client = FakeClient(
        scans=[STALE_SCAN, LIVE_SCAN],
        auths=[{"ok": True, "logged_in": None}, {"ok": True, "logged_in": True}],
    )
    result = _run(client, re_resolve=re_resolve)
    assert len(calls) == 1                       # re-resolved exactly once
    assert result.ok is True and result.status == "authenticated"
    # the drive switched tabs: first scan hit the dead id, the next hit the fresh one
    assert client.tabs_used("/ax_scan") == ["DEAD1", "FRESH"]


def test_stale_tab_re_resolve_happens_at_most_once():
    """A re-resolve that keeps landing on a dead tab must escalate, not loop forever."""
    calls = []

    def re_resolve():
        calls.append(1)
        return {"browser_url": "http://127.0.0.1:9400", "tab_id": "ALSO_DEAD"}

    client = FakeClient(scans=[STALE_SCAN, STALE_SCAN, STALE_SCAN])
    result = _run(client, re_resolve=re_resolve)
    assert len(calls) == 1                       # the one-shot latch held
    assert result.status == "stale_tab"


def test_stale_tab_mid_fill_never_blames_the_stored_password():
    """If the tab dies while the credentials are being typed they never landed — reporting
    bad_credentials there would tell the operator their password is wrong when it isn't."""
    client = FakeClient(
        scans=[LIVE_SCAN],                                  # a real sign-in form is visible
        executes=[{"outcome": "error", "detail": "No target with id 'DEAD1'"}],
    )
    result = _run(client, re_resolve=lambda: None)
    assert result.status == "stale_tab"                     # NOT bad_credentials
    assert "password" not in result.detail.lower()


def test_no_re_resolve_still_escalates_honestly():
    """Without the backup plan wired, a stale tab must still be named rather than swallowed."""
    result = _run(FakeClient(scans=[STALE_SCAN]), re_resolve=None)
    assert result.status == "stale_tab"


# --- no credential, but a way around one -------------------------------------------------------
# MEASURED live 2026-07-30 on LinkedIn's logged-out /jobs page: 118 AX candidates, a password form,
# and "Continue with google" beside it. This account has no LinkedIn password at all — it signs in
# with Google — and the policy still answered "fill the stored credentials and submit".
_LI_LOGGED_OUT = [
    {"role": "textbox", "name": "Email or phone", "backend_node_id": 11},
    {"role": "textbox", "name": "Password", "backend_node_id": 12},
    {"role": "button", "name": "Continue with google", "backend_node_id": 13},
    {"role": "link", "name": "Sign in", "backend_node_id": 14},
]


def test_a_password_form_on_screen_is_not_a_reason_to_fill_it():
    """The form being visible says nothing about whether we hold anything to put in it."""
    step = lr.reason_step("signin_form", _LI_LOGGED_OUT, "", has_creds=False, attempted_creds=False)
    assert step.action == "click"
    assert step.control["name"] == "Continue with google"
    assert "no stored credential" in step.rationale


def test_with_a_stored_credential_the_form_is_still_the_route():
    """SSO is the fallback for having no password, not a preference over having one."""
    step = lr.reason_step("signin_form", _LI_LOGGED_OUT, "", has_creds=True, attempted_creds=False)
    assert step.action == "fill_credentials"


def test_no_credential_and_no_way_around_it_escalates_rather_than_guessing():
    only_form = [c for c in _LI_LOGGED_OUT if c["name"] in ("Email or phone", "Password")]
    step = lr.reason_step("signin_form", only_form, "", has_creds=False, attempted_creds=False)
    assert step.action == "escalate" and step.escalate_status == "no_credentials"
    assert "never type passwords" in step.reason.lower()


def test_a_reasoner_cannot_invent_a_fill_from_no_credential():
    """The rail sits on both paths — the deterministic policy AND anything a model proposes."""
    step = lr.reason_step("signin_form", _LI_LOGGED_OUT, "", has_creds=False, attempted_creds=False,
                          reasoner=lambda _obs: {"action": "fill_credentials", "rationale": "just do it"})
    assert step.action != "fill_credentials"
    assert step.control["name"] == "Continue with google"


# ---------------------------------------------------------------------------------------------
# IDENTIFIER-FIRST SCREENS. Every state above `signin_form` is gated on a password field, so the
# most common modern login shape — email now, secret on the next screen — was unclassifiable by
# construction and arrived as "unknown". Verbatim from secure.indeed.com/auth, 2026-07-30.
# ---------------------------------------------------------------------------------------------

_INDEED_AUTH = [
    {"role": "heading", "name": "Ready to take the next step?", "backend_node_id": 1},
    {"role": "button", "name": "Continue with Apple", "backend_node_id": 1716},
    {"role": "region", "name": "Google sign in", "backend_node_id": 1837},
    {"role": "button", "name": "Continue", "backend_node_id": 1707},
    {"role": "textbox", "name": "Email address", "backend_node_id": 1703},
    # Google Identity Services renders its button in a cross-origin frame: AX offers the FRAME and
    # never the button inside it. This is the node the cockpit was silently dropping.
    {"role": "Iframe", "name": "Sign in with Google Button", "backend_node_id": 1718},
]


def test_an_email_first_screen_is_not_unknown():
    # It has no password field anywhere, and that used to be the end of the story.
    assert not lr._has_password_field(_INDEED_AUTH)
    assert lr.classify_login_state(_INDEED_AUTH, "create an account or sign in") == "identifier_form"


def test_a_password_screen_still_outranks_the_identifier_box():
    # Both boxes on one screen must stay `signin_form` — identifier-first is the state where the
    # secret is ABSENT, not merely accompanied.
    both = _INDEED_AUTH + [{"role": "textbox", "name": "Password", "backend_node_id": 9}]
    assert lr.classify_login_state(both, "") == "signin_form"


def test_an_iframed_sso_button_is_offered_but_marked_as_ours_to_press():
    # Named, not hidden: the operator is looking straight at it. Flagged, not clickable: a click
    # on the frame node lands on nothing and would come back ok.
    entries = {e["name"]: e for e in lr.find_signin_entries(_INDEED_AUTH)}
    assert "Sign in with Google Button" in entries, "a visible route must never be silently dropped"
    assert entries["Sign in with Google Button"]["operator_only"] is True
    assert "cannot click it for you" in entries["Sign in with Google Button"]["why"]
    assert not entries["Continue with Apple"].get("operator_only")


def test_holding_a_credential_does_not_detour_through_someone_elses_provider():
    # THE HAZARD THIS GUARDS. Google is iframe'd and un-clickable here, so "first drivable
    # alternate" resolves to Apple — a provider this account may have no relationship with, on a
    # real account, chosen only because the preferred one was unreachable. With a credential in
    # hand the identifier box is our route, so the honest move is to hand over.
    step = lr._deterministic_policy("identifier_form", _INDEED_AUTH, has_creds=True)
    assert step.action == "escalate" and step.escalate_status == "identifier_form"
    assert "email-first" in step.reason
    step_no_creds = lr._deterministic_policy("identifier_form", _INDEED_AUTH, has_creds=False)
    assert step_no_creds.action == "click"
    assert step_no_creds.control["name"] == "Continue with Apple"


def test_the_unknown_escalation_is_still_there_for_genuinely_unreadable_screens():
    # Naming identifier-first must not swallow the real catch-all: a page with neither a secret
    # nor an identifier is still something we refuse to guess at.
    blank = [{"role": "heading", "name": "Something went wrong", "backend_node_id": 1}]
    assert lr.classify_login_state(blank, "something went wrong") == "unknown"
    step = lr._deterministic_policy("unknown", blank, has_creds=True)
    assert step.escalate_status == "unknown_state"
