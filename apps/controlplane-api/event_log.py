"""Event log — a cross-process console of what the system actually did.

The operator (and the agent) need to see, at a glance, that the system is WORKING and being used:
captures taken, actions driven, accounts created, tabs closed, states labeled. Those happen in TWO
processes — the control plane (this app) and the MCP browser server — and until now nothing tied
them together, so a direct MCP drive left no visible trace.

Both processes share the MCP output dir (`settings.observer_artifacts_dir` == `../mcp/output`), so a
single append-only JSONL file there is the simplest shared bus: each process appends events; the
control plane serves them at `GET /api/events`. Best-effort and never raises into a caller — an
observability aid must never break the thing it observes.

Event shape: {ts, source, kind, summary, detail, domain}. `kind` is a short slug the UI groups/filters
by (capture, drive, account, tab, apply, label, search, error). Keep summaries one-line + concrete.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from settings import settings

_lock = threading.Lock()
_MAX_LINES = 1000  # ring buffer — old events age out so the file can't grow unbounded


def _path() -> Path:
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parent / base).resolve()
    p = base / "cache" / "event_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def log_event(kind: str, summary: str, *, source: str = "controlplane",
              detail: Optional[str] = None, domain: Optional[str] = None) -> dict[str, Any]:
    """Append one event. Best-effort — swallows all errors (never breaks the caller)."""
    ev = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "kind": (kind or "info")[:32],
        "summary": (summary or "")[:300],
        "detail": (detail or "")[:800] or None,
        "domain": domain,
    }
    try:
        with _lock:
            p = _path()
            lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
            lines.append(json.dumps(ev, ensure_ascii=False))
            if len(lines) > _MAX_LINES:
                lines = lines[-_MAX_LINES:]
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001 — observability must never raise
        pass
    return ev


def list_events(limit: int = 100, kind: Optional[str] = None,
                source: Optional[str] = None) -> list[dict[str, Any]]:
    """Newest-first events, optionally filtered by kind/source."""
    p = _path()
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if kind and ev.get("kind") != kind:
                continue
            if source and ev.get("source") != source:
                continue
            out.append(ev)
    except Exception:  # noqa: BLE001
        return []
    return out[-limit:][::-1]
