"""Tests for the Facebook recipe URL→state mapping, describe_tab, and recipe_spec."""

from __future__ import annotations

import facebook_recipe as fb


def test_url_to_state_login_and_home():
    assert fb.map_url_to_state("https://www.facebook.com/login/") == "fb_login_wall"
    assert fb.map_url_to_state("https://www.facebook.com/?sk=welcome") == "fb_home_feed"
    assert fb.map_url_to_state("https://www.facebook.com/checkpoint/12345") == "fb_login_checkpoint"
    assert fb.map_url_to_state("https://www.facebook.com/two_step_verification/") == "fb_login_2fa"


def test_url_to_state_marketplace():
    assert fb.map_url_to_state("https://www.facebook.com/marketplace") == "fb_marketplace_home"
    assert fb.map_url_to_state("https://www.facebook.com/marketplace/create/item") == "fb_create_listing_form"
    assert fb.map_url_to_state("https://www.facebook.com/marketplace/item/987654") == "fb_listing_published"


def test_describe_tab_flags_human_branch():
    d = fb.describe_tab("https://www.facebook.com/checkpoint/1")
    assert d["state"] == "fb_login_checkpoint"
    assert d["is_branch"] is True
    assert d["human_required"] is True


def test_describe_tab_spine_step():
    d = fb.describe_tab("https://www.facebook.com/marketplace")
    assert d["state"] == "fb_marketplace_home"
    assert d["recipe_step"] == 0
    assert "fb_listing_type_picker" in d["expected_next"] or "fb_create_listing_form" in d["expected_next"]
    assert d["human_required"] is False


def test_recipe_spec_shape():
    spec = fb.recipe_spec()
    assert spec["domain"] == "facebook_marketplace"
    assert spec["login"]["terminal_state"] == "fb_home_feed"
    assert spec["create_listing"]["terminal_state"] == "fb_listing_published"
    # publish is the last actionable step before the terminal state
    steps = spec["create_listing"]["recipe"]
    assert steps[-1]["state"] == "fb_listing_published"


# --- match_login_fields: role + accessible-name, the DOM-reshuffle-proof login matcher -----------
def _login_wall_candidates():
    """A realistic CDP-AX scan of facebook.com/ logged out: the two form fields, the form's
    Log In button shipped as a <div role=button> (AX normalises it to role 'button'), plus the
    footer 'Log In' LINK and a 'Forgot password?' link that must NOT win."""
    return [
        {"role": "textbox", "name": "Email or mobile number", "backend_node_id": 11},
        {"role": "textbox", "name": "Password", "backend_node_id": 22},
        {"role": "button", "name": "Log in", "backend_node_id": 33},          # <div role=button>
        {"role": "link", "name": "Forgot password?", "backend_node_id": 44},
        {"role": "link", "name": "Log In", "backend_node_id": 55},            # footer link
        {"role": "button", "name": "Create new account", "backend_node_id": 66},
    ]


def test_match_login_fields_basic():
    m = fb.match_login_fields(_login_wall_candidates())
    assert m == {"email": 11, "password": 22, "submit": 33}


def test_match_login_fields_submit_is_role_gated_not_the_footer_link():
    # The footer 'Log In' is a link (node 55); the form's button (node 33) must win.
    m = fb.match_login_fields(_login_wall_candidates())
    assert m["submit"] == 33


def test_match_login_fields_email_and_password_are_distinct():
    m = fb.match_login_fields(_login_wall_candidates())
    assert m["email"] != m["password"]


def test_match_login_fields_missing_controls_returns_partial():
    # A checkpoint / 2FA screen has no email+password+submit trio → matcher returns what it finds.
    cands = [{"role": "button", "name": "Continue", "backend_node_id": 7},
             {"role": "textbox", "name": "Enter code", "backend_node_id": 8}]
    m = fb.match_login_fields(cands)
    assert "email" not in m and "password" not in m and "submit" not in m


def test_match_login_fields_accepts_caption_key():
    # /ax_scan returns 'name'; the raw proposer returns 'caption'. Both must work.
    cands = [{"role": "textbox", "caption": "Email or mobile number", "backend_node_id": 1},
             {"role": "textbox", "caption": "Password", "backend_node_id": 2},
             {"role": "button", "caption": "Log in", "backend_node_id": 3}]
    assert fb.match_login_fields(cands) == {"email": 1, "password": 2, "submit": 3}
