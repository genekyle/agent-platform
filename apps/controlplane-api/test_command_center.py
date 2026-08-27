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


def test_a_broken_errand_reader_is_named_not_a_plausible_zero(monkeypatch):
    # Seam-audit finding (2026-08-23): "no escalations" and "the reader is broken" rendered
    # identically as zeros. The failure path must NAME itself — the label and a warn chip carry
    # it, because the UI renders a null value as 0 either way.
    import errand_log

    def _broken():
        raise RuntimeError("reader down")

    monkeypatch.setattr(errand_log, "recent_stats", _broken)
    tile = command_center._errand_metrics()
    assert tile["primary"]["value"] is None
    assert "unreachable" in tile["primary"]["label"]
    assert any(c.get("warn") and "unreachable" in c["label"] for c in tile["chips"])
    assert tile["needs_attention"] == 0


# --------------------------------------------------------------------------------------------
# The landing page counts sessions waiting on the operator (2026-08-27)
# --------------------------------------------------------------------------------------------

def _reviewed_ledger(page=1, picks_made=False):
    import session_checkpoints as cps
    ledger = cps.Ledger()
    ledger.mark(cps.page_rung(page).id, evidence="reviewed")
    if picks_made:
        ledger.mark(cps.select_rung(page).id, evidence="picked by operator")
    return ledger.as_dict()


def test_a_reviewed_page_with_no_picks_is_a_session_waiting_on_you():
    """Session 34 held 25 extracted results at `awaiting: choose` while the Overview said
    'Nothing needs your judgment right now' — every counter was true and none counted this."""
    import command_center as cc

    wait = cc.awaiting_of({}, _reviewed_ledger(page=1, picks_made=False))
    assert wait and wait["awaiting"] == "choose" and wait["needs"] == "answer"
    assert "Page 1" in wait["detail"]


def test_picks_made_clears_the_wait():
    import command_center as cc

    assert cc.awaiting_of({}, _reviewed_ledger(page=1, picks_made=True)) is None


def test_an_application_mid_flight_outranks_the_page_wait():
    """A queue step is the nearer wait, and its kind says WHAT it waits for: a NEEDS_OPERATOR
    flag means a judgment; anything else means a Run press."""
    import apply_steps as aps
    import command_center as cc

    q = aps.Queue(page=1)
    q.enqueue([{"job_id": "linkedin:1", "title": "Cost Analyst"}])
    world = {"apply_queue": q.as_dict()}
    wait = cc.awaiting_of(world, _reviewed_ledger(page=1, picks_made=False))
    assert wait["awaiting"] == "apply" and wait["needs"] == "run"
    assert wait["detail"] == "Cost Analyst"


def test_a_blocked_step_needs_an_answer_not_a_press():
    import apply_steps as aps
    import command_center as cc

    q = aps.Queue(page=1)
    q.enqueue([{"job_id": "linkedin:1", "title": "Cost Analyst"}])
    q.current().record("account", aps.BLOCKED, "account wall", initiator="system")
    wait = cc.awaiting_of({"apply_queue": q.as_dict()}, None)
    assert wait["awaiting"] == "apply" and wait["needs"] == "answer"


def test_a_quiet_session_reports_nothing():
    import command_center as cc

    assert cc.awaiting_of({}, None) is None
    assert cc.awaiting_of(None, None) is None
