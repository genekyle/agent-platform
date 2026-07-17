"""Offline tests for TeachSession — the §9 teaching surface (teacher decides, Haiku shadows).

Fake actuator + a tmp journal dir (INTERACTION_ARTIFACTS_DIR) so nothing touches the real corpus.
Proves: propose() surfaces the escalate cue + the backstop shadow without acting; commit() acts,
verifies, journals a PAIRED row (teacher = what happened, shadow = proposed_*) that
shadow_agreement reads; a correction is golden + a disagreement; SUBMIT is held, never acted.
"""

from __future__ import annotations

import pytest

from controller.bundle import build_bundle
from controller.loop import ActOutcome
from controller.metrics import shadow_agreement
from controller.teach_session import TeachSession
from interaction import decision_journal
from interaction.decision import Decision

_QUESTIONS = "https://smartapply.indeed.com/questions/x"


class FakeActuator:
    def __init__(self, bundle, outcome):
        self._bundle = bundle
        self._outcome = outcome
        self.acted = []

    def observe(self):
        return self._bundle

    def act(self, decision):
        self.acted.append(decision)
        return self._outcome


def _bundle():
    return build_bundle("indeed_apply", _QUESTIONS, "",
                        scan=[{"field": "years_experience", "selector": "#i42", "kind": "text"}])


def _model(decision):
    def _call(bundle):
        return decision
    return _call


def _dec(intent, **params):
    return Decision(intent=intent, params=params, confidence=1.0, rung="teacher",
                    rationale="t", expected_next=())


@pytest.fixture
def tmp_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    return tmp_path


# --- propose ---------------------------------------------------------------------------
def test_propose_escalates_to_teacher_with_no_program_no_backstop():
    b = _bundle()
    assert not b.human_required and not b.done          # a plain apply state, teacher's to demonstrate
    step = TeachSession(FakeActuator(b, ActOutcome("ok"))).propose()
    assert step.decision.escalate is True and step.decision.rung == "teacher"
    assert step.shadow is None                          # escalate-shadow suppressed (no real backstop)
    assert step.needs_review is True


def test_propose_surfaces_the_haiku_shadow_when_backstop_on():
    b = _bundle()
    haiku = _dec("set_text", field="years_experience", value="5")
    step = TeachSession(FakeActuator(b, ActOutcome("ok")), shadow_model=_model(haiku)).propose()
    assert step.shadow is not None and step.shadow.intent == "set_text"
    assert step.shadow.params.get("field") == "years_experience"


# --- commit: the paired row + agreement ------------------------------------------------
def test_commit_acts_and_journals_a_paired_shadow_row(tmp_journal):
    b = _bundle()
    haiku = _dec("set_text", field="years_experience", value="5")
    act = FakeActuator(b, ActOutcome("ok", landed_state=b.state))
    sess = TeachSession(act, shadow_model=_model(haiku), session_id="t1")
    sess.propose()

    # The teacher agrees with the backstop this turn.
    res = sess.commit(_dec("set_text", field="years_experience", value="5"))
    assert res.held is False and res.outcome == "ok" and res.agreed is True
    assert act.acted and act.acted[0].intent == "set_text"     # it DID act the teacher's decision

    rows = decision_journal.read_rows()
    assert len(rows) == 1
    assert rows[0]["proposed_intent"] == "set_text" and rows[0]["shadow"] is True
    assert shadow_agreement(rows)["agreement"] == 1.0          # teacher == shadow


def test_commit_correction_is_golden_and_a_disagreement(tmp_journal):
    b = _bundle()
    haiku = _dec("set_text", field="wrong_field", value="x")   # the backstop's bad guess
    act = FakeActuator(b, ActOutcome("ok", landed_state=b.state))
    sess = TeachSession(act, shadow_model=_model(haiku), session_id="t2")
    step = sess.propose()

    # The teacher corrects to a different intent/field — a golden row by construction.
    fix = _dec("select_option", field="years_experience", value="5")
    res = sess.commit(fix, golden_over=step.shadow)
    assert res.golden is True and res.agreed is False

    rows = decision_journal.read_rows()
    assert rows[0]["golden"] is True
    assert rows[0]["intent"] == "select_option" and rows[0]["proposed_intent"] == "set_text"
    rep = shadow_agreement(rows)
    assert rep["disagree"] == 1 and rep["by_category"].get("wrong_intent") == 1


# --- the SUBMIT gate -------------------------------------------------------------------
def test_commit_submit_is_held_never_acted(tmp_journal):
    b = _bundle()
    act = FakeActuator(b, ActOutcome("ok"))
    sess = TeachSession(act, session_id="t3")
    sess.propose()
    res = sess.commit(_dec("submit"))
    assert res.held is True
    assert act.acted == []                                     # never drove the Submit


def test_commit_before_propose_raises():
    sess = TeachSession(FakeActuator(_bundle(), ActOutcome("ok")))
    with pytest.raises(RuntimeError):
        sess.commit(_dec("set_text", field="x", value="y"))
