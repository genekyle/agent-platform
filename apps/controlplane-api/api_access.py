"""A tiny in-memory ring of recent API requests — the "what's touching the system" source for the
Session Activity feed. In-memory (not a file) so per-request logging is cheap and never races the
shared cache files, and it's naturally scoped to the running process. Thread-safe."""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

_LOCK = threading.Lock()
_RING: "deque[dict]" = deque(maxlen=500)


def record(method: str, path: str, status: int, ms: float) -> None:
    with _LOCK:
        _RING.append({"ts": datetime.now(timezone.utc).isoformat(), "method": method,
                      "path": path, "status": status, "ms": round(ms, 1)})


def recent(limit: int = 300) -> list[dict[str, Any]]:
    with _LOCK:
        rows = list(_RING)
    return rows[-limit:]
