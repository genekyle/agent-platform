"""The drive lock — a single-machine latch that says "CDP is driving; the keyboard is owned".

The keyboard and mouse are ONE physical resource on the operator's machine. While a live drive is
dispatching trusted CDP input, the operator typing on the same machine collides with it — the stray
"ur" bug (typing "our" while a drive was live leaked stray letters). This latch makes ownership
EXPLICIT: a drive engages it, the cockpit shows a loud "🔒 CDP DRIVING — keyboard owned" banner, and
the operator (or the drive's teardown) releases it when done — "lock until we unlock it back up again".

Deliberately NOT an OS keyboard seize. It's an ownership SIGNAL plus a gate other code can honour, so
a crash can never lock the operator out of their own machine. (A hard OS-level input seize is a
separate, sign-off-gated build with a guaranteed hotkey kill-switch — not this.)

Single process, in-memory: the drive orchestration (LiveActuator / TeachSession) and the cockpit's
poll both live in this app, so a module-level latch is the honest home. Thread-safe like
`apps/mcp/app/event_log.py` (a module-level `threading.Lock`).
"""

from __future__ import annotations

import contextlib
import threading
from datetime import datetime, timezone
from typing import Iterator, Optional

_lock = threading.Lock()
_state: dict = {"locked": False, "reason": "", "holder": None, "since": None}


def state() -> dict:
    """The current latch state (a copy — callers can't mutate the singleton)."""
    with _lock:
        return dict(_state)


def engage(reason: str = "", holder: Optional[str] = None) -> dict:
    """Take the lock: mark the keyboard owned by CDP. Idempotent — re-engaging keeps the original
    `since` (the drive has been holding it since then) but refreshes reason/holder. Returns state."""
    with _lock:
        if not _state["locked"]:
            _state["since"] = datetime.now(timezone.utc).isoformat()
        _state.update(locked=True, reason=reason, holder=holder)
        return dict(_state)


def release() -> dict:
    """Release the lock: the operator may type again. Idempotent. Returns state."""
    with _lock:
        _state.update(locked=False, reason="", holder=None, since=None)
        return dict(_state)


@contextlib.contextmanager
def driving(reason: str = "", holder: Optional[str] = None) -> Iterator[dict]:
    """Hold the drive lock for the duration of a block, releasing on ANY exit (return/raise) — the
    clean seam for a drive entrypoint (`with drive_lock.driving(reason=...):`). The `finally` release
    is why a drive that crashes still hands the keyboard back."""
    st = engage(reason, holder)
    try:
        yield st
    finally:
        release()
