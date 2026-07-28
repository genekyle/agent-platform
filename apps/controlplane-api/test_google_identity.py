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
def test_the_line_is_drawn_at_the_secret_not_at_the_host():
    """LEARNINGS 2026-07-09 put it here: everything up to and after Google's auth we drive; the
    PASSWORD and second factor are the deliberate hand-off. The account ADDRESS is a username —
    already a display hint, already printed on every chooser tile — so refusing it too stopped the
    flow a screen early for no gain."""
    assert gr.policy_for(gr.CHOOSER) == gr.AUTO
    assert gr.policy_for(gr.EMAIL) == gr.AUTO
    for state in (gr.PASSWORD, gr.TWO_FACTOR, gr.BLOCKED):
        assert gr.policy_for(state) == gr.HUMAN, state
    assert gr.may_drive(gr.CHOOSER) is True
    assert gr.may_drive(gr.PASSWORD) is False
    assert gr.may_drive(gr.PASSWORD, approved=True) is False     # approval cannot buy a credential


def test_the_identifier_step_types_keystrokes_and_submits_with_enter():
    """Per-stack tailoring, and the reason the recipe is data. Google's identifier is a controlled
    input in its own view layer: an assigned value leaves the internal model empty, Next re-renders
    the same screen, and nothing errors. The opposite choice is right on React inputs — which is
    exactly why it cannot be a global default."""
    cands = [_c("textbox", "Email or phone", 7), _c("button", "Next", 9)]
    plan = gr.next_action(gr.EMAIL, cands, username="a@example.com")
    assert plan["action"] == "type" and plan["policy"] == gr.AUTO
    assert plan["target"]["backend_node_id"] == 7
    assert plan["type_style"] == gr.TYPE_STYLE_KEYSTROKES
    assert plan["submit"]["name"] == "Next"          # a CLICK; there is no press intent


def test_the_identifier_step_refuses_to_type_into_a_form_it_cannot_submit():
    """Typing an address and stranding it looks identical to a page that refused us — and the
    first live attempt produced exactly that, from a submit that dispatched into nothing."""
    assert gr.next_action(gr.EMAIL, [_c("textbox", "Email or phone", 7)],
                          username="a@example.com")["action"] == "escalate"


def test_the_identifier_step_will_not_invent_an_address():
    """No stored login means no answer to give — never a guess at whose account this is."""
    cands = [_c("textbox", "Email or phone", 7)]
    assert gr.next_action(gr.EMAIL, cands, username="")["action"] == "escalate"


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


def test_the_passkey_challenge_is_recognised_under_the_identifier_url():
    """MEASURED live (session #22): once the address is accepted Google renders the passkey
    challenge under the UNCHANGED identifier URL, with "Verifying it's you… Complete sign-in using
    your passkey". The first version of the tells matched the phrase "verify it's you" and the
    screen says "VerifyING" — so a screen we may never touch classified as one we may drive. This
    is the exact string that got past it."""
    url = "https://accounts.google.com/v3/signin/identifier?x=1"
    text = ("Hi Geno genomags@gmail.com Verifying it’s you... "
            "Complete sign-in using your passkey Try another way")
    assert gr.classify(url, text) == gr.TWO_FACTOR
    assert gr.policy_for(gr.TWO_FACTOR) == gr.HUMAN
    assert gr.next_action(gr.TWO_FACTOR, [], username="genomags@gmail.com")["action"] == "escalate"


def test_a_passkey_prompt_is_2fa_however_it_is_worded():
    for text in ("Complete sign-in using your passkey", "Use your security key",
                 "Get a verification code", "2-Step Verification"):
        assert gr.classify("https://accounts.google.com/v3/signin/identifier", text) == gr.TWO_FACTOR, text


# --- the clock, which is the only thing that can tell live from dead ---------------------------
def test_an_expired_challenge_is_indistinguishable_so_only_time_can_say():
    """MEASURED session #22: the passkey prompt is a NATIVE dialog, it expired on its own, and the
    page behind it did not change — same URL, same accessible tree, same heading, renderer clear.
    No probe we own can tell a live challenge from a dead one, so elapsed time is the only honest
    signal and the copy has to say so instead of implying the screen is worth acting on."""
    assert gr.challenge_age_note(None) == ""
    assert gr.challenge_age_note(10) == ""
    note = gr.challenge_age_note(600)
    assert "timed out" in note and "Try another way" in note


def test_the_alternatives_fork_is_offered_and_not_taken():
    """'Try another way' is a click, not a credential — but WHICH way to verify is the operator's
    choice. Picking a verification method on someone's behalf is not ours to do."""
    alt = gr.find_alternative_control([{"role": "button", "name": "Try another way"}])
    assert alt is not None
    # ...and it is never turned into an action by the planner
    assert gr.next_action(gr.TWO_FACTOR, [{"role": "button", "name": "Try another way"}],
                          username="a@b.com")["action"] == "escalate"
