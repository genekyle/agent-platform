"""The dedup matcher, pinned to the pairs the REAL corpus produced.

Every false positive asserted against below was found by running the previous scorer over the
355 live rows on 2026-07-30 — these are not invented adversarial cases, they are the actual
output. Keeping them here means the next person to loosen the threshold finds out immediately
which employer's postings they just collapsed.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import job_dedup as jd
from db import Base
from deps import as_aware
from models import Application, Job, JobMatch, ObservedJob

T0 = datetime(2026, 7, 1, tzinfo=timezone.utc)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _job(db, key, title, company, *, platform="indeed", url="", req=None, seen=T0, location=""):
    row = Job(job_key=key, title=title, company=company,
              company_norm=jd.normalize_company(company), location=location,
              canonical_url=url, requisition_id=req, source_platforms=[platform],
              sighting_count=1, first_seen_at=seen, last_seen_at=seen)
    db.add(row)
    db.flush()
    return row


def _sighting(db, job_id, title, company, *, platform="indeed", url="", seen=T0, status="seen"):
    row = ObservedJob(job_id=job_id, platform=platform, external_id=job_id.split(":", 1)[-1],
                      title=title, company=company, url=url, location="", application_status=status,
                      seen_count=1, search_queries=[], capture_filenames=[],
                      first_seen_at=seen, last_seen_at=seen)
    db.add(row)
    db.flush()
    return row


# --- the three measured failure mechanisms -------------------------------------------------

def test_a_generic_title_is_not_every_richer_title_at_that_employer():
    """Liberty Mutual, measured: the old scorer returned 1.00 for this pair."""
    assert jd.title_similarity("Software Engineer",
                               "Senior Data Integration Software Engineer") < jd.FUZZY_TITLE_THRESHOLD


@pytest.mark.parametrize("other", [
    "Client Solutions Business Analyst",
    "Business Analyst Performance Product, Officer",
    "IAM Strategy and Operations Business Analyst, Assistant Vice President",
    "Senior Business Analyst, AVP II - State Street Investment Management",
])
def test_state_street_business_analysts_stay_separate(other):
    """All four scored 1.00 against a bare 'Business Analyst' before the containment guard."""
    assert jd.title_similarity("Business Analyst", other) < jd.FUZZY_TITLE_THRESHOLD


def test_an_interleaved_qualifier_is_not_an_appended_department():
    """State Street, measured 1.00. 'Governance' sits INSIDE the overlap and changes the role."""
    assert jd.title_similarity("Senior Associate - Data Governance Analyst",
                               "Senior Associate - Data Analyst") < jd.FUZZY_TITLE_THRESHOLD


def test_seniority_numerals_are_not_erased():
    """Elbit America, measured 1.00 — the old token filter deleted 'II' and 'III' as too short."""
    assert jd.title_similarity("Financial Analyst III", "Financial Analyst II") == 0.0
    assert jd.level_ranks("Senior Business Analyst, AVP II") == {2}


def test_an_asymmetric_rank_is_a_different_requisition():
    assert jd.title_similarity("Data Integration Software Engineer",
                               "Senior Data Integration Software Engineer") < jd.FUZZY_TITLE_THRESHOLD


# --- what must still match ------------------------------------------------------------------

def test_an_appended_department_still_reads_as_one_job():
    """The case containment exists to protect; breaking it is how one job reads as two."""
    assert jd.title_similarity("Healthcare Data Analyst",
                               "Healthcare Data Analyst - BIDMC, OBGYN Quality") == 1.0


def test_an_en_dash_and_a_hyphen_are_the_same_title():
    assert jd.title_similarity("Healthcare Data Analyst – BIDMC, OBGYN Quality",
                               "Healthcare Data Analyst - BIDMC, OBGYN Quality") == 1.0


def test_the_cross_platform_duplicates_found_in_the_corpus_match():
    """Wellington and Keurig each sat in the table twice, once per board, invisibly."""
    assert jd.title_similarity("Financial Reporting Analyst, US Funds",
                               "Financial Reporting Analyst, US Funds") == 1.0
    assert jd.title_similarity("Senior Analyst, Performance Measurement & Visualization",
                               "Senior Analyst, Performance Measurement & Visualization") == 1.0


def test_sr_and_senior_are_one_word():
    assert jd.rank_words("Sr. Reporting Analyst") == jd.rank_words("Senior Reporting Analyst")


def test_company_suffixes_are_noise_but_title_words_are_not():
    assert jd.normalize_company("Beth Israel Lahey Health") == \
           jd.normalize_company("Beth Israel Lahey Health, Inc.")
    # 'Health' is noise in a company name and the subject of a title — the old shared list lost this.
    assert "health" in jd.title_tokens("Health Data Analyst")


def test_the_same_role_at_a_different_employer_is_never_compared(db):
    _job(db, "a", "Data Analyst", "Acme")
    _job(db, "b", "Data Analyst", "Globex")
    assert jd.propose_matches(db) == []


# --- tiers and what may act ------------------------------------------------------------------

def test_a_shared_requisition_merges_itself(db):
    _job(db, "a", "Healthcare Data Analyst", "BILH", url="https://x.com/job/JR-88822")
    _job(db, "b", "Healthcare Data Analyst - BIDMC", "Beth Israel Lahey Health",
         platform="linkedin", req="jr88822", seen=T0 + timedelta(days=1))
    assert jd.scan_and_record(db) == {"merged": 1, "queued": 0}
    assert db.get(Job, "b").merged_into_key == "a"


def test_an_identical_title_asks_rather_than_merges(db):
    _job(db, "a", "Financial Reporting Analyst, US Funds", "Wellington Management")
    _job(db, "b", "Financial Reporting Analyst, US Funds", "Wellington Management",
         platform="linkedin", seen=T0 + timedelta(days=1))
    assert jd.scan_and_record(db) == {"merged": 0, "queued": 1}
    match = db.query(JobMatch).one()
    assert match.status == "pending" and match.tier == "identical_title"
    assert db.get(Job, "b").merged_into_key is None      # nothing hidden without a human
    assert "seen on indeed, linkedin" in match.evidence


def test_the_older_job_always_survives_whichever_way_the_pair_is_read(db):
    _job(db, "newer", "Data Platform Analyst", "Acme", seen=T0 + timedelta(days=5))
    _job(db, "older", "Data Platform Analyst", "Acme", seen=T0)
    jd.scan_and_record(db)
    assert db.query(JobMatch).one().kept_key == "older"


def test_a_rejected_pair_is_never_proposed_again(db):
    _job(db, "a", "Data Platform Analyst", "Acme")
    _job(db, "b", "Data Platform Analyst", "Acme", seen=T0 + timedelta(days=1))
    jd.scan_and_record(db)
    db.query(JobMatch).one().status = "rejected"
    db.commit()
    assert jd.propose_matches(db) == []


# --- merging keeps everything -----------------------------------------------------------------

def test_a_merge_repoints_sightings_and_leaves_a_tombstone(db):
    _job(db, "a", "Data Analyst", "Acme")
    _job(db, "b", "Data Analyst", "Acme", platform="linkedin", seen=T0 + timedelta(days=2))
    s = _sighting(db, "linkedin:99", "Data Analyst", "Acme", platform="linkedin")
    s.canonical_job_key = "b"
    db.commit()

    jd.apply_merge(db, "a", "b")
    db.commit()

    assert s.canonical_job_key == "a"
    assert db.get(Job, "a").source_platforms == ["indeed", "linkedin"]
    assert db.get(Job, "a").sighting_count == 2
    # The old key still resolves — a reference another domain saved yesterday must not 404.
    assert jd.resolve_key(db, "b") == "a"


def test_a_richer_description_wins_the_merge(db):
    a = _job(db, "a", "Data Analyst", "Acme")
    a.description, a.description_source = "short pane blurb", "indeed_pane"
    b = _job(db, "b", "Data Analyst", "Acme", seen=T0 + timedelta(days=1))
    b.description, b.description_source = "the full ATS posting", "ats"
    db.commit()

    jd.apply_merge(db, "a", "b")
    assert db.get(Job, "a").description == "the full ATS posting"


def test_merging_two_applied_jobs_keeps_one_application_and_every_event(db):
    """The Workday incident, in data: applied twice through two doors that were one job."""
    from application_events import ensure_application, record_event

    _job(db, "a", "Healthcare Data Analyst", "BILH")
    _job(db, "b", "Healthcare Data Analyst - BIDMC", "BILH", seen=T0 + timedelta(days=1))
    app_a = ensure_application(db, "a", applied_at=T0, via_platform="indeed")
    app_b = ensure_application(db, "b", applied_at=T0 + timedelta(days=1), via_platform="workday")
    record_event(db, app_b, kind="rejection", source="human", occurred_at=T0 + timedelta(days=9))
    db.commit()

    jd.apply_merge(db, "a", "b")
    db.commit()

    survivors = db.query(Application).all()
    assert len(survivors) == 1 and survivors[0].job_key == "a"
    assert {e.kind for e in survivors[0].events} == {"applied", "rejection"}
    assert survivors[0].status == "rejected"
    # as_aware: SQLite hands tz-aware columns back naive, so compare on equal footing.
    assert as_aware(survivors[0].applied_at) == T0          # the earliest of the two


# --- resolving sightings to canonical jobs -----------------------------------------------------

def test_the_same_job_on_two_boards_resolves_to_one_job(db):
    _sighting(db, "indeed:abc", "Senior Analyst, Performance Measurement", "Keurig Dr Pepper")
    _sighting(db, "linkedin:xyz", "Senior Analyst, Performance Measurement", "Keurig Dr Pepper",
              platform="linkedin")
    jd.resolve_all(db)

    live = db.query(Job).filter(Job.merged_into_key.is_(None)).all()
    assert len(live) == 1
    assert live[0].source_platforms == ["indeed", "linkedin"]


def test_resolving_is_idempotent(db):
    _sighting(db, "indeed:abc", "Data Analyst", "Acme")
    assert jd.resolve_all(db) == 1
    assert jd.resolve_all(db) == 0
    assert db.query(Job).count() == 1


def test_a_rotated_indeed_id_for_the_same_posting_folds_in(db):
    """Indeed's jk rotates per search session, so one posting arrives as two sightings."""
    _sighting(db, "indeed:jk1", "Principal SAP PaPM Configuration Engineer", "Liberty Mutual")
    _sighting(db, "indeed:jk2", "Principal SAP PaPM Configuration Engineer", "Liberty Mutual")
    jd.resolve_all(db)
    assert db.query(Job).count() == 1
    assert db.query(Job).one().sighting_count == 2


def test_two_levels_of_the_same_role_stay_two_jobs(db):
    _sighting(db, "indeed:1", "Financial Analyst II", "Elbit America")
    _sighting(db, "indeed:2", "Financial Analyst III", "Elbit America")
    jd.resolve_all(db)
    assert db.query(Job).count() == 2
    assert jd.propose_matches(db) == []
