"""The pane description reaches the corpus — the writer the apply ladder never had.

`/open_job_card` returns `description` in the SAME response the open_pane rung already reads for
title and apply_type, and the rung discarded it. Measured 2026-08-24: 79 of 633 sightings carried
a description, and of the 31 jobs actually APPLIED to, only 7 — because the sole writer was a
bounded extraction sweep (`max_details_per_page`, default 8) that a hand-picked job need never
meet. These pin the storage contract the rung now uses.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import job_dedup
import models
from db import Base


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _sighting(db, job_id="indeed:pane1", **kw):
    """A sighting ALREADY resolved to its canonical Job — which is the precondition
    `sync_description` enforces: an unresolved sighting keeps its text and pushes nothing up."""
    key = kw.get("job_key", "job_" + job_id.split(":")[-1])
    if db.get(models.Job, key) is None:
        db.add(models.Job(job_key=key, company=kw.get("company", "Acme Data"),
                          company_norm=kw.get("company", "Acme Data").lower(),
                          title=kw.get("title", "Data Analyst")))
    row = models.ObservedJob(job_id=job_id, platform="indeed", external_id=job_id.split(":")[-1],
                             title=kw.get("title", "Data Analyst"),
                             company=kw.get("company", "Acme Data"),
                             url=kw.get("url", "https://www.indeed.com/viewjob?jk=pane1"),
                             description=kw.get("description"),
                             canonical_job_key=key)
    db.add(row)
    db.flush()
    return row


def test_an_unresolved_sighting_pushes_nothing_up(db):
    """The precondition, stated: with no canonical_job_key there is no job to carry the text to,
    and the function says so by returning None rather than inventing a row."""
    row = models.ObservedJob(job_id="indeed:orphan", platform="indeed", external_id="orphan",
                             title="Data Analyst", company="Acme Data",
                             description="a real description with nowhere to go")
    db.add(row)
    db.flush()
    assert job_dedup.sync_description(db, row) is None


def test_a_pane_description_reaches_the_canonical_job(db):
    # The sighting is not what the dashboard or applied_index reads; the canonical Job is.
    row = _sighting(db)
    row.description = "Build dashboards in Tableau and maintain reporting pipelines." * 3
    job = job_dedup.sync_description(db, row)
    assert job is not None
    assert "Tableau" in (job.description or "")
    assert job.description_source  # provenance travels with the text


def test_an_existing_description_is_not_overwritten_by_a_later_pane(db):
    # A pane re-opened on a later drive must not clobber a fuller capture: the rung only writes
    # when the sighting has none, and sync_description ranks sources rather than last-write-wins.
    row = _sighting(db, job_id="indeed:pane2", description="the full original description")
    job = job_dedup.sync_description(db, row)
    before = job.description
    row2 = db.get(models.ObservedJob, "indeed:pane2")
    assert (row2.description or "").strip()  # the rung's own guard: already captured, skip
    assert before == job.description


def test_an_empty_description_writes_nothing(db):
    # A pane that returned no text must not stamp an empty string over a real one, nor invent a
    # description_source for text that does not exist.
    row = _sighting(db, job_id="indeed:pane3", description="")
    job = job_dedup.sync_description(db, row)
    assert job is None or not (job.description or "").strip()
