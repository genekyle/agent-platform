"""Workspace routes — the command-center rollup + per-domain automation posture.

Extracted from main.py (router split — docs/PLAN_main-split.md). Pure delegators to the
command_center / domain_settings modules. NOTE: /api/domains/{id}/training_readiness stays
in main for now — it composes training_coverage (a training-domain handler), so it moves
with the training router once that logic lands in a service.
"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from deps import get_db

router = APIRouter()


@router.get("/api/command-center/summary")
def command_center_summary(db: Session = Depends(get_db)):
    """One cheap read for the landing page: per-domain health tiles, the open-attention
    count, and a merged cross-domain activity feed. Best-effort — a dead source degrades a
    single tile, never the page."""
    import command_center
    return command_center.build_summary(db)


class DomainSettingsBody(BaseModel):
    # Partial patch — only the keys present are applied.
    automation_mode: Optional[str] = None       # manual | supervised | autopilot
    goals: Optional[dict[str, bool]] = None      # per-goal on/off switches


@router.get("/api/domains/{domain_id}/settings")
def get_domain_settings(domain_id: str):
    """The operator's automation posture for a domain (mode + per-goal switches)."""
    import domain_settings
    return domain_settings.get_settings(domain_id)


@router.put("/api/domains/{domain_id}/settings")
def put_domain_settings(domain_id: str, body: DomainSettingsBody):
    """Update a domain's automation posture. Unknown modes are ignored so a typo can't
    silently disable the approval gate."""
    import domain_settings
    return domain_settings.put_settings(domain_id, body.model_dump(exclude_none=True))
