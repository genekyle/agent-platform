"""Shared dependencies + small cross-cutting helpers for the control-plane routers.

Extracted from main.py (the router split — see docs/PLAN_main-split.md) so routers can
import shared helpers WITHOUT importing main. main includes the routers, so a router
importing from main would be a circular import; importing from here is not. Everything
in this module is deliberately low-dependency (no imports from main or routers).
"""
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from db import get_db  # re-exported so routers can do `from deps import get_db`
from models import TrainingSession
from settings import settings

__all__ = ["get_db", "utcnow", "as_aware", "_artifacts_dir", "_session_browser_url", "_slugify"]


def utcnow():
    return datetime.now(timezone.utc)


def as_aware(value):
    """A datetime that is safe to compare, whatever backend handed it over.

    Postgres round-trips `DateTime(timezone=True)` as tz-aware; SQLite — which the unit tests run
    on — hands back a naive value for the same column. Sorting a list that contains both raises
    `TypeError`, and it raises it at whichever call site happens to mix a freshly-created row with
    a reloaded one, which is a maddening way to find out. Anything comparing stored timestamps
    goes through here first; naive values are read as UTC, which is what they are.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _slugify(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
    return s.strip("_")


def _artifacts_dir() -> Path:
    return Path(settings.observer_artifacts_dir)


def _session_browser_url(session: TrainingSession) -> str:
    if not session.chrome_debug_port:
        raise HTTPException(status_code=400, detail="Training session Chrome port is not configured")
    return f"http://127.0.0.1:{session.chrome_debug_port}"
