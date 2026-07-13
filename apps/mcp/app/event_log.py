"""Event log (MCP side) — append browser-action events to the SHARED log the control plane serves.

The control plane's `observer_artifacts_dir` is this app's `output/` dir, so both processes write to
the same `output/cache/event_log.jsonl`. This lets a direct MCP drive (capture/execute/close_tab)
show up in the operator's event console instead of leaving no trace. Best-effort; never raises.
See controlplane-api/event_log.py for the reader + schema.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_lock = threading.Lock()
_MAX_LINES = 1000


def _path() -> Path:
    # apps/mcp/app/event_log.py -> apps/mcp/output/cache/event_log.jsonl
    p = Path(__file__).resolve().parent.parent / "output" / "cache" / "event_log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def log_event(kind: str, summary: str, *, detail: Optional[str] = None,
              domain: Optional[str] = None) -> None:
    """Append one MCP event (source='mcp'). Best-effort — swallows all errors."""
    ev = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": "mcp",
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
    except Exception:  # noqa: BLE001
        pass
