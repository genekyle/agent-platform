"""Job-search targets — the persisted (query, location) list the cadence runs against.

This is the small "written down, not in my head" store for WHAT we're searching and WHERE.
search_cadence.py references a "job_preference profile" in the abstract; this is the concrete,
serializable version of that — so "Nashua, NH / reporting analyst" lives in a file the search
phase reads, not in a Claude/Haiku context that resets between sessions.

Deliberately not a model and not a DB table: it's a flat JSON list persisted next to the other
cache-backed config (escalation_rules.json, apply_blackboards/) under <artifacts>/cache. Mirrors
escalation_rules.py's seed-on-first-use pattern so it's editable without code changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from settings import settings

# Seeded from the live session we're standing up. status='active' = part of the current cadence;
# 'paused' targets are remembered but skipped. Order is run order.
_SEED_TARGETS: list[dict[str, Any]] = [
    {"query": "reporting analyst", "location": "Nashua, NH", "status": "active"},
]


def _targets_path() -> Path:
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parent / base).resolve()
    path = base / "cache" / "job_search_targets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _norm(query: str, location: str) -> tuple[str, str]:
    """Canonical form used for dedupe + comparison (case/space-insensitive)."""
    return (" ".join((query or "").split()).lower(), " ".join((location or "").split()).lower())


def load_targets() -> list[dict[str, Any]]:
    """Load persisted targets, seeding the file on first use."""
    path = _targets_path()
    if not path.exists():
        path.write_text(json.dumps(_SEED_TARGETS, indent=2), encoding="utf-8")
        return list(_SEED_TARGETS)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return list(_SEED_TARGETS)


def save_targets(targets: list[dict[str, Any]]) -> None:
    _targets_path().write_text(json.dumps(targets, indent=2), encoding="utf-8")


def add_target(query: str, location: str, status: str = "active") -> dict[str, Any]:
    """Add a (query, location) target, or return the existing one if a case-insensitive
    duplicate is already present (no duplicate rows — the store stays the source of truth)."""
    query = (query or "").strip()
    location = (location or "").strip()
    if not query:
        raise ValueError("query is required")
    targets = load_targets()
    key = _norm(query, location)
    for t in targets:
        if _norm(t.get("query", ""), t.get("location", "")) == key:
            return t  # already known — idempotent add
    row = {"query": query, "location": location,
           "status": status if status in {"active", "paused"} else "active"}
    targets.append(row)
    save_targets(targets)
    return row


def active_targets() -> list[dict[str, Any]]:
    """The targets the cadence should actually run, in order."""
    return [t for t in load_targets() if t.get("status", "active") == "active"]


def active_target() -> dict[str, Any] | None:
    """The single target a fresh session seeds its search_state from (first active)."""
    act = active_targets()
    return act[0] if act else None
