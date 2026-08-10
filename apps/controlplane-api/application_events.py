"""What happened to an application — the event vocabulary, and the status derived from it.

`Application.status` is a projection, not a field anyone sets. The events are the record; this
module is the only thing allowed to turn them into a single word. Keeping the derivation in one
pure function means the manual path the operator uses today and the Gmail/ATS pollers that will
write to the same table later cannot drift into disagreeing about what "responded" means.

--------------------------------------------------------------------------------------
An auto-acknowledgement is not a response
--------------------------------------------------------------------------------------
The question being built toward is *"which companies actually respond?"*, and it is worthless if
"Thank you for your application" counts. So the vocabulary splits three ways:

    what I did            applied, withdrawn, note
    automated noise       confirmation ("we received it"), viewed ("a recruiter opened it")
    an actual human       recruiter_contact, screening_invite, assessment, interview_invite,
                          rejection, offer

Only the third group is a RESPONSE. A rejection counts — it is a decision, and an employer that
rejects in nine days is behaving better than one that never writes back at all.

--------------------------------------------------------------------------------------
Ghosted is a question, not a status
--------------------------------------------------------------------------------------
"No reply in six weeks" is true of an application whose state never changed, so storing it as a
status would mean rewriting rows as time passes and having a column that is silently wrong until
some job re-runs. It is computed at read time instead — see `is_ghosted`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from deps import as_aware, utcnow
from models import Application, ApplicationEvent

#: Every kind an event may carry. Closed on purpose: a typo'd kind from a future Gmail matcher
#: would silently vanish from every rollup, so unknown kinds are rejected at the door.
EVENT_KINDS: tuple[str, ...] = (
    "applied",           # I submitted it
    "confirmation",      # automated "we received your application"
    "viewed",            # the posting/application was opened by the employer
    "recruiter_contact", # a human reached out
    "screening_invite",  # phone screen / recruiter call requested
    "assessment",        # take-home, coding test, personality survey
    "interview_invite",  # a real interview
    "offer",
    "rejection",
    "withdrawn",         # I pulled out
    "note",              # operator commentary, no state change
)

#: Where an event came from. The reason the timeline exists now rather than later: adding Gmail
#: or ATS polling adds VALUES here, not columns to the table.
EVENT_SOURCES: tuple[str, ...] = ("human", "gmail", "ats_portal", "agent")

#: Kinds that mean a person on the other side did something. The numerator of every response rate
#: in the dashboard — see the module docstring for why `confirmation` and `viewed` are excluded.
EMPLOYER_RESPONSE_KINDS = frozenset({
    "recruiter_contact", "screening_invite", "assessment", "interview_invite",
    "offer", "rejection",
})

#: How far along the process each kind implies we got. Compared by maximum, so events arriving
#: out of order (a rejection email found before the interview invite it followed) still resolve
#: to the furthest point actually reached.
_PROGRESS = {
    "applied": (1, "applied"),
    "confirmation": (2, "acknowledged"),
    "viewed": (3, "viewed"),
    "recruiter_contact": (4, "responded"),
    "screening_invite": (5, "screening"),
    "assessment": (5, "screening"),
    "interview_invite": (6, "interview"),
    "offer": (7, "offer"),
}

#: Kinds that END the application. Resolved by RECENCY rather than rank, because reaching an
#: interview and then being rejected is a rejection — the furthest point reached is history, and
#: the last decision is the status.
_TERMINAL = {"rejection": "rejected", "withdrawn": "withdrawn", "offer": "offer"}

#: No employer signal for this long and the application is, in practice, dead. Six weeks is a
#: reporting convention, not a fact about the world, which is exactly why it is a read-time
#: threshold and not a stored status.
GHOSTED_AFTER = timedelta(days=42)


def reduce_status(events: Iterable[ApplicationEvent]) -> str:
    """The one word that describes an application, derived from everything on its timeline."""
    ordered = sorted(events, key=lambda e: (as_aware(e.occurred_at), e.id or 0))
    if not ordered:
        return "applied"

    terminal = [e for e in ordered if e.kind in _TERMINAL]
    if terminal:
        return _TERMINAL[terminal[-1].kind]

    best = max((_PROGRESS.get(e.kind, (0, "applied")) for e in ordered), key=lambda p: p[0])
    return best[1] if best[0] else "applied"


def has_response(events: Iterable[ApplicationEvent]) -> bool:
    """Did a human on the employer's side ever engage? The definition behind every response rate."""
    return any(e.kind in EMPLOYER_RESPONSE_KINDS for e in events)


def first_response_at(events: Iterable[ApplicationEvent]) -> Optional[datetime]:
    """When the employer first engaged — the clock behind "how fast do they get back to you"."""
    hits = sorted(as_aware(e.occurred_at) for e in events if e.kind in EMPLOYER_RESPONSE_KINDS)
    return hits[0] if hits else None


def is_ghosted(app: Application, events: Iterable[ApplicationEvent], *,
               now: Optional[datetime] = None) -> bool:
    """Applied long enough ago, never answered, not yet closed out. Computed, never stored."""
    if app.status in ("rejected", "withdrawn", "offer") or has_response(events):
        return False
    if not app.applied_at:
        return False
    return (now or utcnow()) - as_aware(app.applied_at) > GHOSTED_AFTER


def mirror_application(db: Session, sighting, *, search_id: Optional[int] = None) -> None:
    """Give a just-applied sighting a canonical Application. Best-effort by design: the apply
    already happened out in the world, so failing to mirror it must not fail the request that
    records it. `/api/career_search/reindex` rebuilds anything missed.

    Lived in `main.py` and was called only by the MANUAL status endpoint — the live drive's
    submit seam (`_record_outcome`) updated the sighting and never created the Application, so
    "did we apply?" had two answers depending on which table you asked (found 2026-08-10). Moved
    here so every recorder of an applied sighting can reach it.

    Inside a SAVEPOINT, not a bare try/except: a plain `db.rollback()` would also discard the
    caller's own `application_status = applied` write, turning a cosmetic mirroring failure into
    losing the very fact being recorded. The nested transaction scopes the undo to this function.
    """
    import job_dedup  # function-local: job_dedup imports this module

    try:
        with db.begin_nested():
            job = job_dedup.resolve_sighting(db, sighting)
            if job is None:
                return
            db.flush()
            ensure_application(db, job.job_key, applied_at=sighting.applied_at,
                               via_platform=sighting.platform, ats=sighting.application_platform,
                               search_id=search_id)
            if job.status in ("new", ""):
                job.status = "applied"
    except Exception:  # noqa: BLE001 — see docstring: best-effort, scoped by the savepoint
        pass


def ensure_application(db: Session, job_key: str, *, applied_at: Optional[datetime] = None,
                       via_platform: Optional[str] = None, ats: Optional[str] = None,
                       search_id: Optional[int] = None) -> Application:
    """The application for a job, created on first use.

    One per job by construction (`Application.job_key` is unique), so applying twice through two
    doors — the failure mode that started all of this — updates one row instead of making a second.
    `search_id` is the provenance to the query that surfaced the job (models.Search) — recorded
    when the caller knows it, never invented.
    """
    app = db.scalar(select(Application).where(Application.job_key == job_key))
    if app is None:
        app = Application(job_key=job_key, applied_at=applied_at or utcnow(),
                          via_platform=via_platform, ats=ats, status="applied",
                          search_id=search_id)
        db.add(app)
        db.flush()
        record_event(db, app, kind="applied", source="agent" if via_platform else "human",
                     occurred_at=app.applied_at,
                     summary=f"Applied via {via_platform}" if via_platform else "Marked as applied")
    else:
        if applied_at and (app.applied_at is None
                           or as_aware(applied_at) < as_aware(app.applied_at)):
            app.applied_at = applied_at
        app.via_platform = app.via_platform or via_platform
        app.ats = app.ats or ats
        app.search_id = app.search_id or search_id
    return app


def record_event(db: Session, app: Application, *, kind: str, source: str = "human",
                 summary: str = "", occurred_at: Optional[datetime] = None,
                 evidence: Optional[dict[str, Any]] = None) -> ApplicationEvent:
    """Append one event and re-derive the application's status.

    Validation is strict because this is the seam every future observer writes through: an
    unrecognised kind from a Gmail matcher would not raise anywhere else, it would just quietly
    stop counting toward the response rate the operator is reading.
    """
    if kind not in EVENT_KINDS:
        raise ValueError(f"unknown event kind {kind!r}; expected one of {', '.join(EVENT_KINDS)}")
    if source not in EVENT_SOURCES:
        raise ValueError(f"unknown event source {source!r}; expected one of {', '.join(EVENT_SOURCES)}")

    ev = ApplicationEvent(application_id=app.id, kind=kind, source=source,
                          summary=(summary or "")[:500], evidence=evidence or {},
                          occurred_at=occurred_at or utcnow())
    db.add(ev)
    db.flush()

    events = list(db.scalars(select(ApplicationEvent)
                             .where(ApplicationEvent.application_id == app.id)).all())
    app.status = reduce_status(events)
    app.last_event_at = max(as_aware(e.occurred_at) for e in events)
    if kind == "applied" and (app.applied_at is None
                               or as_aware(ev.occurred_at) < as_aware(app.applied_at)):
        app.applied_at = ev.occurred_at
    return ev


def event_dict(ev: ApplicationEvent) -> dict[str, Any]:
    return {
        "id": ev.id, "kind": ev.kind, "source": ev.source, "summary": ev.summary,
        "evidence": ev.evidence or {},
        "occurred_at": ev.occurred_at.isoformat() if ev.occurred_at else None,
        "is_response": ev.kind in EMPLOYER_RESPONSE_KINDS,
    }


def application_dict(app: Application, events: Optional[list[ApplicationEvent]] = None,
                     *, now: Optional[datetime] = None) -> dict[str, Any]:
    evs = events if events is not None else list(app.events)
    responded_at = first_response_at(evs)
    applied_at = as_aware(app.applied_at)
    return {
        "id": app.id, "job_key": app.job_key, "status": app.status,
        "via_platform": app.via_platform, "ats": app.ats, "notes": app.notes,
        "applied_at": app.applied_at.isoformat() if app.applied_at else None,
        "last_event_at": app.last_event_at.isoformat() if app.last_event_at else None,
        "responded": responded_at is not None,
        "first_response_at": responded_at.isoformat() if responded_at else None,
        "days_to_response": ((responded_at - applied_at).days
                             if responded_at and applied_at else None),
        "ghosted": is_ghosted(app, evs, now=now),
        "events": [event_dict(e) for e in sorted(evs, key=lambda e: (as_aware(e.occurred_at), e.id or 0))],
    }
