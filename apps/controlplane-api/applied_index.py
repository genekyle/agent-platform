"""Have we applied to this job already? — asked of the DATABASE, before we spend a drive finding out.

Operator, 2026-07-27, after a full drive ended on Workday's *"You've already applied for this job"*:
*"we need logic on whether we applied to things or not and that needs to be checked on initial
landing on a page and scan for the jobs, we need to check the name, check everything we have on it
and see if we applied in the database or not."*

The cost of not asking is the whole reason this exists. That drive reopened a step, re-walked the
ladder, hopped a branded wrapper into Workday, signed in — and the answer was on file the whole
time. Every one of those actions is real traffic against a real account.

--------------------------------------------------------------------------------------
Why a job_id lookup is not enough
--------------------------------------------------------------------------------------
`ObservedJob` is keyed by `platform:external_id`, and the SAME job carries different ids depending
on where you meet it:

    Indeed card          indeed:e5c794ae32973697       (a jk, and jk rotates per search session)
    employer wrapper     jobs.bilh.org/...-jr88822/    (the req id, in the path)
    the ATS itself       bilh.wd1.myworkdayjobs.com/... (the same req, different host)

So a job applied to through Workday is invisible to a lookup keyed on the Indeed jk. Matching has
to use everything we hold — the id, the requisition id, and the (company, title) pair — and it has
to say WHICH of those matched, because they are not equally trustworthy.

--------------------------------------------------------------------------------------
Four tiers, and why the weakest one still earns its place
--------------------------------------------------------------------------------------
    exact       the same job_id                      — certain
    canonical   the same canonical Job (merged)      — certain; the CROSS-ENGINE tier
    requisition a req id shared by both records      — certain enough to act on
    fuzzy       same company, same role words        — a WARNING, never a decision

The canonical tier exists because exact ids can never match across engines — the platform prefix
is part of the id — so a job applied through Indeed met again on LinkedIn could only ever fuzzy-
warn, every search, forever (Joslin did exactly that, 08-17 → 08-20). Once the two sightings are
folded into one Job — by the matcher, the duplicates queue, or the operator's own already-applied
call — the answer is CERTAIN from either engine's side. Operator, 2026-08-20: *"indeed can search
into linkedin's db and vice versa to confirm because reducing errors on if we applied or not
helps us from wasting time."*

The fuzzy tier exists because company+title is often all we have, and it is right far more often
than it is wrong. But it is exactly the match that would wrongly skip "Data Analyst II" for having
applied to "Data Analyst I", so it never reports `applied` — it reports `likely_applied`, which the
ladder surfaces to the operator instead of acting on. **A near-miss that silently skips a job the
operator picked is worse than one that asks.**

Pure except for the query itself: the comparison primitives now live in `job_dedup` (imported
below, no DB in sight), so the interesting half is testable without a database — and so the same
scoring answers both this question and "are these two rows one job?".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

# The matching primitives live in `job_dedup` and are re-exported here, because "have I applied
# to this?" and "are these one job?" are the same comparison asked for two purposes. They used to
# be two implementations; running the older one over the real 355-row corpus on 2026-07-30 showed
# it equating 'Financial Analyst II' with 'Financial Analyst III'. One implementation means a fix
# lands in both questions at once — see the `job_dedup` module docstring for the failures it fixes.
from job_dedup import (  # noqa: F401  (re-exported for callers and tests)
    FUZZY_TITLE_THRESHOLD,
    normalize_company,
    requisition_ids,
    resolve_key,
    title_similarity,
)
from models import Application, ObservedJob

#: `application_status` values that mean an application exists. `applied` is the only one the
#: epilogue stamps; the others are historical/manual and treated the same way on purpose — the
#: question being asked is "is there an application on file", not "who put it there".
APPLIED_STATUSES = frozenset({"applied", "submitted"})

#: What the caller gets back.
STATUS_APPLIED = "applied"                # certain: same job, or same requisition
STATUS_LIKELY = "likely_applied"          # company + role words line up; ASK, do not act
STATUS_NONE = "not_applied"               # nothing on file

@dataclass
class AppliedVerdict:
    """What the database knows about whether this job has been applied to."""

    status: str = STATUS_NONE
    matched_on: str = ""                      # exact | requisition | company_title
    job_id: Optional[str] = None              # the row that matched
    title: str = ""
    company: str = ""
    applied_at: Optional[str] = None
    platform: Optional[str] = None
    evidence: list[str] = field(default_factory=list)

    @property
    def applied(self) -> bool:
        """Certain enough to ACT on — skip the job rather than ask about it."""
        return self.status == STATUS_APPLIED

    @property
    def worth_asking(self) -> bool:
        return self.status == STATUS_LIKELY

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "matched_on": self.matched_on, "job_id": self.job_id,
                "title": self.title, "company": self.company, "applied_at": self.applied_at,
                "platform": self.platform, "evidence": list(self.evidence)}


def check(db: Session, *, job_id: str = "", title: str = "", company: str = "",
          url: str = "", tenant_id: str = "") -> AppliedVerdict:
    """Has this job been applied to? Ask with everything you hold; the tiers sort themselves out.

    Every argument is optional because callers know different amounts: a results card has a jk and
    a title, a landing page has a url and a heading, an apply step has all of it.
    """
    rows = _applied_rows(db, company=company)

    # Tier 1 — the same job, by id. Nothing beats this.
    if job_id:
        for r in rows:
            if r.job_id == job_id:
                return _verdict(r, STATUS_APPLIED, "exact", [f"job_id {job_id}"])

    # Tier 1.5 — the same CANONICAL job, applied through another engine's door. Exact ids only
    # ever match within one engine (the prefix is part of the id), so this is what lets Indeed's
    # scan see LinkedIn's applications and vice versa: once two sightings are one Job, either
    # engine's next encounter is CERTAIN instead of a fuzzy warning re-judged every search.
    if job_id:
        canonical = _canonical_match(db, job_id, rows)
        if canonical is not None:
            return canonical

    # Tier 2 — the same requisition, seen through a different door. This is the tier that would
    # have caught BIDMC: applied through Workday, met again as an Indeed jk.
    wanted_reqs = requisition_ids(url, tenant_id, job_id)
    if wanted_reqs:
        for r in rows:
            shared = wanted_reqs & requisition_ids(r.url, r.tenant_id, r.external_id)
            if shared:
                return _verdict(r, STATUS_APPLIED, "requisition",
                                [f"requisition {sorted(shared)[0]}"])

    # Tier 3 — same employer, same role. A WARNING: reported, never acted on.
    if company and title:
        want_company = normalize_company(company)
        best: Optional[tuple[float, ObservedJob]] = None
        for r in rows:
            if normalize_company(r.company) != want_company:
                continue
            score = title_similarity(title, r.title)
            if score >= FUZZY_TITLE_THRESHOLD and (best is None or score > best[0]):
                best = (score, r)
        if best is not None:
            score, r = best
            return _verdict(r, STATUS_LIKELY, "company_title",
                            [f"same employer ({r.company})",
                             f"title overlap {score:.0%} with {r.title!r}"])

    return AppliedVerdict()


def check_many(db: Session, cards: list[dict[str, Any]], *, platform: str = "indeed"
               ) -> dict[str, AppliedVerdict]:
    """The scan-time question: which of these result cards have we already applied to?

    Keyed by the card's `external_id` so a caller can annotate its own list. One query serves the
    whole page — the point of checking at scan time is that it costs nothing per card.
    """
    out: dict[str, AppliedVerdict] = {}
    for c in cards or []:
        ext = str(c.get("external_id") or c.get("jk") or "").strip()
        if not ext:
            continue
        out[ext] = check(db, job_id=f"{platform}:{ext}", title=c.get("title") or "",
                         company=c.get("company") or "", url=c.get("url") or "")
    return out


def _canonical_match(db: Session, job_id: str, rows: list[ObservedJob]
                     ) -> Optional[AppliedVerdict]:
    """An application on file for the same CANONICAL job as `job_id` — certain, engine-blind.

    Two witnesses, asked in order: an applied SIGHTING whose canonical job resolves to ours
    (carries the richer provenance — which door, when), then the canonical Application row
    itself, which survives merges whose applied sighting predates the canonical layer. Both
    resolve through the tombstone chain, so a reference from before a merge still answers.
    """
    me = db.get(ObservedJob, job_id)
    if me is None or not me.canonical_job_key:
        return None
    alive = resolve_key(db, me.canonical_job_key)
    if not alive:
        return None
    for r in rows:
        if r.job_id == job_id or not r.canonical_job_key:
            continue
        if resolve_key(db, r.canonical_job_key) == alive:
            return _verdict(r, STATUS_APPLIED, "canonical",
                            [f"one canonical job ({alive})", f"applied as {r.job_id}"])
    app = db.scalar(select(Application).where(Application.job_key == alive))
    if app is not None:
        return AppliedVerdict(
            status=STATUS_APPLIED, matched_on="canonical", job_id=alive,
            title=me.title or "", company=me.company or "",
            applied_at=app.applied_at.isoformat() if app.applied_at else None,
            platform=app.via_platform,
            evidence=[f"application on file for canonical job {alive}"
                      + (f" via {app.via_platform}" if app.via_platform else "")])
    return None


def _applied_rows(db: Session, *, company: str = "") -> list[ObservedJob]:
    """Every row that represents an application on file. Narrowed by company when we have one —
    the fuzzy tier only ever compares within an employer, so there is no reason to load the rest."""
    stmt = select(ObservedJob).where(
        or_(ObservedJob.application_status.in_(tuple(APPLIED_STATUSES)),
            ObservedJob.applied_at.isnot(None)))
    return list(db.scalars(stmt).all())


def _verdict(row: ObservedJob, status: str, matched_on: str, evidence: list[str]) -> AppliedVerdict:
    return AppliedVerdict(
        status=status, matched_on=matched_on, job_id=row.job_id, title=row.title or "",
        company=row.company or "", platform=row.application_platform or row.platform,
        applied_at=row.applied_at.isoformat() if row.applied_at else None,
        evidence=evidence)
