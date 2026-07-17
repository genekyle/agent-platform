"""M4 tests: propose-approve teaching (the DAgger gate). Recipe acts free; model/teacher decisions
are proposed; a correction journals a golden {proposed, corrected, outcome} row on the controller's
own state; approve/escalate/abort paths; the CLI reviewer."""

from __future__ import annotations

from controller import loop as loop_mod
from controller.loop import ActOutcome, run_controller
from controller.programs import IntentProgram
from controller.teach import (
    Review,
    ReviewAction,
    abort,
    approve,
    cli_reviewer,
    correct,
    scripted_reviewer,
)
from interaction import decision_journal
from interaction.decision import Bundle, Decision


def _bundle(state, fields=(), *, done=False, expected=("next_state",)) -> Bundle:
    return Bundle(task="workday_apply", goal_text="apply", done=done,
                  url="https://acme.wd1.myworkdayjobs.com/x", route="acme.wd1.myworkdayjobs.com/x",
                  state=state, is_branch=False, human_required=False, ats="workday",
                  expected_next=tuple(expected),
                  unanswered=tuple({"field": f, "kind": "text", "required_via": "required-attr",
                                    "answered": False, "valid": True} for f in fields))


class DictStore:
    def __init__(self, progs): self.progs = progs
    def get(self, task, state): return self.progs.get((task, state))


class OneStepSim:
    """Proposes once at a novel state (model rung), then done."""
    def __init__(self):
        self.acted, self.done = [], False
    def observe(self):
        if self.done:
            return _bundle("next_state", [], done=True)
        return _bundle("workday_my_information", ["Source"], expected=("next_state",))
    def act(self, d):
        self.acted.append(d)
        self.done = True
        return ActOutcome("ok", landed_state="next_state")


def _model(decision):
    return lambda bundle: decision


# --- gate policy: which rungs propose ----------------------------------------
def test_recipe_acts_free_even_with_a_reviewer(monkeypatch, tmp_path):
    """A rung-0 decision is a verified program — the reviewer must never see it."""
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    store = DictStore({("workday_apply", "workday_my_information"): IntentProgram(
        task="workday_apply", state="workday_my_information", guard_fields=("Source",),
        steps=({"intent": "set_text", "params": {"field": "Source"}},), expected_exit=("next_state",))})
    seen = []
    reviewer = lambda b, d: (seen.append(d) or approve())  # noqa: E731
    sim = OneStepSim()
    run_controller(sim, programs=store, reviewer=reviewer, session_id="r")
    assert seen == []                       # recipe never proposed
    assert sim.acted and sim.acted[0].rung == "recipe"


def test_model_decision_is_proposed_and_approved(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    proposal = Decision("set_text", {"field": "Source"}, 0.9, "model", "fill source",
                        expected_next=("next_state",))
    sim = OneStepSim()
    seen = []
    reviewer = lambda b, d: (seen.append(d) or approve())  # noqa: E731
    run_controller(sim, programs=DictStore({}), model=_model(proposal),
                   reviewer=reviewer, session_id="r")
    assert len(seen) == 1 and seen[0].rung == "model"      # proposed
    assert sim.acted[0].intent == "set_text"                # approved -> acted as proposed


def test_correction_journals_a_golden_row(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    proposal = Decision("set_text", {"field": "Source", "value": "wrong"}, 0.9, "model",
                        "fill source", expected_next=("next_state",))
    fix = Decision("select_option", {"field": "Source", "value": "Indeed"}, 1.0, "model",
                   "it's a dropdown", expected_next=("next_state",))
    sim = OneStepSim()
    run_controller(sim, programs=DictStore({}), model=_model(proposal),
                   reviewer=scripted_reviewer([correct(fix)]), session_id="r")

    # acted on the CORRECTION, not the proposal
    assert sim.acted[0].intent == "select_option"
    rows = decision_journal.read_rows()
    golden = [r for r in rows if r.get("golden")]
    assert len(golden) == 1
    g = golden[0]
    assert g["intent"] == "select_option"                   # what was acted (the correction)
    assert g["proposed_intent"] == "set_text"               # the proposal is preserved, not overwritten
    assert g["proposed_rung"] == "model"
    assert g["bundle_snapshot"] is not None                 # a golden row is a permanent replay case
    assert g["params"].get("field") == "Source"


def test_correction_is_restamped_teacher():
    r = correct(Decision("click", {"control": "Next"}, 0.5, "model", "x"))
    assert r.action == ReviewAction.CORRECT and r.correction.rung == "teacher"


def test_escalate_at_review(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    proposal = Decision("set_text", {"field": "Source"}, 0.9, "model", "x", expected_next=("next_state",))

    class Novel:
        def observe(self): return _bundle("workday_my_information", ["Source"])
        def act(self, d): raise AssertionError("escalated review must not act")
    escalated = []
    # Escalate-at-review hands up; the loop re-observes once and, on a second escalate, stops
    # (the two-consecutive rule) — never acting.
    res = run_controller(Novel(), programs=DictStore({}), model=_model(proposal),
                         reviewer=scripted_reviewer([Review(ReviewAction.ESCALATE),
                                                     Review(ReviewAction.ESCALATE)]),
                         on_escalate=lambda b, d: escalated.append(d), session_id="r")
    assert res.status == loop_mod.STATUS_ESCALATED and len(escalated) == 2


def test_abort_stops_the_drive(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    proposal = Decision("set_text", {"field": "Source"}, 0.9, "model", "x", expected_next=("next_state",))

    class Novel:
        def observe(self): return _bundle("workday_my_information", ["Source"])
        def act(self, d): raise AssertionError("aborted drive must not act")
    res = run_controller(Novel(), programs=DictStore({}), model=_model(proposal),
                         reviewer=scripted_reviewer([abort()]), session_id="r")
    assert res.status == loop_mod.STATUS_ABORTED


def test_corrected_submit_is_still_held_by_the_consequential_gate(monkeypatch, tmp_path):
    """A correction that becomes a Submit must STILL hit the consequential gate — the operator's."""
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    proposal = Decision("click", {"control": "Next"}, 0.9, "model", "advance",
                        expected_next=("next_state",))
    fix = Decision("submit", {}, 1.0, "model", "actually this submits", expected_next=("submitted",))

    class Novel:
        def observe(self): return _bundle("workday_review", [])
        def act(self, d): raise AssertionError("a held submit must not act")
    res = run_controller(Novel(), programs=DictStore({}), model=_model(proposal),
                         reviewer=scripted_reviewer([correct(fix)]), session_id="r")
    assert res.status == loop_mod.STATUS_CONSEQUENTIAL and res.last_decision.intent == "submit"


# --- the CLI reviewer --------------------------------------------------------
def test_cli_reviewer_approve_and_correct():
    b = _bundle("workday_my_information", ["Source"])
    d = Decision("set_text", {"field": "Source"}, 0.8, "model", "fill it", expected_next=("next_state",))

    # empty input -> approve
    r = cli_reviewer(input_fn=lambda *_: "", print_fn=lambda *_: None)(b, d)
    assert r.action == ReviewAction.APPROVE

    # 'c' then a scripted correction
    answers = iter(["c", "select_option", "Source", "Indeed", ""])
    r = cli_reviewer(input_fn=lambda *_: next(answers), print_fn=lambda *_: None)(b, d)
    assert r.action == ReviewAction.CORRECT
    assert r.correction.intent == "select_option"
    assert r.correction.params == {"field": "Source", "value": "Indeed"}
    assert r.correction.rung == "teacher"
