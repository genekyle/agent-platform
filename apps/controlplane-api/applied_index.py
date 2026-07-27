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
Three tiers, and why the weakest one still earns its place
--------------------------------------------------------------------------------------
    exact      the same job_id                      — certain
    requisition a req id shared by both records      — certain enough to act on
    fuzzy      same company, same role words         — a WARNING, never a decision

The fuzzy tier exists because company+title is often all we have, and it is right far more often
than it is wrong. But it is exactly the match that would wrongly skip "Data Analyst II" for having
applied to "Data Analyst I", so it never reports `applied` — it reports `likely_applied`, which the
ladder surfaces to the operator instead of acting on. **A near-miss that silently skips a job the
operator picked is worse than one that asks.**

Pure except for the query itself: the matching lives in module functions with no DB in sight, so
the interesting half is testable without a database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from models import ObservedJob

#: `application_status` values that mean an application exists. `applied` is the only one the
#: epilogue stamps; the others are historical/manual and treated the same way on purpose — the
#: question being asked is "is there an application on file", not "who put it there".
APPLIED_STATUSES = frozenset({"applied", "submitted"})

#: What the caller gets back.
STATUS_APPLIED = "applied"                # certain: same job, or same requisition
STATUS_LIKELY = "likely_applied"          # company + role words line up; ASK, do not act
STATUS_NONE = "not_applied"               # nothing on file

#: Words that carry no signal about WHICH role a posting is, so they must not prop up a fuzzy
#: match. Without this, "Senior Analyst - Boston" and "Analyst - Boston" share half their words.
_NOISE = frozenset({
    "the", "and", "for", "with", "our", "you", "your", "job", "jobs", "role", "position",
    "opening", "opportunity", "full", "time", "part", "remote", "hybrid", "onsite", "new",
    "inc", "llc", "ltd", "corp", "corporation", "company", "group", "holdings", "health",
})

#: A requisition id as ATS platforms write them: Workday's JR#####, iCIMS's numeric job id in the
#: path, Greenhouse's gh_jid. Matched case-insensitively against both records' urls and tenant ids.
_REQ_PATTERNS = (
    re.compile(r"\b(jr[-_]?\d{4,})\b", re.I),            # Workday: JR88822
    re.compile(r"\bgh_jid=(\d{4,})\b", re.I),            # Greenhouse
    re.compile(r"/jobs?/(\d{3,})(?:/|\b)", re.I),        # iCIMS: /jobs/3915/
    re.compile(r"\breq[-_]?(\d{4,})\b", re.I),           # generic REQ-12345
)


def normalize_company(name: str) -> str:
    """A company name reduced to its identifying words. 'Beth Israel Lahey Health' and
    'Beth Israel Lahey Health, Inc.' are one employer; the suffixes are noise."""
    words = _words(name)
    return " ".join(sorted(words)) if words else ""


def _words(text: str) -> set[str]:
    """Alphanumeric words of the text, lowercased, minus noise and one/two-letter fragments.

    Punctuation is stripped rather than normalised because the SAME title arrives spelled with an
    en-dash on Indeed and a hyphen on Workday ('Healthcare Data Analyst – BIDMC' vs '- BIDMC'),
    and a comparison that survives that is the only kind worth having.
    """
    raw = "".join(c if c.isalnum() else " " for c in (text or "").lower()).split()
    return {w for w in raw if len(w) > 2 and w not in _NOISE}


def title_similarity(a: str, b: str) -> float:
    """How much of the SHORTER title's meaning the longer one covers, 0..1.

    Asymmetric on purpose: 'Healthcare Data Analyst' inside 'Healthcare Data Analyst - BIDMC,
    OBGYN Quality' should score high — the ATS routinely appends a department the results card
    omits. Jaccard would punish that, and punishing it is how the same job reads as two.
    """
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def requisition_ids(*texts: Optional[str]) -> set[str]:
    """Every requisition id we can read out of these urls / ids, lowercased and punctuation-free
    (so 'JR-88822' and 'jr88822' are one id)."""
    out: set[str] = set()
    for text in texts:
        for pat in _REQ_PATTERNS:
            for m in pat.finditer(str(text or "")):
                out.add(re.sub(r"[-_]", "", m.group(1)).lower())
    return out


#: How similar two titles must be, at the same company, to be worth flagging. Tuned to catch a
#: title the ATS decorated ('- BIDMC, OBGYN Quality') without collapsing seniority levels: 'Data
#: Analyst I' vs 'Data Analyst II' shares its words but differs by one token the noise list keeps.
FUZZY_TITLE_THRESHOLD = 0.75


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
