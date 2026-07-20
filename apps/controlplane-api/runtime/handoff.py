"""Handoff — the human-in-the-loop escalation record + notification.

When the loop stops for a human (a captcha gate, a low-confidence pick, a verify
failure, an unreadable page), it should not fail silently into a log line. This module
turns a `LoopResult` into a durable HANDOFF RECORD that answers the two questions the
operator actually needs:

  * WHY did it stop?            — the escalation reason, in plain language.
  * WHAT did it try first?      — a compact trace of every step it took before giving up
                                  (which action, which decision layer, how confident, and
                                  whether the action verified), so the operator can see the
                                  agent's reasoning, not just its final shrug.

Records are appended to `<artifacts>/cache/handoffs.jsonl` (durable; the control panel can
list unresolved ones) and, when configured, pushed as a live macOS notification so the
operator is pinged the moment the agent needs help. Cost is a few extra output tokens per
escalation — deliberately paid, because a good handoff is what lets the operator unblock
in seconds and keeps the whole "take work off Claude's desk" loop honest.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from settings import settings

logger = logging.getLogger("controlplane.runtime.handoff")
_lock = threading.Lock()


# Plain-language WHY + a concrete next step, keyed by the loop's escalation_reason.
_REASON_GUIDANCE: dict[str, dict[str, str]] = {
    "stop_state": {
        "why": "Hit a stop-state (captcha / security checkpoint) the agent must never handle itself.",
        "suggestion": "Solve the challenge in the browser, then resume the run.",
    },
    "gate_blocked": {
        "why": "A safety gate refused the next action (e.g. a live captcha, or an invariant like an "
               "incomplete required field).",
        "suggestion": "Clear the blocker shown in the gate detail, then resume.",
    },
    "low_confidence": {
        "why": "The agent wasn't confident enough about which element to act on.",
        "suggestion": "Do this step by hand (that seeds the cache so it's free next time), or confirm "
                      "the intended target.",
    },
    "no_match": {
        "why": "No candidate on the page matched the task goal.",
        "suggestion": "Check the page is what the agent expected; perform the step manually to teach it.",
    },
    "budget_exceeded": {
        "why": "The weekly autonomous LLM budget cap was hit, so the agent stopped paying for picks.",
        "suggestion": "Handle this step manually, or raise the budget if this is expected load.",
    },
    "verifier_failed": {
        "why": "The action fired but the page didn't change the way the agent predicted.",
        "suggestion": "Check whether the action actually landed; finish the step by hand if needed.",
    },
    "stage_error": {
        "why": "An unexpected error inside the select stage stopped the run.",
        "suggestion": "See the reason detail; this usually needs a code look, not just a click.",
    },
    "unknown_state": {
        "why": "The agent couldn't read the page (no candidates — possibly a failed observation).",
        "suggestion": "Confirm the tab is on the expected page and reachable, then resume.",
    },
    "not_authenticated": {
        "why": "The browser isn't signed in to this site, so the task can't run yet.",
        "suggestion": "Log in to the site in the session's browser window (do any 2FA/checkpoint "
                      "by hand), then run again — a persistent profile keeps you signed in after.",
    },
    # --- "we are not where we assumed" (controller/unexpected.py) ---------------------------
    "stale_tab": {
        "why": "The tab being driven no longer exists — it navigated away, was reloaded, or was "
               "closed — and re-finding the site's live tab didn't turn one up.",
        "suggestion": "Reopen the page in the Career-Search browser, then run the step again.",
    },
    "unexpected_state": {
        "why": "The page wasn't a state the agent recognises, so it stopped instead of guessing.",
        "suggestion": "Check what's on screen. If it's a legitimate new page, label it — it's been "
                      "recorded as a candidate state, so approving it teaches the agent for next time.",
    },
    # --- login-drive escalations (login_reasoner statuses) ----------------------------------
    "captcha": {
        "why": "A captcha is blocking sign-in. The agent never solves these.",
        "suggestion": "Solve it in the browser, then press Login again.",
    },
    "mfa": {
        "why": "Sign-in needs a 2FA / verification code — that step is always yours.",
        "suggestion": "Enter the code in the browser, then press Login again.",
    },
    "bad_credentials": {
        "why": "The site rejected the stored sign-in. It was NOT retried — repeated attempts against "
               "a real account risk locking it.",
        "suggestion": "Fix the password in the Account Manager, then press Login again.",
    },
    "account_exists": {
        "why": "This email already has an account, but the Sign In control couldn't be found to "
               "switch to it.",
        "suggestion": "Sign in yourself (or fix the password), then mark the account created.",
    },
}


@dataclass
class HandoffRecord:
    id: str
    ts: str
    status: str                 # "open" | "resolved"
    task_goal: str
    training_session_id: Optional[int]
    loop_status: str
    escalation_reason: Optional[str]
    why: str                    # plain-language reason
    detail: str                 # the loop's raw reason string (has the specifics)
    suggestion: str             # what the human can do
    url: str
    screenshot_path: str
    tried: list[dict[str, Any]] = field(default_factory=list)   # compact per-step trace
    tab_url: Optional[str] = None
    diagnostic: Optional[dict[str, Any]] = None                 # e.g. post-failure captcha probe
    resolved_at: Optional[str] = None


def _compact_step(step: Any) -> dict[str, Any]:
    """One step of the 'what it tried' trace — the fields an operator reads, not the full row."""
    d = asdict(step) if not isinstance(step, dict) else step
    verify = d.get("verify") or {}
    return {
        "step": d.get("step_index"),
        "route": d.get("route"),
        "action": d.get("action_id"),
        "layer": d.get("layer"),            # cache (free/practiced) vs som_haiku (paid) vs classify
        "confidence": d.get("confidence"),
        "outcome": d.get("status"),
        "reason": d.get("reason_code"),
        "verified": verify.get("ok") if verify else None,
        "gate": d.get("gate"),
    }


def build_handoff(result: Any, *, task_goal: str, training_session_id: Optional[int] = None,
                  last_observation: Any = None, tab_url: Optional[str] = None,
                  diagnostic: Optional[dict[str, Any]] = None) -> HandoffRecord:
    """Turn a LoopResult into a HandoffRecord. Pure — no I/O — so it's easy to test.

    `diagnostic` is an optional post-failure probe result (e.g. a captcha check run as a
    LAST CALL before handing to a human): when it reports a blocking captcha, that becomes
    the headline reason (it's likely the real cause), otherwise the handoff notes the check
    ran and came back clean so the operator knows it's a DIFFERENT problem."""
    reason = result.escalation_reason or ""
    loop_status = getattr(result.status, "value", str(result.status))
    default = {
        "why": "The agent stopped and needs a human.",
        "suggestion": "Review the trace below and complete or unblock the step.",
    }
    if not reason and loop_status == "max_steps":
        # ran the step budget without hitting the task's terminal state — incomplete, not done.
        default = {
            "why": "Ran out of steps before reaching the task's completion state — the task is "
                   "incomplete (not dropped silently).",
            "suggestion": "Check how far it got in the trace; raise max_steps if it was on track, "
                          "or finish the remaining steps by hand.",
        }
    g = dict(_REASON_GUIDANCE.get(reason, default))
    if diagnostic and diagnostic.get("reason") == "captcha":
        g["why"] = "A live captcha is blocking progress (found by the last-call check after the " \
                   "action failed to take effect)."
        g["suggestion"] = diagnostic.get("guidance") or g["suggestion"]
    # A gate-blocked stop carries a specific block dict on the last step — prefer its
    # concrete guidance (e.g. "needs approval to submit", "captcha") over the generic text.
    last = (result.steps or [])[-1] if result.steps else None
    block = getattr(last, "gate", None) if last is not None else None
    if block:
        if block.get("reason") == "needs_approval":
            g["why"] = f"Reached an irreversible action ({block.get('action')} on " \
                       f"{block.get('target') or 'a control'!r}) that needs your approval."
        elif block.get("reason") == "captcha":
            g["why"] = "A live captcha is blocking the next action."
        if block.get("guidance"):
            g["suggestion"] = block["guidance"]
    url = getattr(last_observation, "url", "") or ""
    screenshot = getattr(last_observation, "screenshot_path", "") or ""
    return HandoffRecord(
        id=f"hoff_{uuid.uuid4().hex[:12]}",
        ts=datetime.now(timezone.utc).isoformat(),
        status="open",
        task_goal=task_goal,
        training_session_id=training_session_id,
        loop_status=getattr(result.status, "value", str(result.status)),
        escalation_reason=result.escalation_reason,
        why=g["why"],
        detail=result.reason or "",
        suggestion=g["suggestion"],
        url=url,
        screenshot_path=screenshot,
        tried=[_compact_step(s) for s in (result.steps or [])],
        tab_url=tab_url,
        diagnostic=diagnostic,
    )


# --- Persistence -------------------------------------------------------------
def _handoffs_path() -> Path:
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parents[1] / base).resolve()
    p = base / "cache" / "handoffs.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def persist(handoff: HandoffRecord) -> None:
    """Append the handoff to the durable log. Best-effort — never raises."""
    try:
        with _lock:
            with _handoffs_path().open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(handoff)) + "\n")
    except Exception:
        logger.exception("failed to persist handoff %s", handoff.id)


def list_handoffs(*, open_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
    """Read handoffs newest-first. Reconstructs the current status from resolve markers."""
    path = _handoffs_path()
    if not path.exists():
        return []
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            hid = row.get("id")
            if hid is None:
                continue
            if hid not in by_id:
                order.append(hid)
            # later rows for the same id (e.g. a resolve marker) overlay the original
            by_id.setdefault(hid, {}).update(row)
    except Exception:
        logger.exception("failed to read handoffs log")
        return []
    rows = [by_id[h] for h in reversed(order)]
    if open_only:
        rows = [r for r in rows if r.get("status") != "resolved"]
    return rows[:limit] if limit else rows


def resolve(handoff_id: str) -> bool:
    """Mark a handoff resolved by appending an overlay row. Returns False if unknown."""
    known = {r.get("id") for r in list_handoffs(limit=0)}
    if handoff_id not in known:
        return False
    marker = {"id": handoff_id, "status": "resolved",
              "resolved_at": datetime.now(timezone.utc).isoformat()}
    try:
        with _lock:
            with _handoffs_path().open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(marker) + "\n")
        return True
    except Exception:
        logger.exception("failed to resolve handoff %s", handoff_id)
        return False


# --- Notification ------------------------------------------------------------
def _notify_channel() -> str:
    """`HANDOFF_NOTIFY` env: 'macos' | 'log' | 'off'. Default: macOS banner on darwin,
    otherwise log-only."""
    choice = os.environ.get("HANDOFF_NOTIFY", "").strip().lower()
    if choice in ("macos", "log", "off"):
        return choice
    return "macos" if platform.system() == "Darwin" else "log"


def _macos_banner(title: str, message: str) -> None:
    """Fire a native macOS notification via osascript. Best-effort; text is passed as
    argv (not interpolated into the AppleScript source) so quotes/specials can't break it."""
    script = 'on run argv\n display notification (item 1 of argv) with title (item 2 of argv)\nend run'
    try:
        subprocess.run(["osascript", "-e", script, message, title],
                       check=False, capture_output=True, timeout=5)
    except Exception as exc:  # noqa: BLE001
        logger.warning("macOS notification failed: %s", exc)


def notify(handoff: HandoffRecord) -> None:
    """Ping the operator that the agent needs help. Always logs; also fires the configured
    live channel (macOS banner by default). Best-effort — never raises into the run."""
    steps = len(handoff.tried)
    summary = f"{handoff.why} ({steps} step{'s' if steps != 1 else ''} tried)"
    logger.warning("HANDOFF %s [%s] goal=%r — %s | %s",
                   handoff.id, handoff.escalation_reason, handoff.task_goal, handoff.why,
                   handoff.suggestion)
    channel = _notify_channel()
    if channel == "macos":
        _macos_banner(title=f"Agent needs help: {handoff.task_goal[:40] or 'task'}", message=summary)


def emit(result: Any, *, task_goal: str, training_session_id: Optional[int] = None,
         last_observation: Any = None, tab_url: Optional[str] = None,
         diagnostic: Optional[dict[str, Any]] = None) -> HandoffRecord:
    """Build → persist → notify, in one call. Returns the record so the endpoint can
    include it in its response."""
    handoff = build_handoff(result, task_goal=task_goal, training_session_id=training_session_id,
                            last_observation=last_observation, tab_url=tab_url, diagnostic=diagnostic)
    persist(handoff)
    notify(handoff)
    return handoff


def emit_escalation(*, reason: str, task_goal: str, detail: str = "", url: str = "",
                    tab_url: Optional[str] = None, tried: Optional[list[dict[str, Any]]] = None,
                    training_session_id: Optional[int] = None,
                    loop_status: str = "escalated") -> Optional[HandoffRecord]:
    """The same alert, for callers that DON'T have a `runtime.loop.LoopResult`.

    `build_handoff`/`emit` are shaped around that one result object, which is why the login drive
    and the controller — neither of which produces one — had no operator alert at all: they failed
    into a JSON field and a log line. This is the plain-fields entry point so every escalation,
    whichever loop raised it, lands in the SAME handoffs log (and therefore the same Session
    Activity timeline, as `kind:"escalation"`) and fires the same notification.

    Best-effort by construction — an alert must never break the drive it is reporting on.
    """
    try:
        g = _REASON_GUIDANCE.get(reason, {
            "why": "The agent stopped and needs a human.",
            "suggestion": "Review the detail below and complete or unblock the step.",
        })
        handoff = HandoffRecord(
            id=f"hoff_{uuid.uuid4().hex[:12]}",
            ts=datetime.now(timezone.utc).isoformat(),
            status="open",
            task_goal=task_goal,
            training_session_id=training_session_id,
            loop_status=loop_status,
            escalation_reason=reason,
            why=g["why"],
            detail=detail or "",
            suggestion=g["suggestion"],
            url=url or "",
            screenshot_path="",
            tried=list(tried or []),
            tab_url=tab_url,
        )
        persist(handoff)
        notify(handoff)
        return handoff
    except Exception:  # noqa: BLE001 — never raise into the drive being reported on
        logger.exception("failed to emit escalation handoff (reason=%s)", reason)
        return None


def escalation_callback(*, task_goal: str, reason: str = "unexpected_state",
                        training_session_id: Optional[int] = None):
    """An `on_escalate`-shaped callback `(bundle, decision) -> None` for `controller/loop.py`'s
    seam, so a controller escalation raises a real operator alert instead of only a journal row.

    Kept here (and injected) rather than called inside `run_controller` on purpose: the loop stays
    pure control-flow with no disk/notification I/O, which is what makes it testable offline with a
    fake actuator. Wire this in at the live call site.
    """
    def _on_escalate(bundle: Any, decision: Any) -> None:
        emit_escalation(
            reason=reason,
            task_goal=task_goal or getattr(bundle, "goal_text", "") or getattr(bundle, "task", ""),
            detail=getattr(decision, "rationale", "") or "",
            url=getattr(bundle, "url", "") or "",
            training_session_id=training_session_id,
            tried=[{
                "state": getattr(bundle, "state", None),
                "action": getattr(decision, "intent", None),
                "layer": getattr(decision, "rung", None),
                "confidence": getattr(decision, "confidence", None),
            }],
        )
    return _on_escalate
