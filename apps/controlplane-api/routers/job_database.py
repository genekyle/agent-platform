"""The career-search job database — one cross-platform table, and the questions asked of it.

Every existing jobs route is scoped to ONE engine (`/api/dashboards/indeed_jobs`), which was fine
while a job was whatever Indeed said it was. It stops being fine the moment the same posting is
on two boards: "how many jobs have I found" then has two answers and neither is right.

These routes read `Job` — the canonical entity — so the grain is one row per real posting no
matter how many boards surfaced it or how many times a sweep re-saw it. `platform` survives as a
FILTER rather than a partition, which is the only way the Indeed and LinkedIn tabs and the
cross-platform view can agree on a number.

Three groups of routes, matching the three things the operator asked to be able to do:

    read      /jobs, /jobs/{key}, /overview      — see everything, including the descriptions
    dedupe    /duplicates + merge / reject       — the review queue; only humans fold a job away
    track     /apply, /events, /status           — record what happened, manually today
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

import job_dedup
from application_events import (
    EMPLOYER_RESPONSE_KINDS,
    EVENT_KINDS,
    EVENT_SOURCES,
    application_dict,
    ensure_application,
    is_ghosted,
    record_event,
)
from deps import as_aware, get_db, utcnow
from models import Application, ApplicationEvent, Job, JobMatch, ObservedJob
from schemas import StrictModel

router = APIRouter()

#: The triage states the operator may put a job in. `applied` is not here on purpose — a job
#: becomes applied by recording an application, not by relabelling it, or the two can disagree.
JOB_STATUSES = ("new", "shortlisted", "skipped", "closed")


# --------------------------------------------------------------------------------------
# Request bodies
# --------------------------------------------------------------------------------------

class MarkAppliedBody(StrictModel):
    applied_at: Optional[datetime] = None
    via_platform: Optional[str] = None
    ats: Optional[str] = None
    notes: Optional[str] = None


class EventBody(StrictModel):
    kind: str
    source: str = "human"
    summary: str = ""
    occurred_at: Optional[datetime] = None
    evidence: Optional[dict[str, Any]] = None


class StatusBody(StrictModel):
    status: str
    notes: Optional[str] = None


class MergeBody(StrictModel):
    #: Lets the operator flip which row survives — the proposal picks the older one, but the
    #: newer one is sometimes the better record (it has the ATS url, or the real company name).
    keep: Optional[str] = None


# --------------------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------------------

def _job_dict(job: Job, *, app: Optional[Application] = None,
              events: Optional[list[ApplicationEvent]] = None,
              with_description: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "job_key": job.job_key,
        "title": job.title, "company": job.company, "location": job.location,
        "url": job.canonical_url, "salary": job.salary,
        "ats": job.ats, "requisition_id": job.requisition_id,
        "status": job.status, "notes": job.notes,
        "source_platforms": job.source_platforms or [],
        "sighting_count": job.sighting_count, "seen_count": job.seen_count,
        "has_description": bool((job.description or "").strip()),
        "description_source": job.description_source,
        "first_seen_at": job.first_seen_at.isoformat() if job.first_seen_at else None,
        "last_seen_at": job.last_seen_at.isoformat() if job.last_seen_at else None,
        "application": None,
    }
    if with_description:
        out["description"] = job.description or ""
    if app is not None:
        out["application"] = application_dict(app, events)
    return out


@router.get("/api/career_search/jobs")
def list_jobs(
    q: Optional[str] = None,
    company: Optional[str] = None,
    platform: Optional[str] = None,
    ats: Optional[str] = None,
    status: Optional[str] = None,
    application_status: Optional[str] = None,
    has_description: Optional[bool] = None,
    applied: Optional[bool] = None,
    responded: Optional[bool] = None,
    sort: str = "last_seen",
    limit: int = Query(100, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """The job table, filtered. Merged-away jobs are never listed — only ever followed to.

    `platform` filters on `source_platforms` rather than partitioning the table, so asking for
    LinkedIn returns jobs SEEN on LinkedIn, including the ones also seen on Indeed. That double
    counting is the honest answer: it is one job, and both boards found it.
    """
    stmt = select(Job).where(Job.merged_into_key.is_(None))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Job.title.ilike(like), Job.company.ilike(like),
                              Job.description.ilike(like)))
    if company:
        stmt = stmt.where(Job.company_norm == job_dedup.normalize_company(company))
    if ats:
        stmt = stmt.where(Job.ats == ats)
    if status:
        stmt = stmt.where(Job.status == status)
    if has_description is not None:
        filled = func.coalesce(Job.description, "") != ""
        stmt = stmt.where(filled if has_description else ~filled)

    jobs = list(db.scalars(stmt).all())
    if platform:
        jobs = [j for j in jobs if platform in (j.source_platforms or [])]

    apps = _applications_by_key(db, [j.job_key for j in jobs])
    events = _events_by_application(db, [a.id for a in apps.values()])

    if applied is not None:
        jobs = [j for j in jobs if (j.job_key in apps) == applied]
    if application_status:
        jobs = [j for j in jobs if j.job_key in apps
                and apps[j.job_key].status == application_status]
    if responded is not None:
        def _responded(j: Job) -> bool:
            a = apps.get(j.job_key)
            return bool(a) and any(e.kind in EMPLOYER_RESPONSE_KINDS
                                   for e in events.get(a.id, []))
        jobs = [j for j in jobs if _responded(j) == responded]

    jobs.sort(key=_sort_key(sort, apps), reverse=True)
    total = len(jobs)
    page = jobs[offset:offset + limit]
    return {
        "total": total, "limit": limit, "offset": offset,
        "jobs": [_job_dict(j, app=apps.get(j.job_key),
                           events=events.get(getattr(apps.get(j.job_key), "id", None), []))
                 for j in page],
    }


def _sort_key(sort: str, apps: dict[str, Application]):
    """Sort keys that never compare None against a datetime — every branch returns one type."""
    floor = datetime.min.replace(tzinfo=utcnow().tzinfo)
    if sort == "seen":
        return lambda j: (j.seen_count or 0)
    if sort == "first_seen":
        return lambda j: as_aware(j.first_seen_at) or floor
    if sort == "applied":
        return lambda j: as_aware(getattr(apps.get(j.job_key), "applied_at", None)) or floor
    if sort == "company":
        # Reversed by the caller, so invert to keep A–Z reading the way a person expects.
        return lambda j: tuple(-ord(c) for c in (j.company or "").lower()[:40])
    return lambda j: as_aware(j.last_seen_at) or floor


def _applications_by_key(db: Session, keys: list[str]) -> dict[str, Application]:
    """One query for the whole page, not one per row — the list view is the hot path."""
    if not keys:
        return {}
    rows = db.scalars(select(Application).where(Application.job_key.in_(keys))).all()
    return {a.job_key: a for a in rows}


def _events_by_application(db: Session, ids: list[int]) -> dict[int, list[ApplicationEvent]]:
    if not ids:
        return {}
    out: dict[int, list[ApplicationEvent]] = {}
    for e in db.scalars(select(ApplicationEvent)
                        .where(ApplicationEvent.application_id.in_(ids))).all():
        out.setdefault(e.application_id, []).append(e)
    return out


@router.get("/api/career_search/jobs/{job_key}")
def get_job(job_key: str, db: Session = Depends(get_db)):
    """One job in full: the description, every sighting behind it, and the application timeline.

    Goes through `resolve_key`, so a key saved before a merge still lands on the right job rather
    than 404ing — that promise is the reason merges tombstone instead of deleting.
    """
    alive = job_dedup.resolve_key(db, job_key)
    job = db.get(Job, alive) if alive else None
    if job is None:
        raise HTTPException(status_code=404, detail=f"no such job: {job_key}")

    app = db.scalar(select(Application).where(Application.job_key == job.job_key))
    events = list(db.scalars(select(ApplicationEvent)
                             .where(ApplicationEvent.application_id == app.id)).all()) if app else []
    sightings = db.scalars(select(ObservedJob)
                           .where(ObservedJob.canonical_job_key == job.job_key)).all()
    merged = db.scalars(select(Job).where(Job.merged_into_key == job.job_key)).all()

    out = _job_dict(job, app=app, events=events, with_description=True)
    out["redirected_from"] = job_key if alive != job_key else None
    out["sightings"] = [{
        "job_id": s.job_id, "platform": s.platform, "url": s.url,
        "seen_count": s.seen_count, "search_queries": s.search_queries or [],
        "title": s.title, "company": s.company,
        "first_seen_at": s.first_seen_at.isoformat() if s.first_seen_at else None,
        "last_seen_at": s.last_seen_at.isoformat() if s.last_seen_at else None,
    } for s in sightings]
    out["merged_jobs"] = [{"job_key": m.job_key, "title": m.title, "company": m.company}
                          for m in merged]
    return out


@router.get("/api/career_search/overview")
def overview(db: Session = Depends(get_db)):
    """The headline numbers and the rollups behind them.

    Response rate is reported against APPLICATIONS, never against jobs found — dividing replies by
    postings-seen would produce a number that falls every time a sweep runs well.
    """
    jobs = list(db.scalars(select(Job).where(Job.merged_into_key.is_(None))).all())
    by_key = {j.job_key: j for j in jobs}
    apps = list(db.scalars(select(Application)).all())
    events = _events_by_application(db, [a.id for a in apps])
    now = utcnow()

    responded = [a for a in apps
                 if any(e.kind in EMPLOYER_RESPONSE_KINDS for e in events.get(a.id, []))]
    ghosted = [a for a in apps if is_ghosted(a, events.get(a.id, []), now=now)]
    with_desc = [j for j in jobs if (j.description or "").strip()]

    per_company: dict[str, dict[str, Any]] = {}
    for a in apps:
        job = by_key.get(a.job_key)
        if job is None:
            continue
        row = per_company.setdefault(job.company or "unknown",
                                     {"company": job.company or "unknown", "applied": 0,
                                      "responded": 0, "rejected": 0, "interviewing": 0})
        row["applied"] += 1
        if any(e.kind in EMPLOYER_RESPONSE_KINDS for e in events.get(a.id, [])):
            row["responded"] += 1
        if a.status == "rejected":
            row["rejected"] += 1
        if a.status in ("interview", "screening", "offer"):
            row["interviewing"] += 1

    return {
        "totals": {
            "jobs": len(jobs),
            "sightings": db.scalar(select(func.count()).select_from(ObservedJob)) or 0,
            "duplicates_collapsed": sum(max((j.sighting_count or 1) - 1, 0) for j in jobs),
            "observations": sum((j.seen_count or 0) for j in jobs),
            "with_description": len(with_desc),
            "description_coverage": round(len(with_desc) / len(jobs), 3) if jobs else 0.0,
            "applied": len(apps),
            "responded": len(responded),
            "ghosted": len(ghosted),
            "response_rate": round(len(responded) / len(apps), 3) if apps else 0.0,
            "pending_duplicates": db.scalar(
                select(func.count()).select_from(JobMatch).where(JobMatch.status == "pending")) or 0,
        },
        "by_platform": _platform_rollup(jobs, by_key, apps),
        "by_application_status": job_dedup.summarize(a.status for a in apps),
        "by_job_status": job_dedup.summarize(j.status for j in jobs),
        "by_ats": job_dedup.summarize(j.ats for j in jobs if j.ats),
        "by_company": sorted(per_company.values(),
                             key=lambda r: (-r["applied"], r["company"]))[:25],
        "description_by_platform": _description_rollup(jobs),
    }


def _platform_rollup(jobs: list[Job], by_key: dict[str, Job],
                     apps: list[Application]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for j in jobs:
        for p in (j.source_platforms or ["unknown"]):
            row = rows.setdefault(p, {"platform": p, "jobs": 0, "with_description": 0, "applied": 0})
            row["jobs"] += 1
            if (j.description or "").strip():
                row["with_description"] += 1
    for a in apps:
        job = by_key.get(a.job_key)
        for p in (job.source_platforms or []) if job else []:
            if p in rows:
                rows[p]["applied"] += 1
    return sorted(rows.values(), key=lambda r: -r["jobs"])


def _description_rollup(jobs: list[Job]) -> list[dict[str, Any]]:
    """Description coverage per board — the number that answers "is LinkedIn capturing JDs yet"."""
    rows: dict[str, dict[str, Any]] = {}
    for j in jobs:
        for p in (j.source_platforms or ["unknown"]):
            row = rows.setdefault(p, {"platform": p, "jobs": 0, "with_description": 0})
            row["jobs"] += 1
            if (j.description or "").strip():
                row["with_description"] += 1
    for row in rows.values():
        row["coverage"] = round(row["with_description"] / row["jobs"], 3) if row["jobs"] else 0.0
    return sorted(rows.values(), key=lambda r: -r["jobs"])


# --------------------------------------------------------------------------------------
# Deduplication
# --------------------------------------------------------------------------------------

@router.get("/api/career_search/duplicates")
def list_duplicates(status: str = "pending", limit: int = Query(100, le=500),
                    db: Session = Depends(get_db)):
    """The review queue, strongest evidence first, with both jobs inlined so the operator can
    decide without opening two tabs."""
    rows = list(db.scalars(select(JobMatch).where(JobMatch.status == status)
                           .order_by(JobMatch.score.desc(), JobMatch.created_at.desc())
                           .limit(limit)).all())
    out = []
    for m in rows:
        kept, folded = db.get(Job, m.kept_key), db.get(Job, m.folded_key)
        if kept is None or folded is None:
            continue
        out.append({
            "id": m.id, "tier": m.tier, "score": m.score, "status": m.status,
            "evidence": m.evidence or [], "decided_by": m.decided_by,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "keep": _job_dict(kept), "fold": _job_dict(folded),
        })
    return {"total": len(out), "duplicates": out}


@router.post("/api/career_search/duplicates/{match_id}/merge")
def merge_duplicate(match_id: int, body: MergeBody, db: Session = Depends(get_db)):
    """Confirm two jobs are one. The folded row keeps its key and gains a tombstone."""
    m = db.get(JobMatch, match_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"no such match: {match_id}")
    if m.status != "pending":
        raise HTTPException(status_code=409, detail=f"match {match_id} is already {m.status}")

    kept_key, folded_key = m.kept_key, m.folded_key
    if body.keep and body.keep not in (kept_key, folded_key):
        raise HTTPException(status_code=400,
                            detail=f"{body.keep} is not part of match {match_id}")
    if body.keep == folded_key:
        kept_key, folded_key = folded_key, kept_key

    try:
        job = job_dedup.apply_merge(db, kept_key, folded_key)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    m.status, m.decided_by, m.decided_at = "merged", "human", utcnow()
    m.kept_key, m.folded_key = kept_key, folded_key
    db.commit()
    return {"ok": True, "kept": _job_dict(job), "folded": folded_key}


@router.post("/api/career_search/duplicates/{match_id}/reject")
def reject_duplicate(match_id: int, db: Session = Depends(get_db)):
    """Say these are two different jobs. Remembered, so the pair is never proposed again."""
    m = db.get(JobMatch, match_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"no such match: {match_id}")
    if m.status != "pending":
        raise HTTPException(status_code=409, detail=f"match {match_id} is already {m.status}")
    m.status, m.decided_by, m.decided_at = "rejected", "human", utcnow()
    db.commit()
    return {"ok": True, "id": match_id, "status": "rejected"}


@router.post("/api/career_search/reindex")
def reindex(db: Session = Depends(get_db)):
    """Resolve any unresolved sightings, recount, and rescan for duplicates. Idempotent."""
    return {"ok": True, **job_dedup.backfill(db)}


# --------------------------------------------------------------------------------------
# Tracking what happened
# --------------------------------------------------------------------------------------

def _live_job(db: Session, job_key: str) -> Job:
    alive = job_dedup.resolve_key(db, job_key)
    job = db.get(Job, alive) if alive else None
    if job is None:
        raise HTTPException(status_code=404, detail=f"no such job: {job_key}")
    return job


@router.post("/api/career_search/jobs/{job_key}/apply")
def mark_applied(job_key: str, body: MarkAppliedBody, db: Session = Depends(get_db)):
    """Record that an application exists for this job, creating its timeline.

    Idempotent: applying twice through two doors updates one application rather than making a
    second, which is the whole failure this table was added to prevent.
    """
    job = _live_job(db, job_key)
    app = ensure_application(db, job.job_key, applied_at=body.applied_at,
                             via_platform=body.via_platform, ats=body.ats)
    if body.notes:
        app.notes = "\n".join(n for n in (app.notes, body.notes) if n)
    job.status = "applied"
    db.commit()
    return {"ok": True, "application": application_dict(app)}


@router.post("/api/career_search/jobs/{job_key}/events")
def add_event(job_key: str, body: EventBody, db: Session = Depends(get_db)):
    """Append something that happened — a rejection, a recruiter email, an interview invite.

    Manual today (`source: human`); the Gmail matcher and ATS pollers write through this same
    function with their own `source` and their evidence attached, which is why the vocabulary is
    validated here rather than trusted from the caller.
    """
    job = _live_job(db, job_key)
    app = db.scalar(select(Application).where(Application.job_key == job.job_key))
    if app is None:
        # An employer replying is proof an application exists, whatever the table currently says.
        app = ensure_application(db, job.job_key, applied_at=body.occurred_at)
        job.status = "applied"
    try:
        record_event(db, app, kind=body.kind, source=body.source, summary=body.summary,
                     occurred_at=body.occurred_at, evidence=body.evidence)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return {"ok": True, "application": application_dict(app)}


@router.post("/api/career_search/jobs/{job_key}/status")
def set_status(job_key: str, body: StatusBody, db: Session = Depends(get_db)):
    """Set the operator's own triage state for a job (shortlisted, skipped, …)."""
    if body.status not in JOB_STATUSES:
        raise HTTPException(status_code=422,
                            detail=f"status must be one of {', '.join(JOB_STATUSES)}")
    job = _live_job(db, job_key)
    job.status = body.status
    if body.notes is not None:
        job.notes = body.notes
    db.commit()
    return {"ok": True, "job": _job_dict(job)}


@router.get("/api/career_search/event_vocabulary")
def event_vocabulary():
    """What the UI is allowed to record, straight from the module that validates it — so the
    dropdown and the validator cannot drift apart."""
    return {
        "kinds": [{"kind": k, "is_response": k in EMPLOYER_RESPONSE_KINDS} for k in EVENT_KINDS],
        "sources": list(EVENT_SOURCES),
        "job_statuses": list(JOB_STATUSES),
    }
