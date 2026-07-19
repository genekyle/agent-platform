"""Tests for the reasoning login loop — the classifier and the HARD SAFETY RAILS (MFA/captcha/
wrong-password always escalate and can't be reasoned around), which is the whole point of adding
reasoning to a sensitive flow."""

from __future__ import annotations

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
