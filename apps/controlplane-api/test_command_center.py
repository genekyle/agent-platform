"""Tests for the cross-domain Command Center rollup."""

from __future__ import annotations

import pytest

import command_center
import domain_settings
import inventory


class _FakeScalars:
    def all(self):
        return []


class _FakeDB:
    """A DB that answers every query empty — the rollup must still produce a full page."""

    def scalar(self, *a, **k):
        return None

    def scalars(self, *a, **k):
        return _FakeScalars()


@pytest.fixture(autouse=True)
def _tmp_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(inventory, "_path", lambda: tmp_path / "inventory.json")
    monkeypatch.setattr(domain_settings, "_path", lambda: tmp_path / "domain_settings.json")
    from runtime import handoff as handoff_mod
    monkeypatch.setattr(handoff_mod, "_handoffs_path", lambda: tmp_path / "handoffs.jsonl")


def test_attribute_domain_by_url():
    assert command_center._attribute_domain({"url": "https://facebook.com/marketplace"}) == "facebook_marketplace"
    assert command_center._attribute_domain({"tab_url": "indeed.com/jobs"}) == "indeed_jobs"
    assert command_center._attribute_domain({"tab_url": "linkedin.com/jobs"}) == "linkedin_jobs"
    assert command_center._attribute_domain({"url": "https://example.com"}) is None


def test_platform_for_maps_domain_to_observed_job_platform():
    # The registry id and the scrape's platform tag are different strings on purpose; every
    # "which jobs are this domain's?" query needs the latter, from ONE mapping.
    assert command_center.platform_for("indeed_jobs") == "indeed"
    assert command_center.platform_for("linkedin_jobs") == "linkedin"
    # An unregistered domain reads as itself, never silently as another domain's jobs.
    assert command_center.platform_for("ziprecruiter") == "ziprecruiter"


def test_recent_activity_is_newest_first():
    feed = command_center._recent_activity([
        {"ts": "2026-07-01T00:00:00Z", "why": "older", "status": "resolved"},
        {"ts": "2026-07-03T00:00:00Z", "why": "newer", "status": "open"},
    ])
    assert [f["message"] for f in feed] == ["newer", "older"]
    assert feed[0]["kind"] == "handoff"


def test_build_summary_shape_on_empty_platform():
    summary = command_center.build_summary(_FakeDB())
    assert {d["id"] for d in summary["domains"]} == {"facebook_marketplace", "indeed_jobs",
                                                     "linkedin_jobs", "gmail"}
    for tile in summary["domains"]:
        assert tile["status"] in ("ready", "attention", "idle")
        assert tile["automation_mode"] == "manual"
        assert "primary" in tile and "chips" in tile
    assert summary["attention_open_count"] == 0
    assert summary["activity"] == []


def test_needs_attention_items_flip_tile_status(monkeypatch):
    # an inventory item flagged needs_attention should push the selling tile to "attention"
    inventory.create_item({"title": "Broken Lamp", "internal_status": "needs_attention"})
    summary = command_center.build_summary(_FakeDB())
    fb = next(d for d in summary["domains"] if d["id"] == "facebook_marketplace")
    assert fb["status"] == "attention"
    assert fb["attention_count"] >= 1
