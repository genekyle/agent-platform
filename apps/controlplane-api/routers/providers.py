"""Provider-group routes — the bucket above domains (Google ▸ gmail, calendar, docs, sheets).

Extracted-style thin router (docs/PLAN_main-split.md): the group definitions live in the
top-level `providers` module; this only resolves each group's members against the live registry
so the UI can render "Google" as one bucket with its real vs. planned member domains.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from deps import get_db
from models import DomainRegistry

router = APIRouter()


@router.get("/api/providers")
def list_providers(db: Session = Depends(get_db)):
    """Provider groups with membership resolved against the domain registry. `members` are
    domains that actually exist (seeded + active); `planned_members` are declared-but-not-yet."""
    import providers as providers_lib

    live = set(
        db.scalars(
            select(DomainRegistry.domain_id).where(DomainRegistry.status == "active")
        ).all()
    )
    out = []
    for p in providers_lib.list_providers():
        split = providers_lib.resolve_members(p, live)
        out.append(
            {
                "provider_id": p["provider_id"],
                "display_name": p["display_name"],
                "icon": p.get("icon"),
                "profile": p.get("profile"),
                "auth": p.get("auth", {}),
                "members": split["live"],
                "planned_members": split["planned"],
            }
        )
    return out
