"""The decision ledger: every card under review, picked AND passed, with its choice set.

The invariant that matters: a corpus of picks alone teaches "apply to everything". The passes
are what make a boundary learnable, and they are the half that disappears when the page moves.
"""

from __future__ import annotations

import job_decisions as jd
import pytest
from db import Base
from models import JobDecision, ObservedJob
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/decisions.db")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def _cards(n=4):
    return [{"job_id": f"indeed:j{i}", "title": f"Data Engineer {i}", "company": f"Co{i}",
             "location": "Nashua, NH", "salary": "$120k", "url": f"https://x/{i}"}
            for i in range(n)]


def test_the_passes_are_recorded_not_just_the_picks(db):
    counts = jd.record_page_decisions(db, cards=_cards(4), picked={"indeed:j1"},
                                      decided_by="operator", session_id=7, page=1,
                                      query="data engineer")
    db.commit()
    assert counts == {"picked": 1, "passed": 3, "skipped": 0}
    rows = db.query(JobDecision).all()
    assert {r.decision for r in rows} == {"picked", "passed"}
    # Without the passes there is no boundary to learn — this is the whole point of the table.
    assert sum(1 for r in rows if r.decision == "passed") == 3


def test_each_row_carries_the_choice_set_it_was_decided_in(db):
    jd.record_page_decisions(db, cards=_cards(4), picked={"indeed:j2"}, decided_by="operator",
                             session_id=7, page=2, query="data engineer")
    db.commit()
    row = db.query(JobDecision).filter_by(job_id="indeed:j2").one()
    assert row.shown_count == 4 and row.page == 2 and row.query == "data engineer"
    assert row.rank == 3                       # position on the page, 1-based, in render order
    assert row.decision == "picked" and row.decided_by == "operator"
    # The card AS SEEN, not the canonical job, which drifts once the ATS enriches it.
    assert row.card["title"] == "Data Engineer 2" and row.card["company"] == "Co2"


def test_rank_is_recorded_because_position_bias_is_real(db):
    jd.record_page_decisions(db, cards=_cards(5), picked=set(), decided_by="operator",
                             session_id=7, page=1, query="q")
    db.commit()
    ranks = sorted(r.rank for r in db.query(JobDecision).all())
    assert ranks == [1, 2, 3, 4, 5]


def test_choosing_again_revises_the_page_rather_than_stacking_duplicates(db):
    # `choose` is a standing rung the operator re-presses; a second press is a REVISED decision.
    jd.record_page_decisions(db, cards=_cards(3), picked=set(), decided_by="operator",
                             session_id=7, page=1, query="q")
    db.commit()
    jd.record_page_decisions(db, cards=_cards(3), picked={"indeed:j0"}, decided_by="operator",
                             session_id=7, page=1, query="q",
                             reasons={"indeed:j0": "senior, in range, hybrid"})
    db.commit()
    rows = db.query(JobDecision).all()
    assert len(rows) == 3                                   # revised, not doubled
    picked = [r for r in rows if r.decision == "picked"]
    assert len(picked) == 1 and picked[0].reason == "senior, in range, hybrid"


def test_a_reason_is_optional_and_never_invented(db):
    jd.record_page_decisions(db, cards=_cards(2), picked={"indeed:j0"}, decided_by="operator",
                             session_id=7, page=1, query="q")
    db.commit()
    assert all(r.reason == "" for r in db.query(JobDecision).all())


def test_a_job_that_cannot_be_canonicalised_is_still_recorded(db):
    # No sighting row, no canonical Job — the decision is still worth keeping, keyed by job_id.
    jd.record_page_decisions(db, cards=[{"job_id": "indeed:orphan", "title": "T"}],
                             picked=set(), decided_by="operator", session_id=7, page=1, query="q")
    db.commit()
    row = db.query(JobDecision).one()
    assert row.job_id == "indeed:orphan" and row.decision == "passed"


def test_a_decision_follows_the_job_through_a_merge(db):
    import job_dedup
    from models import Job
    sighting = ObservedJob(job_id="indeed:j0", platform="indeed", external_id="j0",
                           title="Data Engineer", company="Co0")
    db.add(sighting)
    key = job_dedup.mint_job_key("indeed:j0")
    db.add(Job(job_key=key, company="Co0", title="Data Engineer"))
    db.commit()
    jd.record_page_decisions(db, cards=_cards(1), picked={"indeed:j0"}, decided_by="operator",
                             session_id=7, page=1, query="q")
    db.commit()
    # The key is resolved at WRITE time, so the outcome join (ApplicationEvent -> job_key) holds.
    assert db.query(JobDecision).one().job_key == key


def test_the_summary_watches_the_ratio_that_matters(db):
    jd.record_page_decisions(db, cards=_cards(4), picked={"indeed:j0"}, decided_by="operator",
                             session_id=7, page=1, query="q", reasons={"indeed:j0": "in range"})
    db.commit()
    s = jd.summary(db)
    assert s["decisions"] == 4 and s["picked"] == 1 and s["passed"] == 3
    assert s["with_reason"] == 1 and s["by_decider"] == {"operator": 4}


def test_decisions_carry_the_search_join(db):
    """Picks and passes tie to the Search row that put the cards on the table (2026-08-10) —
    the query string stays for display, the id is the join."""
    cards = [{"job_id": "indeed:s1", "title": "A"}, {"job_id": "indeed:s2", "title": "B"}]
    jd.record_page_decisions(db, cards=cards, picked={"indeed:s1"}, decided_by="operator",
                             session_id=25, page=1, query="data analytics", search_id=7)
    db.commit()
    rows = db.scalars(select(JobDecision)).all()
    assert len(rows) == 2
    assert all(r.search_id == 7 for r in rows)
    # a re-choose without a known search must not erase the recorded provenance
    jd.record_page_decisions(db, cards=cards, picked={"indeed:s2"}, decided_by="operator",
                             session_id=25, page=1, query="data analytics", search_id=None)
    db.commit()
    assert all(r.search_id == 7 for r in db.scalars(select(JobDecision)).all())


def test_a_second_search_in_one_session_does_not_overwrite_the_firsts_page_one(db):
    """TWO SEARCHES, TWO DECISIONS, BOTH KEPT — the silent overwrite found building the repick.

    A session holds many searches and every one of them starts again at page 1, so an idempotence
    key of `(session, page)` says "page 1 of this session" — which is two different pages of two
    different result sets. A job surfaced by both queries (likely: same location, adjacent terms)
    had its first decision REWRITTEN by its second, and that pair — passed on one query, picked on
    the next — is exactly the contrast a boundary is learned from.
    """
    shared = {"job_id": "indeed:both", "title": "Report Analyst", "company": "Co"}
    jd.record_page_decisions(db, cards=[shared], picked=set(), decided_by="operator",
                             session_id=28, page=1, query="report analyst", search_id=3)
    db.commit()
    jd.record_page_decisions(db, cards=[shared], picked={"indeed:both"}, decided_by="operator",
                             session_id=28, page=1, query="data analyst", search_id=4)
    db.commit()

    rows = db.scalars(select(JobDecision).where(JobDecision.job_id == "indeed:both")).all()
    assert len(rows) == 2, "the second search overwrote the first search's decision"
    assert {(r.search_id, r.decision, r.query) for r in rows} == {
        (3, "passed", "report analyst"), (4, "picked", "data analyst")}

    # And within ONE search a re-press is still a revision, not a duplicate — the standing select
    # rung has to stay re-pressable, which is the property this key must not break.
    jd.record_page_decisions(db, cards=[shared], picked=set(), decided_by="operator",
                             session_id=28, page=1, query="data analyst", search_id=4)
    db.commit()
    rows = db.scalars(select(JobDecision).where(JobDecision.job_id == "indeed:both")).all()
    assert len(rows) == 2
    assert {(r.search_id, r.decision) for r in rows} == {(3, "passed"), (4, "passed")}


def test_a_legacy_row_with_no_search_is_revised_rather_than_duplicated(db):
    """Rows written before searches were rows carry `search_id IS NULL`. A re-press that now knows
    its search must ADOPT that row, not shadow it — the alternative is a duplicate for every
    decision made before the column existed."""
    card = {"job_id": "indeed:legacy", "title": "Analyst"}
    jd.record_page_decisions(db, cards=[card], picked=set(), decided_by="operator",
                             session_id=9, page=1, query="q")
    db.commit()
    jd.record_page_decisions(db, cards=[card], picked={"indeed:legacy"}, decided_by="operator",
                             session_id=9, page=1, query="q", search_id=11)
    db.commit()
    rows = db.scalars(select(JobDecision).where(JobDecision.job_id == "indeed:legacy")).all()
    assert len(rows) == 1
    assert rows[0].search_id == 11 and rows[0].decision == "picked"
