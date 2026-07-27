"""Tests for the applied-index — the question that should have been asked before a whole drive.

What is being pinned, in order of what it costs to get wrong:

  1. A job applied to through the ATS is recognised when met again as an Indeed card. That is the
     BIDMC case, and missing it cost a full drive.
  2. A fuzzy match NEVER reports `applied`. Silently skipping a job the operator picked is worse
     than asking about it.
  3. Punctuation and appended departments do not make one job read as two.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import applied_index as ai
from models import Base, ObservedJob, utcnow


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _job(db, job_id, title, company, *, url="", status="seen", applied=False, tenant=None):
    row = ObservedJob(job_id=job_id, platform=job_id.split(":")[0],
                      external_id=job_id.split(":", 1)[1], title=title, company=company,
                      url=url, tenant_id=tenant, application_status=status,
                      applied_at=utcnow() if applied else None)
    db.add(row)
    db.commit()
    return row


# --- tier 1: the same job ----------------------------------------------------------------
def test_the_same_job_id_is_certain(db):
    _job(db, "indeed:abc123", "Data Analyst", "Acme", status="applied", applied=True)
    v = ai.check(db, job_id="indeed:abc123")
    assert v.applied and v.matched_on == "exact"


def test_a_job_merely_seen_is_not_applied(db):
    _job(db, "indeed:abc123", "Data Analyst", "Acme")            # status stays 'seen'
    assert ai.check(db, job_id="indeed:abc123").status == ai.STATUS_NONE


# --- tier 2: the same requisition through a different door -------------------------------
def test_a_workday_application_is_found_again_from_the_indeed_card(db):
    """THE case this module exists for. Applied through bilh.wd1.myworkdayjobs.com; met again as
    an Indeed jk, which shares no id with it. Only the requisition ties them together."""
    _job(db, "workday:JR88822",
         "Healthcare Data Analyst - BIDMC, OBGYN Quality", "Beth Israel Lahey Health",
         url="https://bilh.wd1.myworkdayjobs.com/External/job/JR88822",
         status="applied", applied=True)

    v = ai.check(db, job_id="indeed:e5c794ae32973697",
                 title="Healthcare Data Analyst – BIDMC, OBGYN Quality",
                 company="Beth Israel Lahey Health",
                 url="https://jobs.bilh.org/jobs/healthcare-data-analyst-bidmc-obgyn-quality-boston-ma-jr88822/")
    assert v.applied
    assert v.matched_on == "requisition"
    assert "jr88822" in v.evidence[0]


def test_requisition_ids_survive_punctuation_and_case():
    assert ai.requisition_ids("…/job/JR-88822") == ai.requisition_ids("…/job/jr88822")
    assert "3915" in ai.requisition_ids("https://jobs-joslin.icims.com/jobs/3915/x/candidate")
    assert "12345" in ai.requisition_ids("https://boards.greenhouse.io/x?gh_jid=12345")


# --- tier 3: same employer, same role — a warning, never a decision -----------------------
def test_a_fuzzy_match_asks_rather_than_acts(db):
    _job(db, "workday:JR1", "Healthcare Data Analyst", "Beth Israel Lahey Health",
         status="applied", applied=True)
    v = ai.check(db, job_id="indeed:zzz", title="Healthcare Data Analyst - BIDMC, OBGYN Quality",
                 company="Beth Israel Lahey Health")
    assert v.status == ai.STATUS_LIKELY          # NOT applied
    assert v.worth_asking and not v.applied
    assert v.matched_on == "company_title"


def test_seniority_is_not_collapsed_by_the_fuzzy_tier(db):
    """The match that would wrongly skip a job the operator picked."""
    _job(db, "workday:JR1", "Principal Data Engineer", "Acme", status="applied", applied=True)
    assert ai.check(db, job_id="indeed:zzz", title="Junior Data Engineer",
                    company="Acme").status == ai.STATUS_NONE


def test_the_same_role_at_a_different_employer_is_not_a_match(db):
    _job(db, "workday:JR1", "Data Analyst", "Acme", status="applied", applied=True)
    assert ai.check(db, job_id="indeed:zzz", title="Data Analyst",
                    company="Globex").status == ai.STATUS_NONE


# --- the matching primitives ---------------------------------------------------------------
def test_an_appended_department_does_not_make_one_job_read_as_two():
    assert ai.title_similarity("Healthcare Data Analyst",
                               "Healthcare Data Analyst - BIDMC, OBGYN Quality") == 1.0


def test_an_en_dash_and_a_hyphen_are_the_same_title():
    a = "Healthcare Data Analyst – BIDMC, OBGYN Quality"      # Indeed
    b = "Healthcare Data Analyst - BIDMC, OBGYN Quality"      # Workday
    assert ai.title_similarity(a, b) == 1.0


def test_company_suffixes_are_noise():
    assert ai.normalize_company("Beth Israel Lahey Health") == \
           ai.normalize_company("Beth Israel Lahey Health, Inc.")


# --- the scan-time question ----------------------------------------------------------------
def test_check_many_annotates_a_whole_results_page(db):
    _job(db, "indeed:applied1", "Data Analyst", "Acme", status="applied", applied=True)
    cards = [{"external_id": "applied1", "title": "Data Analyst", "company": "Acme"},
             {"external_id": "fresh2", "title": "BI Developer", "company": "Globex"},
             {"title": "no id — skipped"}]
    out = ai.check_many(db, cards)
    assert out["applied1"].applied
    assert out["fresh2"].status == ai.STATUS_NONE
    assert len(out) == 2
