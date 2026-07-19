"""Session Activity routes — the unified system feed (reasoning + actions + events + escalations +
API touches), merged chronologically and domain/session-filterable. See session_activity.py."""

from typing import Optional

from fastapi import APIRouter

import session_activity

router = APIRouter()


@router.get("/api/activity")
def get_activity(domain: Optional[str] = None, session: Optional[str] = None,
                 kinds: Optional[str] = None, include_api: bool = False, limit: int = 200) -> dict:
    """The merged system feed, newest-first. `domain` scopes it (e.g. `career_search`); `kinds` is a
    comma list (reasoning,action,event,escalation,error,api); `include_api` folds in the API-touch
    ring (off by default so the feed isn't drowned by the cockpit's own polling)."""
    kset = {k.strip() for k in kinds.split(",") if k.strip()} if kinds else None
    entries = session_activity.build_feed(domain=domain, session=session, kinds=kset,
                                          include_api=include_api, limit=min(limit, 500))
    return {"entries": entries, "count": len(entries)}
