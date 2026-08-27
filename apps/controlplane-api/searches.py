"""Search rows — the durable identity of one executed query (models.Search).

The architectural rule this module carries (operator, 2026-08-10): **the session is the browser,
the search is the query.** A session holds cookies and a signed-in account and must stay alive
across many queries; everything a query finds — sightings, and eventually applications — hangs off
the SEARCH row, never directly off the session. The blackboard's `SearchState` stays the live
cursor (page, phase); this table is the identity those cursors write through.

Lazy by design: a Search row is minted the first time a page of results is actually recorded for
a (session, engine, query, location) that has no active row — declaring a query costs nothing,
running it creates the record. Re-recording under the same active tuple reuses the row, so
pagination and re-reads never mint twins; a NEW query in the same session simply creates a second
row beside the first, which is the whole point.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import Application, ObservedJob, Search, SearchSighting

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _norm(s: Optional[str]) -> str:
    return " ".join((s or "").split())


def ensure_active_search(db: Session, *, session_id: Optional[int], engine: str,
                         query: str, location: str = "",
                         radius_miles: Optional[int] = None,
                         filters: Optional[dict[str, Any]] = None) -> Optional[Search]:
    """The active Search row for this tuple, created on first use. None for a blank query —
    a sweep of an undeclared search records sightings but cannot claim an identity for them.

    `filters` is what the ENGINE said its result set was, read off the live results URL
    (`search_cadence.result_set_identity`). It is stored on creation and BACKFILLED onto a row that
    has none, but never overwritten: a row whose filters have changed is not this row's set any
    more, and quietly relabelling it would erase exactly the provenance the column exists to keep
    (2026-08-26 — 23 rows gathered under an Easy-Apply filter, recorded under a row that said
    nothing about one). Detecting that drift is the sweep's job; this only refuses to lie about it.

    LOCATION HAS ONE AUTHORITY, AND IT IS THE PAGE (SESSION 15/16, §15). Search 14 recorded
    `location="Nashua, NH"` — the active target's default — over a page carrying no location
    filter at all: a caller's wish stored as a page fact, the query column's lie one column over.
    So when the engine's own params are in hand, the URL decides and the caller's argument is a
    FALLBACK for when the page says nothing:

      * the page states a place  -> that is the location, whatever the caller passed;
      * the page names a set and no location in it -> the caller's claim is DROPPED (logged), not
        stored: "" is the honest record of a set that states no place;
      * nobody looked (`filters=None`), or an engine whose params we cannot read -> the caller's
        claim stands unjudged, because absence of evidence is not evidence (the tri-state rule).

    *This deliberately does NOT raise, and the first cut did.* A raise here would have been a
    cliff, not a guard: neither `/api/jobs/extract` nor the ladder's `review_page` crank wraps
    this call, so a ValueError would surface as a 500 — and it would have fired first on the
    LinkedIn preferences landing, the very surface that motivated the rule. `interaction.refusal`
    already states the standard: a refusal names its exit, and a crash names nothing. Preferring
    the page needs no exit, because nothing is blocked and nothing false is stored.
    (`check_provenance` still raises on a PLATFORM mismatch, and should: a wrong platform mints a
    row that can never dedupe. A wrong location only mislabels one field of one row.)
    """
    q = _norm(query)
    if not q:
        return None
    loc = _norm(location)
    if filters is not None:
        import search_cadence
        stated = _norm(search_cadence.declared_location(filters, engine or "indeed"))
        backed = search_cadence.location_backed(filters, engine or "indeed")
        if stated:
            if loc and stated.casefold() != loc.casefold():
                # Not an error: "Greater Boston" vs "Boston, MA" is the same place written two
                # ways, and refusing on that would cry wolf on honest data. The page still wins.
                logger.info("search location: recording the page's %r over the caller's %r",
                            stated, loc)
            loc = stated
        elif loc and backed is False:
            logger.warning(
                "search location: dropping %r — the engine named its set (%s) and no location "
                "filter is in it, so that location is the caller's default, not a page fact",
                loc, ", ".join(sorted(filters)) or "no params")
            loc = ""
    row = db.scalar(select(Search).where(
        Search.session_id == session_id, Search.engine == (engine or "indeed"),
        Search.query == q, Search.location == loc,
        Search.status == "active"))
    encoded = json.dumps(filters, sort_keys=True) if filters is not None else ""
    if row is None:
        row = Search(session_id=session_id, engine=engine or "indeed", query=q,
                     location=loc, radius_miles=radius_miles, filters=encoded)
        db.add(row)
        db.flush()
    elif encoded and not (row.filters or "").strip():
        # BACKFILL ONLY ONTO A ROW THAT HAS GATHERED NOTHING YET. Stamping today's filters onto a
        # row whose sightings were banked BEFORE we started reading filters is a claim about data
        # collected while nobody was looking — the precise shape of lie this column exists to stop.
        # Search 13 (the 2026-08-26 LinkedIn sweep) is the live example: 25 rows gathered
        # unfiltered, then f_AL=true appeared, and a re-sweep would have labelled all of them as
        # Easy-Apply-filtered. An empty row has no such history, so backfilling it is free.
        if not db.scalar(select(func.count()).select_from(SearchSighting)
                         .where(SearchSighting.search_id == row.id)):
            row.filters = encoded
    return row


def ensure_active_feed(db: Session, *, session_id: Optional[int], engine: str,
                      surface: str = "home_feed",
                      filters: Optional[dict[str, Any]] = None) -> Optional[Search]:
    """The active FEED process for this session, created on first use.

    The query-kind sibling above refuses a blank query, and rightly: a sweep of an undeclared
    search has no identity to attribute its sightings to. A feed run has no query and is not
    undeclared — its identity is the SURFACE, so it is keyed on (session, engine, surface) and the
    query column stays empty rather than being filled with a lie like "(feed)".

    Same lifecycle as a search, for the same reason: re-entering the feed in a living session
    reuses the row instead of minting a twin, and closing it must never close the browser.
    """
    surf = _norm(surface) or "home_feed"
    row = db.scalar(select(Search).where(
        Search.session_id == session_id, Search.engine == (engine or "indeed"),
        Search.kind == "feed", Search.surface == surf, Search.status == "active"))
    encoded = json.dumps(filters, sort_keys=True) if filters is not None else ""
    if row is None:
        row = Search(session_id=session_id, engine=engine or "indeed", query="",
                     location="", kind="feed", surface=surf, filters=encoded)
        db.add(row)
        db.flush()
    elif encoded and not (row.filters or "").strip():
        row.filters = encoded
    return row


def link_sightings(db: Session, search: Optional[Search], job_ids: Iterable[str],
                   *, page: Optional[int] = None, results_on_page: int = 0) -> int:
    """Attach one recorded page to its search: association rows + the rollup counters.

    Idempotent per (search, job) — re-reading a page bumps activity, never duplicates links.
    Returns how many jobs were newly attributed to this search.
    """
    if search is None:
        return 0
    linked = 0
    for jid in job_ids:
        if not jid:
            continue
        exists = db.scalar(select(SearchSighting.id).where(
            SearchSighting.search_id == search.id, SearchSighting.job_id == jid))
        if exists is None:
            db.add(SearchSighting(search_id=search.id, job_id=jid, page=page))
            linked += 1
    # `or 0`: column defaults land at flush, so a just-created row still carries None here.
    search.results_seen = (search.results_seen or 0) + int(results_on_page or 0)
    if page and page > (search.pages_swept or 0):
        search.pages_swept = page
    search.last_activity_at = _utcnow()
    return linked


def close_search(db: Session, search_id: int, *, status: str = "exhausted") -> Optional[Search]:
    """Mark a search finished (exhausted | abandoned). Closing a search never touches the
    session — keeping the browser alive across queries is the reason this table exists."""
    row = db.get(Search, search_id)
    if row is not None and status in ("exhausted", "abandoned", "active"):
        row.status = status
        row.last_activity_at = _utcnow()
    return row


def summarize(db: Session, *, session_id: Optional[int] = None,
              limit: int = 50) -> list[dict[str, Any]]:
    """The searches list the cockpit renders: one row per search with its yield attached."""
    stmt = select(Search).order_by(Search.started_at.desc()).limit(limit)
    if session_id is not None:
        stmt = stmt.where(Search.session_id == session_id)
    out: list[dict[str, Any]] = []
    for s in db.scalars(stmt):
        jobs = db.scalar(select(func.count(SearchSighting.id))
                         .where(SearchSighting.search_id == s.id)) or 0
        apps = db.scalar(select(func.count(Application.id))
                         .where(Application.search_id == s.id)) or 0
        out.append({
            "id": s.id, "session_id": s.session_id, "engine": s.engine,
            # `kind` travels with every row so the cockpit can label the SAME counters correctly:
            # `pages_swept` is pages on a query and BATCHES on a feed, and a UI that had to infer
            # which from an empty query string would guess wrong on the first odd row.
            "kind": getattr(s, "kind", "query") or "query",
            "surface": getattr(s, "surface", "") or "",
            "label": (s.query or "").strip() or (f"{(getattr(s, 'surface', '') or 'feed').replace('_', ' ')}"
                                                 if (getattr(s, "kind", "") == "feed") else ""),
            "query": s.query, "location": s.location, "radius_miles": s.radius_miles,
            # What the engine said this set WAS. Surfaced rather than kept in the row, so the
            # operator can see "gathered with f_AL=true" without curling anything (LEARNINGS
            # 2026-08-26: the contamination was invisible because nothing displayed it either).
            # TRI-STATE, for the same reason `has_next` is one: "" means NOBODY LOOKED and "{}"
            # means we looked and the set carried no filters. Collapsing them to {} would let a
            # search gathered before this column existed — Search 13, the 08-26 LinkedIn sweep whose
            # result set demonstrably changed under it — read as "confirmed unfiltered".
            "filters": json.loads(s.filters) if (s.filters or "").strip() else None,
            "filters_recorded": bool((s.filters or "").strip()),
            "status": s.status, "pages_swept": s.pages_swept, "results_seen": s.results_seen,
            "jobs_found": int(jobs), "applications": int(apps),
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "last_activity_at": s.last_activity_at.isoformat() if s.last_activity_at else None,
        })
    return out


def jobs_for(db: Session, search_id: int) -> list[dict[str, Any]]:
    """The sightings one search surfaced, joined to their card facts — the per-search yield."""
    pairs = db.execute(
        select(SearchSighting, ObservedJob)
        .join(ObservedJob, ObservedJob.job_id == SearchSighting.job_id)
        .where(SearchSighting.search_id == search_id)
        .order_by(SearchSighting.seen_at)).all()
    return [{
        "job_id": oj.job_id, "title": oj.title, "company": oj.company,
        "location": oj.location, "url": oj.url, "page": ss.page,
        "seen_at": ss.seen_at.isoformat() if ss.seen_at else None,
        "application_status": oj.application_status,
        "canonical_job_key": oj.canonical_job_key,
    } for ss, oj in pairs]
