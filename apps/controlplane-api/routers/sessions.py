"""Session routes — the live browser-session view + the protect (do-not-touch) switch.

Extracted from main.py (router split — docs/PLAN_main-split.md). The /api/sessions list folds
each session with a live CDP probe; /protect marks a session human-owned so automated stop/reap/
delete refuse it without force. Delegates to accounts / channel_browser / session_manager (lazy).
"""
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from deps import get_db
from models import TrainingSession
from schemas import TrainingSessionRead

router = APIRouter()


def _session_tab_count(port: Optional[int]) -> Optional[int]:
    """Best-effort count of open page tabs in a session's Chrome (None if it isn't answering)."""
    if not port:
        return None
    try:
        with httpx.Client(timeout=1.5) as client:
            targets = client.get(f"http://127.0.0.1:{port}/json").json()
        return sum(1 for t in targets if t.get("type") == "page")
    except Exception:  # noqa: BLE001
        return None


@router.get("/api/sessions")
def list_sessions(db: Session = Depends(get_db)):
    """Every browser session, each folded with a LIVE CDP probe, its account label, and whether
    it's protected — the view that makes concurrent/training sessions safe to reason about."""
    import accounts as accounts_mod
    import channel_browser
    import session_manager
    label_by_id = {a["account_id"]: a["label"] for a in accounts_mod.list_accounts()}
    rows = []
    for s in db.scalars(select(TrainingSession).order_by(TrainingSession.created_at.desc())).all():
        live = channel_browser.cdp_reachable(s.chrome_debug_port, timeout=0.5)
        rows.append(session_manager.view_row(
            s, cdp_reachable=live,
            account_label=label_by_id.get(s.account_id),
            tab_count=_session_tab_count(s.chrome_debug_port) if live else None,
        ))
    return {"sessions": rows}


@router.get("/api/sessions/tabs")
def list_live_tabs(match: Optional[str] = None, db: Session = Depends(get_db)):
    """The 'tab manager' — every open tab across all LIVE session Chromes, so the operator (and the
    account login/create drives) can find the right tab by URL instead of guessing a browser/port.
    `match` filters by URL substring (e.g. 'myworkdayjobs.com')."""
    import tab_finder
    tabs = tab_finder.live_tabs(db)
    if match:
        m = match.lower()
        tabs = [t for t in tabs if m in t["tab_url"].lower()]
    return {"tabs": tabs, "count": len(tabs)}


class ProtectBody(BaseModel):
    protected: bool = True


@router.post("/api/sessions/{session_id}/protect", response_model=TrainingSessionRead)
def set_session_protected(session_id: int, body: ProtectBody, db: Session = Depends(get_db)):
    """Mark a session human-owned (or release it). While protected it refuses stop / reap /
    delete without force=true — the switch that keeps your live session safe from an automated step."""
    session = db.get(TrainingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    session.protected = bool(body.protected)
    db.commit()
    db.refresh(session)
    return session
