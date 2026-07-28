"""Google as an IDENTITY, and the boundary that makes one-click SSO possible without giving away
the credential.

Two similar-looking processes had to be told apart (live, session #22, 2026-07-27):

  * the DOMAIN's login process — LinkedIn's own logged-out wall, its "Continue with google" button,
    its signed-in check. Tested in test_session_control.
  * the IDENTITY's login process — Google's chooser, consent and credential screens, shared by
    every site that offers SSO. That is this file.

The single idea both rest on: on accounts.google.com the boundary is the STATE, not the host.
"""

from __future__ import annotations

import google_recipe as gr


def _c(role, name, node=1):
    return {"role": role, "name": name, "backend_node_id": node}


# --- classification ---------------------------------------------------------------------------
def test_the_chooser_is_recognised_before_the_generic_signin_rules():
    """Every challenge path lives under /signin, so a bare rule placed first swallows them. The
    chooser is the one-click path and must not be read as 'asking for an email address'."""
    assert gr.classify("https://accounts.google.com/gsi/select?client_id=x") == gr.CHOOSER
    assert gr.classify("https://accounts.google.com/signinchooser?flow=y") == gr.CHOOSER
    assert gr.classify("https://accounts.google.com/v3/signin/identifier?x=1") == gr.EMAIL
    assert gr.classify("https://accounts.google.com/v3/signin/challenge/pwd") == gr.PASSWORD
    assert gr.classify("https://accounts.google.com/signin/v2/challenge/totp") == gr.TWO_FACTOR
    assert gr.classify("https://accounts.google.com/signin/oauth/consent?x") == gr.CONSENT


def test_text_promotes_a_screen_the_url_under_reports_but_never_overrides_a_challenge():
    """Google re-renders the chooser under the identifier URL. Text may promote that — but a URL
    that explicitly says 'password' is the thing Google cannot fake by re-rendering."""
    ident = "https://accounts.google.com/v3/signin/identifier"
    assert gr.classify(ident, "Choose an account to continue to LinkedIn") == gr.CHOOSER
    pwd = "https://accounts.google.com/v3/signin/challenge/pwd"
    assert gr.classify(pwd, "Choose an account") == gr.PASSWORD      # URL wins over the text


def test_a_page_that_is_not_google_is_not_this_modules_business():
    assert gr.classify("https://www.linkedin.com/jobs/", "Choose an account") == gr.UNKNOWN


# --- the policy that is the whole point -------------------------------------------------------
def test_choosing_among_your_own_accounts_is_ours_and_the_credential_never_is():
    assert gr.policy_for(gr.CHOOSER) == gr.AUTO
    for state in (gr.EMAIL, gr.PASSWORD, gr.TWO_FACTOR, gr.BLOCKED):
        assert gr.policy_for(state) == gr.HUMAN, state
    assert gr.may_drive(gr.CHOOSER) is True
    assert gr.may_drive(gr.PASSWORD) is False
    assert gr.may_drive(gr.PASSWORD, approved=True) is False     # approval cannot buy a credential


def test_granting_access_is_approval_gated_not_automatic():
    """Consent is reversible but it IS a grant — same gate as applying and publishing."""
    assert gr.policy_for(gr.CONSENT) == gr.APPROVAL
    assert gr.may_drive(gr.CONSENT) is False
    assert gr.may_drive(gr.CONSENT, approved=True) is True
    plan = gr.next_action(gr.CONSENT, [_c("button", "Continue")])
    assert plan["action"] == "escalate" and plan["needs_approval"] is True
    approved = gr.next_action(gr.CONSENT, [_c("button", "Continue")], approved=True)
    assert approved["action"] == "click" and approved["target"]["name"] == "Continue"


def test_an_unmet_screen_on_the_identity_provider_stops():
    """The one place a confident guess is least affordable."""
    assert gr.policy_for("google_something_new") == gr.HUMAN
    assert gr.next_action(gr.UNKNOWN, [])["action"] == "escalate"


# --- picking the RIGHT account ----------------------------------------------------------------
def test_the_chooser_picks_the_account_we_were_asked_for():
    tiles = [_c("link", "Work Account\nwork@example.com", 1),
             _c("link", "Personal\nperson@example.com", 2)]
    plan = gr.next_action(gr.CHOOSER, tiles, username="person@example.com")
    assert plan["action"] == "click" and plan["target"]["backend_node_id"] == 2


def test_the_chooser_refuses_to_fall_back_to_the_first_account():
    """Signing into the WRONG Google account does not fail loudly — it succeeds, and every capture
    after it is attributed to the wrong identity. So a miss escalates rather than guesses."""
    tiles = [_c("link", "Work Account\nwork@example.com", 1),
             _c("link", "Other\nother@example.com", 2)]
    plan = gr.next_action(gr.CHOOSER, tiles, username="nobody@example.com")
    assert plan["action"] == "escalate"
    assert "nobody@example.com" in plan["why"]


def test_with_no_username_the_chooser_only_answers_when_the_choice_is_unambiguous():
    one = [_c("link", "Only\nonly@example.com", 1)]
    assert gr.find_account_tile(one, "") is not None
    two = [_c("link", "A\na@example.com", 1), _c("link", "B\nb@example.com", 2)]
    assert gr.find_account_tile(two, "") is None


# --- the shared vocabulary --------------------------------------------------------------------
def test_state_ids_are_shared_with_the_gmail_recipe_not_forked():
    """The same screen must not have two names depending on who looked at it, or the corpus splits
    and neither half trains."""
    import gmail_recipe
    for url, state in (("https://accounts.google.com/v3/signin/identifier", gr.EMAIL),
                       ("https://accounts.google.com/v3/signin/challenge/pwd", gr.PASSWORD),
                       ("https://accounts.google.com/signin/v2/challenge/totp", gr.TWO_FACTOR)):
        assert gmail_recipe.map_url_to_state(url) == state, url


def test_the_spec_states_the_boundary_out_loud():
    spec = gr.spec()
    by_state = {r["state"]: r["policy"] for r in spec["states"]}
    assert by_state[gr.CHOOSER] == gr.AUTO
    assert by_state[gr.PASSWORD] == gr.HUMAN
    assert "STATE, not the host" in spec["note"]
