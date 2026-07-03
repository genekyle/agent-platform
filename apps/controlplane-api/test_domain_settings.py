"""Tests for per-domain automation posture (mode + goal switches + approval gate)."""

from __future__ import annotations

import pytest

import domain_settings


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(domain_settings, "_path", lambda: tmp_path / "domain_settings.json")


def test_defaults_when_untouched():
    s = domain_settings.get_settings("facebook_marketplace")
    assert s["automation_mode"] == "manual"     # safe default: nothing runs unattended
    assert s["goals"] == {}
    assert s["domain_id"] == "facebook_marketplace"


def test_set_and_persist_mode():
    domain_settings.put_settings("facebook_marketplace", {"automation_mode": "supervised"})
    assert domain_settings.get_settings("facebook_marketplace")["automation_mode"] == "supervised"


def test_bad_mode_is_ignored():
    domain_settings.put_settings("indeed_jobs", {"automation_mode": "supervised"})
    domain_settings.put_settings("indeed_jobs", {"automation_mode": "YOLO"})
    # the typo does not clobber the last valid value
    assert domain_settings.get_settings("indeed_jobs")["automation_mode"] == "supervised"


def test_goal_switches_merge():
    domain_settings.put_settings("facebook_marketplace", {"goals": {"create_listing": True}})
    domain_settings.put_settings("facebook_marketplace", {"goals": {"reply_to_buyer": False}})
    goals = domain_settings.get_settings("facebook_marketplace")["goals"]
    assert goals == {"create_listing": True, "reply_to_buyer": False}


def test_domains_are_isolated():
    domain_settings.put_settings("facebook_marketplace", {"automation_mode": "autopilot"})
    assert domain_settings.get_settings("indeed_jobs")["automation_mode"] == "manual"


def test_approval_required_by_mode():
    # manual → everything asks
    domain_settings.put_settings("d", {"automation_mode": "manual"})
    assert domain_settings.approval_required("d", outward_facing=False) is True
    assert domain_settings.approval_required("d", outward_facing=True) is True
    # supervised → only outward-facing asks
    domain_settings.put_settings("d", {"automation_mode": "supervised"})
    assert domain_settings.approval_required("d", outward_facing=False) is False
    assert domain_settings.approval_required("d", outward_facing=True) is True
    # autopilot → nothing asks
    domain_settings.put_settings("d", {"automation_mode": "autopilot"})
    assert domain_settings.approval_required("d", outward_facing=True) is False
