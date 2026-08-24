"""Tests for prediction-before-escalation — no hand-up is ever information-free.

The problem this fixes: `decide()` used to escalate as `intent="observe", confidence=0.0` with no
hypothesis at all. So on the hardest turns — precisely the ones the teacher gets paid for — the
local system made no prediction, and `shadow_agreement` had nothing to score. The teacher looked
like it was doing everything because, on the record, it was: the student never took the exam.

Now every hand-up carries a real proposal at an honest confidence, which turns one escalation into
four separate answers: did we name the state, pick the right field, pick the right verb, or only
fail to ground it?

The two guards that matter are `test_a_prediction_never_invents_an_answer_value` and
`test_prediction_confidence_is_always_below_the_acting_floor` — a prediction must never become a
bid to act, and must never fill in the one thing the human owns.
"""

from __future__ import annotations

import pytest

from interaction.contract import Intent
from interaction.decision import (
    DECISION_CONFIDENCE_THRESHOLD,
    ESCALATION_AXES,
    Bundle,
    Decision,
)

from controller.decide import decide, local_prediction


class NoPrograms:
    def get(self, task, state):
        return None


def bundle(*, state="workday_questions", unanswered=(), identities=(), **over):
    kw = dict(task="apply", goal_text="", done=False, url="https://x/y", route="/y",
              state=state, is_branch=False, human_required=False, ats="workday",
              unanswered=tuple(unanswered), ax_identities=tuple(identities))
    kw.update(over)
    return Bundle(**kw)


# --- the prediction itself ----------------------------------------------------------
@pytest.mark.parametrize("kind,expected", [
    ("react_select", Intent.SELECT_OPTION.value),
    ("select", Intent.SELECT_OPTION.value),
    ("checkbox_group", Intent.CHECK_GROUP.value),
    ("radio_group", Intent.CHECK_GROUP.value),
    ("input", Intent.SET_TEXT.value),
    ("textarea", Intent.SET_TEXT.value),
])
def test_the_verb_is_guessed_from_the_widget_shape_the_scanner_reports(kind, expected):
    """The kind vocabulary is taken from `protocols.SCAN_REQUIRED_JS` itself, so a guess here is
    a guess about a shape that really occurs rather than an invented one."""
    intent, params, _, _, _ = local_prediction(
        bundle(unanswered=({"field": "sponsorship", "kind": kind},)))
    assert intent == expected
    assert params == {"field": "sponsorship"}


def test_an_unfamiliar_shape_predicts_describe_not_a_blind_attempt():
    intent, _, _, why, _ = local_prediction(
        bundle(unanswered=({"field": "signature", "kind": "canvas"},)))
    assert intent == Intent.DESCRIBE.value
    assert "describe it before touching it" in why


def test_a_complete_form_predicts_the_advance_control_that_is_actually_present():
    intent, params, _, _, _ = local_prediction(
        bundle(identities=("button|Save and Continue", "link|Back")))
    assert intent == Intent.CLICK.value
    assert params == {"control": "Save and Continue"}


def test_it_never_predicts_submit():
    """A proposal is a thing a teacher can approve, so the guess must never name the one
    irreversible action — not even marked escalate."""
    intent, params, _, _, _ = local_prediction(
        bundle(identities=("button|Submit application", "button|Submit")))
    assert intent != Intent.SUBMIT.value
    assert params.get("control") != "Submit"


def test_no_field_and_no_advance_control_predicts_observe_honestly():
    intent, _, confidence, why, _ = local_prediction(bundle(identities=("heading|Thanks",)))
    assert intent == Intent.OBSERVE.value
    assert confidence == 0.0
    assert "no local guess is available" in why


def test_a_prediction_never_invents_an_answer_value():
    """The `answer` axis belongs to `resolve_answer` and past that to the human. A policy that
    fills in application answers from form shape is the llama3.2 failure mode (0/4, invented
    answers against a prompt forbidding it) built into the system on purpose."""
    for kind in ("react_select", "input", "radio_group", "canvas"):
        _, params, *_ = local_prediction(
            bundle(unanswered=({"field": "sponsorship_required", "kind": kind},)))
        assert set(params) <= {"field", "control"}, params
        assert "value" not in params and "values" not in params


def test_a_prediction_cites_its_receipts():
    *_, evidence = local_prediction(bundle(unanswered=({"field": "phone", "kind": "input"},)))
    assert "unanswered[0].field" in evidence and "unanswered[0].kind" in evidence


# --- how decide() uses it -----------------------------------------------------------
def test_no_program_hands_up_WITH_a_prediction():
    d = decide(bundle(unanswered=({"field": "phone", "kind": "input"},)), programs=NoPrograms())
    assert d.escalate
    assert d.escalation_axis == "no_program"
    assert d.intent == Intent.SET_TEXT.value and d.params == {"field": "phone"}
    assert "local guess" in d.rationale


def test_an_unknown_state_still_predicts_from_form_shape():
    """We cannot name the page, but the form is still readable — and 'right field, right verb,
    wrong state' is a completely different lesson from 'no idea at all'."""
    d = decide(bundle(state=None, unanswered=({"field": "email", "kind": "input"},)),
               programs=NoPrograms())
    assert d.escalate and d.escalation_axis == "unknown_state"
    assert d.intent == Intent.SET_TEXT.value


@pytest.mark.parametrize("kw,axis", [
    ({"human_required": True}, "human_required"),
    ({"is_branch": True}, "branch"),
    ({"done": True}, "task_complete"),
])
def test_stop_states_hand_up_EMPTY_and_that_is_correct(kw, axis):
    """On a sign-in wall, an off-spine page, or a finished task, a 'best guess at the next action'
    is not a prediction — it is a suggestion to do something the agent must never do."""
    d = decide(bundle(unanswered=({"field": "password", "kind": "input"},), **kw),
               programs=NoPrograms())
    assert d.escalate and d.escalation_axis == axis
    assert d.intent == Intent.OBSERVE.value and d.params == {}


def test_a_declining_model_hands_up_with_the_local_guess():
    d = decide(bundle(unanswered=({"field": "phone", "kind": "input"},)),
               programs=NoPrograms(), model=lambda b: None)
    assert d.escalation_axis == "model_declined"
    assert d.intent == Intent.SET_TEXT.value


def test_a_shaky_model_keeps_its_OWN_proposal_not_the_shape_guess():
    """The model's guess beats a form-shape guess, so it is what the teacher gets to score."""
    weak = Decision(intent=Intent.SELECT_OPTION.value, params={"field": "visa"}, confidence=0.4,
                    rung="model", rationale="looks like a dropdown", evidence=("state",))
    d = decide(bundle(unanswered=({"field": "phone", "kind": "input"},)),
               programs=NoPrograms(), model=lambda b: weak)
    assert d.escalate and d.escalation_axis == "low_confidence"
    assert d.intent == Intent.SELECT_OPTION.value and d.params == {"field": "visa"}
    assert d.evidence == ("state",)


# --- the phase rail (the 2026-08-22 click↔observe finding) --------------------------
def test_an_observe_phase_predicts_observe_even_beside_a_clickable_advance():
    """The teacher's verb on a consuming rung is `observe` by construction — the same page whose
    shape guess would click. 47 of 119 shadow disagreements were exactly this turn."""
    for phase in ("verify_identity", "classify", "account"):
        intent, params, _, why, evidence = local_prediction(
            bundle(state="indeed_job_posting", ats="indeed_quick_apply", phase=phase,
                   identities=("button|Continue", "link|Apply now")))
        assert intent == Intent.OBSERVE.value and params == {}
        assert "consuming look" in why
        assert "phase" in evidence


def test_an_enter_phase_reaches_the_apply_control_the_advance_lexicon_cannot():
    """"Apply now" is invisible to `_ADVANCE_CONTROLS` — 12 of the reverse-direction misses were
    the shape guess answering `observe` while the teacher clicked the posting's own Apply."""
    intent, params, *_ = local_prediction(
        bundle(state="company_site_job_posting", ats="company_site", phase="enter_apply",
               identities=("link|Apply now", "link|Apply now Help", "button|Save")))
    assert intent == Intent.CLICK.value
    assert params == {"control": "Apply now"}     # rendered label; the Help link is refused


def test_an_enter_phase_with_no_visible_control_still_names_the_verb():
    """Loose agreement scores intent + field; a verb-only click is an honest, scoreable guess."""
    intent, params, _, why, _ = local_prediction(
        bundle(phase="open_pane", identities=("heading|Search results",)))
    assert intent == Intent.CLICK.value and params == {}
    assert "no entering control is visible" in why


def test_a_submit_phase_gets_no_rail_and_never_the_gate():
    """`submit` is in neither phase set on purpose: the one irreversible control is never
    proposed, so a submit-turn disagreement is an honest, permanent one."""
    intent, params, *_ = local_prediction(
        bundle(phase="submit", identities=("button|Submit application",)))
    assert intent == Intent.OBSERVE.value and params == {}


def test_the_phase_rail_beats_orientation_in_the_handup():
    """Orientation always proposes the Apply click — exactly wrong mid-look. A wired `orient`
    must not override the rail on a phase-claimed turn."""
    from controller.orientation import predict as orient_predict
    d = decide(bundle(state=None, phase="verify_identity",
                      url="https://jobs.example.com/posting/1",
                      identities=("link|Apply now",)),
               programs=NoPrograms(), orient=orient_predict)
    assert d.escalate
    assert d.intent == Intent.OBSERVE.value and d.params == {}


def test_a_broken_apply_matcher_is_LOUD_not_a_quiet_downgrade(monkeypatch):
    """Only the lazy IMPORT is guarded. If the matcher itself raises, the rail must not swallow
    it into a bare-verb click: every enter-phase prediction would silently lose its control name,
    agreement would sag, and nothing would ever fail."""
    import controller.orientation as orientation

    def exploding(_identities):
        raise TypeError("matcher changed shape under us")

    monkeypatch.setattr(orientation, "apply_control", exploding)
    with pytest.raises(TypeError):
        local_prediction(bundle(phase="enter_apply", identities=("link|Apply now",)))


def test_no_phase_changes_nothing():
    """The rail is inert until a ladder claims the turn — every phase-less caller (the loop,
    teach sessions, all journaled history) predicts exactly as before."""
    with_none = local_prediction(bundle(identities=("button|Continue",), phase=None))
    assert with_none[0] == Intent.CLICK.value and with_none[1] == {"control": "Continue"}
    unmapped = local_prediction(bundle(identities=("button|Continue",), phase="workday_questions"))
    assert unmapped == with_none


# --- the calibration guard ----------------------------------------------------------
def test_prediction_confidence_is_always_below_the_acting_floor():
    """A prediction is a thing to be SCORED, never a bid to act. If a guess could ever clear the
    acting floor, the escalation path would become a way to act unreviewed."""
    cases = [
        bundle(unanswered=({"field": "phone", "kind": "input"},)),
        bundle(unanswered=({"field": "x", "kind": "mystery"},)),
        bundle(identities=("button|Continue",)),
        bundle(identities=("heading|Nothing here",)),
        bundle(phase="verify_identity", identities=("link|Apply now",)),
        bundle(phase="enter_apply", identities=("link|Apply now",)),
    ]
    for b in cases:
        d = decide(b, programs=NoPrograms())
        assert d.escalate
        assert d.confidence < DECISION_CONFIDENCE_THRESHOLD, d


def test_every_axis_decide_emits_is_in_the_closed_vocabulary():
    seen = {
        decide(bundle(), programs=NoPrograms()).escalation_axis,
        decide(bundle(state=None), programs=NoPrograms()).escalation_axis,
        decide(bundle(human_required=True), programs=NoPrograms()).escalation_axis,
        decide(bundle(is_branch=True), programs=NoPrograms()).escalation_axis,
        decide(bundle(done=True), programs=NoPrograms()).escalation_axis,
        decide(bundle(), programs=NoPrograms(), model=lambda b: None).escalation_axis,
    }
    assert seen <= set(ESCALATION_AXES)
    assert "none" not in seen        # every one of these really is an escalation


def test_an_acting_decision_carries_no_escalation_axis():
    from controller.programs import IntentProgram
    program = IntentProgram(task="apply", state="workday_questions", guard_fields=("phone",),
                            expected_exit=("workday_review",),
                            steps=({"intent": "set_text", "params": {"field": "phone"}},))

    class Store:
        def get(self, task, state):
            return program

    d = decide(bundle(unanswered=({"field": "phone", "kind": "input"},)), programs=Store())
    assert not d.escalate and d.escalation_axis == ""


def test_the_proposed_control_is_the_label_the_page_actually_renders():
    """"Continue" is a substring of "Save and Continue". Matching on the lexicon and then
    proposing the LEXICON hands the teacher a control name the page does not have — a proposal
    nobody can approve as written."""
    _, params, *_ = local_prediction(bundle(identities=("button|Save and Continue",)))
    assert params == {"control": "Save and Continue"}


def test_the_most_specific_advance_control_wins():
    _, params, *_ = local_prediction(
        bundle(identities=("button|Continue", "button|Save and Continue")))
    assert params == {"control": "Save and Continue"}
