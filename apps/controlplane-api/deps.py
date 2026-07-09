"""Shared dependencies + small cross-cutting helpers for the control-plane routers.

Extracted from main.py (the router split — see docs/PLAN_main-split.md) so routers can
import shared helpers WITHOUT importing main. main includes the routers, so a router
importing from main would be a circular import; importing from here is not. Everything
in this module is deliberately low-dependency (no imports from main or routers).
"""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from db import get_db  # re-exported so routers can do `from deps import get_db`
from models import TrainingSession
from settings import settings

__all__ = ["get_db", "utcnow", "_artifacts_dir", "_session_browser_url"]


def utcnow():
    return datetime.now(timezone.utc)


def _artifacts_dir() -> Path:
    return Path(settings.observer_artifacts_dir)


def _session_browser_url(session: TrainingSession) -> str:
    if not session.chrome_debug_port:
        raise HTTPException(status_code=400, detail="Training session Chrome port is not configured")
    return f"http://127.0.0.1:{session.chrome_debug_port}"
