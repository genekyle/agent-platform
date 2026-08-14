"""The decision ledger — every triage call, with the alternatives it was made against.

`choose` is where a human judgement enters the system: N cards on screen, some picked, the rest
passed over. Until now only the picks survived (as queue entries) and the passes existed nowhere
— which cannot train a boundary, because a corpus of picks alone says "apply to everything".

This module writes one row per job under review, every time a page is decided. It is deliberately
dumb: no scoring, no model, no opinion. The judgement is the operator's; this records it, along
with what else was on the table, where each card sat on the page, and what the card actually said
at the time.

The outcome side (replies, callbacks, interviews) lands on `ApplicationEvent` and joins here
through `job_key`. That join is why the key is tombstone-resolved on write: a job merged into
another next month must not orphan the decision made about it today.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import JobDecision, ObservedJob

logger = logging.getLogger("job_decisions")

PICKED = "picked"
PASSED = "passed"


def _canonical_key(db: Session, job_id: str) -> Optional[str]:
    """The live canonical key for a sighting, following any merge tombstone. None when the job
    has not been canonicalised yet — a decision about it is still worth keeping, so this never
    raises and the caller stores `job_id` regardless."""
    try:
        import job_dedup
        sighting = db.get(ObservedJob, job_id)
        if sighting is not None and sighting.canonical_job_key:
            return job_dedup.resolve_key(db, sighting.canonical_job_key)
        return job_dedup.resolve_key(db, job_dedup.mint_job_key(job_id))
    except Exception:  # noqa: BLE001 — the ledger must never sink the decision it records
        logger.debug("could not canonicalise %s", job_id, exc_info=True)
        return None


def record_page_decisions(db: Session, *, cards: list[dict[str, Any]], picked: set[str],
                          decided_by: str, session_id: Optional[int], page: int,
                          query: str, reasons: Optional[dict[str, str]] = None,
                          search_id: Optional[int] = None) -> dict[str, int]:
    """One row per card under review — picked AND passed. Returns {picked, passed, skipped}.

    `cards` is the page as the decider saw it, IN ORDER, so `rank` is the card's own position.
    Idempotent per (job, session, page, SEARCH): choosing again after adding a pick REPLACES that
    page's decisions rather than stacking duplicates, because `choose` is a standing rung the
    operator re-presses (a second press is a revised decision, not a second one).

    THE SEARCH IS PART OF THE KEY, and leaving it out was a silent overwrite (found 2026-08-13
    building the repick). A session holds many searches and each one starts again at page 1, so
    `(session, page)` alone says "page 1 of this session" — which is two different pages of two
    different result sets once the operator searches for something else. A job that appears in both
    (likely: same location, adjacent queries) would have its FIRST decision rewritten by its
    second, and the pair — passed on one query, picked on another — is exactly the contrast a
    boundary is learned from. Two searches, two decisions, both kept.

    A legacy row (`search_id IS NULL`, written before searches were rows) still matches, because
    the alternative is a duplicate for every job decided before that column existed.
    """
    reasons = reasons or {}
    shown = len(cards)
    counts = {PICKED: 0, PASSED: 0, "skipped": 0}

    existing = {
        d.job_id: d for d in db.scalars(
            select(JobDecision).where(JobDecision.session_id == session_id,
                                      JobDecision.page == page)).all()
        if d.search_id is None or search_id is None or d.search_id == search_id
    } if session_id is not None else {}

    for rank, card in enumerate(cards, start=1):
        job_id = str(card.get("job_id") or "").strip()
        if not job_id:
            counts["skipped"] += 1
            continue
        decision = PICKED if job_id in picked else PASSED
        # RESOLVE THE KEY BEFORE THE ROW EXISTS. `_canonical_key` runs queries, and a query
        # autoflushes — which would try to write a half-built row (no `decision` yet) and fail
        # the NOT NULL constraint, poisoning the whole session's transaction.
        job_key = _canonical_key(db, job_id)
        row = existing.get(job_id)
        if row is None:
            row = JobDecision(job_id=job_id, session_id=session_id, page=page,
                              decision=decision)
            db.add(row)
        row.job_key = job_key
        row.decision = decision
        row.decided_by = decided_by
        row.reason = (reasons.get(job_id) or "")[:500]
        row.rank = rank
        row.shown_count = shown
        row.query = (query or "")[:300]
        row.search_id = search_id if search_id is not None else row.search_id
        row.platform = str(card.get("platform") or job_id.split(":", 1)[0])[:40]
        # What the decider could SEE — never the enriched canonical job, which drifts.
        row.card = {k: card.get(k) for k in ("title", "company", "location", "salary", "url")
                    if card.get(k)}
        counts[decision] += 1
    return counts


def decisions_for(db: Session, job_key: str) -> list[JobDecision]:
    """Every decision ever made about one job, oldest first — the audit trail behind a status."""
    return list(db.scalars(
        select(JobDecision).where(JobDecision.job_key == job_key)
        .order_by(JobDecision.decided_at)).all())


def summary(db: Session) -> dict[str, Any]:
    """How much decision data exists, and how balanced it is.

    The ratio is the number to watch: a ledger that is 95% passes is normal and fine, but a
    ledger with ZERO passes means the negatives are not being recorded and nothing here can
    ever learn a boundary.
    """
    rows = list(db.scalars(select(JobDecision)).all())
    picked = sum(1 for r in rows if r.decision == PICKED)
    by_decider: dict[str, int] = {}
    with_reason = 0
    for r in rows:
        by_decider[r.decided_by] = by_decider.get(r.decided_by, 0) + 1
        with_reason += 1 if (r.reason or "").strip() else 0
    return {
        "decisions": len(rows),
        "picked": picked,
        "passed": len(rows) - picked,
        "with_reason": with_reason,
        "by_decider": by_decider,
        "distinct_jobs": len({r.job_key or r.job_id for r in rows}),
    }
