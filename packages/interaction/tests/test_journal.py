"""The journal's invariants — chiefly the two the event log gets wrong."""

from __future__ import annotations

import json

import pytest

from interaction import journal as journal_mod
from interaction.contract import Intent, Outcome


@pytest.fixture()
def jrnl(tmp_path, monkeypatch):
    """The journal pointed at a temp corpus.

    `_default_artifacts_dir` reads the env var on every call (not at import), so setting it
    here is enough — no module reload needed.
    """
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    return journal_mod


def _lines(tmp_path):
    p = tmp_path / "cache" / "intent_journal.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def test_journal_appends_it_does_not_truncate(jrnl, tmp_path):
    """THE regression that reordered the plan.

    event_log.jsonl is a 1000-line ring buffer (read → truncate → rewrite), so a long live
    session silently loses its oldest rows. A training corpus may never do that.
    """
    for i in range(1500):
        jrnl.log_intent(intent=Intent.CLICK, outcome=Outcome.OK, field=f"f{i}")
    rows = _lines(tmp_path)
    assert len(rows) == 1500, "the journal truncated — it is a corpus, not a ring buffer"
    assert rows[0]["field"] == "f0", "the OLDEST row is the one a ring buffer eats first"


def test_journal_redacts_secrets_without_being_asked(jrnl, tmp_path):
    # Redaction is the journal's job, not the caller's — a caller that forgets leaks a
    # password into the corpus forever. PRINCIPLES §4.
    jrnl.log_intent(intent=Intent.SET_TEXT, outcome=Outcome.OK, field="password", value="hunter2")
    row = _lines(tmp_path)[0]
    assert "hunter2" not in json.dumps(row)
    assert row["value"] == "[redacted:7]"


def test_journal_records_dry_runs_as_not_executed(jrnl, tmp_path):
    # The event log emits byte-identical rows for record_only and a real drive, so the
    # corpus could not tell a rehearsal from a performance.
    jrnl.log_intent(intent=Intent.CLICK, outcome=Outcome.OK, driver="record_only", executed=False)
    jrnl.log_intent(intent=Intent.CLICK, outcome=Outcome.OK, driver="direct", executed=True)
    rows = _lines(tmp_path)
    assert [r["executed"] for r in rows] == [False, True]


def test_journal_carries_the_join_keys(jrnl, tmp_path):
    jrnl.log_intent(intent=Intent.SELECT_OPTION, outcome=Outcome.OK,
                 route="myworkday.com/job/apply", fingerprint="abc123", session_id="s1")
    row = _lines(tmp_path)[0]
    # fingerprint joins to selection_telemetry.jsonl and loop_steps.jsonl.
    assert row["fingerprint"] == "abc123"
    assert row["route"] == "myworkday.com/job/apply"
    assert row["session_id"] == "s1"


def test_journal_records_the_per_step_log(jrnl, tmp_path):
    # /widget_select already PRODUCES this trace and throws it away. It is also exactly the
    # intermediate-state vocabulary L3 lacks (popup_open, option_staged).
    steps = [{"step": "precheck"}, {"step": "open", "n_options": 5}, {"step": "select"}]
    jrnl.log_intent(intent=Intent.SELECT_OPTION, outcome=Outcome.NOT_COMMITTED, steps=steps)
    row = _lines(tmp_path)[0]
    assert [s["step"] for s in row["steps"]] == ["precheck", "open", "select"]
    assert row["outcome"] == "not_committed"   # names WHICH step broke


def test_journal_never_raises_into_the_hot_path(jrnl, monkeypatch):
    # A journal write must never break a live drive.
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(jrnl, "_path", boom)
    rec = jrnl.log_intent(intent=Intent.CLICK, outcome=Outcome.OK)
    assert rec.intent == "click"   # still returns the record for the response echo


def test_summarize_reports_the_phase1_scoreboard(jrnl):
    jrnl.log_intent(intent=Intent.SELECT_OPTION, outcome=Outcome.OK, widget_type="react_select",
                 ats="greenhouse", fingerprint="fp1")
    jrnl.log_intent(intent=Intent.SELECT_OPTION, outcome=Outcome.NO_OPTION, widget_type="react_select",
                 ats="greenhouse")
    jrnl.log_intent(intent=Intent.PROBE, outcome=Outcome.OK, ats="greenhouse")
    s = jrnl.summarize()
    assert s["corpus_size"] == 3
    assert s["verified_rate"] == round(2 / 3, 4)
    # probe_share is the metric the plan is chasing: it should FALL as protocols land.
    assert s["probe_share"] == round(1 / 3, 4)
    assert s["fingerprinted_rate"] == round(1 / 3, 4)
    assert s["by_outcome"][0]["count"] == 2
