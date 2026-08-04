"""The transition-corpus review surface: list → narrate → correct, both sides kept (§10).

The panel's one promise: the operator sees WHAT THE SYSTEM WAS THINKING AT THE TIME —
belief (the witnesses' own rationale), the prediction declared before the act, the act, what
changed, and how the verdict was reached — and a correction never erases the original verdict.
"""

from __future__ import annotations

import main
import pytest
import step_runner as sr
from fastapi.testclient import TestClient
from routers import transitions as tr

client = TestClient(main.app)


def _seed_row(session_key, *, verdict=sr.CONFIRMED, claimed="ok", belief=None):
    before = sr.Observation(ts="t0", ok=True, url="https://a.test/1")
    before.belief = belief if belief is not None else {
        "state": "job_posting", "uncertainty": {"state": 0.12},
        "rationale": "both witnesses say job_posting"}
    after = sr.Observation(ts="t1", ok=True, url="https://a.test/1?vjk=x")
    return sr.record_transition(
        session_id=session_key, rung_id="open_pane",
        action={"rung": "open_pane", "job_id": "indeed:x", "initiator": "operator"},
        expect=sr.expectation_for("open_pane", external_id="x"),
        before=before, after=after, changes=sr.diff(before, after),
        verdict=verdict, evidence="the window carries vjk=x", claimed=claimed)


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "_transitions_dir", lambda: tmp_path)
    return tmp_path


# --- narration: the row in the order the system lived it ---------------------------------------

def test_the_narration_shows_the_thinking_not_just_the_flag(corpus):
    _seed_row(41)
    r = client.get("/api/transitions/41").json()
    n = r["rows"][0]["narration"]
    # believed → predicted → did → saw → settled, each in English, the rationale verbatim.
    assert "job_posting" in n["believed"] and "both witnesses" in n["believed"]
    assert "vjk=x" in n["expected"]
    assert "open_pane" in n["did"] and "operator" in n["did"]
    assert "URL moved" in n["changed"]
    assert "CONFIRMED" in n["headline"] and "vjk=x" in n["headline"]


def test_a_beliefless_look_is_named_not_hidden(corpus):
    # collect=False rows (credential posture) and witness-less looks must say so — an empty
    # field would read as "nothing to see" when the truth is "we chose not to look closer".
    _seed_row(42, belief={})
    n = client.get("/api/transitions/42").json()["rows"][0]["narration"]
    assert "no belief" in n["believed"] or "identity-only" in n["believed"]


def test_a_demotion_reads_as_claim_versus_world(corpus):
    _seed_row(43, verdict=sr.MISMATCH, claimed="ok")
    n = client.get("/api/transitions/43").json()["rows"][0]["narration"]
    assert "claimed ok" in n["headline"] and "DISAGREED" in n["headline"]


def test_a_mismatch_under_a_failed_claim_reads_as_agreement(corpus):
    # Both the action and the world said it did not land — that is agreement, and the first
    # real corpus render showed the old wording calling it disagreement (2026-08-04).
    _seed_row(49, verdict=sr.MISMATCH, claimed="failed")
    n = client.get("/api/transitions/49").json()["rows"][0]["narration"]
    assert "agrees nothing landed" in n["headline"]
    assert "DISAGREED" not in n["headline"]


# --- corrections: both sides kept ---------------------------------------------------------------

def test_a_correction_keeps_both_sides_and_survives_reread(corpus):
    _seed_row(44)
    row = client.get("/api/transitions/44").json()["rows"][0]
    out = client.post("/api/transitions/44/correct", json={
        "index": row["index"], "ts": row["ts"], "verdict": sr.MISMATCH,
        "note": "the vjk in the URL is a different job's id — evidence names the wrong window"})
    assert out.status_code == 200
    stored = client.get("/api/transitions/44").json()["rows"][0]
    assert stored["verdict"] == sr.CONFIRMED                     # the original is untouched
    assert stored["teacher_correction"]["verdict"] == sr.MISMATCH
    assert stored["teacher_correction"]["original_verdict"] == sr.CONFIRMED
    assert "different job" in stored["teacher_correction"]["note"]


def test_an_unexplained_override_is_refused(corpus):
    _seed_row(45)
    row = client.get("/api/transitions/45").json()["rows"][0]
    out = client.post("/api/transitions/45/correct",
                      json={"index": row["index"], "ts": row["ts"],
                            "verdict": sr.MISMATCH, "note": "   "})
    assert out.status_code == 422
    assert "teaches nothing" in out.json()["detail"]


def test_a_stale_review_screen_cannot_annotate_the_wrong_row(corpus):
    _seed_row(46)
    out = client.post("/api/transitions/46/correct",
                      json={"index": 0, "ts": "2020-01-01T00:00:00", "verdict": sr.MISMATCH,
                            "note": "citing evidence"})
    assert out.status_code == 409
    assert "moved" in out.json()["detail"]


def test_correcting_a_missing_corpus_or_row_is_a_404(corpus):
    assert client.post("/api/transitions/none/correct",
                       json={"index": 0, "note": "x"}).status_code == 404
    _seed_row(47)
    assert client.post("/api/transitions/47/correct",
                       json={"index": 99, "note": "x"}).status_code == 404


# --- health: is the corpus becoming trainable? --------------------------------------------------

def test_health_counts_what_the_trainers_will_ask_about(corpus):
    _seed_row(48)
    _seed_row(48, verdict=sr.MISMATCH, claimed="ok")
    h = client.get("/api/transitions/48").json()["health"]
    assert h["rows"] == 2 and h["demotions"] == 1
    assert h["verdicts"] == {sr.CONFIRMED: 1, sr.MISMATCH: 1}
    assert h["claim_agreement"] == 0.5
    assert "job_posting" in h["states"]
    # An unmodeled row changes the share the inspection step exists to shrink.
    before, after = sr.Observation(ts="t", ok=True), sr.Observation(ts="t", ok=True)
    sr.record_transition(session_id=48, rung_id="apply_fill", action={"action": "apply_fill"},
                         expect=sr.Expectation(kind="unmodeled"), before=before, after=after,
                         changes=None, verdict=sr.UNOBSERVED, evidence="no measured postcondition",
                         claimed="ok")
    h = client.get("/api/transitions/48").json()["health"]
    assert h["expectation_kinds"]["unmodeled"] == 1 and h["unmodeled_share"] == round(1 / 3, 3)


def test_the_landing_view_lists_every_corpus_with_its_shape(corpus):
    _seed_row("account-acme-workday")
    _seed_row(50, verdict=sr.MISMATCH, claimed="ok")
    r = client.get("/api/transitions").json()
    keys = {c["key"]: c for c in r["corpora"]}
    assert "account-acme-workday" in keys and "50" in keys
    assert keys["50"]["verdicts"] == {sr.MISMATCH: 1}
    assert r["health"]["rows"] == 2


# --- train-as-we-go: the corpus feeds the planner's edge table, confidence-gated ---------------

def _seed_belief_row(session_key, *, b_state, a_state, b_unc, a_unc, rung="open_pane"):
    before = sr.Observation(ts="t0", ok=True, url="https://a.test/1")
    before.belief = {"state": b_state, "uncertainty": {"state": b_unc}, "rationale": "r"}
    after = sr.Observation(ts="t1", ok=True, url="https://a.test/2")
    after.belief = {"state": a_state, "uncertainty": {"state": a_unc}, "rationale": "r"}
    sr.record_transition(session_id=session_key, rung_id=rung, action={"rung": rung},
                         expect=sr.Expectation(kind="content_changed"), before=before,
                         after=after, changes=sr.diff(before, after), verdict=sr.CONFIRMED,
                         evidence="e", claimed="ok")


def test_training_refuses_a_corpus_the_witnesses_never_confidently_saw(corpus):
    # The first real corpus's exact shape: beliefs present, every uncertainty 1.0 (the AX scan
    # ran dry). Training the planner's edges on that would teach junk roads — the gate says so.
    for _ in range(3):
        _seed_belief_row(60, b_state="search_results", a_state="job_posting",
                         b_unc=1.0, a_unc=1.0)
    out = client.post("/api/transitions/train").json()
    assert out["ok"] is False and out["reason"] == "insufficient_confident_rows"
    assert out["skipped"]["uncertain"] == 3
    assert "drive with working observations" in out["detail"]


def test_training_fits_the_edge_table_from_confident_rows(corpus, tmp_path, monkeypatch):
    from routers import transitions as tr
    monkeypatch.setattr(tr, "_artifacts_root", lambda: tmp_path)
    for _ in range(4):
        _seed_belief_row(61, b_state="search_results", a_state="job_posting",
                         b_unc=0.2, a_unc=0.3)
    _seed_belief_row(61, b_state="search_results", a_state="job_posting",
                     b_unc=0.9, a_unc=0.2)          # the one uncertain row stays out
    out = client.post("/api/transitions/train").json()
    assert out["ok"] is True
    assert out["eligible"] == 4 and out["skipped"]["uncertain"] == 1
    assert out["metrics"]["distinct_transitions"] == 1
    # The model landed on disk where every other trainer writes.
    assert list(tmp_path.glob("models/*state_transition*/model.json"))
