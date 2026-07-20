"""Tests for the controller contract: Bundle/Decision shapes, the FROZEN prompt
serialization, the selector guard, and the decision-journal round-trip."""

from __future__ import annotations

import os
import tempfile

import pytest

from interaction.decision import (
    DECISION_CONFIDENCE_THRESHOLD,
    DECISION_SCHEMA_VERSION,
    Bundle,
    Decision,
    bundle_digest,
    bundle_to_prompt,
    is_real_rationale,
    looks_like_selector,
    sanitize_unanswered,
)
from interaction import decision_journal


def _bundle(**over) -> Bundle:
    base = dict(
        task="indeed_apply",
        goal_text="Apply to this job",
        done=False,
        url="https://smartapply.indeed.com/questions/abc123",
        route="smartapply.indeed.com/questions/{id}",
        state="indeed_apply_questions",
        is_branch=False,
        human_required=False,
        ats="indeed_quick_apply",
        fingerprint=None,
        branch_note="",
        recipe_step=2,
        next_action="autofill + Continue",
        expected_next=("indeed_apply_contact_info", "indeed_apply_review"),
        lessons="",
        unanswered=(
            {"field": "Work authorization", "kind": "radio_group",
             "required_via": "required-attr", "answered": False, "valid": True},
        ),
        recent=({"intent": "click", "field": None, "outcome": "ok"},),
    )
    base.update(over)
    return Bundle(**base)


# --- shape / version ---------------------------------------------------------
def test_version_is_v1():
    assert DECISION_SCHEMA_VERSION == "v1"
    assert DECISION_CONFIDENCE_THRESHOLD == 0.75


def test_bundle_and_decision_are_frozen():
    b = _bundle()
    d = Decision(intent="click", params={"control": "Continue"}, confidence=1.0,
                 rung="recipe", rationale="advance the spine")
    with pytest.raises(Exception):
        b.state = "x"          # frozen
    with pytest.raises(Exception):
        d.intent = "submit"    # frozen


def test_decision_acts_property():
    assert Decision("click", {}, 1.0, "recipe", "r").acts is True
    assert Decision("click", {}, 0.3, "model", "r", escalate=True).acts is False


# --- the FROZEN serialization (snapshot) -------------------------------------
def test_bundle_to_prompt_is_stable():
    """This exact string is the feature contract. If this test fails you changed what every
    distilled policy sees — bump DECISION_SCHEMA_VERSION deliberately, don't just re-snapshot."""
    got = bundle_to_prompt(_bundle())
    expected = (
        "# GOAL\n"
        "task: indeed_apply\n"
        "goal: Apply to this job\n"
        "done: no\n"
        "\n"
        "# STATE\n"
        "ats: indeed_quick_apply\n"
        "state: indeed_apply_questions\n"
        "url: https://smartapply.indeed.com/questions/abc123\n"
        "branch: no\n"
        "human_required: no\n"
        "\n"
        "# RECIPE\n"
        "step: 2\n"
        "next_action: autofill + Continue\n"
        "expected_next: indeed_apply_contact_info, indeed_apply_review\n"
        "\n"
        "# UNANSWERED (1)\n"
        "  - Work authorization [radio_group] required-attr answered=no valid=yes\n"
        "\n"
        "# RECENT\n"
        "  - click -> ok"
    )
    assert got == expected


def test_prompt_shows_branch_note_and_lessons_when_present():
    p = bundle_to_prompt(_bundle(is_branch=True, human_required=True,
                                 branch_note="reCAPTCHA — human clears it",
                                 lessons="workday: use My Last Application"))
    assert "branch: yes" in p
    assert "human_required: yes" in p
    assert "branch_note: reCAPTCHA — human clears it" in p
    assert "# LESSONS\nworkday: use My Last Application" in p


def test_empty_unanswered_and_recent_render_readably():
    p = bundle_to_prompt(_bundle(unanswered=(), recent=()))
    assert "# UNANSWERED (0)\n  (none" in p
    assert "# RECENT\n  (no prior steps this run)" in p


def test_digest_is_stable_and_pii_free():
    d1 = bundle_digest(_bundle())
    d2 = bundle_digest(_bundle())
    assert d1 == d2 and len(d1) == 64
    # A different unanswered field-set changes the digest; a different goal prose does not.
    assert bundle_digest(_bundle(goal_text="different prose")) == d1
    assert bundle_digest(_bundle(state="indeed_apply_review")) != d1


# --- selector guard (invariant #10) ------------------------------------------
@pytest.mark.parametrize("val", [
    "#first_name", ".jobsearch-Foo", "[data-automation-id=email]",
    "div:nth-child(2)", "//*[@id='x']", "node 4821", "backend_node_id",
])
def test_looks_like_selector_catches_addressing(val):
    assert looks_like_selector(val) is True


@pytest.mark.parametrize("val", [
    "Work authorization", "Mobile", "Bachelor's Degree", "Continue", "Yes", 42, None,
])
def test_looks_like_selector_passes_semantic_names(val):
    assert looks_like_selector(val) is False


def test_sanitize_unanswered_drops_selectors_and_previews():
    raw = [{
        "field": "Email", "selector": "#email", "kind": "text",
        "required_via": "required-attr", "value_read_at": "[class*=singleValue]",
        "answered": False, "valid": True, "value_preview": "secret@x.com",
    }]
    clean = sanitize_unanswered(raw)
    assert clean == ({"field": "Email", "kind": "text",
                      "required_via": "required-attr", "answered": False, "valid": True},)
    assert "selector" not in clean[0] and "value_preview" not in clean[0]
    assert "value_read_at" not in clean[0]


# --- the decision journal ----------------------------------------------------
def test_decision_journal_round_trips_with_a_join_key(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", tmp)
        d = Decision(intent="click", params={"control": "Continue"}, confidence=1.0,
                     rung="recipe", rationale="advance",
                     expected_next=("indeed_apply_review",))
        rec = decision_journal.record_for(d, _bundle(fingerprint="abc"),
                                          outcome="ok", landed_state="indeed_apply_review",
                                          verified=True)
        saved = decision_journal.log_decision(rec)
        assert saved is not None and saved.ts     # ts stamped at log time
        rows = decision_journal.read_rows()
        assert len(rows) == 1
        row = rows[0]
        assert row["intent"] == "click"
        assert row["rung"] == "recipe"
        assert row["outcome"] == "ok"
        assert row["verified"] is True
        assert row["route"] == "smartapply.indeed.com/questions/{id}"
        assert row["fingerprint"] == "abc"
        assert len(row["bundle_digest"]) == 64


def test_journal_refuses_a_row_with_no_join_key(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", tmp)
        d = Decision(intent="click", params={}, confidence=1.0, rung="recipe", rationale="r")
        rec = decision_journal.record_for(d, _bundle(route="", url=""))
        assert decision_journal.log_decision(rec) is None       # spine rule
        assert decision_journal.read_rows() == []


def test_journal_redacts_sensitive_params_and_drops_selectors(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", tmp)
        d = Decision(intent="set_text",
                     params={"field": "password", "value": "hunter2", "selector": "#pw"},
                     confidence=1.0, rung="teacher", rationale="login")
        decision_journal.log_decision(decision_journal.record_for(d, _bundle()))
        row = decision_journal.read_rows()[0]
        assert row["params"]["value"] == "[redacted:7]"     # value hidden by field name
        assert "selector" not in row["params"]              # addressing dropped
        assert row["params"]["field"] == "password"


def test_summarize_reports_rung_and_verified(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", tmp)
        b = _bundle()
        decision_journal.log_decision(decision_journal.record_for(
            Decision("click", {}, 1.0, "recipe", "r", expected_next=("s",)),
            b, outcome="ok", verified=True))
        decision_journal.log_decision(decision_journal.record_for(
            Decision("submit", {}, 0.4, "model", "r", escalate=True), b))
        s = decision_journal.summarize()
        assert s["corpus_size"] == 2
        assert s["verified_rate"] == 1.0            # 1 verifiable, 1 verified
        assert s["escalation_rate"] == 0.5          # 1 of 2 escalated
        rungs = {r["rung"]: r["count"] for r in s["by_rung"]}
        assert rungs == {"recipe": 1, "model": 1}


# --- §10, the Open Brain: reasoning is captured on both sides and its coverage is measured ------
def test_is_real_rationale_rejects_stubs_and_empties():
    assert is_real_rationale("clicked Continue — every required field is now answered")
    assert not is_real_rationale("")
    assert not is_real_rationale(None)
    assert not is_real_rationale("operator correction")   # the old hardcoded stub
    assert not is_real_rationale("x")                     # too short / placeholder
    assert not is_real_rationale("   TODO   ")            # stripped + lowercased into the stub set


def test_open_brain_keeps_both_sides_of_a_correction(monkeypatch):
    # A golden correction: the backstop proposed set_text (with its own why), the teacher chose
    # select_option (with theirs). Both "why"s and both citations must survive into the row —
    # the contrast is the lesson (§10). Before proposed_rationale existed, the proposal's why was
    # dropped at record_for.
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", tmp)
        proposed = Decision("set_text", {"field": "Source", "value": "Indeed"}, 0.6, "model",
                            "Source looks like a text field", evidence=("unanswered[0].field",))
        teacher = Decision("select_option", {"field": "Source", "value": "Indeed"}, 1.0, "teacher",
                           "Source is a dropdown — set_text never commits it", evidence=("state",))
        rec = decision_journal.record_for(teacher, _bundle(fingerprint="f"), proposed=proposed,
                                          golden=True, outcome="ok", verified=True)
        row = decision_journal.log_decision(rec)
        assert row is not None
        # the acted (teacher) side
        assert row.rationale == "Source is a dropdown — set_text never commits it"
        assert row.evidence == ("state",)
        # the proposal (backstop) side — kept, not dropped
        assert row.proposed_intent == "set_text"
        assert row.proposed_rationale == "Source looks like a text field"
        assert row.proposed_evidence == ("unanswered[0].field",)


def test_summarize_measures_reasoning_coverage_on_teaching_rows(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", tmp)
        b = _bundle()
        # a well-reasoned teacher demonstration
        decision_journal.log_decision(decision_journal.record_for(
            Decision("click", {"control": "Continue"}, 1.0, "teacher",
                     "all required fields on this step are answered", expected_next=("s",)), b,
            outcome="ok", verified=True))
        # a golden correction that arrived reasoning-blank (the failure §10 makes visible)
        decision_journal.log_decision(decision_journal.record_for(
            Decision("select_option", {"field": "Source"}, 1.0, "teacher", "", expected_next=("s",)),
            b, proposed=Decision("set_text", {"field": "Source"}, 0.6, "model", "guess"),
            golden=True, outcome="ok", verified=True))
        # a recipe row is NOT a teaching row — it must not dilute the coverage metric
        decision_journal.log_decision(decision_journal.record_for(
            Decision("click", {}, 1.0, "recipe", "program replay", expected_next=("s",)), b,
            outcome="ok", verified=True))
        s = decision_journal.summarize()
        assert s["teach_row_count"] == 2                 # the two teacher/golden rows
        assert s["reasoned_rate"] == 0.5                 # one real why, one blank
        assert s["unreasoned_teach_count"] == 1


def test_every_journalled_row_is_replayable_not_just_golden_and_shadow():
    """A corpus that stores only what was DECIDED, without what it was decided FROM, is half a
    training row. Measured 2026-07-20: 4 of 45 real rows carried a snapshot, so 41 could never
    teach `Bundle -> Decision`. `replay_snapshot` is PII/selector-free and ~300 bytes."""
    from interaction.decision_journal import record_for
    plain = record_for(Decision("click", {}, 1.0, "recipe", "r"), _bundle())
    assert plain.bundle_snapshot is not None
    assert plain.bundle_snapshot["route"] == plain.route
    # still PII-free: the raw url and the lessons prose never enter the corpus
    assert "lessons" not in plain.bundle_snapshot
    assert plain.bundle_snapshot["url"] == _bundle().route     # route, not the query-bearing url
