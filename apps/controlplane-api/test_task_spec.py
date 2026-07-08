"""Tests for TaskSpec terminal-state detection + resolution."""

from __future__ import annotations

from dataclasses import dataclass

import task_spec


@dataclass
class _Obs:
    url: str = ""
    page_text: str = ""


def test_indeed_apply_terminal_by_url():
    spec = task_spec.spec_for(task="indeed_apply")
    assert spec is not None
    assert spec.is_complete("https://smartapply.indeed.com/beta/indeedapply/form/post-apply") is True
    assert spec.is_complete("https://smartapply.indeed.com/beta/indeedapply/form/questions") is False


def test_terminal_by_text_fallback():
    spec = task_spec.spec_for(task="indeed_apply")
    assert spec.is_complete("https://x", page_text="Your application has been submitted!") is True


def test_spec_for_by_goal_alias():
    spec = task_spec.spec_for(task_goal="Create a listing for a bike")
    assert spec is not None
    assert spec.name == "facebook_create_listing"


def test_spec_for_explicit_name_wins():
    spec = task_spec.spec_for(task="facebook_login", task_goal="create a listing")
    assert spec.name == "facebook_login"


def test_spec_for_unknown_returns_none():
    assert task_spec.spec_for(task_goal="do something unmapped") is None
    assert task_spec.spec_for(task="nonexistent") is None


def test_is_done_for_builds_predicate():
    spec = task_spec.spec_for(task="facebook_create_listing")
    is_done = task_spec.is_done_for(spec)
    assert is_done(_Obs(url="https://www.facebook.com/marketplace/item/123456")) is True
    assert is_done(_Obs(url="https://www.facebook.com/marketplace/create/item")) is False


def test_is_done_for_none_is_none():
    assert task_spec.is_done_for(None) is None


def test_facebook_login_bare_domain_is_not_terminal():
    """Regression (found live 2026-07-02): facebook.com/ serves BOTH the logged-out login
    wall and the logged-in feed, so a bare-domain URL must NOT count as 'logged in' — only
    the authed composer text (or an authed-only URL) does."""
    spec = task_spec.spec_for(task="facebook_login")
    # login wall — bare domain, no composer text → NOT complete
    assert spec.is_complete("https://www.facebook.com/") is False
    assert spec.is_complete("https://www.facebook.com/?stype=lo") is False
    # authed feed — composer text present → complete
    assert spec.is_complete("https://www.facebook.com/", page_text="What's on your mind?") is True
    assert spec.is_complete("https://www.facebook.com/home.php") is True


def test_open_marketplace_resolves_by_goal_alias():
    spec = task_spec.spec_for(task_goal="Open Facebook Marketplace")
    assert spec is not None and spec.name == "facebook_open_marketplace"


def test_open_marketplace_terminal_by_url_only():
    spec = task_spec.spec_for(task="facebook_open_marketplace")
    # any /marketplace* URL = arrived (home, a location id, a category, the selling page)
    assert spec.is_complete("https://www.facebook.com/marketplace") is True
    assert spec.is_complete("https://www.facebook.com/marketplace/103703779667744") is True
    assert spec.is_complete("https://www.facebook.com/marketplace/you/selling") is True


def test_open_marketplace_home_feed_is_not_terminal():
    """The home feed's left-nav contains the word 'Marketplace', so a text signal would falsely
    report 'done' before we click. Only the /marketplace URL counts — the feed URL is facebook.com/."""
    spec = task_spec.spec_for(task="facebook_open_marketplace")
    feed_text = "Home Marketplace Groups What's on your mind? Create a post"
    assert spec.is_complete("https://www.facebook.com/", page_text=feed_text) is False
