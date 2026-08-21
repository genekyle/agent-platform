"""Tests for the Supervisor's rung-0 classifier.

The acceptance test at the bottom is the one that matters: the taxonomy was MINED from this
repo's own logs, so it is only worth having if it actually fits the incidents it was mined from.
Each case there cites the LEARNINGS entry or journal row it came from.
"""

from __future__ import annotations

import dataclasses

import pytest

from interaction.contract import Outcome
from interaction.delta import StateDelta, compute
from interaction.supervision import (
    DIAGNOSTIC_NONE,
    PLAY_FOR_CLASS,
    SUPERVISION_SCHEMA_VERSION,
    FailureClass,
    RecoveryPlay,
    SupervisorVerdict,
    classify,
    sanitize_recovery_params,
    verdict_to_prompt,
)

MOVED = StateDelta(appeared=("button|next",), disappeared=("button|continue",))
STILL = StateDelta()


def _classify(**over):
    base = dict(outcome=Outcome.OK.value, verified=True, delta=MOVED,
                intent="click", state="indeed_apply_questions")
    return classify(**{**base, **over})


# --- the contract holds ---------------------------------------------------------------
def test_every_class_has_a_play():
    """A class with no playbook entry is a class the supervisor can name and not act on — which
    is the one thing the closed-vocabulary design exists to prevent."""
    assert set(PLAY_FOR_CLASS) == {c.value for c in FailureClass}
    assert set(PLAY_FOR_CLASS.values()) <= {p.value for p in RecoveryPlay}


def test_unknown_and_the_stop_states_escalate():
    """`UNKNOWN` must never fall through to a mechanical retry, and neither may a challenge or an
    auth wall — those are the human's, always."""
    for cls in (FailureClass.UNKNOWN, FailureClass.CHALLENGE, FailureClass.AUTH_WALL,
                FailureClass.UNRECOGNIZED_STATE):
        assert PLAY_FOR_CLASS[cls.value] == RecoveryPlay.ESCALATE.value


def test_rung_0_never_requests_vision():
    """Vision is an instrument rung 1+ reaches for, never a firehose. If rung 0 could ask, it
    would ask on every stuck turn and the cost argument for the whole cascade collapses."""
    for kwargs in ({}, {"verified": False, "delta": STILL},
                   {"state": None}, {"human_required": True},
                   {"outcome": Outcome.BLOCKED.value},
                   {"outcome": Outcome.COMMITTED_UNCONFIRMED.value, "verified": False}):
        assert _classify(**kwargs).diagnostic_request == DIAGNOSTIC_NONE


def test_every_verdict_carries_evidence_and_a_real_rationale():
    """§10 — a "why" is training signal only if it is really there."""
    from interaction.decision import is_real_rationale
    for kwargs in ({}, {"verified": False, "delta": STILL},
                   {"outcome": Outcome.NOT_FOUND.value, "verified": False},
                   {"state": None}, {"human_required": True}):
        v = _classify(**kwargs)
        assert v.evidence, f"no evidence for {v.failure_class}"
        assert is_real_rationale(v.rationale), f"stub rationale for {v.failure_class}"


def test_recovery_params_are_selector_guarded():
    assert sanitize_recovery_params({"field": "Salary", "selector": "#q1"}) == {"field": "Salary"}
    assert sanitize_recovery_params({"x": "[data-automation-id=foo]"}) == {}


def test_verdict_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        SupervisorVerdict(state_hypothesis="", expectation_delta="", stuck_signal=0.0,
                          failure_class="none", proposed_recovery="none").stuck_signal = 1.0


# --- stop-states are checked FIRST ----------------------------------------------------
def test_a_captcha_is_a_challenge_not_a_mechanical_failure():
    """Ordering matters: a challenge page also has "no control we can find", and filing it as
    CONTROL_NOT_FOUND would send the loop re-observing instead of stopping."""
    v = _classify(state="captcha", outcome=Outcome.NOT_FOUND.value, verified=False, delta=STILL)
    assert v.failure_class == FailureClass.CHALLENGE.value
    assert v.proposed_recovery == RecoveryPlay.ESCALATE.value
    assert v.stuck_signal == 1.0


def test_a_blocked_outcome_is_a_challenge():
    assert _classify(outcome=Outcome.BLOCKED.value, verified=False,
                     delta=STILL).failure_class == FailureClass.CHALLENGE.value


def test_an_unobservable_tab_is_stale_not_a_diagnosis_of_the_page():
    """`observe()` marks an untrustworthy reading as human_required with state=None. Diagnosing
    anything below that would be inventing findings about a page we could not read (07-19)."""
    v = _classify(human_required=True, state=None, verified=False, delta=STILL)
    assert v.failure_class == FailureClass.STALE_TAB.value
    assert v.proposed_recovery == RecoveryPlay.RE_RESOLVE_TAB.value


def test_a_human_required_state_is_an_auth_wall():
    v = _classify(human_required=True, state="workday_sign_in", verified=False, delta=STILL)
    assert v.failure_class == FailureClass.AUTH_WALL.value


def test_an_unregistered_page_is_named_as_such():
    v = _classify(state=None, verified=False, delta=STILL)
    assert v.failure_class == FailureClass.UNRECOGNIZED_STATE.value


# --- the nominal path -----------------------------------------------------------------
def test_a_verified_action_that_moved_the_page_is_nominal():
    v = _classify()
    assert v.nominal and v.failure_class == FailureClass.NONE.value
    assert v.stuck_signal == 0.0
    assert v.proposed_recovery == RecoveryPlay.NONE.value
    assert v.summary().startswith("ok — ")


def test_the_nominal_verdict_is_still_a_full_readable_verdict():
    """Legibility on every turn without a model — the reason the commentary pane is free."""
    v = _classify()
    assert v.state_hypothesis and v.expectation_delta and v.rationale
    assert "# LAST STEP" in verdict_to_prompt(v)


# --- the mechanical classes -----------------------------------------------------------
@pytest.mark.parametrize("outcome", [Outcome.NOT_FOUND.value, Outcome.NOT_OPENED.value,
                                     Outcome.AMBIGUOUS.value, Outcome.NO_OPTION.value])
def test_the_protocols_stale_outcomes_are_control_not_found(outcome):
    v = _classify(outcome=outcome, verified=False, delta=STILL, field_name="Salary")
    assert v.failure_class == FailureClass.CONTROL_NOT_FOUND.value
    assert v.proposed_recovery == RecoveryPlay.RE_OBSERVE.value
    assert v.recovery_params == {"field": "Salary"}


@pytest.mark.parametrize("outcome", [Outcome.NOT_STAGED.value, Outcome.NOT_COMMITTED.value])
def test_the_protocol_names_the_stage_commit_failure_itself(outcome):
    """Take the endpoint's word for it — it knows WHICH half broke, which no inference does."""
    v = _classify(outcome=outcome, verified=False, delta=STILL, intent="select_option",
                  field_name="Ethnicity")
    assert v.failure_class == FailureClass.STAGED_NOT_COMMITTED.value
    assert v.proposed_recovery == RecoveryPlay.COMMIT_WIDGET.value


def test_committed_unconfirmed_is_honest_uncertainty_not_a_failure():
    v = _classify(outcome=Outcome.COMMITTED_UNCONFIRMED.value, verified=False, delta=STILL)
    assert v.failure_class == FailureClass.UNKNOWN.value
    assert "outside" in v.rationale.lower()


def test_a_verified_action_that_moved_nothing_is_the_treadmill():
    """The 2026-07-19 Longroad incident, finally NAMED rather than merely counted."""
    v = _classify(verified=True, delta=STILL, intent="click", unanswered_count=3)
    assert v.failure_class == FailureClass.NO_PROGRESS.value
    assert v.proposed_recovery == RecoveryPlay.SETTLE_AND_RETRY.value
    assert "byte-identical" in v.expectation_delta


def test_a_field_set_that_reported_ok_but_moved_nothing_is_staged_not_committed():
    """The Ethnicity react-single-select (07-18): the field shows "Asian" while scan_required
    still calls it unanswered. Inferred here from (field intent + ok + nothing changed) for the
    endpoints that do NOT emit not_staged/not_committed."""
    v = _classify(outcome=Outcome.OK.value, verified=False, delta=STILL,
                  intent="select_option", field_name="Ethnicity")
    assert v.failure_class == FailureClass.STAGED_NOT_COMMITTED.value
    assert v.recovery_params == {"field": "Ethnicity"}


def test_a_click_on_a_complete_form_that_does_nothing_means_a_control_was_missed():
    """The lone required acknowledgment checkbox (07-18): scan_required reported 0 unanswered
    while Continue was blocked with "Choose an option to continue"."""
    v = _classify(outcome=Outcome.OK.value, verified=False, delta=STILL,
                  intent="click", unanswered_count=0)
    assert v.failure_class == FailureClass.MISSED_REQUIRED_CONTROL.value
    assert v.proposed_recovery == RecoveryPlay.RESCAN_REQUIRED.value


def test_landing_somewhere_unexpected_is_UNKNOWN_not_a_forced_class():
    """Honesty over coverage: rung 0 has no rule for this, so it hands up instead of guessing.
    This is the population rung 1 exists to refine (PLAN_supervisor §2)."""
    v = _classify(outcome=Outcome.OK.value, verified=False, delta=MOVED,
                  expected_next=("indeed_apply_review",), landed_state="indeed_apply_demographics")
    assert v.failure_class == FailureClass.UNKNOWN.value
    assert "indeed_apply_review" in v.expectation_delta
    assert "indeed_apply_demographics" in v.expectation_delta


# --- acceptance: the taxonomy must fit the incidents it was mined from -----------------
#: Each case is a REAL stuck moment, cited. (label, kwargs, expected class)
_MINED_INCIDENTS = [
    (
        "Longroad 07-19: Continue clicked 8x, ok+verified, page never moved",
        dict(outcome=Outcome.OK.value, verified=True, delta=STILL, intent="click",
             state="indeed_apply_questions", unanswered_count=2),
        FailureClass.NO_PROGRESS,
    ),
    (
        "journal x5: select_option/set_text -> not_found on indeed_apply_questions",
        dict(outcome=Outcome.NOT_FOUND.value, verified=False, delta=STILL,
             intent="select_option", field_name="sponsorship_required",
             state="indeed_apply_questions"),
        FailureClass.CONTROL_NOT_FOUND,
    ),
    (
        "Lactalis 07-18: Ethnicity react-select stages but does not commit",
        dict(outcome=Outcome.OK.value, verified=False, delta=STILL,
             intent="select_option", field_name="Ethnicity", state="indeed_apply_demographics"),
        FailureClass.STAGED_NOT_COMMITTED,
    ),
    (
        "Lactalis 07-18: lone required acknowledgment checkbox the scan never saw",
        dict(outcome=Outcome.OK.value, verified=False, delta=STILL, intent="click",
             unanswered_count=0, state="indeed_apply_questions"),
        FailureClass.MISSED_REQUIRED_CONTROL,
    ),
    (
        "07-19: a stale CDP target returns a SUCCESSFUL empty scan",
        dict(outcome=Outcome.OK.value, verified=False, delta=STILL,
             human_required=True, state=None),
        FailureClass.STALE_TAB,
    ),
    (
        "handoffs x12: not_authenticated mid-drive",
        dict(outcome=Outcome.ERROR.value, verified=False, delta=STILL,
             human_required=True, state="workday_sign_in"),
        FailureClass.AUTH_WALL,
    ),
    (
        "handoffs x6: the page is not in the state registry",
        dict(outcome=Outcome.OK.value, verified=False, delta=MOVED, state=None),
        FailureClass.UNRECOGNIZED_STATE,
    ),
    (
        "escalation_rules: a real reCAPTCHA, verified live",
        dict(outcome=Outcome.BLOCKED.value, verified=False, delta=STILL, state="captcha"),
        FailureClass.CHALLENGE,
    ),
    (
        "07-18: Greenhouse date month is a react-select; the protocol reports the failing half",
        dict(outcome=Outcome.NOT_COMMITTED.value, verified=False, delta=STILL,
             intent="set_date", field_name="Start date", state="greenhouse_apply_form"),
        FailureClass.STAGED_NOT_COMMITTED,
    ),
]


@pytest.mark.parametrize("label,kwargs,expected", _MINED_INCIDENTS,
                         ids=[c[0][:40] for c in _MINED_INCIDENTS])
def test_rung_0_classifies_the_mined_incidents(label, kwargs, expected):
    v = classify(**kwargs)
    assert v.failure_class == expected.value, f"{label}: got {v.failure_class}"


def test_rung_0_names_every_mined_incident_without_reaching_for_a_model():
    """The headline number for PLAN_supervisor §8's first falsifier: rung-0 coverage. If this
    ever needs UNKNOWN to pass, the deterministic table has stopped fitting reality."""
    unknown = [label for label, kwargs, _ in _MINED_INCIDENTS
               if classify(**kwargs).failure_class == FailureClass.UNKNOWN.value]
    assert unknown == [], f"rung 0 could not name: {unknown}"


def test_the_delta_is_what_separates_a_treadmill_from_real_progress():
    """End-to-end with a real delta rather than a hand-built one: the SAME action, outcome and
    state, distinguished only by whether the control set turned over — which is precisely what
    `progress_signature` could not see."""
    base = "https://smartapply.indeed.com/beta/indeedapply/form/questions-module/questions"
    stuck = compute(before=("button|continue",), after=("button|continue",),
                    url_before=f"{base}/1", url_after=f"{base}/1",
                    state_before="indeed_apply_questions", state_after="indeed_apply_questions")
    advanced = compute(before=("button|continue", "radio|work authorization"),
                       after=("button|continue", "textbox|why this role"),
                       url_before=f"{base}/1", url_after=f"{base}/2",
                       state_before="indeed_apply_questions", state_after="indeed_apply_questions")

    args = dict(outcome=Outcome.OK.value, verified=True, intent="click",
                state="indeed_apply_questions", unanswered_count=1)
    assert classify(delta=stuck, **args).failure_class == FailureClass.NO_PROGRESS.value
    assert classify(delta=advanced, **args).failure_class == FailureClass.NONE.value


def test_schema_version_is_pinned():
    assert SUPERVISION_SCHEMA_VERSION == "v1"


def test_landing_on_the_platforms_error_page_is_the_platforms_fault():
    """workday_error_retry is the 4th most common state in the corpus (36/356) and every
    encounter burned a human escalation — for a page whose entire content is "something went
    wrong, try again". Detected from the LANDED state, narrowly (`*_error_retry` only), and
    checked before the nominal branch: landing on an error page is not success whatever moved."""
    v = classify(outcome=Outcome.OK.value, verified=False, delta=MOVED,
                 intent="click", state="workday_my_experience",
                 expected_next=("workday_questions",), landed_state="workday_error_retry")
    assert v.failure_class == FailureClass.PLATFORM_ERROR.value
    assert v.proposed_recovery == RecoveryPlay.SETTLE_AND_RETRY.value
    assert not v.nominal
    # Even a delta that "verified" cannot launder the landing into nominal.
    v2 = classify(outcome=Outcome.OK.value, verified=True, delta=MOVED,
                  intent="click", state="workday_my_experience",
                  expected_next=("workday_error_retry",), landed_state="workday_error_retry")
    assert v2.failure_class == FailureClass.PLATFORM_ERROR.value


def test_a_form_error_state_is_not_a_platform_error():
    """The suffix is deliberately narrow: greenhouse_apply_error is a FORM with validation
    errors — our input's problem, not the platform's — and must not settle-and-retry."""
    v = classify(outcome=Outcome.OK.value, verified=False, delta=MOVED,
                 intent="click", state="greenhouse_apply_form",
                 expected_next=("greenhouse_apply_confirmation",),
                 landed_state="greenhouse_apply_error")
    assert v.failure_class != FailureClass.PLATFORM_ERROR.value
