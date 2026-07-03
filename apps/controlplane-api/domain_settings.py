"""Per-domain operator settings — the automation posture for each domain workspace.

The cockpit lets the operator choose HOW autonomous each domain is, and which of a
domain's goals are switched on. That's an operator preference, not training data and not
something Claude decides, so it lives here in one small JSON doc
(`<artifacts>/cache/domain_settings.json`) next to the other operator-owned state
(inventory.json, job_search_targets.json).

Three automation modes, from the product spec — the clean bridge from manual to automated:

  * ``manual``     — nothing runs unless the operator clicks Run. (Safe default.)
  * ``supervised`` — safe/read-only tasks may run on their own, but anything outward-facing
                     (publish, apply, message, purchase) still needs per-action approval.
  * ``autopilot``  — approved recipes run on a schedule; the human is pinged only for blocks,
                     unknown states, or high-risk actions.

Scheduling for ``autopilot`` is intentionally NOT wired here yet — this module records the
posture and the UI is honest that the scheduler is a later pass. What IS real today: the mode
gates which controls the cockpit surfaces and whether an action needs an approval click.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from settings import settings

_lock = threading.Lock()

MODES = ("manual", "supervised", "autopilot")
DEFAULT_MODE = "manual"


def _path() -> Path:
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parent / base).resolve()
    p = base / "cache" / "domain_settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(doc: dict[str, Any]) -> None:
    _path().write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _defaults(domain_id: str) -> dict[str, Any]:
    """The posture a domain starts in before the operator has touched anything."""
    return {
        "domain_id": domain_id,
        "automation_mode": DEFAULT_MODE,
        # Per-goal on/off switches, keyed by goal_id. Absent → treated as enabled so a
        # newly-registered goal shows up without a migration.
        "goals": {},
        "updated_at": None,
    }


def get_settings(domain_id: str) -> dict[str, Any]:
    """Current settings for a domain, merged over defaults (never raises, always complete)."""
    doc = _load()
    merged = _defaults(domain_id)
    stored = doc.get(domain_id)
    if isinstance(stored, dict):
        merged.update(stored)
        merged["domain_id"] = domain_id  # never let a stale/renamed id leak through
    return merged


def put_settings(domain_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Merge ``patch`` into a domain's settings and persist. Returns the full new settings.

    Only known keys are accepted; ``automation_mode`` is validated against ``MODES`` and an
    unknown value is ignored (keeps a typo from silently disabling the gate)."""
    with _lock:
        doc = _load()
        current = doc.get(domain_id)
        merged = _defaults(domain_id)
        if isinstance(current, dict):
            merged.update(current)

        if "automation_mode" in patch:
            mode = patch["automation_mode"]
            if mode in MODES:
                merged["automation_mode"] = mode
        if "goals" in patch and isinstance(patch["goals"], dict):
            goals = dict(merged.get("goals") or {})
            for goal_id, val in patch["goals"].items():
                goals[goal_id] = bool(val)
            merged["goals"] = goals

        merged["domain_id"] = domain_id
        merged["updated_at"] = datetime.now(timezone.utc).isoformat()
        doc[domain_id] = merged
        _save(doc)
        return merged


def approval_required(domain_id: str, *, outward_facing: bool) -> bool:
    """Does an action need an explicit approval click under the domain's current mode?

    ``outward_facing`` marks the irreversible / published / sent actions (post a listing,
    submit an application, send a message, buy). The cockpit uses this to decide whether a
    task button runs immediately or asks first.
    """
    mode = get_settings(domain_id)["automation_mode"]
    if mode == "manual":
        return True                       # everything is a deliberate click
    if mode == "supervised":
        return outward_facing             # safe tasks flow; risky ones ask
    return False                          # autopilot: approved recipes run unattended
