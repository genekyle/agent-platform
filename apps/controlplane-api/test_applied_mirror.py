"""The applied-jobs record: a confirmed submit must exist in BOTH halves of the database.

The failure this pins (found 2026-08-10): the live drive's submit seam (`_record_outcome`)
stamped the SIGHTING applied and never created the canonical Application — the manual mark
endpoint has mirrored since 07-30, so "did we apply?" had two answers depending on which table
you asked. Now the mirror lives in `application_events` and every recorder of an applied
sighting calls it, with the Search that led there carried as provenance.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import apply_steps as aps
from application_events import mirror_application
from models import Application, Base, Job, ObservedJob, utcnow


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()


def _sighting(db, job_id="indeed:nhbb1", *, applied=True):
    row = ObservedJob(job_id=job_id, platform="indeed", external_id=job_id.split(":", 1)[1],
                      title="Continuous Improvement Engineer",
                      company="New Hampshire Ball Bearings",
                      application_status="applied" if applied else "seen",
                      applied_at=utcnow() if applied else None)
    db.add(row)
    db.commit()
    return row


def test_mirror_creates_the_canonical_application_with_search_provenance(db):
    s = _sighting(db)
    mirror_application(db, s, search_id=7)
    db.commit()

    app = db.scalar(select(Application))
    assert app is not None, "an applied sighting must yield a canonical Application"
    assert app.search_id == 7
    job = db.get(Job, app.job_key)
    assert job is not None and job.status == "applied"


def test_mirror_is_idempotent_and_keeps_first_provenance(db):
    s = _sighting(db)
    mirror_application(db, s, search_id=7)
    mirror_application(db, s, search_id=99)
    db.commit()
    apps = db.scalars(select(Application)).all()
    assert len(apps) == 1
    assert apps[0].search_id == 7          # first recorded provenance wins, never overwritten


def test_record_outcome_writes_both_halves(db):
    """The live seam: a SUBMITTED step through `_record_outcome` lands in observed_jobs AND
    applications — the exact wire that was missing."""
    from routers.session_control import _record_outcome

    step = aps.ApplyStep(job_id="indeed:nhbb1", title="Continuous Improvement Engineer",
                         company="New Hampshire Ball Bearings")
    step.terminal = aps.SUBMITTED
    step.terminal_detail = "confirmed by indeed_apply_submitted"

    out = _record_outcome(db, step, ats_url="https://smartapply.indeed.com/x", search_id=7)

    assert out["recorded"] is True and out["status"] == "applied"
    row = db.get(ObservedJob, "indeed:nhbb1")
    assert row.application_status == "applied" and row.applied_at is not None
    app = db.scalar(select(Application))
    assert app is not None and app.search_id == 7


def test_a_linkedin_submission_is_filed_under_linkedin(db):
    """BOTH ENGINES WRITE, and the platform comes from the job_id's own prefix.

    Operator, 2026-08-17: *"make sure both indeed and linkedin have access to it and are writing
    to."* The read half was proven live that day — three LinkedIn cards matched Indeed
    applications across three different ATSs — but the WRITE half had never fired from LinkedIn,
    because nothing has been submitted from LinkedIn yet. Measured on the real table that morning:
    24 applied rows, every one of them `indeed:`. So this path was correct by inspection and
    unpinned by any test, which is exactly the combination this log keeps paying for.

    `_record_outcome`'s own comment calls filing a LinkedIn application under Indeed "the kind of
    wrong that survives forever" — an applied row is what every later drive trusts to skip a job,
    so a mis-filed platform silently poisons the answer on both engines.
    """
    from routers.session_control import _record_outcome

    step = aps.ApplyStep(job_id="linkedin:4451068100", title="Clinical Reporting Analyst",
                         company="Charles River Community Health")
    step.terminal = aps.SUBMITTED
    step.platform = "paylocity"          # the ATS it was actually submitted through
    out = _record_outcome(db, step, ats_url="https://recruiting.paylocity.com/x/Apply/9", search_id=3)

    assert out["recorded"] is True and out["status"] == "applied"
    row = db.get(ObservedJob, "linkedin:4451068100")
    assert row.platform == "linkedin", "the engine that found it comes from the job_id prefix"
    assert row.application_platform == "paylocity", "the ATS it was submitted through is kept too"
    assert row.application_status == "applied" and row.applied_at is not None
    assert db.scalar(select(Application)) is not None


def test_a_linkedin_application_is_found_by_the_applied_check(db):
    """The round trip that makes the two engines one memory: written from LinkedIn, found by a
    check made from Indeed. `applied_index` never filters by platform — an application is an
    application whichever board led to it — and this pins that so a future narrowing has to
    break a test to happen."""
    import applied_index
    from routers.session_control import _record_outcome

    step = aps.ApplyStep(job_id="linkedin:4451068100", title="Clinical Reporting Analyst",
                         company="Charles River Community Health")
    step.terminal = aps.SUBMITTED
    _record_outcome(db, step, search_id=3)

    # The same job met again as an INDEED card: different id, same employer and role.
    verdict = applied_index.check(db, job_id="indeed:890102b652e9bb25",
                                  title="Clinical Reporting Analyst",
                                  company="Charles River Community Health")
    assert verdict.worth_asking, "a LinkedIn application must be visible to an Indeed-side check"
    assert verdict.job_id == "linkedin:4451068100"
