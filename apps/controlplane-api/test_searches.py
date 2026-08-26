"""Tests for the search layer — the session is the browser, the search is the query (2026-08-10).

Pinned, in the order it costs to get wrong:

  1. Re-recording pages under one active (session, engine, query, location) reuses ONE Search row —
     pagination must never mint twins, and a NEW query in the same session must mint a sibling
     without touching the first (that would end a session's history the way the old session-keyed
     shape did).
  2. Sighting links are idempotent per (search, job) — a re-read page bumps activity, not rows.
  3. The upsert integration: cards recorded with a search attached join it, and the per-search
     yield (jobs_for / summarize) answers from the association, not the JSON list.
  4. An application stamped with its search keeps it, and summarize counts it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import searches as searches_mod
from application_events import ensure_application
from models import Base, Search, SearchSighting
from observed_jobs import upsert_observed_jobs


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _cards(*exts):
    return [{"external_id": e, "title": f"T {e}", "company": "Acme", "location": "Boston, MA"}
            for e in exts]


def test_one_active_tuple_is_one_search_and_a_new_query_is_a_sibling(db):
    a1 = searches_mod.ensure_active_search(db, session_id=25, engine="indeed",
                                           query="data analytics", location="Boston, MA")
    a2 = searches_mod.ensure_active_search(db, session_id=25, engine="indeed",
                                           query="  data   analytics ", location="Boston, MA")
    assert a1.id == a2.id                      # normalization reuses, pagination never twins
    b = searches_mod.ensure_active_search(db, session_id=25, engine="indeed",
                                          query="data engineer", location="Boston, MA")
    assert b.id != a1.id                       # a new query is a SIBLING in the same session
    assert db.scalar(select(Search.status).where(Search.id == a1.id)) == "active"


def test_a_blank_query_claims_no_identity(db):
    assert searches_mod.ensure_active_search(db, session_id=25, engine="indeed",
                                             query="   ") is None


def test_links_are_idempotent_and_rollups_move(db):
    s = searches_mod.ensure_active_search(db, session_id=25, engine="indeed",
                                          query="data analytics")
    first = searches_mod.link_sightings(db, s, ["indeed:a", "indeed:b"], page=1,
                                        results_on_page=2)
    again = searches_mod.link_sightings(db, s, ["indeed:a", "indeed:b"], page=1,
                                        results_on_page=2)
    assert (first, again) == (2, 0)            # re-reading a page adds no rows
    assert db.scalar(select(Search).where(Search.id == s.id)).pages_swept == 1
    assert db.scalar(select(Search).where(Search.id == s.id)).results_seen == 4


def test_upsert_with_a_search_attaches_every_card(db):
    s = searches_mod.ensure_active_search(db, session_id=25, engine="indeed",
                                          query="data analytics", location="Boston, MA")
    new, dup = upsert_observed_jobs(db, _cards("j1", "j2"), "indeed", "data analytics",
                                    search=s, page=1)
    db.commit()
    assert (new, dup) == (2, 0)
    linked = db.scalars(select(SearchSighting.job_id)
                        .where(SearchSighting.search_id == s.id)).all()
    assert sorted(linked) == ["indeed:j1", "indeed:j2"]
    yielded = searches_mod.jobs_for(db, s.id)
    assert {j["job_id"] for j in yielded} == {"indeed:j1", "indeed:j2"}
    assert all(j["page"] == 1 for j in yielded)


def test_summarize_counts_jobs_and_applications(db):
    s = searches_mod.ensure_active_search(db, session_id=25, engine="indeed",
                                          query="data analytics")
    upsert_observed_jobs(db, _cards("j1"), "indeed", "data analytics", search=s, page=1)
    app = ensure_application(db, "JOB-1", via_platform="indeed", search_id=s.id)
    db.commit()
    assert app.search_id == s.id
    row = searches_mod.summarize(db, session_id=25)[0]
    assert (row["jobs_found"], row["applications"]) == (1, 1)
    assert row["query"] == "data analytics"


def test_closing_a_search_is_not_closing_anything_else(db):
    s1 = searches_mod.ensure_active_search(db, session_id=25, engine="indeed", query="q one")
    s2 = searches_mod.ensure_active_search(db, session_id=25, engine="indeed", query="q two")
    searches_mod.close_search(db, s1.id, status="exhausted")
    assert db.get(Search, s1.id).status == "exhausted"
    assert db.get(Search, s2.id).status == "active"   # siblings unaffected
    # and a re-declare of the closed query mints a FRESH row rather than reviving the old one
    s1b = searches_mod.ensure_active_search(db, session_id=25, engine="indeed", query="q one")
    assert s1b.id != s1.id


def test_a_feed_run_is_a_process_keyed_on_its_surface_not_a_query(db):
    """`ensure_active_search` refuses a blank query — a sweep of an undeclared search has no
    identity to attribute sightings to. A feed run is not undeclared: its identity is the SURFACE.
    Operator, 2026-08-26 — working the front page is a process inside the living session."""
    searches = searches_mod
    feed = searches.ensure_active_feed(db, session_id=32, engine="indeed")
    assert feed is not None and feed.kind == "feed" and feed.surface == "home_feed"
    # The query column stays EMPTY rather than being filled with a lie like "(feed)".
    assert feed.query == ""

    # Re-entering the feed in a living session reuses the row instead of minting a twin.
    again = searches.ensure_active_feed(db, session_id=32, engine="indeed")
    assert again.id == feed.id

    # A different surface on the same engine is a different process.
    other = searches.ensure_active_feed(db, session_id=32, engine="indeed", surface="saved_jobs")
    assert other.id != feed.id

    # And a query-kind search is still refused for a blank query — the feed path did not soften it.
    assert searches.ensure_active_search(db, session_id=32, engine="indeed", query="  ") is None

    # A feed row must not be handed back to the query lookup, or the two processes collide.
    q = searches.ensure_active_search(db, session_id=32, engine="indeed", query="data analyst")
    assert q.id != feed.id and q.kind == "query"
    assert {r["kind"] for r in searches.summarize(db, session_id=32)} == {"feed", "query"}
