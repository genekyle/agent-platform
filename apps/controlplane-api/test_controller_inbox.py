"""Tests for the teacher inbox — the seat that lets Claude be a rung instead of a script author.

The three properties that matter, each with a test named for it:

  * `test_a_timeout_reproduces_the_old_behaviour` — nobody listening must never be WORSE than
    nobody asking. A park that times out has to land exactly where the drive landed before the
    inbox existed, or this feature is a new way to hang.
  * `test_a_timeout_escalates_it_never_approves` — and it must never fail OPEN. A transport that
    approves on silence inverts the gate the whole plan installs.
  * `test_an_instruction_without_reasoning_is_refused` — §10. The teacher's WHY is the training
    signal; `cli_reviewer` hardcoding "operator correction" is how every human correction used to
    teach reasoning-blind.
"""

from __future__ import annotations

import pytest

from interaction.decision import Bundle, Decision
from interaction.lesson import Lesson, LessonKind, accept

from controller import inbox as inbox_mod
from controller import teach

WHY = "this control stages the value and only commits on the footer button"


def a_decision(intent="click", **params):
    return Decision(intent=intent, params=params, confidence=0.4, rung="model",
                    rationale="looks like the advance control", expected_next=("next_state",))


def a_bundle():
    return Bundle(task="apply", goal_text="", done=False, url="https://x/y", route="/y",
                  state="workday_questions", is_branch=False, human_required=False, ats="workday")


def instant_clock():
    """A clock that always reports the deadline as passed — so a timeout test costs no wall time."""
    ticks = iter([0.0, 10_000.0, 20_000.0, 30_000.0, 40_000.0])
    return lambda: next(ticks, 90_000.0)


# --- ask / answer round trip ---------------------------------------------------------
def test_a_question_appears_in_pending_and_carries_the_whole_package():
    """The teacher must be able to answer WITHOUT reading the drive's memory — the request is the
    escalation package, not a pointer to one."""
    req = inbox_mod.ask(kind=inbox_mod.RequestKind.INSTRUCT.value, task="apply",
                        state="workday_questions", url="https://wd/x", mode="orange",
                        maturity="unseen", authority_reason="no record of this transition",
                        bundle_prompt="# BUNDLE\nstate: workday_questions",
                        belief_prompt="state: workday_questions",
                        prediction={"intent": "select_option", "params": {"field": "visa"}},
                        reach_gaps=["widget:unknown@signature"])
    rows = {r["id"]: r for r in inbox_mod.pending()}
    assert req.id in rows
    got = rows[req.id]
    assert got["mode"] == "orange" and got["maturity"] == "unseen"
    assert got["prediction"]["intent"] == "select_option"
    assert got["reach_gaps"] == ["widget:unknown@signature"]
    assert got["bundle_prompt"].startswith("# BUNDLE")


def test_answering_closes_the_question():
    req = inbox_mod.ask(kind=inbox_mod.RequestKind.REVIEW.value)
    inbox_mod.respond(req.id, action=inbox_mod.ResponseAction.APPROVE.value)
    assert req.id not in {r["id"] for r in inbox_mod.pending()}
    assert inbox_mod.get(req.id)["status"] == "answered"


def test_pending_is_oldest_first():
    """A parked drive is waiting on the OLDEST question; newest-first would starve it."""
    before = {r["id"] for r in inbox_mod.pending(limit=0)}
    first = inbox_mod.ask(kind=inbox_mod.RequestKind.REVIEW.value, task="first")
    second = inbox_mod.ask(kind=inbox_mod.RequestKind.REVIEW.value, task="second")
    fresh = [r["id"] for r in inbox_mod.pending(limit=0) if r["id"] not in before]
    assert fresh.index(first.id) < fresh.index(second.id)


def test_a_response_never_rewrites_the_question():
    """Append-and-overlay, like `runtime/handoff.py`'s resolve markers — the original request must
    survive so an escalation can be audited after it was answered."""
    req = inbox_mod.ask(kind=inbox_mod.RequestKind.INSTRUCT.value,
                        prediction={"intent": "set_text", "params": {"field": "phone"}})
    inbox_mod.respond(req.id, action=inbox_mod.ResponseAction.INSTRUCT.value,
                      decision=a_decision("select_option", field="visa"), rationale=WHY)
    row = inbox_mod.get(req.id)
    assert row["prediction"]["intent"] == "set_text"        # the local guess, still there
    assert row["response"]["decision"]["intent"] == "select_option"


# --- validation ----------------------------------------------------------------------
def test_an_instruction_without_reasoning_is_refused():
    req = inbox_mod.ask(kind=inbox_mod.RequestKind.INSTRUCT.value)
    with pytest.raises(inbox_mod.InboxError, match="real reasoning"):
        inbox_mod.respond(req.id, action=inbox_mod.ResponseAction.INSTRUCT.value,
                          decision=a_decision(), rationale="operator correction")


def test_an_instruction_without_a_decision_is_refused():
    req = inbox_mod.ask(kind=inbox_mod.RequestKind.INSTRUCT.value)
    with pytest.raises(inbox_mod.InboxError, match="must carry the decision"):
        inbox_mod.respond(req.id, action=inbox_mod.ResponseAction.INSTRUCT.value, rationale=WHY)


def test_approve_and_abort_need_no_decision():
    for action in (inbox_mod.ResponseAction.APPROVE.value, inbox_mod.ResponseAction.ABORT.value):
        req = inbox_mod.ask(kind=inbox_mod.RequestKind.REVIEW.value)
        assert inbox_mod.respond(req.id, action=action)["action"] == action


def test_unknown_request_and_double_answer_are_loud():
    """A silently-dropped answer parks the drive until it times out with nobody knowing why."""
    with pytest.raises(inbox_mod.InboxError, match="unknown request"):
        inbox_mod.respond("tr_nope", action=inbox_mod.ResponseAction.APPROVE.value)
    req = inbox_mod.ask(kind=inbox_mod.RequestKind.REVIEW.value)
    inbox_mod.respond(req.id, action=inbox_mod.ResponseAction.APPROVE.value)
    with pytest.raises(inbox_mod.InboxError, match="already answered"):
        inbox_mod.respond(req.id, action=inbox_mod.ResponseAction.APPROVE.value)


def test_an_unknown_action_is_refused():
    req = inbox_mod.ask(kind=inbox_mod.RequestKind.REVIEW.value)
    with pytest.raises(inbox_mod.InboxError, match="unknown action"):
        inbox_mod.respond(req.id, action="wing_it")


# --- takeover tickets ----------------------------------------------------------------
def test_a_takeover_always_carries_stop_conditions():
    """A RED handoff without an exit condition means 'finish the application' — the teacher
    replacing the driver rather than borrowing the wheel."""
    req = inbox_mod.ask(kind=inbox_mod.RequestKind.TAKEOVER.value)
    assert req.stop_conditions == list(inbox_mod.DEFAULT_STOP_CONDITIONS)
    assert any("irreversible" in c for c in req.stop_conditions)
    assert any("recipe already knows" in c for c in req.stop_conditions)


def test_stop_conditions_can_be_narrowed_but_not_dropped():
    req = inbox_mod.ask(kind=inbox_mod.RequestKind.TAKEOVER.value,
                        stop_conditions=["the questionnaire is complete"])
    assert req.stop_conditions == ["the questionnaire is complete"]


def test_a_review_carries_no_takeover_conditions():
    assert inbox_mod.ask(kind=inbox_mod.RequestKind.REVIEW.value).stop_conditions == []


# --- park / resume -------------------------------------------------------------------
def test_wait_returns_the_response_once_it_lands():
    req = inbox_mod.ask(kind=inbox_mod.RequestKind.INSTRUCT.value)

    def answer_on_first_poll(_seconds):
        inbox_mod.respond(req.id, action=inbox_mod.ResponseAction.INSTRUCT.value,
                          decision=a_decision("select_option", field="visa"), rationale=WHY)

    got = inbox_mod.wait_for(req.id, timeout=60, sleep=answer_on_first_poll,
                             now=lambda: 0.0)
    assert got["action"] == "instruct"
    assert inbox_mod.decision_from_response(got).params == {"field": "visa"}


def test_a_timeout_reproduces_the_old_behaviour():
    """Returns None and marks the request expired — the caller's contract is that this lands
    exactly where the drive landed before the inbox existed."""
    req = inbox_mod.ask(kind=inbox_mod.RequestKind.INSTRUCT.value)
    assert inbox_mod.wait_for(req.id, timeout=1, sleep=lambda s: None,
                              now=instant_clock()) is None
    assert inbox_mod.get(req.id)["status"] == "expired"


def test_a_teacher_decision_is_restamped_as_the_teacher():
    """The corpus must show who really decided, exactly as `teach.correct()` does."""
    req = inbox_mod.ask(kind=inbox_mod.RequestKind.INSTRUCT.value)
    payload = inbox_mod.respond(req.id, action=inbox_mod.ResponseAction.INSTRUCT.value,
                                decision=a_decision("set_text", field="phone"), rationale=WHY)
    got = inbox_mod.decision_from_response(payload)
    assert got.rung == "teacher" and got.rationale == WHY


def test_a_lesson_rides_along_with_the_answer():
    req = inbox_mod.ask(kind=inbox_mod.RequestKind.INSTRUCT.value)
    taught = accept(Lesson(kind=LessonKind.FIELD_ALIAS.value, scope="platform:workday",
                           subject="visa", payload={"alias": "requires_sponsorship"},
                           rationale=WHY), verified=True)
    payload = inbox_mod.respond(req.id, action=inbox_mod.ResponseAction.INSTRUCT.value,
                                decision=a_decision("select_option", field="visa"),
                                rationale=WHY, lesson=taught)
    back = inbox_mod.lesson_from_response(payload)
    assert back.subject == "visa" and back.scope == "platform:workday" and back.accepted


# --- the Reviewer adapter ------------------------------------------------------------
def test_the_reviewer_serves_yellow_over_the_same_transport():
    review_holder = {}

    def answer(_seconds):
        req_id = inbox_mod.pending()[0]["id"]
        review_holder["id"] = req_id
        inbox_mod.respond(req_id, action=inbox_mod.ResponseAction.CORRECT.value,
                          decision=a_decision("select_option", field="visa"), rationale=WHY)

    reviewer = inbox_mod.inbox_reviewer(timeout=60, sleep=answer, now=lambda: 0.0)
    review = reviewer(a_bundle(), a_decision())
    assert review.action == teach.ReviewAction.CORRECT
    assert review.correction.params == {"field": "visa"}
    assert review.correction.rung == "teacher"


def test_a_timeout_escalates_it_never_approves():
    """Failing OPEN here would invert the gate the whole plan installs."""
    seen = []
    reviewer = inbox_mod.inbox_reviewer(timeout=1, sleep=lambda s: None, now=instant_clock(),
                                        on_timeout=lambda r, b, d: seen.append(r.id))
    review = reviewer(a_bundle(), a_decision())
    assert review.action == teach.ReviewAction.ESCALATE
    assert seen, "the caller must be told the park expired, not left guessing"


def test_the_reviewer_passes_the_local_proposal_through_for_scoring():
    """Even at YELLOW the local prediction is on the record, so agreement is measurable on the
    turns a teacher was involved — which is where it matters most."""
    captured = {}

    def answer(_seconds):
        row = inbox_mod.pending()[0]
        captured.update(row["prediction"])
        inbox_mod.respond(row["id"], action=inbox_mod.ResponseAction.APPROVE.value)

    reviewer = inbox_mod.inbox_reviewer(timeout=60, sleep=answer, now=lambda: 0.0)
    reviewer(a_bundle(), a_decision("click", control="Continue"))
    assert captured["intent"] == "click" and captured["params"] == {"control": "Continue"}
    assert captured["rung"] == "model"


def test_the_adapter_does_not_edit_teach():
    """`controller/teach.py` is claimed by the operator's in-flight work. The `Reviewer` protocol
    is injectable precisely so a new transport is an addition, never a change."""
    import inspect
    assert "inbox" not in inspect.getsource(teach)
