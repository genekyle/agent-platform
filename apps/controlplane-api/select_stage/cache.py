"""SelectionCacheV1 — exact-match reuse of prior action picks (no LLM, FREE).

Before paying Haiku, check whether we've already grounded this exact state+goal.
Key = StateFingerprintV1 + task goal + SELECTOR_SCHEMA_VERSION, so a schema bump
or a goal change cleanly misses. We cache the pick's STABLE identity
(role + accessible name + action_id), not its bbox or backend id — those drift
between captures — and re-resolve to the current candidate on lookup.

A confirmed pick (Haiku's, or later a human correction) seeds the cache; the
next identical state is free. This is the first lever that drives Haiku calls
down over time. Storage: <artifacts>/cache/selection_cache.json.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from settings import settings

from .schema import SELECTOR_SCHEMA_VERSION, ActionId, Candidate

_lock = threading.Lock()


def _path() -> Path:
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parents[1] / base).resolve()
    p = base / "cache" / "selection_cache.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def cache_key(*, fingerprint: str, task_goal: str) -> str:
    return f"{fingerprint}:{(task_goal or '').strip().lower()}:{SELECTOR_SCHEMA_VERSION}"


def _load() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def lookup(*, fingerprint: str, task_goal: str, candidates: list[Candidate]) -> Optional[dict[str, Any]]:
    """Return {candidate, action_id} for a prior pick, re-resolved to THIS
    capture's candidates, or None on miss."""
    entry = _load().get(cache_key(fingerprint=fingerprint, task_goal=task_goal))
    if not entry:
        return None
    want = (entry.get("role", ""), (entry.get("name") or "").strip())
    for cand in candidates:
        if (cand.role, cand.name.strip()) == want:
            try:
                action_id = ActionId(entry.get("action_id", "click"))
            except ValueError:
                action_id = ActionId.CLICK
            return {"candidate": cand, "action_id": action_id}
    return None  # state matched but the element is gone — miss


def store(*, fingerprint: str, task_goal: str, chosen: Candidate, action_id: ActionId,
          source: str = "som_haiku") -> None:
    entry = {
        "role": chosen.role, "name": chosen.name.strip(),
        "action_id": action_id.value, "source": source,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    key = cache_key(fingerprint=fingerprint, task_goal=task_goal)
    with _lock:
        data = _load()
        data[key] = entry
        _path().write_text(json.dumps(data, indent=2), encoding="utf-8")
