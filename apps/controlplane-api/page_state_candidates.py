"""Candidate page states — how the state registry GROWS from what the agent actually sees.

Why this exists. Two components already *notice* a page they don't know, and both threw the
observation away: `map_url_to_state` returns the literal string `"unknown"` for any unrecognised
URL (apply_recipe.py), and the Haiku page-state teacher already proposes a name for a state it
judges new (`is_new` + `proposed_name`, select_stage/haiku_page_state.py) — which
`suggest_page_state` returned in its response and then dropped. So every unregistered page the
agent met was forgotten the moment it moved on, and the same mystery screen stayed a mystery
forever. This is the operator's "collect them as expected states as they go along".

A **candidate** is a page state we have OBSERVED but not yet blessed. It lives in the same
`page_state_registry` table as a real state, with `status="candidate"`, which buys three things
for free:

  * it is invisible to the labeler and to the classifier's menu (both filter `status == "active"`),
    so an unapproved guess can never contaminate a training label;
  * promotion is a one-field flip — `PATCH /api/training/page-states/{state_id} {"status": "active"}`
    — through the CRUD that already exists;
  * it inherits the whole scoping model (global/domain/goal/scenario), so there is no new table
    and no new UI surface to maintain.

Everything here is BEST-EFFORT: recording what we saw must never break the drive that saw it.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import PageStateRegistry

logger = logging.getLogger("page_state_candidates")

#: The status that marks a state as observed-but-unapproved. Anything reading the registry for
#: training or classification filters on "active", so this value is inert by design.
CANDIDATE_STATUS = "candidate"

#: Category for auto-proposed states, so the operator can find them as a group in the labeler.
CANDIDATE_CATEGORY = "unverified"


def slugify(name: str) -> str:
    """A registry-safe state_id: lowercase, non-alphanumerics collapsed to underscores."""
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return slug[:120]


def _scope_for(domain_id: Optional[str], goal_id: Optional[str],
               scenario_id: Optional[str]) -> str:
    """Narrowest scope the observation supports — a state seen inside one scenario shouldn't be
    offered globally."""
    if scenario_id:
        return "scenario"
    if goal_id:
        return "goal"
    if domain_id:
        return "domain"
    return "global"


def record_candidate(db: Session, *, proposed_name: str, domain_id: Optional[str] = None,
                     goal_id: Optional[str] = None, scenario_id: Optional[str] = None,
                     url: str = "", source: str = "haiku_page_state") -> Optional[dict[str, Any]]:
    """Write down an observed-but-unregistered page state so it can be approved later.

    Idempotent: if the id already exists — whether as a live state or an earlier candidate — the
    existing row is returned untouched. In particular this NEVER downgrades an `active` state back
    to a candidate. Returns the row as a dict, or None if nothing usable could be recorded.
    """
    state_id = slugify(proposed_name)
    if not state_id:
        return None
    try:
        existing = db.get(PageStateRegistry, state_id)
        if existing is not None:
            return _as_dict(existing)
        row = PageStateRegistry(
            state_id=state_id,
            display_name=(proposed_name or state_id).strip()[:200],
            scope=_scope_for(domain_id, goal_id, scenario_id),
            domain_id=domain_id,
            goal_id=goal_id,
            scenario_id=scenario_id,
            category=CANDIDATE_CATEGORY,
            description=(f"Auto-proposed by {source} from an unrecognised page"
                         + (f" at {url[:200]}" if url else "")
                         + ". Approve it (set status=active) to teach the agent this state."),
            status=CANDIDATE_STATUS,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("recorded candidate page state %r (scope=%s)", state_id, row.scope)
        return _as_dict(row)
    except Exception:  # noqa: BLE001 — an unrecordable observation must not break the drive
        logger.exception("failed to record candidate page state %r", state_id)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def list_candidates(db: Session, *, limit: int = 100) -> list[dict[str, Any]]:
    """Every state awaiting approval, newest first — the operator's promotion queue."""
    rows = db.scalars(
        select(PageStateRegistry)
        .where(PageStateRegistry.status == CANDIDATE_STATUS)
        .order_by(PageStateRegistry.created_at.desc())
    ).all()
    return [_as_dict(r) for r in rows[:limit]] if limit else [_as_dict(r) for r in rows]


def _as_dict(row: PageStateRegistry) -> dict[str, Any]:
    return {
        "state_id": row.state_id, "display_name": row.display_name, "scope": row.scope,
        "domain_id": row.domain_id, "goal_id": row.goal_id, "scenario_id": row.scenario_id,
        "category": row.category, "description": row.description, "status": row.status,
    }
