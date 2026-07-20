"""S12b tests: the play executor, and above all the things it REFUSES to do.

The dangerous failure mode of a recovery layer is not that a play misfires — it is that a play
fires when nothing authorised it. So most of this file is about refusal: shadow mode, the
stop-states, the one-shot latch.
"""

from __future__ import annotations

import pytest

from controller import recovery
from controller.recovery import PlayResult, apply_play
from interaction.decision import Decision
from interaction.supervision import FailureClass, RecoveryPlay, SupervisorVerdict


def _verdict(cls: FailureClass, play: RecoveryPlay, **over) -> SupervisorVerdict:
    base = dict(state_hypothesis="on indeed_apply_questions", expectation_delta="x",
                stuck_signal=0.7, failure_class=cls.value, proposed_recovery=play.value,
                rationale="because", evidence=("outcome",), confidence=0.8)
    return SupervisorVerdict(**{**base, **over})


def _decision(**params) -> Decision:
    return Decision(intent="click", params=params, confidence=1.0, rung="recipe", rationale="r")


class FakeRecoveryActuator:
    def __init__(self, *, tab=True, missed=(), committed=True, boom=None):
        self.calls = []
        self._tab, self._missed, self._committed, self._boom = tab, missed, committed, boom

    def settle(self):
        self.calls.append("settle")
        if self._boom:
            raise RuntimeError(self._boom)

    def re_resolve_tab(self):
        self.calls.append("re_resolve_tab")
        return self._tab

    def rescan_required(self):
        self.calls.append("rescan_required")
        return self._missed

    def commit_widget(self, field, value):
        self.calls.append(("commit_widget", field, value))
        return self._committed


ALL = frozenset(c.value for c in FailureClass)


# --- refusal: the whole point ---------------------------------------------------------
def test_shadow_mode_is_the_default_and_runs_nothing():
    """Stage 1 (PLAN_supervisor §6). The executor exists and does nothing until an operator names
    a class that has earned promotion. `AUTONOMOUS_CLASSES` ships empty and this is the test that
    keeps it that way."""
    assert recovery.AUTONOMOUS_CLASSES == frozenset()
    act = FakeRecoveryActuator()
    res = apply_play(_verdict(FailureClass.RACE_SETTLE, RecoveryPlay.SETTLE_AND_RETRY),
                     _decision(), act)
    assert res.skipped and res.retry is False
    assert "shadow" in res.detail
    assert act.calls == []


@pytest.mark.parametrize("cls", [FailureClass.CHALLENGE, FailureClass.AUTH_WALL,
                                 FailureClass.UNRECOGNIZED_STATE, FailureClass.UNKNOWN])
def test_the_stop_states_are_refused_even_when_explicitly_enabled(cls):
    """Enabling one of these is a configuration mistake; this is what makes it harmless rather
    than dangerous. A captcha is never auto-solved — not once, not ever."""
    act = FakeRecoveryActuator()
    res = apply_play(_verdict(cls, RecoveryPlay.SETTLE_AND_RETRY), _decision(),
                     act, enabled_classes=ALL)
    assert res.skipped
    assert "human" in res.detail
    assert act.calls == []


def test_the_loop_owned_plays_get_no_second_implementation():
    """`RE_OBSERVE` / `ESCALATE` are what `unexpected.respond` already does. Two places deciding
    "re-observe or escalate" is the exact duplication unexpected.py was extracted to end."""
    act = FakeRecoveryActuator()
    for play in (RecoveryPlay.NONE, RecoveryPlay.RE_OBSERVE, RecoveryPlay.ESCALATE):
        res = apply_play(_verdict(FailureClass.CONTROL_NOT_FOUND, play), _decision(),
                         act, enabled_classes=ALL)
        assert res.skipped and "unexpected.respond" in res.detail
    assert act.calls == []


def test_only_one_recovery_attempt_per_step():
    """"Try harder" is exactly what the stale taxonomy exists to prevent. A recovery that loops is
    a treadmill with better manners."""
    act = FakeRecoveryActuator()
    res = apply_play(_verdict(FailureClass.RACE_SETTLE, RecoveryPlay.SETTLE_AND_RETRY),
                     _decision(), act, enabled_classes=ALL, already_recovered=True)
    assert res.skipped and "treadmill" in res.detail
    assert act.calls == []


def test_a_missing_actuator_is_reported_not_crashed():
    res = apply_play(_verdict(FailureClass.RACE_SETTLE, RecoveryPlay.SETTLE_AND_RETRY),
                     _decision(), None, enabled_classes=ALL)
    assert res.skipped and "no recovery actuator" in res.detail


def test_a_skipped_play_always_says_why():
    """A silently-skipped recovery is indistinguishable from one that ran and did nothing — which
    is precisely the ambiguity this plan exists to remove."""
    for kwargs in ({"enabled_classes": frozenset()}, {"enabled_classes": ALL,
                                                      "already_recovered": True}):
        res = apply_play(_verdict(FailureClass.RACE_SETTLE, RecoveryPlay.SETTLE_AND_RETRY),
                         _decision(), FakeRecoveryActuator(), **kwargs)
        assert res.skipped and res.detail


def test_a_raising_play_becomes_a_handoff_not_a_crash():
    act = FakeRecoveryActuator(boom="cdp died")
    res = apply_play(_verdict(FailureClass.RACE_SETTLE, RecoveryPlay.SETTLE_AND_RETRY),
                     _decision(), act, enabled_classes=ALL)
    assert res.attempted and res.retry is False
    assert "cdp died" in res.detail


# --- the plays themselves -------------------------------------------------------------
def test_settle_and_retry_settles_then_asks_for_a_fresh_decision():
    act = FakeRecoveryActuator()
    res = apply_play(_verdict(FailureClass.RACE_SETTLE, RecoveryPlay.SETTLE_AND_RETRY),
                     _decision(), act, enabled_classes=ALL)
    assert act.calls == ["settle"]
    assert res.attempted and res.retry is True


def test_re_resolve_tab_retries_only_if_a_fresh_tab_was_found():
    found = apply_play(_verdict(FailureClass.STALE_TAB, RecoveryPlay.RE_RESOLVE_TAB), _decision(),
                       FakeRecoveryActuator(tab=True), enabled_classes=ALL)
    assert found.retry is True
    gone = apply_play(_verdict(FailureClass.STALE_TAB, RecoveryPlay.RE_RESOLVE_TAB), _decision(),
                      FakeRecoveryActuator(tab=False), enabled_classes=ALL)
    assert gone.attempted and gone.retry is False and "gone" in gone.detail


def test_rescan_that_finds_nothing_does_NOT_retry():
    """No news is not good news: the form still scans complete and the advance still no-ops, so we
    have learned nothing and must hand up rather than click the same button again."""
    empty = apply_play(_verdict(FailureClass.MISSED_REQUIRED_CONTROL, RecoveryPlay.RESCAN_REQUIRED),
                       _decision(), FakeRecoveryActuator(missed=()), enabled_classes=ALL)
    assert empty.attempted and empty.retry is False and empty.found == ()

    found = apply_play(_verdict(FailureClass.MISSED_REQUIRED_CONTROL, RecoveryPlay.RESCAN_REQUIRED),
                       _decision(),
                       FakeRecoveryActuator(missed=({"role": "checkbox", "name": "I acknowledge"},)),
                       enabled_classes=ALL)
    assert found.retry is True and found.found[0]["name"] == "I acknowledge"


def test_commit_widget_takes_the_value_from_the_decision_that_failed():
    """The verdict names the FIELD; only the decision knows the VALUE (values are never
    journaled), so the play needs both halves."""
    act = FakeRecoveryActuator()
    res = apply_play(
        _verdict(FailureClass.STAGED_NOT_COMMITTED, RecoveryPlay.COMMIT_WIDGET,
                 recovery_params={"field": "Ethnicity"}),
        _decision(field="Ethnicity", value="Asian"), act, enabled_classes=ALL)
    assert act.calls == [("commit_widget", "Ethnicity", "Asian")]
    assert res.retry is True


def test_commit_widget_without_a_field_is_refused_not_guessed():
    act = FakeRecoveryActuator()
    res = apply_play(_verdict(FailureClass.STAGED_NOT_COMMITTED, RecoveryPlay.COMMIT_WIDGET),
                     _decision(), act, enabled_classes=ALL)
    assert res.skipped and "no field" in res.detail
    assert act.calls == []


def test_a_play_with_no_executor_says_so_rather_than_silently_passing():
    class Invented(str):
        pass
    res = apply_play(_verdict(FailureClass.NO_PROGRESS, RecoveryPlay.SETTLE_AND_RETRY,
                              proposed_recovery="teleport"),
                     _decision(), FakeRecoveryActuator(), enabled_classes=ALL)
    assert res.skipped and "not an action yet" in res.detail


# --- every play in the vocabulary is accounted for ------------------------------------
def test_every_play_is_either_loop_owned_or_has_an_executor():
    """The guard against the vocabulary growing past the pharmacy: a new RecoveryPlay member with
    no executor and no loop owner would silently do nothing."""
    act = FakeRecoveryActuator(missed=({"role": "checkbox", "name": "x"},))
    for play in RecoveryPlay:
        res = apply_play(_verdict(FailureClass.NO_PROGRESS, play,
                                  recovery_params={"field": "f"}),
                         _decision(field="f", value="v"), act, enabled_classes=ALL)
        owned = "unexpected.respond" in res.detail
        assert owned or res.attempted, f"{play.value} does nothing and nobody owns it"
