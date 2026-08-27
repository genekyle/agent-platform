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


# --- provenance at the door -------------------------------------------------------------------
# Everything below was measured in the LIVE corpus on 2026-08-26 and none of it was caught by a
# test, which is why it shipped. The counts in the docstrings are real rows.
def test_a_feed_batch_cannot_be_tagged_with_the_sessions_last_query(db):
    """14 rows whose only sighting is Indeed's suggestion feed claim they were found by searching
    "data analyst". Nobody searched anything — the feed offered them. `ensure_active_feed`'s own
    docstring calls this "a lie the provenance then has to carry", and the very next line of its
    only caller passed `bb.search_state.query` straight into the upsert."""
    feed = searches_mod.ensure_active_feed(db, session_id=1, engine="indeed")
    with pytest.raises(ValueError, match="feed has no query"):
        upsert_observed_jobs(db, _cards("a1"), "indeed", "data analyst", search=feed)
    # ...and the honest call is accepted, with the sighting still joined to the feed
    new, _dup = upsert_observed_jobs(db, _cards("a1"), "indeed", None, search=feed)
    assert new == 1
    assert db.scalar(select(SearchSighting).where(SearchSighting.search_id == feed.id)) is not None


def test_a_query_with_no_search_to_justify_it_is_refused(db):
    """/api/jobs/extract recorded search_query on every row and passed no search at all, so the
    query landed on the job and nothing linked it to anything. 20 live rows carry a query no
    sighting of theirs supports, and no evidence can now adjudicate them — which is the whole
    argument for refusing at the door instead of auditing afterwards."""
    with pytest.raises(ValueError, match="no Search to justify it"):
        upsert_observed_jobs(db, _cards("a1"), "indeed", "reporting analyst")
    # recording nothing about the query is always allowed — silence is not a lie
    assert upsert_observed_jobs(db, _cards("a1"), "indeed", None)[0] == 1


def test_the_recorded_query_must_be_the_query_of_the_search_that_surfaced_it(db):
    s = searches_mod.ensure_active_search(db, session_id=1, engine="indeed",
                                          query="reporting analyst")
    with pytest.raises(ValueError, match="records the query that surfaced it"):
        upsert_observed_jobs(db, _cards("a1"), "indeed", "data engineer", search=s)


def test_casing_is_not_a_provenance_mismatch(db):
    """Three live rows carry both 'Reporting Analyst' and 'reporting analyst' — the same query from
    a target list, stored twice. `_norm` collapses whitespace but not case, so a case-sensitive
    check would raise on real, honest data."""
    s = searches_mod.ensure_active_search(db, session_id=1, engine="indeed",
                                          query="reporting analyst")
    assert upsert_observed_jobs(db, _cards("a1"), "indeed", "  Reporting   Analyst ", search=s)[0] == 1


def test_a_platform_the_page_disagrees_with_is_refused_not_corrected(db):
    """job_id is f"{platform}:{external_id}", so a wrong platform does not mislabel a row — it mints
    a DIFFERENT row that can never dedupe against the real one. Silently rewriting it to the
    observed platform would hide the actual fault, which is a call aimed at the wrong tab."""
    with pytest.raises(ValueError, match="aim at the right tab"):
        upsert_observed_jobs(db, _cards("a1"), "indeed", None, observed_platform="linkedin")
    # agreement passes, and an extractor that did not say stays out of the way
    assert upsert_observed_jobs(db, _cards("a1"), "linkedin", None,
                                observed_platform="linkedin")[0] == 1
    assert upsert_observed_jobs(db, _cards("a2"), "indeed", None, observed_platform=None)[0] == 1


def test_a_search_and_a_feed_both_record_the_filters_they_were_gathered_under(db):
    s = searches_mod.ensure_active_search(db, session_id=1, engine="linkedin", query="analyst",
                                          filters={"f_AL": "true"})
    f = searches_mod.ensure_active_feed(db, session_id=1, engine="indeed",
                                        filters={"radius": "50"})
    assert s.filters == '{"f_AL": "true"}' and f.filters == '{"radius": "50"}'
    # never overwritten: a row whose filters changed is not that set any more
    again = searches_mod.ensure_active_search(db, session_id=1, engine="linkedin", query="analyst",
                                              filters={"f_AL": "", "f_TPR": "r86400"})
    assert again.id == s.id and again.filters == '{"f_AL": "true"}'
    # ...but a row that has none gets backfilled rather than left blank
    bare = searches_mod.ensure_active_search(db, session_id=2, engine="linkedin", query="analyst")
    assert bare.filters == ""
    searches_mod.ensure_active_search(db, session_id=2, engine="linkedin", query="analyst",
                                      filters={"f_AL": "true"})
    assert bare.filters == '{"f_AL": "true"}'


def test_nobody_looked_and_there_were_none_do_not_encode_alike(db):
    """The same tri-state `has_next` already keeps. A search gathered before this column existed
    must not read as "confirmed unfiltered" — that is precisely the claim we cannot make about
    Search 13, whose result set demonstrably changed under it."""
    never = searches_mod.ensure_active_search(db, session_id=9, engine="indeed", query="analyst")
    looked = searches_mod.ensure_active_search(db, session_id=9, engine="indeed", query="welder",
                                               filters={})
    assert never.filters == "" and looked.filters == "{}"
    rows = {r["query"]: r for r in searches_mod.summarize(db, session_id=9)}
    assert rows["analyst"]["filters"] is None and rows["analyst"]["filters_recorded"] is False
    assert rows["welder"]["filters"] == {} and rows["welder"]["filters_recorded"] is True


# --- adjudicating what got in before the door had a lock ---------------------------------------
def test_the_audit_repairs_the_feed_lie_and_refuses_what_it_cannot_prove(db):
    """Two rows, two different reasons for an unbacked query, and only one of them is knowable.

    A row the feed alone ever surfaced cannot have been found by a query — repairable. A row that
    was ALSO surfaced by a real search might have picked up its extra query from a path that
    recorded one and created no link (the /api/jobs/extract shape), and nothing in the data can now
    say whether it was real — so it stands, counted, untouched."""
    import observed_jobs

    feed = searches_mod.ensure_active_feed(db, session_id=1, engine="indeed")
    real = searches_mod.ensure_active_search(db, session_id=1, engine="indeed",
                                             query="report analyst")
    upsert_observed_jobs(db, _cards("f1"), "indeed", None, search=feed)
    upsert_observed_jobs(db, _cards("q1"), "indeed", "report analyst", search=real)
    # the lie, as the old call site wrote it: a query on rows only the feed ever showed
    from models import ObservedJob
    db.get(ObservedJob, "indeed:f1").search_queries = ["data analyst"]
    db.get(ObservedJob, "indeed:q1").search_queries = ["report analyst", "data analyst"]
    db.flush()

    audit = observed_jobs.audit_query_provenance(db)
    assert audit["repairable"] == 1 and audit["unadjudicable"] == 1
    assert audit["rows"]["feed_only"][0]["job_id"] == "indeed:f1"
    assert audit["rows"]["feed_only"][0]["surfaced_by"] == ["feed:home_feed"]
    assert audit["rows"]["unadjudicable"][0]["job_id"] == "indeed:q1"

    dry = observed_jobs.repair_query_provenance(db)
    assert dry["applied"] is False and dry["repaired"] == 1
    assert db.get(ObservedJob, "indeed:f1").search_queries == ["data analyst"], "a dry run wrote"

    done = observed_jobs.repair_query_provenance(db, apply=True)
    assert done["applied"] is True and done["changes"][0]["removed"] == ["data analyst"]
    assert db.get(ObservedJob, "indeed:f1").search_queries == []
    # the one it could not prove is exactly as it was
    assert db.get(ObservedJob, "indeed:q1").search_queries == ["report analyst", "data analyst"]
    assert done["refused"]["unadjudicable"] == 1


def test_a_row_with_no_sighting_links_is_never_called_a_liar(db):
    """Rows predating the join table have queries and no links. Absence of a link is not evidence
    of a lie, and an audit that counted them would report the whole corpus as contaminated."""
    import observed_jobs
    from models import ObservedJob

    upsert_observed_jobs(db, _cards("old"), "indeed", None)
    db.get(ObservedJob, "indeed:old").search_queries = ["some ancient query"]
    db.flush()
    audit = observed_jobs.audit_query_provenance(db)
    assert audit["joined_rows"] == 0
    assert audit["repairable"] == 0 and audit["unadjudicable"] == 0


# --- one fact, one place (§16) -----------------------------------------------------------------
def test_the_queries_that_surfaced_a_job_are_derived_not_stored(db):
    """The column is no longer written; `SearchSighting` is the only record, and `queries_for`
    reads it back. There is nowhere left to ASSERT a query — only somewhere to record a search that
    surfaced a job — which is what makes the 20-row class impossible rather than discouraged."""
    import observed_jobs
    from models import ObservedJob

    s = searches_mod.ensure_active_search(db, session_id=1, engine="indeed",
                                          query="reporting analyst")
    upsert_observed_jobs(db, _cards("a1"), "indeed", "reporting analyst", search=s)
    assert db.get(ObservedJob, "indeed:a1").search_queries == [], "the column was written"
    assert observed_jobs.queries_for(db, ["indeed:a1"]) == {"indeed:a1": ["reporting analyst"]}


def test_a_job_only_a_feed_surfaced_derives_NO_query(db):
    """The exact 14-row lie, now impossible to express. A feed has no query; the derivation says so
    by construction instead of by a caller remembering."""
    import observed_jobs

    feed = searches_mod.ensure_active_feed(db, session_id=1, engine="indeed")
    upsert_observed_jobs(db, _cards("f1"), "indeed", None, search=feed)
    assert observed_jobs.queries_for(db, ["indeed:f1"]) == {"indeed:f1": []}


def test_two_searches_that_found_the_same_job_both_show_oldest_first(db):
    import observed_jobs

    a = searches_mod.ensure_active_search(db, session_id=1, engine="indeed", query="report analyst")
    upsert_observed_jobs(db, _cards("j1"), "indeed", "report analyst", search=a)
    b = searches_mod.ensure_active_search(db, session_id=1, engine="indeed", query="data analyst")
    upsert_observed_jobs(db, _cards("j1"), "indeed", "data analyst", search=b)
    assert observed_jobs.queries_for(db, ["indeed:j1"]) == {
        "indeed:j1": ["report analyst", "data analyst"]}


def test_deriving_a_whole_page_costs_ONE_statement(db):
    """A display field must not become an N+1 over a 100-row dashboard — that is how a correctness
    fix turns into a performance regression nobody attributes to it."""
    import observed_jobs
    from sqlalchemy import event

    s = searches_mod.ensure_active_search(db, session_id=1, engine="indeed", query="analyst")
    ids = [f"indeed:p{i}" for i in range(40)]
    upsert_observed_jobs(db, _cards(*[f"p{i}" for i in range(40)]), "indeed", "analyst", search=s)
    db.flush()

    seen: list[str] = []
    def _count(conn, cursor, statement, *a):        # noqa: ANN001
        if statement.lstrip().upper().startswith("SELECT"):
            seen.append(statement)
    event.listen(db.get_bind(), "before_cursor_execute", _count)
    try:
        out = observed_jobs.queries_for(db, ids)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", _count)
    assert len(out) == 40 and out["indeed:p7"] == ["analyst"]
    assert len(seen) == 1, f"derivation ran {len(seen)} SELECTs for one page of rows"


def test_filters_are_not_backfilled_onto_a_search_that_already_gathered_rows(db):
    """Search 13 is the live case: 25 rows banked unfiltered, then LinkedIn's Easy-Apply filter
    appeared. Backfilling the new filters onto that row would label 25 already-collected rows as
    having been gathered under a filter that did not exist when they were read — a claim about the
    past, which is exactly what this column exists to prevent."""
    s = searches_mod.ensure_active_search(db, session_id=7, engine="linkedin", query="analyst")
    upsert_observed_jobs(db, _cards("a1"), "linkedin", "analyst", search=s)
    db.flush()
    searches_mod.ensure_active_search(db, session_id=7, engine="linkedin", query="analyst",
                                      filters={"f_AL": "true"})
    assert s.filters == "", "stamped a filter onto rows gathered before anyone looked"
