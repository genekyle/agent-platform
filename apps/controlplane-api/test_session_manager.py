"""Tests for the session-manager guardrails and live-view assembly."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import session_manager


def _session(**over):
    base = dict(
        id=1, domain_id="indeed_jobs", account_id="indeed_default", persistent_profile="indeed",
        chrome_debug_port=9323, chrome_process_pid=4321, status="active", purpose="production",
        goal_id="search_jobs", protected=False,
        started_at=datetime(2026, 7, 4, tzinfo=timezone.utc),
        created_at=datetime(2026, 7, 4, tzinfo=timezone.utc),
    )
    base.update(over)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- may_touch
def test_protected_blocks_disruptive_without_force():
    for action in session_manager.DISRUPTIVE_ACTIONS:
        allowed, reason = session_manager.may_touch(protected=True, action=action)
        assert allowed is False
        assert reason and action in reason


def test_force_overrides_protection():
    allowed, reason = session_manager.may_touch(protected=True, action="delete", force=True)
    assert allowed is True and reason is None


def test_unprotected_is_always_allowed():
    allowed, reason = session_manager.may_touch(protected=False, action="stop")
    assert allowed is True and reason is None


def test_non_disruptive_action_allowed_even_when_protected():
    # Attaching/observing a protected session is fine — only destructive actions are gated.
    allowed, _ = session_manager.may_touch(protected=True, action="observe")
    assert allowed is True


# --------------------------------------------------------------------------- classify_liveness
def test_liveness_live_beats_stale_status():
    # A reachable browser is live regardless of what the row claims.
    assert session_manager.classify_liveness(status="stopped", cdp_reachable=True) == "live"


def test_liveness_stale_when_row_says_active_but_dead():
    # The dangerous case the old status-only checks missed.
    assert session_manager.classify_liveness(status="active", cdp_reachable=False) == "stale"


def test_liveness_stopped_when_row_and_probe_agree():
    assert session_manager.classify_liveness(status="stopped", cdp_reachable=False) == "stopped"


# --------------------------------------------------------------------------- operational_state
def test_closed_browser_is_not_presented_as_an_active_session():
    assert session_manager.operational_state(
        status="active", cdp_reachable=False, process_alive=False,
    ) == "closed"


def test_live_browser_overrules_a_terminal_database_row_but_is_called_orphaned():
    assert session_manager.operational_state(
        status="stopped", cdp_reachable=True, process_alive=True,
    ) == "orphaned"


def test_running_process_with_dead_cdp_is_degraded_not_closed():
    assert session_manager.operational_state(
        status="active", cdp_reachable=False, process_alive=True,
    ) == "degraded"


def test_one_running_session_owns_a_port_reused_by_history():
    rows = [
        _session(id=7, status="active", chrome_debug_port=9323),
        _session(id=6, status="active", chrome_debug_port=9323),
        _session(id=5, status="stopped", chrome_debug_port=9323),
    ]
    assert session_manager.port_owners(rows) == {9323: 7}


# --------------------------------------------------------------------------- view_row
def test_view_row_shape_and_labels():
    row = session_manager.view_row(
        _session(), cdp_reachable=True, account_label="Indeed — default", tab_count=2,
    )
    assert row["id"] == 1
    assert row["live"] is True
    assert row["liveness"] == "live"
    assert row["operational_state"] == "live"
    assert row["profile_kind"] == "persistent"
    assert row["account_label"] == "Indeed — default"
    assert row["tab_count"] == 2
    assert row["protected"] is False


def test_view_row_throwaway_profile_kind():
    row = session_manager.view_row(_session(persistent_profile=None), cdp_reachable=False)
    assert row["profile_kind"] == "throwaway"
    assert row["liveness"] == "stale"  # status active but not reachable


# --------------------------------------------------------------------------- holdings
# What a session still CARRIES, so "which session should I be in?" is answerable from the list
# instead of by opening each one. The list is polled, so this reads the blackboard only.
def test_holdings_counts_unfinished_and_submitted():
    h = session_manager.holdings({"apply_queue": {"steps": [
        {"job_id": "a", "title": "Clinical Reporting Analyst", "done": True, "terminal": "submitted"},
        {"job_id": "b", "title": "Data Business Analyst", "done": False, "terminal": None},
    ]}})
    assert h == {"unfinished": 1, "submitted": 1, "titles": ["Data Business Analyst"]}


def test_holdings_counts_parked_as_unfinished():
    """A parked step is DONE with a terminal flag, but it is not finished work — closing over it
    is what the close-out confirm exists to prevent, so it must show up here too."""
    h = session_manager.holdings({"apply_queue": {"steps": [
        {"job_id": "c", "title": "Walled", "done": True, "terminal": "parked:account_wall"},
    ]}})
    assert h["unfinished"] == 1


def test_holdings_folds_parked_apps_that_outlived_their_queue_without_double_counting():
    """`parked_apps` deliberately outlive their queue, so a session holding only those would read
    as empty — but one that is in BOTH is still one application."""
    world = {"apply_queue": {"steps": [{"job_id": "b", "title": "Both", "done": False}]},
             "parked_apps": [{"job_id": "b", "title": "Both"}, {"job_id": "z", "title": "Only"}]}
    h = session_manager.holdings(world)
    assert h["unfinished"] == 2
    assert h["titles"] == ["Both", "Only"]


def test_holdings_empty_for_a_session_that_never_worked():
    assert session_manager.holdings(None) == {"unfinished": 0, "submitted": 0, "titles": []}
    assert session_manager.holdings({})["unfinished"] == 0
