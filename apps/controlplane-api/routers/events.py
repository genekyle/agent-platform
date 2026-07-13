"""Event console routes — the operator's live feed of what the system did (across both processes).

Reads the shared append-only log (event_log.py). POST lets any caller drop an event (the MCP writes
the file directly; POST is for other clients / manual notes).
"""

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

import event_log

router = APIRouter()


@router.get("/api/events")
def get_events(limit: int = 100, kind: Optional[str] = None, source: Optional[str] = None) -> dict[str, Any]:
    """Newest-first system events (captures, drives, account actions, tabs, …), optionally filtered."""
    events = event_log.list_events(limit=limit, kind=kind, source=source)
    return {"events": events, "count": len(events)}


class EventBody(BaseModel):
    kind: str
    summary: str
    detail: Optional[str] = None
    domain: Optional[str] = None
    source: str = "controlplane"


@router.post("/api/events")
def post_event(body: EventBody) -> dict[str, Any]:
    """Append an event to the shared log."""
    return event_log.log_event(body.kind, body.summary, source=body.source,
                               detail=body.detail, domain=body.domain)
