"""Inbox sweep routes — the operator's handle on the Gmail → application-timeline loop.

Thin by contract (docs/PLAN_verify_email_leg.md Part 2): the sweep itself lives in
`inbox_sweep.sweep()` so the drive-end hook in `session_control` and this endpoint are the same
crank. These routes add only what HTTP needs — the live-tab read, the ledger listing, and the
human resolution of `needs_review` rows.

The sweep is its OWN crank, not a rider on `/reindex`: reindex is DB-only and must keep working
with no browser running, while a sweep needs a live signed-in Gmail tab. An unchanged inbox
re-sweeps to zero writes, so the button is always safe to press.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import inbox_sweep
from application_events import EVENT_KINDS, application_dict
from deps import get_db, utcnow
from models import InboxEmail
from schemas import StrictModel

router = APIRouter()


class SweepBody(StrictModel):
    #: Rows from any inbox reader, for offline runs and tests. When omitted, the live Gmail tab
    #: is read through the capture server — same knobs as the fetch_login_code errand.
    rows: Optional[list[dict[str, Any]]] = None
    #: None means DISCOVER: the signed-in google-profile browser usually runs on a per-session
    #: provisioned port, and `_gmail_browser_url` (the same seam the verify leg reads) knows
    #: which. An explicit value still wins, for tests and unusual setups.
    browser_url: Optional[str] = None
    tab_id: Optional[str] = None
    tab_url: str = "mail.google.com"


class ResolveBody(StrictModel):
    action: str  # confirm | dismiss
    #: Overrides for the matcher's prefill — the review screen offers the candidates; the human
    #: may also name a job or kind the matcher never proposed.
    job_key: Optional[str] = None
    kind: Optional[str] = None
    #: Why, on a dismissal — appended to the row's reasons so the labeled negative says what the
    #: human saw that the matcher did not.
    note: Optional[str] = None


@router.post("/api/career_search/inbox/sweep")
async def sweep_endpoint(body: SweepBody, db: Session = Depends(get_db)):
    """Run the matcher over the inbox (live tab unless rows are supplied) and write what is safe
    to write. See `inbox_sweep.sweep` for the record / needs_review / ignored contract.

    The live path IS `sweep_live` — the same call the drive-end hook makes — so the button and
    the hook cannot drift (the drift the inbox_sweep docstring promises against; this endpoint
    briefly re-implemented it and the review caught the copy)."""
    if body.rows is not None:
        return inbox_sweep.sweep(db, body.rows)
    if body.browser_url is None:
        # Function-local for the same reason session_control imports inbox_sweep at the call
        # site: the two routers otherwise stay independent.
        from routers.session_control import _gmail_browser_url
        browser_url = _gmail_browser_url(db)
    else:
        browser_url = body.browser_url
    return await inbox_sweep.sweep_live(db, browser_url=browser_url,
                                        tab_id=body.tab_id, tab_url=body.tab_url)


@router.get("/api/career_search/inbox")
def list_ledger(status: Optional[str] = Query(None), limit: int = Query(100, ge=1, le=500),
                db: Session = Depends(get_db)):
    """The ledger, newest first — the review queue when filtered to `needs_review`. Ignored rows
    are fingerprint-only stubs and excluded unless asked for."""
    stmt = select(InboxEmail).order_by(InboxEmail.created_at.desc(), InboxEmail.id.desc())
    stmt = stmt.where(InboxEmail.status == status) if status \
        else stmt.where(InboxEmail.status != "ignored")
    rows = list(db.scalars(stmt.limit(limit)).all())
    # The TRUE queue depth, not the page length — the badge reads this, and a page-capped count
    # would understate the backlog exactly when it matters (review finding: `total` is post-limit).
    pending = db.scalar(select(func.count()).select_from(InboxEmail)
                        .where(InboxEmail.status == "needs_review")) or 0
    return {"total": len(rows), "pending": pending, "has_pending": pending > 0,
            "emails": [inbox_sweep.ledger_dict(r) for r in rows]}


@router.post("/api/career_search/inbox/{ledger_id}/resolve")
def resolve_review(ledger_id: int, body: ResolveBody, db: Session = Depends(get_db)):
    """Resolve one review row: `confirm` writes the event (with the human's job/kind standing in
    for the matcher's guess), `dismiss` says this mail is not ours. Either way the row keeps its
    evidence — a dismissal is a labeled negative the phrase lists can be tuned against."""
    row = db.get(InboxEmail, ledger_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no such inbox row: {ledger_id}")
    if row.status != "needs_review":
        raise HTTPException(status_code=409, detail=f"row {ledger_id} is already {row.status}")

    if body.action == "dismiss":
        row.status, row.decided_by, row.decided_at = "dismissed", "human", utcnow()
        if body.note:
            row.reasons = [*(row.reasons or []), f"dismissed: {body.note[:200]}"]
        db.commit()
        return {"ok": True, "email": inbox_sweep.ledger_dict(row)}
    if body.action != "confirm":
        raise HTTPException(status_code=422, detail="action must be confirm or dismiss")

    job_key = body.job_key or row.job_key
    kind = body.kind or row.kind
    if not job_key or not kind:
        raise HTTPException(status_code=422,
                            detail="confirming needs a job_key and a kind — the matcher could "
                                   "not fill one in, so the human must")
    if kind not in EVENT_KINDS:
        raise HTTPException(status_code=422,
                            detail=f"kind must be one of {', '.join(EVENT_KINDS)}")

    reader_row = {"sender": f"{row.from_address} {row.sender_name}".strip(),
                  "subject": row.subject, "snippet": row.snippet,
                  "received_at": row.received_at.isoformat() if row.received_at else None,
                  # The stored identity, not a recompute — the ledger's datetime round-trips
                  # differently than the reader emitted it, so a recompute would never join back.
                  "fingerprint": row.fingerprint}
    try:
        app, ev = inbox_sweep.write_event(db, job_key, kind, reader_row, ats_id=row.ats_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row.status, row.decided_by, row.decided_at = "confirmed", "human", utcnow()
    row.job_key, row.kind, row.event_id = app.job_key, kind, ev.id
    db.commit()
    return {"ok": True, "email": inbox_sweep.ledger_dict(row), "application": application_dict(app)}
