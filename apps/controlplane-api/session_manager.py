"""Session manager — one honest, live view of every browser session the platform owns, and the
guardrails that keep a new run from disturbing a live one.

Each training / channel session already runs in its OWN Chrome: a distinct remote-debugging port
and a distinct user-data-dir (a persistent profile, or a throwaway). That isolates sessions by
construction. What was missing is *cognizance* — a single place that answers:

  * which sessions are actually ALIVE right now (probed over CDP, not guessed from a DB `status`
    that goes stale the moment the API restarts while the Chrome keeps running),
  * what account / profile / port each one is, so two runs can't silently land on the same one,
  * which sessions are HUMAN-OWNED and must never be stopped, relaunched, or deleted by an
    automated step.

This module holds the pure, testable pieces. The db-touching launch/stop wiring stays in main.py
where the Chrome helpers and the session model already live.
"""

from __future__ import annotations

from typing import Any, Optional

# Actions that could disrupt or destroy a running session. A protected session refuses all of
# these unless the caller explicitly passes force=True.
DISRUPTIVE_ACTIONS = ("stop", "relaunch", "reap", "delete")


def may_touch(*, protected: bool, action: str, force: bool = False) -> tuple[bool, Optional[str]]:
    """Guard decision: may a `action` proceed against a session?

    Returns ``(allowed, reason)``. A human-owned (``protected``) session blocks every disruptive
    action unless ``force`` is set — this is the "don't let a new run kill my live session" rule.
    Non-disruptive actions (anything not in ``DISRUPTIVE_ACTIONS``) are always allowed.
    """
    if action not in DISRUPTIVE_ACTIONS:
        return True, None
    if protected and not force:
        return (
            False,
            f"session is protected (human-owned); refusing to {action} it without force=true",
        )
    return True, None


def classify_liveness(*, status: str, cdp_reachable: bool) -> str:
    """Fold the DB status and a live CDP probe into one honest label.

    ``live``   — the browser answers CDP right now (regardless of what the row says).
    ``stale``  — the row claims it's running, but no browser answers (crashed / killed / the API
                 restarted and lost the process). This is the dangerous state the old
                 status-only checks missed.
    ``stopped``— not running and the row agrees.
    """
    if cdp_reachable:
        return "live"
    if status in ("active", "starting"):
        return "stale"
    return "stopped"


def operational_state(*, status: str, cdp_reachable: bool,
                      process_alive: Optional[bool] = None) -> str:
    """Return the operator-facing lifecycle state.

    A durable database status and a socket probe cannot answer the human question alone. In
    particular, an ``active`` row with no browser process is not an active session — its browser
    was closed and the durable record has not caught up yet.

    ``process_alive`` is optional so callers without process visibility keep the conservative
    ``stale`` answer instead of manufacturing a close.
    """
    if cdp_reachable:
        return "orphaned" if status not in ("active", "starting") else "live"
    if status == "starting":
        return "starting"
    if status == "active":
        if process_alive is False:
            return "closed"
        return "degraded" if process_alive else "stale"
    return "closed"


def port_owners(sessions: list[Any]) -> dict[int, int]:
    """Choose the one running session that owns each remembered debug port.

    Callers pass newest-first rows. Historical sessions often retain a recycled port, so the first
    active/starting claimant wins and stopped rows never multiply one browser into many sessions.
    """
    owners: dict[int, int] = {}
    for session in sessions:
        port = getattr(session, "chrome_debug_port", None)
        if port and getattr(session, "status", "") in ("active", "starting"):
            owners.setdefault(port, session.id)
    return owners


def view_row(
    session: Any,
    *,
    cdp_reachable: bool,
    account_label: Optional[str] = None,
    tab_count: Optional[int] = None,
    process_alive: Optional[bool] = None,
) -> dict[str, Any]:
    """Assemble one UI-ready row from a TrainingSession + a fresh liveness probe. Pure: the caller
    does the probe and the account lookup, so this stays trivially testable."""
    return {
        "id": session.id,
        "domain_id": session.domain_id,
        "account_id": getattr(session, "account_id", None),
        "account_label": account_label,
        "persistent_profile": session.persistent_profile,
        "profile_kind": "persistent" if session.persistent_profile else "throwaway",
        "port": session.chrome_debug_port,
        "pid": session.chrome_process_pid,
        "status": session.status,
        "liveness": classify_liveness(status=session.status, cdp_reachable=cdp_reachable),
        "operational_state": operational_state(
            status=session.status,
            cdp_reachable=cdp_reachable,
            process_alive=process_alive,
        ),
        "live": cdp_reachable,
        "process_alive": process_alive,
        "protected": bool(getattr(session, "protected", False)),
        "purpose": session.purpose,
        "goal_id": session.goal_id,
        "tab_count": tab_count,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "created_at": session.created_at.isoformat() if session.created_at else None,
    }
