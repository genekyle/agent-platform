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
                search_queries=[search_query] if search_query else [],
                first_seen_at=now, last_seen_at=now, seen_count=1,
            )
            db.add(row)
            new_count += 1
        else:
            row.seen_count += 1
            row.last_seen_at = now
            if search_query and search_query not in (row.search_queries or []):
                row.search_queries = (row.search_queries or []) + [search_query]
            # backfill any fields that were blank before
            row.title = row.title or (j.get("title") or "")[:400]
            row.company = row.company or (j.get("company") or "")[:300]
            row.location = row.location or (j.get("location") or "")[:300]
            dup_count += 1

    # Provenance: the search is the query, the session is only the browser (2026-08-10). When the
    # caller knows which Search this page belongs to, every sighting on it gets the association —
    # the JSON `search_queries` list above stays for display, the join table answers questions.
    if search is not None and touched_ids:
        import searches as searches_mod
        searches_mod.link_sightings(db, search, touched_ids, page=page,
                                    results_on_page=len(touched_ids))

    return new_count, dup_count


# --- the rows that got in before the door had a lock -------------------------------------------
# The gate above stops this happening again; these two answer what already happened. They are
# deliberately separate: an audit that also repairs is one nobody dares run, and a repair that
# cannot be previewed is one nobody should.
#
# THE ADJUDICATOR IS THE JOIN TABLE, NOT THE JSON LIST. `ObservedJob.search_queries` is a display
# field that accumulated whatever any caller asserted; `SearchSighting` records which search
# actually surfaced which job. So the question "is this query real" has an answer for exactly the
# rows whose history is joined — and honestly has no answer for the rest, which is why they are
# reported and never touched.
def _query_provenance(db: Session) -> dict[str, Any]:
    from sqlalchemy import select as _select

    from models import SearchSighting

    searches = {s.id: s for s in db.scalars(_select(Search)).all()}
    by_job: dict[str, list[int]] = {}
    for s in db.scalars(_select(SearchSighting)).all():
        by_job.setdefault(s.job_id, []).append(s.search_id)

    feed_only: list[dict[str, Any]] = []
    unadjudicable: list[dict[str, Any]] = []
    case_variants = joined = 0
    for row in db.scalars(_select(ObservedJob)).all():
        links = by_job.get(row.job_id)
        if not links:
            continue        # never joined: the ABSENCE of a link is not evidence of a lie
        joined += 1
        linked = [searches[i] for i in links if i in searches]
        # A FEED BACKS NO QUERY. That is the whole point of its empty query column.
        backed = {_norm_q(s.query) for s in linked if getattr(s, "kind", "query") != "feed"}
        backed.discard("")
        claimed = [q for q in (row.search_queries or []) if q]
        unbacked = [q for q in claimed if _norm_q(q) not in backed]
        if any(q not in {s.query for s in linked} and _norm_q(q) in backed for q in claimed):
            case_variants += 1          # same query, different casing — real, and left alone
        if not unbacked:
            continue
        entry = {"job_id": row.job_id, "title": (row.title or "")[:80],
                 "claims": claimed, "unbacked": unbacked,
                 "surfaced_by": sorted({(getattr(s, "kind", "query") == "feed"
                                         and f"feed:{s.surface}" or f"query:{s.query}")
                                        for s in linked})}
        if {getattr(s, "kind", "query") for s in linked} == {"feed"}:
            feed_only.append(entry)
        else:
            unadjudicable.append(entry)
    return {"joined_rows": joined, "case_variants": case_variants,
            "feed_only": feed_only, "unadjudicable": unadjudicable}


def audit_query_provenance(db: Session) -> dict[str, Any]:
    """What does the corpus claim that its own join table cannot support? Read-only."""
    found = _query_provenance(db)
    return {
        "joined_rows": found["joined_rows"],
        "repairable": len(found["feed_only"]),
        "unadjudicable": len(found["unadjudicable"]),
        "case_variants_left_alone": found["case_variants"],
        "rows": {"feed_only": found["feed_only"], "unadjudicable": found["unadjudicable"]},
        "why": {
            "feed_only": "the FEED is the only thing that ever surfaced these, and a feed has no "
                         "query — so a query on the row was written by the caller, not earned by a "
                         "search. Repairable.",
            "unadjudicable": "these carry a query no sighting of theirs supports, but they were "
                             "also surfaced by real searches — most likely written by a path that "
                             "recorded a query and created no link at all. Nothing in the data can "
                             "now say whether the query was real, so nothing here is touched.",
        },
    }


def repair_query_provenance(db: Session, *, apply: bool = False) -> dict[str, Any]:
    """Strip the queries that only the feed could have put there. Dry by default; commits nothing.

    Repairs ONE class and refuses the rest by construction. The row's true provenance is not lost
    by this — it is in `SearchSighting`, which is the stronger record and the one that adjudicated
    the repair in the first place.
    """
    found = _query_provenance(db)
    changed = []
    for entry in found["feed_only"]:
        row = db.get(ObservedJob, entry["job_id"])
        if row is None:
            continue
        keep = [q for q in (row.search_queries or []) if q not in entry["unbacked"]]
        changed.append({"job_id": row.job_id, "removed": entry["unbacked"], "kept": keep,
                        "surfaced_by": entry["surfaced_by"]})
        if apply:
            row.search_queries = keep
    return {"ok": True, "applied": bool(apply), "repaired": len(changed), "changes": changed,
            "refused": {"unadjudicable": len(found["unadjudicable"]),
                        "detail": "carry a query no sighting supports but were also surfaced by "
                                  "real searches — no evidence can adjudicate them, so they stand"}}
