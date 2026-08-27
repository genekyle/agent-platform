"""Observed jobs — the shared upsert for scraped job cards.

Lifted out of `main.py` (docs/PLAN_main-split.md) because three callers now need it: the
one-shot `/api/jobs/extract`, the bounded `/api/search/sweep`, and the session control panel's
per-page review. A router importing `main` would fight the split, so the shared helper lives
here where anything can reach it.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from deps import utcnow
from models import ObservedJob, Search


def _norm_q(s: Optional[str]) -> str:
    """Compare queries the way a human would call them the same — whitespace collapsed, case
    ignored. `_norm` in searches.py collapses whitespace only, so 'Reporting Analyst' and
    'reporting analyst' are the SAME query stored twice; three rows in the live corpus carry both,
    and treating that as a provenance mismatch would be a false alarm on real data."""
    return " ".join((s or "").split()).lower()


def check_provenance(*, platform: str, search_query: Optional[str],
                     search: Optional[Search] = None,
                     observed_platform: Optional[str] = None) -> None:
    """Refuse to write a batch whose provenance does not add up. Raises ValueError; never repairs.

    THE RULE, AND IT IS ONE SENTENCE: **a row records the query of the search that surfaced it, on
    the platform the page itself says it is.** Everything below is that sentence with its failures
    named.

    Silent correction is not on the table here. Rewriting a caller's wrong platform to the observed
    one would hide the real fault — a call aimed at the wrong tab — and rows read from the wrong tab
    are exactly what must not enter. Loud, at the door, is the whole design.

    What this exists to stop, all three measured in the live corpus on 2026-08-26:

    * **A FEED BATCH TAGGED WITH THE SESSION'S LAST QUERY.** `ensure_active_feed`'s docstring says
      filling in a query would be "a lie the provenance then has to carry" — and the very next line
      of its only caller passed `bb.search_state.query` into the upsert. 14 rows whose ONLY sighting
      is Indeed's suggestion feed claim they were found by searching "data analyst". Nobody searched
      anything; the feed offered them.
    * **A QUERY WITH NO SEARCH TO JUSTIFY IT.** `/api/jobs/extract` recorded `search_query` while
      passing no `search` at all, so the query landed on the row and NOTHING linked it to a search.
      20 rows carry a query no sighting of theirs supports and no evidence can now adjudicate —
      they are the reason this is enforced at the door rather than audited afterwards.
    * **A PLATFORM THE PAGE DISAGREES WITH.** `job_id` is `f"{platform}:{external_id}"`, so a
      caller's wrong guess does not merely mislabel a row, it mints a *different row* that can never
      dedupe against the real one. The extractor returns the platform it read off the live tab's
      host; passing it here makes the claim checkable instead of assumed.
    """
    if observed_platform and _norm_q(observed_platform) != _norm_q(platform):
        raise ValueError(
            f"provenance: the page says this is {observed_platform!r} and the caller says "
            f"{platform!r}. job_id is built from the platform, so writing this would mint rows that "
            f"can never dedupe against the real ones — aim at the right tab instead.")
    q = _norm_q(search_query)
    if not q:
        return
    if search is None:
        raise ValueError(
            f"provenance: asked to record the query {search_query!r} on these rows with no Search "
            f"to justify it. A query on a row that nothing links to a search cannot be checked "
            f"later — it is exactly the 20 unadjudicable rows this rule exists to stop.")
    if getattr(search, "kind", "query") == "feed":
        raise ValueError(
            f"provenance: this is the {search.surface or 'feed'} FEED, and a feed has no query — "
            f"but {search_query!r} was offered for the rows. The feed surfaced these jobs; no query "
            f"found them, and recording one is a lie the provenance then has to carry.")
    if q != _norm_q(search.query):
        raise ValueError(
            f"provenance: the rows would record {search_query!r} while the search they are being "
            f"linked to is {search.query!r}. A sighting records the query that surfaced it.")


def upsert_observed_jobs(db: Session, jobs: list[dict], platform: str,
                         search_query: Optional[str], *,
                         search: Optional[Search] = None,
                         page: Optional[int] = None,
                         observed_platform: Optional[str] = None) -> tuple[int, int]:
    """UPSERT scraped job cards into observed_jobs, deduped by job_id = '{platform}:{external_id}'.
    A re-seen job bumps seen_count + last_seen_at (and records the search) instead of duplicating;
    blank fields are backfilled. Returns (new, duplicate) counts. Does NOT commit — the caller does,
    so a multi-page sweep commits once per page.

    That `job_id` key dedupes SIGHTINGS, which is a weaker claim than it sounds: Indeed's jk
    rotates per search session and LinkedIn uses its own ids, so the same posting still arrives as
    several rows. Folding those into one canonical `Job` is a separate step — `job_dedup.resolve_*`
    — deliberately NOT called from here: this helper does not own the transaction, and a caller
    that only wants rows written should not silently pay for a resolution pass as well."""
    check_provenance(platform=platform, search_query=search_query, search=search,
                     observed_platform=observed_platform)
    now = utcnow()
    new_count = dup_count = 0
    touched_ids: list[str] = []
    for j in jobs:
        ext = (j.get("external_id") or "").strip()
        if not ext:
            continue
        job_id = f"{platform}:{ext}"
        touched_ids.append(job_id)
        row = db.get(ObservedJob, job_id)
        if row is None:
            row = ObservedJob(
                job_id=job_id, platform=platform, external_id=ext,
                title=(j.get("title") or "")[:400], company=(j.get("company") or "")[:300],
                location=(j.get("location") or "")[:300], url=(j.get("url") or "")[:1200],
                salary=(j.get("salary") or "")[:200] or None,
                # The queries that surfaced a job are DERIVED from `SearchSighting` by
                # `queries_for` — the column itself is GONE (SESSION 15). `search_query` still
                # arrives because `check_provenance` validates it against the Search being
                # linked; the door outlived the store it used to guard.
                first_seen_at=now, last_seen_at=now, seen_count=1,
            )
            db.add(row)
            new_count += 1
        else:
            row.seen_count += 1
            row.last_seen_at = now
            # backfill any fields that were blank before
            row.title = row.title or (j.get("title") or "")[:400]
            row.company = row.company or (j.get("company") or "")[:300]
            row.location = row.location or (j.get("location") or "")[:300]
            dup_count += 1

    # Provenance: the search is the query, the session is only the browser (2026-08-10). When the
    # caller knows which Search this page belongs to, every sighting on it gets the association —
    # and since 2026-08-26 that association is the ONLY record of it (§16). This link is no longer
    # a nicety beside the JSON list; it is where the fact lives.
    if search is not None and touched_ids:
        import searches as searches_mod
        searches_mod.link_sightings(db, search, touched_ids, page=page,
                                    results_on_page=len(touched_ids))

    return new_count, dup_count


# --- the queries that surfaced a job, DERIVED (PRINCIPLES §16) ---------------------------------
# `ObservedJob.search_queries` used to be a column: a JSON list any caller could append to, sitting
# beside `SearchSighting`, which records the same fact with a search behind it. Two stores for one
# fact, and when they disagreed only one could be checked — which is how 20 rows ended up carrying a
# query nothing can support and nothing will ever be able to judge (2026-08-26).
#
# So the column is no longer written and this is the answer instead. It cannot be wrong in the way
# the column was: there is nowhere to assert a query, only somewhere to record a search that
# surfaced a job.
#
# A FEED CONTRIBUTES NO QUERY, by construction — that is what its empty `query` column means, and
# excluding it here is the same rule the write door enforces. One statement for a whole page of
# rows, because this is called from list endpoints and an N+1 over a 100-row dashboard is a real
# cost for a display field.
def queries_for(db: Session, job_ids: list[str]) -> dict[str, list[str]]:
    """{job_id: [queries that surfaced it]}, oldest sighting first. Empty list for a job only a feed
    ever surfaced — which is the honest answer, and the one the column used to get wrong."""
    from sqlalchemy import select as _select

    from models import SearchSighting

    if not job_ids:
        return {}
    out: dict[str, list[str]] = {jid: [] for jid in job_ids}
    rows = db.execute(
        _select(SearchSighting.job_id, Search.query, Search.kind)
        .join(Search, Search.id == SearchSighting.search_id)
        .where(SearchSighting.job_id.in_(job_ids))
        .order_by(SearchSighting.seen_at.asc())).all()
    for job_id, query, kind in rows:
        q = " ".join((query or "").split())
        if not q or (kind or "query") == "feed":
            continue
        if q not in out[job_id]:
            out[job_id].append(q)
    return out


# --- the rows that got in before the door had a lock -------------------------------------------
# HISTORY, IN ORDER, because these functions' shapes only make sense with it. 2026-08-26: the
# audit found 14 feed-only rows (repaired — the join table could prove them) and 20 rows whose
# claims nothing could adjudicate. 2026-08-27 (SESSION 15): the 20 were flagged
# `provenance_quarantined` by a live pass, and THEN the `search_queries` column was dropped —
# claims are now UNEXPRESSIBLE, not merely refused: there is no column to assert into, only a
# `SearchSighting` to earn. The audit therefore no longer has claims to judge; what remains is
# the durable count of the era's damage, and these endpoints keep answering so the number stays
# visible instead of being re-discovered by whoever wonders where the column went.
_COLUMN_DROPPED_NOTE = (
    "`search_queries` was dropped 2026-08-27 (SESSION 15): queries are derived from "
    "SearchSighting by queries_for, so an unbacked claim can no longer be expressed. The 20 "
    "historically unadjudicable rows are flagged `provenance_quarantined` — that flag is the "
    "durable record, written before the drop because the claims left with the column.")


def _quarantined_count(db: Session) -> int:
    from sqlalchemy import func as _func
    from sqlalchemy import select as _select

    return int(db.scalar(
        _select(_func.count()).select_from(ObservedJob)
        .where(ObservedJob.provenance_quarantined.is_(True))) or 0)


def audit_query_provenance(db: Session) -> dict[str, Any]:
    """What does the corpus claim that its own join table cannot support? Read-only.

    Post-drop, the honest answer is structural: NOTHING — a claim has nowhere to live, so
    `repairable` and `unadjudicable` are true zeros (unexpressible, not unexamined). What stays
    countable: `joined_rows` (how much of the corpus has sighting-backed history) and
    `quarantined` (the durable record of the pre-door era's 20)."""
    from sqlalchemy import distinct as _distinct
    from sqlalchemy import func as _func
    from sqlalchemy import select as _select

    from models import SearchSighting

    joined = int(db.scalar(_select(_func.count(_distinct(SearchSighting.job_id)))) or 0)
    return {
        "joined_rows": joined,
        "repairable": 0,
        "unadjudicable": 0,
        "quarantined": _quarantined_count(db),
        "column": "dropped 2026-08-27",
        "why": _COLUMN_DROPPED_NOTE,
    }


def quarantine_unadjudicable(db: Session, *, apply: bool = False) -> dict[str, Any]:
    """The one-time flagging pass, now standing as its own record-keeper (SESSION 15).

    Ran live 2026-08-27 WITH the claims column still present: 20 rows flagged as
    query-history-known-incomplete (a query no sighting supported, beside real sightings — most
    likely a caller that recorded a query and created no link). Post-drop there are no claims
    left to judge, so `newly_flagged` is 0 by construction; the call remains so the durable count
    stays one click away. Flags are never auto-cleared, and a flagged row must not vote in
    anything that learns a query→job association — its sighting record is KNOWN-INCOMPLETE.
    """
    return {"ok": True, "applied": bool(apply), "newly_flagged": 0,
            "already_flagged": _quarantined_count(db), "rows": [],
            "detail": _COLUMN_DROPPED_NOTE}


def repair_query_provenance(db: Session, *, apply: bool = False) -> dict[str, Any]:
    """Nothing left to repair, and that is the design working — kept so the endpoint answers with
    the history instead of a 404 for whoever wonders where the column went. The 2026-08-26 live
    run repaired the 14 feed-only rows while the column existed; both sides are in LEARNINGS."""
    return {"ok": True, "applied": bool(apply), "repaired": 0, "changes": [],
            "refused": {"unadjudicable": 0, "detail": _COLUMN_DROPPED_NOTE}}
