"""Errand log — the durable record of favours one domain asked another.

An errand is the one kind of work whose whole point is to happen somewhere the operator isn't
looking. The Career Search drive pauses, a Gmail tab is read, the drive resumes — and if nobody
writes that down, the only visible trace is an unexplained gap in a different domain's timeline.
This is the write-down.

Two consumers, both of which need it to exist before they are worth building:
  * the Gmail workspace's **Errands** tab — what was asked, by whom, and what came back;
  * `command_center._errand_metrics` — the landing tile, whose one number that matters is how
    many errands ESCALATED, because each of those is a drive elsewhere waiting on a human.

**Never records the code.** The record carries the errand's shape — who asked, what for, what
status came back, which subject line it was read from — and never the value. That is the same rule
the intent journal follows (`redact()` to a length), and the reason `ErrandResult.as_public()`
exists: the caller that asked gets the secret, the corpus and every UI get the shape.

Deliberately a jsonl beside `handoffs.jsonl` rather than a table: it is an append-only operational
record with no relational queries over it, exactly like the handoff records it sits next to. If the
Errands tab ever needs to JOIN this against sessions, promote it then.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import errands
from settings import settings

logger = logging.getLogger("controlplane.errand_log")
_lock = threading.Lock()

#: How far back the landing tile looks. An errand from last month is history, not status.
_STATS_WINDOW = timedelta(days=7)


def _path() -> Path:
    p = Path(settings.observer_artifacts_dir) / "cache" / "errands.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def record(request: "errands.ErrandRequest", result: "errands.ErrandResult",
           *, duration_ms: int = 0) -> dict[str, Any]:
    """Append one errand. Best-effort — a logging failure must never break the drive that is
    currently waiting on the code we just found."""
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "errand_id": request.errand_id,
        "requested_by": request.requested_by,
        "reason": (request.reason or "")[:300],
        "status": result.status,
        # The SHAPE of the answer, never the answer. A caller debugging "did it find anything?"
        # needs the boolean; nobody needs the digits.
        "found": result.value is not None,
        "escalated": result.status in errands.ESCALATING,
        "escalation": result.escalation[:300],
        "evidence": {k: v for k, v in (result.evidence or {}).items() if k != "value"},
        "considered_count": len(result.considered),
        "duration_ms": duration_ms,
    }
    try:
        with _lock:
            with _path().open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — see the docstring
        logger.warning("could not append errand record", exc_info=True)
    return row


def recent(limit: int = 50, *, requested_by: Optional[str] = None) -> list[dict[str, Any]]:
    """Newest-first errand records, optionally filtered to one calling domain."""
    path = _path()
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # a torn final line from a killed process — skip it, don't die
    except OSError:
        return []
    if requested_by:
        rows = [r for r in rows if r.get("requested_by") == requested_by]
    rows.reverse()
    return rows[:limit]


def recent_stats(window: timedelta = _STATS_WINDOW) -> dict[str, int]:
    """Counts for the landing tile. `escalated` is the one to watch — every escalation is another
    domain's drive parked until a human looks."""
    cutoff = (datetime.now(timezone.utc) - window).isoformat()
    rows = [r for r in recent(limit=500) if (r.get("ts") or "") >= cutoff]
    return {
        "served": len(rows),
        "ok": sum(1 for r in rows if r.get("status") == errands.OK),
        "escalated": sum(1 for r in rows if r.get("escalated")),
    }
