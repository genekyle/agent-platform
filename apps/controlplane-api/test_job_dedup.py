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


def _job(db, key, title, company, *, platform="indeed", url="", req=None, seen=T0, location="",
         salary=None):
    row = Job(job_key=key, title=title, company=company,
              company_norm=jd.normalize_company(company), location=location, salary=salary,
              canonical_url=url, requisition_id=req, source_platforms=[platform],
              sighting_count=1, first_seen_at=seen, last_seen_at=seen)
    db.add(row)
    db.flush()
    return row


def _sighting(db, job_id, title, company, *, platform="indeed", url="", seen=T0, status="seen",
              location="", salary=None):
    row = ObservedJob(job_id=job_id, platform=platform, external_id=job_id.split(":", 1)[-1],
                      title=title, company=company, url=url, location=location, salary=salary,
                      application_status=status,
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
    # Both jobs get a real sighting: counts and platforms are DERIVED from these rows, so a job
    # asserting a platform no sighting backs is a fixture that cannot occur in the live table.
    _sighting(db, "indeed:11", "Data Analyst", "Acme").canonical_job_key = "a"
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


def test_a_scrape_that_missed_the_employer_is_matched_to_the_named_row(db):
    """9 of 355 live rows had no company and duplicated a job we already had with one."""
    _job(db, "named", "Software Engineer - Back-End", "DEKA Research & Development",
         seen=T0 + timedelta(days=3))
    _job(db, "blank", "Software Engineer - Back-End", "", seen=T0)
    assert jd.scan_and_record(db) == {"merged": 0, "queued": 1}
    match = db.query(JobMatch).one()
    assert match.tier == "blank_company"
    # The NAMED row survives even though it is the newer one — the two are not equal records.
    assert match.kept_key == "named" and match.folded_key == "blank"


def test_merging_into_a_blank_row_never_loses_the_employer(db):
    """The operator can flip the direction; doing so must not drop the only company name we have."""
    _job(db, "named", "Software Engineer - Back-End", "DEKA Research & Development")
    _job(db, "blank", "Software Engineer - Back-End", "", seen=T0 + timedelta(days=1))

    jd.apply_merge(db, "blank", "named")     # deliberately the wrong way round
    db.commit()

    kept = db.get(Job, "blank")
    assert kept.company == "DEKA Research & Development"
    assert kept.company_norm == jd.normalize_company("DEKA Research & Development")


def test_two_levels_of_the_same_role_stay_two_jobs(db):
    _sighting(db, "indeed:1", "Financial Analyst II", "Elbit America")
    _sighting(db, "indeed:2", "Financial Analyst III", "Elbit America")
    jd.resolve_all(db)
    assert db.query(Job).count() == 2
    assert jd.propose_matches(db) == []


# ==============================================================================================
# One board serving one requisition several times — session 24, page 1, measured
# ==============================================================================================
#
# Both groups below are verbatim from the live table on 2026-07-30, and they are the pair that
# decides where the auto-merge line goes. Every field agrees in the first and exactly one field
# disagrees in the second, so a matcher that gets one right by being loose gets the other wrong.

#: Indeed served ONE Bristol County Savings Bank requisition four times inside a single result
#: page, under four ids. A fifth sighting of it was already on file from five days earlier.
BRISTOL = dict(title="Human Resources Data Analyst", company="BRISTOL COUNTY SAVINGS BANK",
               location="Taunton, MA 02780", salary="From $60,000 a year")
BRISTOL_IDS = ("67a36d0962578890", "3eadcd066f720f0d", "987168995fca71d9", "1c7c998bd1c31dad")

#: Two REAL postings, same title, same employer, same pay band, different office. Nothing about
#: these may ever be folded together.
LIBERTY = dict(title="Principal SAP PaPM Configuration Engineer", company="Liberty Mutual",
               salary="$120,000 - $225,000 a year")


def test_the_bristol_four_merge_to_one_job(db):
    """Four Job rows, every field identical, one engine → one job and three tombstones."""
    for i, ext in enumerate(BRISTOL_IDS):
        _job(db, f"job_{ext}", BRISTOL["title"], BRISTOL["company"],
             location=BRISTOL["location"], salary=BRISTOL["salary"], seen=T0 + timedelta(minutes=i))
    db.commit()

    assert jd.scan_and_record(db) == {"merged": 3, "queued": 0}

    live = [j for j in db.query(Job).all() if j.merged_into_key is None]
    assert len(live) == 1
    # Tombstoned, never deleted: every id the operator may have saved still resolves to the winner.
    for ext in BRISTOL_IDS:
        assert jd.resolve_key(db, f"job_{ext}") == live[0].job_key
    assert db.query(JobMatch).filter_by(tier="same_posting").count() == 3


def test_the_bristol_four_carry_their_sightings_onto_the_one_job(db):
    """The same four arriving as SIGHTINGS: one job, four sightings recorded against it."""
    for i, ext in enumerate(BRISTOL_IDS):
        _sighting(db, f"indeed:{ext}", BRISTOL["title"], BRISTOL["company"],
                  location=BRISTOL["location"], salary=BRISTOL["salary"],
                  seen=T0 + timedelta(minutes=i))
    db.commit()
    jd.resolve_all(db)

    keys = {s.canonical_job_key for s in db.query(ObservedJob).all()}
    assert len(keys) == 1, "four cards for one requisition became four jobs"
    job = db.get(Job, keys.pop())
    assert job.sighting_count == 4
    assert [j for j in db.query(Job).all() if j.merged_into_key is None] == [job]


def test_the_same_title_in_two_cities_is_two_jobs(db):
    """Liberty Mutual posts this role in Boston AND Portsmouth. Merging them loses a real job.

    Not queued either: a stated address that positively disagrees is not a near-miss awaiting a
    human, it is the answer.
    """
    _job(db, "boston", LIBERTY["title"], LIBERTY["company"],
         location="Hybrid work in Boston, MA", salary=LIBERTY["salary"])
    _job(db, "portsmouth", LIBERTY["title"], LIBERTY["company"],
         location="Hybrid work in Portsmouth, NH", salary=LIBERTY["salary"],
         seen=T0 + timedelta(days=1))
    db.commit()

    assert jd.scan_and_record(db) == {"merged": 0, "queued": 0}
    assert db.get(Job, "portsmouth").merged_into_key is None
    assert db.get(Job, "boston").merged_into_key is None


def test_the_liberty_pair_stays_two_jobs_when_it_arrives_as_sightings(db):
    """The path that actually collapsed them in the live table: attach-on-scrape, location-blind."""
    _sighting(db, "indeed:b6fe51544f2909ef", LIBERTY["title"], LIBERTY["company"],
              location="Hybrid work in Boston, MA", salary=LIBERTY["salary"])
    _sighting(db, "indeed:de43b5246ab451d8", LIBERTY["title"], LIBERTY["company"],
              location="Hybrid work in Portsmouth, NH", salary=LIBERTY["salary"],
              seen=T0 + timedelta(days=1))
    db.commit()
    jd.resolve_all(db)

    keys = {s.canonical_job_key for s in db.query(ObservedJob).all()}
    assert len(keys) == 2, "two cities collapsed into one job on the way in"


# --- what 'same place' means ------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("Boston, MA", "Hybrid work in Boston, MA"),          # Joslin Diabetes Center, measured
    ("Burlington, MA", "Burlington, MA (Hybrid)"),        # Keurig Dr Pepper, measured
    ("Taunton, MA 02780", "Taunton, MA"),                 # a zip one scrape carried
])
def test_a_working_arrangement_is_not_a_different_address(a, b):
    assert jd.normalize_location(a) == jd.normalize_location(b)
    assert not jd.locations_conflict(a, b)
    assert jd.locations_agree(a, b)


@pytest.mark.parametrize("a,b", [
    ("Hybrid work in Boston, MA", "Hybrid work in Portsmouth, NH"),   # Liberty Mutual
    ("Boston, MA", "Needham, MA"),                                    # Wellington Management
    ("Boston, MA (Remote)", "Providence, RI (Remote)"),               # Husch Blackwell
    ("Hybrid work in Manchester, NH", "Newington, NH"),               # Northwestern Mutual
])
def test_the_cross_city_pairs_the_corpus_had_already_collapsed(a, b):
    """All four were one row apiece in the live table on 2026-07-30. All four are two jobs."""
    assert jd.locations_conflict(a, b)
    assert not jd.locations_agree(a, b)


def test_an_unscraped_location_is_unknown_and_not_elsewhere(db):
    """DEKA Research's back-end engineer is on file twice, once with no location at all."""
    assert not jd.locations_conflict("", "Manchester, NH 03101")
    assert not jd.locations_agree("", "Manchester, NH 03101")   # ...but it corroborates nothing


# --- what the auto tier still refuses ----------------------------------------------------------

def test_two_boards_agreeing_is_proposed_rather_than_merged(db):
    """`same_posting` is about ONE board re-rendering its own card. Two boards is a wider claim."""
    _job(db, "a", **BRISTOL)
    _job(db, "b", platform="linkedin", seen=T0 + timedelta(days=1), **BRISTOL)
    db.commit()
    assert jd.scan_and_record(db) == {"merged": 0, "queued": 1}
    assert db.query(JobMatch).one().tier == "identical_title"


def test_pay_that_disagrees_blocks_the_merge(db):
    _job(db, "a", BRISTOL["title"], BRISTOL["company"],
         location=BRISTOL["location"], salary="From $60,000 a year")
    _job(db, "b", BRISTOL["title"], BRISTOL["company"],
         location=BRISTOL["location"], salary="From $85,000 a year", seen=T0 + timedelta(days=1))
    db.commit()
    assert jd.scan_and_record(db) == {"merged": 0, "queued": 0}


def test_both_cards_quoting_no_pay_is_not_a_disagreement(db):
    """Sonsoft's 'Informatica B2B' appeared four times in Springfield with pay on none of them."""
    for i in range(3):
        _job(db, f"s{i}", "Informatica B2B", "Sonsoft Inc", location="Springfield, MA",
             seen=T0 + timedelta(minutes=i))
    db.commit()
    assert jd.scan_and_record(db) == {"merged": 2, "queued": 0}


def test_two_grades_at_one_address_are_two_jobs(db):
    """Elbit America, Merrimack NH — 'Financial Analyst II' and 'III', correctly on file as two."""
    _job(db, "ii", "Financial Analyst II", "Elbit America", location="Merrimack, NH")
    _job(db, "iii", "Financial Analyst III", "Elbit America", location="Merrimack, NH",
         seen=T0 + timedelta(days=1))
    db.commit()
    assert jd.scan_and_record(db) == {"merged": 0, "queued": 0}


# --- repairing what the location-blind rule already did ----------------------------------------

def test_the_repair_splits_a_job_holding_two_cities(db):
    job = _job(db, "lm", LIBERTY["title"], LIBERTY["company"],
               location="Hybrid work in Boston, MA", salary=LIBERTY["salary"])
    for ext, loc in (("b6fe51544f2909ef", "Hybrid work in Boston, MA"),
                     ("de43b5246ab451d8", "Hybrid work in Portsmouth, NH")):
        s = _sighting(db, f"indeed:{ext}", LIBERTY["title"], LIBERTY["company"],
                      location=loc, salary=LIBERTY["salary"])
        s.canonical_job_key = job.job_key
    db.commit()

    assert len(jd.split_conflicting_sightings(db)) == 1
    keys = {s.canonical_job_key for s in db.query(ObservedJob).all()}
    assert len(keys) == 2
    assert db.get(Job, "lm").sighting_count == 1        # recounted, not left claiming two


def test_the_repair_never_undoes_a_merge_a_human_approved(db):
    """DEKA Research, measured: the operator merged a blank-employer row by hand. The first cut of
    this repair offered to take that back, because a merged job legitimately holds sightings whose
    fields differ from the row that survived."""
    kept = _job(db, "kept", "Software Engineer - Back-End", "DEKA Research & Development",
                location="Manchester, NH 03101")
    for ext, loc in (("aa", "Manchester, NH 03101"), ("bb", "Nashua, NH")):
        s = _sighting(db, f"indeed:{ext}", "Software Engineer - Back-End", "DEKA Research",
                      location=loc)
        s.canonical_job_key = kept.job_key
    # The tombstone is the whole signal: `indeed:bb` once had a job of its own, and a recorded
    # merge folded it into `kept`. Without this row the repair would rightly split the pair.
    db.add(Job(job_key=jd.mint_job_key("indeed:bb"), title="Software Engineer - Back-End",
               company="", company_norm="", merged_into_key=kept.job_key,
               first_seen_at=T0, last_seen_at=T0))
    db.add(JobMatch(kept_key=kept.job_key, folded_key=jd.mint_job_key("indeed:bb"),
                    tier="blank_company", score=1.0, evidence=["identical title"],
                    status="merged", decided_by="human"))
    db.commit()

    assert jd.split_conflicting_sightings(db, dry_run=True) == []
    stray = db.query(ObservedJob).filter_by(job_id="indeed:bb").one()
    assert stray.canonical_job_key == kept.job_key


def test_the_repair_is_idempotent(db):
    job = _job(db, "lm", LIBERTY["title"], LIBERTY["company"],
               location="Hybrid work in Boston, MA")
    for ext, loc in (("aa", "Hybrid work in Boston, MA"), ("bb", "Hybrid work in Portsmouth, NH")):
        s = _sighting(db, f"indeed:{ext}", LIBERTY["title"], LIBERTY["company"], location=loc)
        s.canonical_job_key = job.job_key
    db.commit()

    assert len(jd.split_conflicting_sightings(db)) == 1
    assert jd.split_conflicting_sightings(db) == []
