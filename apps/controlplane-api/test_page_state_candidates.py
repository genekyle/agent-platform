"""Tests for candidate page states — the registry growing from what the agent actually meets.

The invariant that matters most: a candidate is INERT. It must never leak into a labeler menu or
a training label until a human promotes it, or the agent would be training on its own guesses.
"""

from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import page_state_candidates as psc
from db import Base
from models import PageStateRegistry


def _db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_slugify_makes_a_registry_safe_id():
    assert psc.slugify("Workday Sign In") == "workday_sign_in"
    assert psc.slugify("  AI  Recruiter — Gate!! ") == "ai_recruiter_gate"
    assert psc.slugify("") == ""


def test_record_candidate_writes_an_inert_row():
    db = _db()
    row = psc.record_candidate(db, proposed_name="AI Recruiter Gate", domain_id="career_search",
                               url="https://acme.example/interview")
    assert row["state_id"] == "ai_recruiter_gate"
    assert row["status"] == psc.CANDIDATE_STATUS      # NOT active — inert until approved
    assert row["scope"] == "domain" and row["domain_id"] == "career_search"
    assert "Approve it" in row["description"]          # tells the operator what to do with it


def test_candidate_is_invisible_to_the_active_state_queries():
    """The whole safety story: everything that feeds labeling/classification filters on
    status == 'active', so an unapproved guess cannot contaminate a training label."""
    db = _db()
    psc.record_candidate(db, proposed_name="Mystery Page", domain_id="career_search")
    active = db.scalars(
        select(PageStateRegistry).where(PageStateRegistry.status == "active")).all()
    assert active == []
    assert [c["state_id"] for c in psc.list_candidates(db)] == ["mystery_page"]


def test_recording_is_idempotent_and_never_downgrades_a_live_state():
    db = _db()
    db.add(PageStateRegistry(state_id="workday_sign_in", display_name="Workday Sign In",
                             scope="global", status="active"))
    db.commit()

    # Seeing it again must NOT flip a blessed state back to a candidate.
    again = psc.record_candidate(db, proposed_name="Workday Sign In")
    assert again["status"] == "active"
    assert psc.list_candidates(db) == []

    # And a repeated candidate doesn't duplicate.
    psc.record_candidate(db, proposed_name="Mystery Page")
    psc.record_candidate(db, proposed_name="Mystery Page")
    assert len(psc.list_candidates(db)) == 1


def test_scope_narrows_to_the_observation():
    db = _db()
    assert psc.record_candidate(db, proposed_name="A")["scope"] == "global"
    assert psc.record_candidate(db, proposed_name="B", domain_id="d")["scope"] == "domain"
    assert psc.record_candidate(db, proposed_name="C", domain_id="d", goal_id="g")["scope"] == "goal"
    assert psc.record_candidate(db, proposed_name="D", domain_id="d", goal_id="g",
                                scenario_id="s")["scope"] == "scenario"


def test_unusable_or_failing_records_return_none_not_an_exception():
    """Recording what we saw must never break the drive that saw it."""
    db = _db()
    assert psc.record_candidate(db, proposed_name="") is None       # nothing to slug
    assert psc.record_candidate(db, proposed_name="!!!") is None

    # A genuinely broken write (no such table) is swallowed, not raised into the caller.
    broken = sessionmaker(bind=create_engine("sqlite://"))()        # never create_all'd
    assert psc.record_candidate(broken, proposed_name="No Table Here") is None


def test_promotion_is_a_single_status_flip():
    """Promotion needs no new machinery — PATCH /api/training/page-states/{id} sets status."""
    db = _db()
    psc.record_candidate(db, proposed_name="New Screen")
    row = db.get(PageStateRegistry, "new_screen")
    row.status = "active"
    db.commit()
    assert psc.list_candidates(db) == []
    assert db.get(PageStateRegistry, "new_screen").status == "active"
