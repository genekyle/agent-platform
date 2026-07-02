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
