"""Output-observing tests for the swallow-by-design seams.

The lesson these compensate for (2026-08-22, the shadow session's near-miss): a recording seam
that swallows by design makes a broken CALL SITE invisible — a stray `cp` erased the shadow wire
and the full suite stayed green over it, because every existing test either unit-tested the
writer directly or spied the wire's kwargs. Neither notices the line that calls the writer
disappearing.

So every test here drives the REAL entry point (the endpoint or crank) and then asserts the
seam's OUTPUT — a journaled row, a written file, a DB row — through the reader the product uses.
The swallowing itself is correct and stays: failing to take an observation about ourselves must
never cost the operator the step they asked for. These tests are the compensation the swallow
requires.

Deliberately a NEW file: the shared fake seam is imported from `test_session_control` (the same
pattern `test_cockpit_reach` uses) rather than adding tests there, so this audit cannot collide
with concurrent work inside that file.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import apply_steps as aps
import main
import step_runner as sr
from db import Base, get_db
from interaction import decision_journal
from interaction import journal as intent_journal
from routers import session_control as sc

from test_session_control import (  # the shared harness — one fake seam, not two
    SEARCH_URL, _install, _tabs, _teardown, _with_queue,
)

client = TestClient(main.app)


# --------------------------------------------------------------------------------------
# /apply_step — three recorders fire on the crank's tail, none previously observed
# --------------------------------------------------------------------------------------

def _drive_one_open_pane_step(monkeypatch):
    """One real crank through the shared harness — the same drive the operator's press makes."""
    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL + "&vjk=a1"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/open_job_card": {"ok": True, "title": "Compliance Reporting Analyst",
                            "apply_type": "indeed_apply"}},
        blackboard=_with_queue(("indeed:a1", "Compliance Reporting Analyst", "MFS")))
    try:
        r = client.post("/api/session_control/1/apply_step", json={}).json()
    finally:
        _teardown()
    assert r["last_step"]["ok"] is True, "the fixture drive itself must succeed"
    return r


def test_apply_step_lands_a_transition_row(monkeypatch):
    """Cut the `sr.record_transition(...)` call in the /apply_step tail and this goes red.

    The transition corpus is the training substrate — a green suite over a severed recorder is
    the flywheel silently starving.
    """
    before = len(sr.read_transitions(1))
    _drive_one_open_pane_step(monkeypatch)
    rows = sr.read_transitions(1)
    assert len(rows) > before, "the crank must append a transition row"
    newest = rows[-1]
    assert newest["action"]["rung"] == "open_pane"
    assert newest["verdict"], "a row without a verdict is not a training row"


def test_apply_step_lands_a_shadow_row(monkeypatch):
    """Cut the `_shadow_the_crank(...)` call and this goes red.

    This is the exact wire the stray `cp` erased: the existing bundle test calls the function
    directly and passes with the call site gone. Only a journal row proves the wire.
    """
    before = sum(1 for row in decision_journal.read_rows() if row.get("shadow"))
    _drive_one_open_pane_step(monkeypatch)
    shadows = [row for row in decision_journal.read_rows() if row.get("shadow")]
    assert len(shadows) > before, "the crank must journal what the controller would have decided"
    assert shadows[-1].get("session_id") == "1", "the row must name the session that cranked"


def test_apply_step_settles_an_orienter_prediction(monkeypatch, tmp_path):
    """Cut the `_score_the_orienter(...)` call and this goes red.

    orientation_log refuses to write under pytest by design (the corpus-pollution guard), so the
    test lifts ALLOW_TEST_WRITES and points the corpus at a temp file — the same two knobs
    test_orientation_log's own fixture uses. Structurally: WITHOUT those knobs this seam is
    unobservable from any endpoint test, which is why no such test existed.
    """
    import orientation_log as ol

    corpus = tmp_path / "orientation_corpus.jsonl"
    monkeypatch.setattr(ol, "_path", lambda: corpus)
    monkeypatch.setattr(ol, "ALLOW_TEST_WRITES", True)

    # The orienter can only predict on a CLASSIFIED step (`step.platform` is None until the
    # classify rung answers it, and an unclassified before-state has no recipe expectations), so
    # the fixture pre-classifies — mirroring a step past classify, which is where every real
    # prediction happens.
    bb = _with_queue(("indeed:a1", "Compliance Reporting Analyst", "MFS"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    q.steps[0].platform = "indeed"
    bb.world["apply_queue"] = q.as_dict()
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL + "&vjk=a1"),
              # The orienter names the state from the SCAN's target_url — the /list_tabs URL
              # alone leaves the observation blank-urled and the state unknown.
              "/ax_scan": {"ok": True, "target_url": SEARCH_URL + "&vjk=a1", "candidates": []},
              "/auth_state": {"ok": True, "logged_in": True},
              "/open_job_card": {"ok": True, "title": "Compliance Reporting Analyst",
                                 "apply_type": "indeed_apply"}},
             blackboard=bb)
    try:
        client.post("/api/session_control/1/apply_step", json={})
    finally:
        _teardown()
    assert corpus.exists(), "the crank must settle the orienter's prediction into the corpus"
    assert "prediction" in corpus.read_text()


# --------------------------------------------------------------------------------------
# /close_out — the drive-end inbox sweep must be reported, even (especially) when blocked
# --------------------------------------------------------------------------------------

def test_close_out_reports_the_inbox_sweep(monkeypatch):
    """Cut the `inbox_sweep.sweep_live` hook line and this goes red.

    In tests the capture server is pinned to the discard port (conftest), so the sweep is always
    BLOCKED here — which is exactly the assertable contract: the close-out account must carry the
    sweep's answer either way, because a silently-dead crank looks identical to an empty mailbox.
    """
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=_with_queue(("indeed:a1", "Compliance Reporting Analyst", "MFS")))
    try:
        r = client.post("/api/session_control/1/close_out",
                        json={"keep_work": True, "reason": "seam audit"}).json()
    finally:
        _teardown()
    assert "inbox_sweep" in r, "the close-out account must carry the sweep's answer"
    assert r["inbox_sweep"]["ok"] is False
    assert r["inbox_sweep"]["blocked"], "a blocked sweep must say why, never sit silent"


# --------------------------------------------------------------------------------------
# /api/errands/fetch_login_code — the OBSERVE row is double-swallowed and was never asserted
# --------------------------------------------------------------------------------------

@pytest.fixture()
def errand_api(monkeypatch, tmp_path):
    """The errand endpoint with the capture server faked at its one seam — the same wiring
    test_gmail_errand's fixture uses, rebuilt here so this file stays collision-free."""
    import errand_log
    from routers import errands as errands_router

    monkeypatch.setattr(errand_log, "_path", lambda: tmp_path / "errands.jsonl")

    async def fake_capture_post(path, payload, timeout=30.0):
        assert path == "/read_inbox"
        return {"ok": True, "signed_in": True, "list_found": True, "row_count": 1,
                "url": "https://mail.google.com/mail/u/0/", "read_at": "2026-08-22T12:00:00Z",
                "rows": [{"sender": "no-reply@indeed.com Indeed",
                          "subject": "123456 is your Indeed sign-in code",
                          "snippet": "Enter this code to sign in.",
                          "received_at": "2026-08-22T11:59:30Z", "unread": True}]}

    monkeypatch.setattr(errands_router, "_capture_post", fake_capture_post)
    return client


def test_fetch_login_code_journals_an_observe_row(errand_api):
    """Cut the `_journal(...)` call in the errand route and this goes red.

    The row is double-swallowed (`_journal`'s own except-pass inside `log_intent`'s), so nothing
    but an output assertion can notice the wire going missing. The redaction contract rides
    along: the journal must know a code was found HERE and must never know the code.
    """
    before = len(intent_journal.read_rows())
    r = errand_api.post("/api/errands/fetch_login_code",
                        json={"requested_by": "indeed_jobs", "reason": "seam audit"}).json()
    assert r["ok"] is True and r["code"] == "123456"

    rows = intent_journal.read_rows()
    assert len(rows) > before, "the errand must journal its observation"
    row = rows[-1]
    assert row["intent"] == "observe"
    assert row["field"] == "login_code"
    assert "123456" not in str(row), "the journal knows a code was read, never the code"


# --------------------------------------------------------------------------------------
# _record_verification_fact — the characteristic write that had never once succeeded in tests
# --------------------------------------------------------------------------------------

@pytest.fixture()
def real_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_verification_fact_lands_and_feeds_the_sender_hints(real_db):
    """The measured-sender loop, closed: the verify seam's write must produce the row that
    `gmail_senders.senders_for` prefers over every static column.

    NOT driven through /apply_account — the shared harness's fake DB has no `query()`, so the
    endpoint path swallows an AttributeError on every existing apply_account test (this audit's
    finding; the fix belongs to that harness's owners). This test pins the write→consume contract
    against a real session so the seam has, for the first time, a test in which it actually
    writes. The endpoint-path assertion is listed as blocked in LEARNINGS.
    """
    import gmail_senders
    from models import AtsCharacteristic

    sc._record_verification_fact(real_db, ats="workable", url="https://x.workable.com/j/1",
                                 key="verification_sender", value="workablemail.com",
                                 evidence="code mail matched from this sender (seam audit)")
    sc._record_verification_fact(real_db, ats="workable", url="https://x.workable.com/j/1",
                                 key="verification_sender", value="workablemail.com",
                                 evidence="code mail matched from this sender (seam audit)")
    real_db.commit()

    row = real_db.scalar(select(AtsCharacteristic).where(
        AtsCharacteristic.key == "verification_sender"))
    assert row is not None, "the verify seam's measurement must persist"
    assert row.value == "workablemail.com"
    assert (row.observations or 0) >= 2, "a repeat observation increments, never duplicates"

    hints = gmail_senders.senders_for("workable", db=real_db)
    assert hints[0] == "workablemail.com", \
        "a measured sender must outrank the static columns in the errand's hint order"


# --------------------------------------------------------------------------------------
# The spend ledger — telemetry that must never break the call, so only a row can prove it
# --------------------------------------------------------------------------------------

def test_train_after_label_produces_the_transition_table_artifact():
    """The train-on-label background crank, observed by its PRODUCT for the first time.

    Existing tests spy that the task gets SCHEDULED; all three of its stages swallow, so a broken
    stage inside the task is invisible to them. This seeds confident rows through the real corpus
    writer and asserts stage 1's artifact (the fitted transition table) lands on disk. Stages 2-3
    (witness promotion, program recompile) remain output-unobserved through this path — noted in
    LEARNINGS; recompile is at least covered writer-direct elsewhere.
    """
    from routers import transitions as tr
    from test_transitions_review import _seed_belief_row  # the real corpus writer, right shape

    models_dir = tr._artifacts_root() / "models"
    before = set(models_dir.iterdir()) if models_dir.exists() else set()

    for _ in range(3):
        _seed_belief_row(991, b_state="search_results", a_state="job_posting",
                         b_unc=0.1, a_unc=0.1)
    tr.train_after_label()

    after = set(models_dir.iterdir()) if models_dir.exists() else set()
    new_dirs = after - before
    assert new_dirs, "the label crank must persist a fitted transition table"
    assert any((d / "model.json").exists() for d in new_dirs)


def test_every_request_lands_in_the_activity_ring():
    """The api-access middleware swallows by contract; the Session Activity feed reads its ring.
    One real request through the app must be readable back — cut the middleware's record call
    and this goes red."""
    import api_access

    client.get("/api/career_search/event_vocabulary")
    rows = api_access.recent(limit=50)
    assert any(r.get("path") == "/api/career_search/event_vocabulary" for r in rows), \
        "a served request must appear in the activity ring the cockpit feed reads"


def test_usage_ledger_row_lands_where_the_summary_reads():
    """`record_usage` swallows everything by contract ("never raise into the caller's hot
    path") — and it is the SPEND ledger, where a severed wire renders as free-looking spend
    against a hard $5/week cap. Writer→reader roundtrip through the module's own path; the file
    lands under `observer_artifacts_dir`, which conftest already redirects for the whole suite."""
    import anthropic_usage as au

    au.record_usage(model="claude-haiku-4-5-20251001", input_tokens=100, output_tokens=20,
                    purpose="seam_audit")
    summary = au.summarize(recent_limit=10)
    assert any(r.get("purpose") == "seam_audit" for r in summary["recent"]), \
        "a recorded call must be readable back through the summary the operator sees"
    assert summary["totals"]["calls"] >= 1
