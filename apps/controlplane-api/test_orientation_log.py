"""The orientation corpus — one row per distinct situation, and the operator's answer on it."""

from __future__ import annotations

import orientation_log as ol
import pytest


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Isolate the FILE, then lift the under-test guard for this module only.

    `record()` refuses to write while pytest is running, because the rest of the suite drives the
    observer against fakes and those verdicts would land in the operator's real training corpus.
    These tests are the exception that must still write, so they opt in explicitly — the guard
    stays unconditional everywhere it is not deliberately removed."""
    monkeypatch.setattr(ol, "_path", lambda: tmp_path / "orientation_corpus.jsonl")
    monkeypatch.setattr(ol, "ALLOW_TEST_WRITES", True)


def _verdict(state="appvault_job_posting", url="https://x.careerswithus.com/job/a", mismatch=None):
    return {"url": url, "state": state, "platform": "appvault", "kind": "job_posting",
            "confidence": "medium", "mismatch": mismatch,
            "witnesses": [{"source": "signpost", "claim": "appvault", "weight": 1.0}],
            "plan": [{"id": "press_apply"}, {"id": "reorient"}]}


def test_a_verdict_is_recorded_with_its_features_and_its_label():
    row = ol.record(23, _verdict(), step_job_id="linkedin:1", rung="account")
    assert row["state"] == "appvault_job_posting" and row["platform"] == "appvault"
    assert row["witnesses"][0]["source"] == "signpost"      # the features, as fused
    assert row["plan"] == ["press_apply", "reorient"]
    assert row["outcome"] == ""                              # unlabelled until the operator acts


def test_staring_at_one_page_does_not_become_a_thousand_rows():
    """THE DEDUPE IS THE POINT. _orient_now fires on every poll; a parked tab left open would
    otherwise teach a model that whatever we stare at longest is the truth."""
    assert ol.record(23, _verdict()) is not None
    for _ in range(50):
        assert ol.record(23, _verdict()) is None
    assert ol.stats()["rows"] == 1


def test_the_same_page_reaching_a_new_state_is_new_knowledge():
    ol.record(23, _verdict(state="appvault_job_posting"))
    assert ol.record(23, _verdict(state="appvault_account_gate")) is not None
    assert ol.stats()["distinct_situations"] == 2


def test_coming_back_to_a_situation_after_leaving_it_records_the_return():
    """Only the LAST verdict suppresses: a transition away and back is exactly what a sequencing
    model needs to see."""
    ol.record(23, _verdict(state="a"))
    ol.record(23, _verdict(state="b"))
    assert ol.record(23, _verdict(state="a")) is not None
    assert ol.stats()["rows"] == 3


def test_sessions_do_not_suppress_each_other():
    ol.record(23, _verdict())
    assert ol.record(24, _verdict()) is not None


def test_taking_the_offered_action_labels_the_row_confirmed():
    ol.record(23, _verdict())
    row = ol.resolve(23, action_id="press_apply", agreed=True)
    assert row["outcome"] == ol.CONFIRMED and row["operator_action"] == "press_apply"


def test_overriding_the_verdict_is_the_most_valuable_row():
    """A labelled mistake is the training signal — the teacher's correction at the observer's
    altitude. It must be recorded as a correction, not lost as a no-op."""
    ol.record(23, _verdict())
    row = ol.resolve(23, action_id="something_else", agreed=False)
    assert row["outcome"] == ol.CORRECTED
    assert ol.stats()["corrected"] == 1


def test_a_verdict_with_no_state_is_not_a_row():
    assert ol.record(23, {"url": "https://x/", "state": ""}) is None
    assert ol.record(23, None) is None


def test_stats_counts_distinct_situations_not_volume():
    """The number to watch, same as the capture corpus: re-reading a known page adds nothing."""
    ol.record(23, _verdict(state="a"))
    for _ in range(10):
        ol.record(23, _verdict(state="a"))
    ol.record(23, _verdict(state="b", mismatch={"rung": "account"}))
    s = ol.stats()
    assert s["distinct_situations"] == 2
    assert s["mismatches"] == 1
