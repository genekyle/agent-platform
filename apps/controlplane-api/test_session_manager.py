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


# --------------------------------------------------------------------------- view_row
def test_view_row_shape_and_labels():
    row = session_manager.view_row(
        _session(), cdp_reachable=True, account_label="Indeed — default", tab_count=2,
    )
    assert row["id"] == 1
    assert row["live"] is True
    assert row["liveness"] == "live"
    assert row["profile_kind"] == "persistent"
    assert row["account_label"] == "Indeed — default"
    assert row["tab_count"] == 2
    assert row["protected"] is False


def test_view_row_throwaway_profile_kind():
    row = session_manager.view_row(_session(persistent_profile=None), cdp_reachable=False)
    assert row["profile_kind"] == "throwaway"
    assert row["liveness"] == "stale"  # status active but not reachable
