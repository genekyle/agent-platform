"""The Gmail matcher, pinned to the applications the REAL ledger holds.

Every company below is one of the 28 live applications (dumped 2026-08-22), and the near-miss
cases are the collisions that corpus actually contains: "Boston Children's Hospital" vs "Boston
College" share a token, and a generic "credit union" phrase must not claim Metro Credit Union's
mail. The mail shapes mirror what the senders in question really send — Indeed notifies from
@indeedemail.com, a Workday tenant writes from its own myworkday.com address space.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import inbox_matcher as im
import inbox_sweep
import job_dedup as jd
import main
from db import Base, get_db
from models import Application, ApplicationEvent, AtsFlow, InboxEmail, Job

client = TestClient(main.app)

T0 = datetime(2026, 8, 10, tzinfo=timezone.utc)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    main.app.dependency_overrides[get_db] = lambda: session
    try:
        yield session
    finally:
        main.app.dependency_overrides.pop(get_db, None)
        session.close()


def _applied(db, key, company, title, *, ats=None):
    db.add(Job(job_key=key, company=company, company_norm=jd.normalize_company(company),
               title=title, source_platforms=["indeed"], status="applied"))
    db.add(Application(job_key=key, applied_at=T0, via_platform="indeed", ats=ats,
                       status="applied"))
    db.flush()


@pytest.fixture()
def corpus(db):
    """A slice of the real application ledger — the collision-bearing rows on purpose."""
    _applied(db, "job_datadog", "Datadog", "Sales Revenue Analyst - Boston", ats="indeed")
    _applied(db, "job_bilh", "Beth Israel Lahey Health", "Healthcare Data Analyst", ats="workday")
    _applied(db, "job_bch", "Boston Children's Hospital", "Analyst I, Healthcare Data",
             ats="brassring")
    _applied(db, "job_bc", "Boston College", "Business Intelligence Analyst/Developer",
             ats="cornerstone")
    _applied(db, "job_metro", "Metro Credit Union", "Financial Crimes Data Analyst")
    _applied(db, "job_gardner", "Isabella Stewart Gardner Museum",
             "Community Relations Database Analyst", ats="paylocity")
    db.commit()
    return db


def _apps(db):
    return inbox_sweep.applications_for_matching(db)


# --------------------------------------------------------------------------------------
# Sender → ATS
# --------------------------------------------------------------------------------------

def test_sender_ats_registry_and_mail_domains():
    assert im.sender_ats("bidmc@myworkday.com") == "workday"          # registry host
    assert im.sender_ats("no-reply@indeedemail.com") == "indeed_quick_apply"  # mail-only domain
    assert im.sender_ats("careers@us.greenhouse-mail.io") == "greenhouse"
    assert im.sender_ats("noreply@adp.com") == "adp"                  # hosts list only subdomains
    assert im.sender_ats("mom@gmail.com") is None


def test_sender_address_splits_reader_format():
    address, name = im.sender_address("no-reply@indeedemail.com Indeed Apply")
    assert address == "no-reply@indeedemail.com"
    assert name == "Indeed Apply"


# --------------------------------------------------------------------------------------
# Kind classification
# --------------------------------------------------------------------------------------

def test_rejection_outranks_its_polite_opener():
    # Real rejection mail opens with the CONFIRMATION family's phrasing; order must win.
    kind, strong, _ = im.classify_kind(
        "Your application to Beth Israel Lahey Health",
        "Thank you for your interest. We regret to inform you that we will not be moving forward.")
    assert (kind, strong) == ("rejection", True)


def test_unfortunately_alone_is_weak():
    kind, strong, _ = im.classify_kind("An update on your application",
                                       "Unfortunately, we have decided to go in another direction.")
    assert (kind, strong) == ("rejection", False)


# --------------------------------------------------------------------------------------
# The decision — record / review / ignore
# --------------------------------------------------------------------------------------

def _row(sender, subject, snippet="", received="2026-08-20T14:00:00Z"):
    return {"sender": sender, "subject": subject, "snippet": snippet,
            "received_at": received, "unread": True}


def test_indeed_confirmation_records_unattended(corpus):
    d = im.decide(_row("no-reply@indeedemail.com Indeed Apply",
                       "Application submitted: Sales Revenue Analyst - Boston at Datadog"),
                  _apps(corpus))
    assert d.action == im.RECORD
    assert d.kind == "confirmation"
    assert d.job_key == "job_datadog"


def test_strong_rejection_with_single_company_records(corpus):
    d = im.decide(_row("bidmc@myworkday.com Beth Israel Lahey Health",
                       "Your application to Beth Israel Lahey Health",
                       "we will not be moving forward with your candidacy"),
                  _apps(corpus))
    assert d.action == im.RECORD
    assert d.kind == "rejection"
    assert d.job_key == "job_bilh"


def test_weak_rejection_goes_to_review_prefilled(corpus):
    d = im.decide(_row("talent@gardnermuseum.org Isabella Stewart Gardner Museum",
                       "Your application — Isabella Stewart Gardner Museum",
                       "Unfortunately we have decided to go another direction."),
                  _apps(corpus))
    assert d.action == im.REVIEW
    assert d.kind == "rejection"
    assert d.job_key == "job_gardner"  # prefilled, not written


def test_interview_invite_never_auto_writes(corpus):
    # The strongest possible phrasing and a clean single match — still a human's call.
    d = im.decide(_row("recruiting@datadoghq.com Datadog",
                       "Datadog — schedule an interview",
                       "We would like to invite you to interview for Sales Revenue Analyst."),
                  _apps(corpus))
    assert d.action == im.REVIEW
    assert d.kind == "interview_invite"
    assert d.job_key == "job_datadog"


def test_boston_childrens_does_not_claim_boston_college(corpus):
    d = im.decide(_row("recruit@bostonchildrens.org Boston Children's Hospital",
                       "Thank you for applying to Boston Children's Hospital"),
                  _apps(corpus))
    assert d.action == im.RECORD
    assert d.job_key == "job_bch"
    assert [c.job_key for c in d.candidates] == ["job_bch"]  # 1/2 tokens is not a match


def test_generic_credit_union_mail_does_not_claim_metro(corpus):
    d = im.decide(_row("news@somecu.org Harborview Credit Union",
                       "Your Harborview Credit Union application",
                       "Thank you for applying to Harborview Credit Union."),
                  _apps(corpus))
    # 2 of Metro Credit Union's 3 tokens is 0.67 — below the bar, so no candidate; the
    # application language still keeps it out of the ignore bucket.
    assert d.action == im.REVIEW
    assert d.candidates == []


def test_personal_mail_is_ignored(corpus):
    d = im.decide(_row("mom@gmail.com Mom", "Dinner Sunday?", "Are you coming home this weekend?"),
                  _apps(corpus))
    assert d.action == im.IGNORE


# --------------------------------------------------------------------------------------
# The sweep — persistence, idempotency, privacy
# --------------------------------------------------------------------------------------

def test_sweep_writes_events_and_is_idempotent(corpus):
    rows = [
        _row("no-reply@indeedemail.com Indeed Apply",
             "Application submitted: Sales Revenue Analyst - Boston at Datadog"),
        _row("bidmc@myworkday.com Beth Israel Lahey Health",
             "Your application to Beth Israel Lahey Health",
             "we will not be moving forward with your candidacy"),
        _row("mom@gmail.com Mom", "Dinner Sunday?", "personal"),
    ]
    first = inbox_sweep.sweep(corpus, rows)
    assert first["ok"] and len(first["recorded"]) == 2 and first["ignored"] == 1

    events = list(corpus.scalars(select(ApplicationEvent)
                                 .where(ApplicationEvent.source == "gmail")).all())
    assert {e.kind for e in events} == {"confirmation", "rejection"}
    assert all(e.evidence.get("fingerprint") for e in events)

    # The rejection flipped the application's derived status — the outcome loop closing.
    rejected = corpus.scalar(select(Application).where(Application.job_key == "job_bilh"))
    assert rejected.status == "rejected"

    second = inbox_sweep.sweep(corpus, rows)
    assert second["skipped_known"] == 3 and not second["recorded"]
    assert len(list(corpus.scalars(select(ApplicationEvent)
                                   .where(ApplicationEvent.source == "gmail")).all())) == 2


def test_ignored_rows_keep_fingerprint_only(corpus):
    inbox_sweep.sweep(corpus, [_row("mom@gmail.com Mom", "Dinner Sunday?", "personal")])
    stub = corpus.scalar(select(InboxEmail).where(InboxEmail.status == "ignored"))
    assert stub.fingerprint
    assert stub.subject == "" and stub.from_address == "" and stub.snippet == ""


def test_confirmation_surfaces_open_flows_as_witness(corpus):
    corpus.add(AtsFlow(instance_key="datadog:1", ats_id="indeed_quick_apply",
                       job_key="job_datadog", terminal=None))
    corpus.commit()
    out = inbox_sweep.sweep(corpus, [
        _row("no-reply@indeedemail.com Indeed Apply",
             "Application submitted: Sales Revenue Analyst - Boston at Datadog")])
    assert out["recorded"][0]["flow_terminal_witness"], \
        "an open flow for the confirmed job should be named as gaining a second witness"


# --------------------------------------------------------------------------------------
# The endpoints
# --------------------------------------------------------------------------------------

def test_sweep_endpoint_with_rows(corpus):
    res = client.post("/api/career_search/inbox/sweep", json={"rows": [
        _row("no-reply@indeedemail.com Indeed Apply",
             "Application submitted: Sales Revenue Analyst - Boston at Datadog")]})
    assert res.status_code == 200 and res.json()["ok"]
    assert len(res.json()["recorded"]) == 1


def test_review_confirm_writes_the_event(corpus):
    inbox_sweep.sweep(corpus, [
        _row("recruiting@datadoghq.com Datadog", "Datadog — schedule an interview",
             "We would like to invite you to interview.")])
    pending = client.get("/api/career_search/inbox", params={"status": "needs_review"}).json()
    assert pending["has_pending"] and pending["total"] == 1
    row = pending["emails"][0]
    assert (row["kind"], row["job_key"]) == ("interview_invite", "job_datadog")

    res = client.post(f"/api/career_search/inbox/{row['id']}/resolve", json={"action": "confirm"})
    assert res.status_code == 200
    assert res.json()["application"]["status"] == "interview"
    assert res.json()["email"]["event_id"]

    # A resolved row stays resolved.
    again = client.post(f"/api/career_search/inbox/{row['id']}/resolve", json={"action": "confirm"})
    assert again.status_code == 409


def test_review_dismiss_writes_nothing(corpus):
    inbox_sweep.sweep(corpus, [
        _row("news@somecu.org Harborview Credit Union", "Your Harborview Credit Union application",
             "Thank you for applying to Harborview Credit Union.")])
    row = client.get("/api/career_search/inbox", params={"status": "needs_review"}).json()["emails"][0]
    res = client.post(f"/api/career_search/inbox/{row['id']}/resolve", json={"action": "dismiss"})
    assert res.status_code == 200 and res.json()["email"]["status"] == "dismissed"
    assert not list(corpus.scalars(select(ApplicationEvent)
                                   .where(ApplicationEvent.source == "gmail")).all())


def test_confirm_without_a_job_key_is_refused(corpus):
    inbox_sweep.sweep(corpus, [
        _row("news@somecu.org Harborview Credit Union", "Your Harborview Credit Union application",
             "Thank you for applying to Harborview Credit Union.")])
    row = client.get("/api/career_search/inbox", params={"status": "needs_review"}).json()["emails"][0]
    res = client.post(f"/api/career_search/inbox/{row['id']}/resolve",
                      json={"action": "confirm", "kind": "confirmation"})
    assert res.status_code == 422  # no job_key prefilled and none supplied — never guess
