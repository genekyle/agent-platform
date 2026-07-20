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
