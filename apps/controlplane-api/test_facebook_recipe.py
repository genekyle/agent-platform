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
