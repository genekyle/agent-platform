"""The inbox sweep — one crank of the Gmail → application-timeline loop.

Its own module by contract (docs/PLAN_verify_email_leg.md Part 2): the cockpit's sweep endpoint
and the drive-end hook in `session_control` both enter through `sweep()`, so the drive-end wiring
is one line and the two callers cannot drift. `inbox_matcher` stays pure (no DB, no browser);
this module owns persistence, idempotency, and the event writes.

A sweep is always safe to run: rows already in the `inbox_emails` ledger are skipped by
fingerprint, so an unchanged inbox sweeps to zero writes. Blocked is honest, never silently
empty (the errands lesson): unreachable browser, signed-out profile, and list-not-found each
return `{ok: False, blocked: …}` — none of them is "no mail".
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

import inbox_matcher
import job_dedup
from application_events import ensure_application, record_event
from deps import utcnow
from models import Application, AtsFlow, InboxEmail, Job


def applications_for_matching(db: Session) -> list[dict[str, Any]]:
    """Every application joined to its job — the candidate set the matcher works against."""
    rows = db.execute(select(Application, Job).join(Job, Job.job_key == Application.job_key)).all()
    return [{"job_key": app.job_key, "company": job.company, "company_norm": job.company_norm,
             "title": job.title, "ats": app.ats or job.ats} for app, job in rows]


def write_event(db: Session, job_key: str, kind: str, row: dict[str, Any],
                *, ats_id: Optional[str]) -> tuple[Any, Any]:
    """One gmail-sourced event on the right application, resolved through merge tombstones.

    An unresolvable key RAISES rather than falling back to the raw string: the fallback would
    mint a phantom Application on a job no view can see (every job view starts from `Job`), which
    is exactly the two-answers drift this ledger exists to prevent. The matcher's own keys come
    from applications joined to jobs and always resolve — this guard is for the human free-text
    path, where a typo'd paste must be a 422, not a 200.
    """
    alive = job_dedup.resolve_key(db, job_key)
    if alive is None:
        raise ValueError(f"no such job: {job_key!r} — events attach to jobs the ledger knows")
    app = db.scalar(select(Application).where(Application.job_key == alive))
    if app is None:
        # An employer writing about an application is proof one exists (same rule as add_event).
        app = ensure_application(db, alive)
    ev = record_event(db, app, kind=kind, source="gmail",
                     summary=str(row.get("subject") or "")[:500],
                     occurred_at=inbox_matcher.parse_received_at(row),
                     evidence=inbox_matcher.event_evidence(row, ats_id=ats_id))
    return app, ev


def flow_witness(db: Session, job_key: str) -> list[int]:
    """ats_flows for this job that never recorded a terminal — the confirmation mail is their
    second, durable witness. Surfaced, never written: `terminal` describes what the DRIVE did,
    and an email cannot retroactively change that."""
    return list(db.scalars(select(AtsFlow.id).where(AtsFlow.job_key == job_key,
                                                    AtsFlow.terminal.is_(None))).all())


def ledger_dict(row: InboxEmail) -> dict[str, Any]:
    return {
        "id": row.id, "fingerprint": row.fingerprint, "status": row.status,
        "from_address": row.from_address, "sender_name": row.sender_name,
        "subject": row.subject, "snippet": row.snippet,
        "received_at": row.received_at.isoformat() if row.received_at else None,
        "ats_id": row.ats_id, "kind": row.kind, "job_key": row.job_key,
        "candidates": row.candidates or [], "reasons": row.reasons or [],
        "event_id": row.event_id, "decided_by": row.decided_by,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def read_live_inbox(browser_url: str = "http://127.0.0.1:9222",
                          tab_id: Optional[str] = None,
                          tab_url: str = "mail.google.com") -> dict[str, Any]:
    """The live Gmail tab through the capture server, with the three blocked states named.
    Returns `{ok: True, rows: […]}` or `{ok: False, blocked: reason}`."""
    from routers.errands import _capture_post  # the one seam for capture-server POSTs

    read = await _capture_post("/read_inbox", {"browser_url": browser_url,
                                               "tab_id": tab_id, "tab_url": tab_url})
    if not read.get("ok"):
        return {"ok": False, "blocked": f"could not read the inbox: "
                                        f"{read.get('detail') or 'unknown error'}"}
    if read.get("signed_in") is False:
        return {"ok": False, "blocked": "the shared Google profile is signed out — one "
                                        "supervised sign-in unblocks it"}
    if not read.get("list_found"):
        return {"ok": False, "blocked": "reached the tab but could not find the inbox list — "
                                        "a stale tab or an unknown layout, not 'no mail'"}
    return {"ok": True, "rows": read.get("rows") or []}


async def sweep_live(db: Session, browser_url: str = "http://127.0.0.1:9222",
                     tab_id: Optional[str] = None,
                     tab_url: str = "mail.google.com") -> dict[str, Any]:
    """The drive-end hook: read the live Gmail tab and sweep it, in one call.

    NEVER raises — this rides the close-out epilogue, and a cleanup's job is to report what
    happened, not to 500 halfway through it. An unreachable browser, signed-out profile, or
    unknown layout comes back as `{ok: False, blocked: …}`, same as the endpoint."""
    try:
        read = await read_live_inbox(browser_url=browser_url, tab_id=tab_id, tab_url=tab_url)
        if not read.get("ok"):
            return read
        return sweep(db, read["rows"])
    except Exception as exc:  # noqa: BLE001 — see docstring
        return {"ok": False, "blocked": f"{type(exc).__name__}: {exc}"}


def sweep(db: Session, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Run the matcher over inbox rows and write what is safe to write. Commits.

    Each NEW row lands in exactly one bucket: `recorded` (event written unattended — unambiguous
    company match + distinctive automated phrasing), `needs_review` (application-related, human
    glance needed), or `ignored` (personal mail — fingerprint kept for idempotency, content
    deliberately not; see the InboxEmail docstring).
    """
    applications = applications_for_matching(db)
    known = set(db.scalars(select(InboxEmail.fingerprint)).all())

    recorded: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    ignored = skipped = 0

    for row in rows:
        fp = inbox_matcher.fingerprint(row)
        if fp in known:
            skipped += 1
            continue
        known.add(fp)

        decision = inbox_matcher.decide(row, applications)
        address, name = inbox_matcher.sender_address(str(row.get("sender") or ""))

        if decision.action == inbox_matcher.IGNORE:
            # Fingerprint only — personal mail is never persisted (see the model docstring).
            db.add(InboxEmail(fingerprint=fp, status="ignored", decided_by="auto",
                              decided_at=utcnow(), reasons=decision.reasons))
            ignored += 1
            continue

        ledger = InboxEmail(
            fingerprint=fp, from_address=address, sender_name=name,
            subject=str(row.get("subject") or "")[:300],
            snippet=str(row.get("snippet") or "")[:300],
            received_at=inbox_matcher.parse_received_at(row),
            ats_id=decision.ats_id, kind=decision.kind, job_key=decision.job_key,
            candidates=[c.as_dict() for c in decision.candidates], reasons=decision.reasons,
            status="needs_review",
        )
        db.add(ledger)

        if decision.action == inbox_matcher.RECORD:
            app, ev = write_event(db, decision.job_key, decision.kind, row,
                                  ats_id=decision.ats_id)
            ledger.status, ledger.decided_by, ledger.decided_at = "recorded", "auto", utcnow()
            # The ALIVE key, post-tombstone — the event landed there, and an audit join through
            # the ledger must land on the same row (the confirm path already does this).
            ledger.job_key = app.job_key
            db.flush()
            ledger.event_id = ev.id
            entry = {**ledger_dict(ledger), "application_status": app.status}
            if decision.kind == "confirmation":
                entry["flow_terminal_witness"] = flow_witness(db, app.job_key)
            recorded.append(entry)
        else:
            db.flush()
            review.append(ledger_dict(ledger))

    db.commit()
    return {"ok": True, "read": len(rows), "skipped_known": skipped, "ignored": ignored,
            "recorded": recorded, "needs_review": review}
