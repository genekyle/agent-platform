"""Observed jobs — the shared upsert for scraped job cards.

Lifted out of `main.py` (docs/PLAN_main-split.md) because three callers now need it: the
one-shot `/api/jobs/extract`, the bounded `/api/search/sweep`, and the session control panel's
per-page review. A router importing `main` would fight the split, so the shared helper lives
here where anything can reach it.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from deps import utcnow
from models import ObservedJob, Search


def upsert_observed_jobs(db: Session, jobs: list[dict], platform: str,
                         search_query: Optional[str], *,
                         search: Optional[Search] = None,
                         page: Optional[int] = None) -> tuple[int, int]:
    """UPSERT scraped job cards into observed_jobs, deduped by job_id = '{platform}:{external_id}'.
    A re-seen job bumps seen_count + last_seen_at (and records the search) instead of duplicating;
    blank fields are backfilled. Returns (new, duplicate) counts. Does NOT commit — the caller does,
    so a multi-page sweep commits once per page.

    That `job_id` key dedupes SIGHTINGS, which is a weaker claim than it sounds: Indeed's jk
    rotates per search session and LinkedIn uses its own ids, so the same posting still arrives as
    several rows. Folding those into one canonical `Job` is a separate step — `job_dedup.resolve_*`
    — deliberately NOT called from here: this helper does not own the transaction, and a caller
    that only wants rows written should not silently pay for a resolution pass as well."""
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
