"""Drive-lock routes — the single-machine "CDP is driving; keyboard owned" latch.

A global latch (not per-session): the keyboard is one physical resource on the operator's machine.
The cockpit polls `GET /api/drive_lock` and shows a loud banner when locked; a drive (or the operator)
sets/clears it via `POST`. See `drive_lock.py` for why this is an ownership signal, not an OS seize.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

import drive_lock

router = APIRouter()


class DriveLockBody(BaseModel):
    locked: bool
    reason: str = ""
    holder: Optional[str] = None


@router.get("/api/drive_lock")
def get_drive_lock() -> dict:
    """The current latch state: `{locked, reason, holder, since}`. The cockpit polls this."""
    return drive_lock.state()


@router.post("/api/drive_lock")
def set_drive_lock(body: DriveLockBody) -> dict:
    """Engage (`locked=true`) or release (`locked=false`) the latch. The operator's release button
    posts `{locked:false}`; a drive engages `{locked:true, reason, holder}` on start."""
    if body.locked:
        return drive_lock.engage(body.reason, body.holder)
    return drive_lock.release()
