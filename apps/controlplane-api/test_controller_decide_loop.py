"""M2 tests: intent programs, decide() rung 0, and the loop harness.

The loop's happy path is the offline proof of the 'replay drive' — rung 0 drives an apply
state machine end-to-end with ZERO model calls, holding Submit for the operator."""

from __future__ import annotations

from typing import Optional

import pytest

from controller import decide as decide_mod
from controller import loop as loop_mod
from controller import programs as programs_mod
from controller.decide import decide
from controller.loop import ActOutcome, LoopResult, run_controller
from controller.programs import IntentProgram, compile_from_journal
from interaction.decision import Bundle, Decision


# --- helpers -----------------------------------------------------------------
def _bundle(state, fields=(), *, done=False, human=False, branch=False, expected=(),
            task="indeed_apply") -> Bundle:
    return Bundle(
        task=task, goal_text="apply", done=done,
        url="https://smartapply.indeed.com/x", route="smartapply.indeed.com/x",
        state=state, is_branch=branch, human_required=human, ats="indeed_quick_apply",
        expected_next=tuple(expected),
        unanswered=tuple({"field": f, "kind": "text", "required_via": "required-attr",
                          "answered": False, "valid": True} for f in fields),
    )


class DictStore:
    def __init__(self, progs): self.progs = progs
    def get(self, task, state): return self.progs.get((task, state))


def _prog(task, state, steps, guard, exit_states=(), stale=False) -> IntentProgram:
    return IntentProgram(task=task, state=state, guard_fields=tuple(guard),
                         steps=tuple(steps), expected_exit=tuple(exit_states), stale=stale)


# --- programs: store round-trip + PII/selector discipline --------------------
def test_program_save_load_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTROLLER_PROGRAMS_DIR", str(tmp_path))
    p = _prog("indeed_apply", "indeed_apply_questions",
              [{"intent": "set_text", "params": {"field": "Q1"}},
               {"intent": "click", "params": {"control": "Continue"}}],
              guard=["Q1"], exit_states=["indeed_apply_review"])
    programs_mod.save_program(p)
    got = programs_mod.load_program("indeed_apply", "indeed_apply_questions")
    assert got is not None
    assert got.guard_fields == ("Q1",)
    assert got.steps[0] == {"intent": "set_text", "params": {"field": "Q1"}}
    assert got.expected_exit == ("indeed_apply_review",)
    assert got.verified_at                              # stamped on save


def test_saved_program_drops_values_and_selectors(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTROLLER_PROGRAMS_DIR", str(tmp_path))
    p = _prog("indeed_apply", "s",
              [{"intent": "set_text",
                "params": {"field": "Phone", "value": "555-1234", "selector": "#phone"}}],
              guard=["Phone"])
    programs_mod.save_program(p)
    saved = programs_mod.load_program("indeed_apply", "s")
    assert saved.steps[0]["params"] == {"field": "Phone"}   # value + selector gone
    text = (tmp_path / "indeed-apply__s.json").read_text()
    assert "555-1234" not in text and "#phone" not in text  # PII-free on disk


def test_mark_stale(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTROLLER_PROGRAMS_DIR", str(tmp_path))
    programs_mod.save_program(_prog("t", "s", [{"intent": "click", "params": {}}], guard=[]))
    programs_mod.mark_stale("t", "s")
    assert programs_mod.load_program("t", "s").stale is True


def test_compile_from_journal_builds_program_with_value_refs():
    rows = [
        {"task": "gh", "state": "greenhouse_apply_form", "intent": "set_text",
         "params": {"field": "First name", "value": "[redacted:4]"}, "outcome": "ok",
         "verified": True, "bundle_digest": "d1", "expected_next": ["x"]},
        {"task": "gh", "state": "greenhouse_apply_form", "intent": "click",
         "params": {"control": "APPLY"}, "outcome": "ok", "verified": True,
         "bundle_digest": "d1", "expected_next": ["greenhouse_apply_submitted"]},
    ]
    prog = compile_from_journal(rows)
    assert prog is not None
    assert prog.guard_fields == ("First name",)
    assert prog.steps[0] == {"intent": "set_text", "params": {"field": "First name"}}  # value dropped
    assert prog.expected_exit == ("greenhouse_apply_submitted",)
    assert prog.compiled_from == ("d1",)


def test_compile_skips_unverified_and_rejects_mixed_states():
    # unverified rows are not proven steps
    assert compile_from_journal([{"task": "t", "state": "s", "intent": "click",
                                  "params": {}, "outcome": "error", "verified": False}]) is None
    with pytest.raises(ValueError):
        compile_from_journal([
            {"task": "t", "state": "s1", "intent": "click", "params": {}, "outcome": "ok", "verified": True},
            {"task": "t", "state": "s2", "intent": "click", "params": {}, "outcome": "ok", "verified": True},
        ])


# --- decide(): short-circuits ------------------------------------------------
def test_decide_escalates_on_human_required():
    d = decide(_bundle("workday_sign_in", human=True, branch=True), programs=DictStore({}))
    assert d.escalate and d.rung == "human"


def test_decide_escalates_on_branch():
    d = decide(_bundle("captcha", branch=True), programs=DictStore({}))
    assert d.escalate and d.rung == "human"


def test_decide_escalates_on_unknown_state_to_teacher():
    d = decide(_bundle(None), programs=DictStore({}))
    assert d.escalate and d.rung == "teacher"


def test_decide_escalates_when_no_program_and_no_model():
    d = decide(_bundle("indeed_apply_questions", ["Q1"]), programs=DictStore({}))
    assert d.escalate and d.rung == "teacher"


# --- decide(): rung 0 replay -------------------------------------------------
def test_rung0_fills_next_unanswered_field():
    store = DictStore({("indeed_apply", "indeed_apply_questions"): _prog(
        "indeed_apply", "indeed_apply_questions",
        [{"intent": "set_text", "params": {"field": "Q1"}},
         {"intent": "set_text", "params": {"field": "Q2"}},
         {"intent": "click", "params": {"control": "Continue"}}],
        guard=["Q1", "Q2"], exit_states=["indeed_apply_review"])})
    d = decide(_bundle("indeed_apply_questions", ["Q1", "Q2"]), programs=store)
    assert d.rung == "recipe" and d.confidence == 1.0 and not d.escalate
    assert d.intent == "set_text" and d.params == {"field": "Q1"}
    assert d.expected_next == ("indeed_apply_questions",)   # field-fill stays in state


def test_rung0_advances_when_all_fields_satisfied():
    store = DictStore({("indeed_apply", "indeed_apply_questions"): _prog(
        "indeed_apply", "indeed_apply_questions",
        [{"intent": "set_text", "params": {"field": "Q1"}},
         {"intent": "click", "params": {"control": "Continue"}}],
        guard=["Q1"], exit_states=["indeed_apply_review"])})
    d = decide(_bundle("indeed_apply_questions", []), programs=store)   # nothing unanswered
    assert d.intent == "click" and d.params == {"control": "Continue"}
    assert d.expected_next == ("indeed_apply_review",)


def test_rung0_guard_miss_escalates():
    """An unanswered field the program never saw => the form changed => escalate, don't guess."""
    store = DictStore({("indeed_apply", "indeed_apply_questions"): _prog(
        "indeed_apply", "indeed_apply_questions",
        [{"intent": "set_text", "params": {"field": "Q1"}}], guard=["Q1"])})
    d = decide(_bundle("indeed_apply_questions", ["Q1", "NEW_FIELD"]), programs=store)
    assert d.escalate and d.rung == "teacher"


def test_rung0_stale_program_escalates():
    store = DictStore({("indeed_apply", "s"): _prog(
        "indeed_apply", "s", [{"intent": "click", "params": {}}], guard=[], stale=True)})
    d = decide(_bundle("s", []), programs=store)
    assert d.escalate


# --- decide(): the model seam (M3 preview, tested here with a double) ---------
def test_model_seam_returns_confident_decision():
    def model(bundle) -> Optional[Decision]:
        return Decision("click", {"control": "Continue"}, 0.9, "model", "looks like continue",
                        expected_next=("next",))
    d = decide(_bundle("novel_state", []), programs=DictStore({}), model=model)
    assert not d.escalate and d.rung == "model" and d.confidence == 0.9


def test_model_seam_low_confidence_escalates():
    def model(bundle):
        return Decision("submit", {}, 0.4, "model", "unsure", expected_next=())
    d = decide(_bundle("novel_state", []), programs=DictStore({}), model=model)
    assert d.escalate and d.rung == "model" and "ask, don't guess" in d.rationale


def test_model_seam_none_escalates():
    d = decide(_bundle("novel_state", []), programs=DictStore({}), model=lambda b: None)
    assert d.escalate and d.rung == "model"


# --- the loop: the offline replay drive --------------------------------------
class ApplySim:
    """A minimal Indeed apply state machine: questions(2 fields) -> review -> (submit held)."""
    def __init__(self):
        self.answered: set[str] = set()
        self.state = "indeed_apply_questions"
        self.acted: list[Decision] = []

    def observe(self) -> Bundle:
        if self.state == "indeed_apply_questions":
            unfilled = [f for f in ("Q1", "Q2") if f not in self.answered]
            return _bundle("indeed_apply_questions", unfilled, expected=("indeed_apply_review",))
        if self.state == "indeed_apply_review":
            return _bundle("indeed_apply_review", [], expected=("indeed_apply_submitted",))
        return _bundle("indeed_apply_submitted", [], done=True)

    def act(self, decision: Decision) -> ActOutcome:
        self.acted.append(decision)
        if decision.intent == "set_text":
            self.answered.add(decision.params["field"])
            return ActOutcome("ok", landed_state="indeed_apply_questions")
        if decision.intent == "click":
            self.state = "indeed_apply_review"
            return ActOutcome("ok", landed_state="indeed_apply_review")
        return ActOutcome("ok")


def _apply_programs():
    return DictStore({
        ("indeed_apply", "indeed_apply_questions"): _prog(
            "indeed_apply", "indeed_apply_questions",
            [{"intent": "set_text", "params": {"field": "Q1"}},
             {"intent": "set_text", "params": {"field": "Q2"}},
             {"intent": "click", "params": {"control": "Continue"}}],
            guard=["Q1", "Q2"], exit_states=["indeed_apply_review"]),
        ("indeed_apply", "indeed_apply_review"): _prog(
            "indeed_apply", "indeed_apply_review",
            [{"intent": "submit", "params": {}}], guard=[], exit_states=["indeed_apply_submitted"]),
    })


def test_loop_replays_to_the_submit_gate_with_zero_model_calls(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    sim = ApplySim()
    model_calls = {"n": 0}

    def counting_model(bundle):
        model_calls["n"] += 1
        return None

    res = run_controller(sim, programs=_apply_programs(), model=counting_model,
                         session_id="run-1")
    assert res.status == loop_mod.STATUS_CONSEQUENTIAL      # Submit held for the operator
    assert model_calls["n"] == 0                            # happy path never touched the model
    acted = [(d.intent, d.params) for d in sim.acted]
    assert acted == [("set_text", {"field": "Q1"}), ("set_text", {"field": "Q2"}),
                     ("click", {"control": "Continue"})]
    assert all(r.rung == "recipe" for r in res.records if not r.escalate)
    assert res.last_decision.intent == "submit"


def test_loop_stops_done():
    class DoneSim:
        def observe(self): return _bundle("indeed_apply_submitted", [], done=True)
        def act(self, d): raise AssertionError("should not act when done")
    res = run_controller(DoneSim(), programs=DictStore({}))
    assert res.status == loop_mod.STATUS_DONE


def test_loop_stops_human_required():
    class HumanSim:
        def observe(self):
            return _bundle("workday_sign_in", [], human=True, branch=True)
        def act(self, d): raise AssertionError("should not act on a human-required state")
    escalated = []
    res = run_controller(HumanSim(), programs=DictStore({}),
                         on_escalate=lambda b, d: escalated.append(d))
    assert res.status == loop_mod.STATUS_HUMAN and escalated


def test_loop_two_consecutive_escalations_stop(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))

    class NovelSim:
        def observe(self): return _bundle("novel_state", ["X"])   # no program, no model
        def act(self, d): raise AssertionError("escalations never act")
    res = run_controller(NovelSim(), programs=DictStore({}), session_id="r")
    assert res.status == loop_mod.STATUS_ESCALATED
    assert res.steps == 1                                   # escalated on step 0, stopped on step 1


def test_loop_blocked_hands_to_human(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    store = DictStore({("indeed_apply", "s"): _prog(
        "indeed_apply", "s", [{"intent": "click", "params": {"control": "Go"}}],
        guard=[], exit_states=["next"])})

    class BlockedSim:
        def observe(self): return _bundle("s", [], expected=("next",))
        def act(self, d): return ActOutcome("blocked", landed_state="s")
    res = run_controller(BlockedSim(), programs=store, session_id="r")
    assert res.status == loop_mod.STATUS_BLOCKED


def test_loop_stale_outcome_reobserves_once_then_escalates(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setenv("CONTROLLER_PROGRAMS_DIR", str(tmp_path / "progs"))
    # a real on-disk program so mark_stale has something to flag
    programs_mod.save_program(_prog("indeed_apply", "s",
                                    [{"intent": "click", "params": {"control": "Go"}}],
                                    guard=[], exit_states=["next"]))
    acts = {"n": 0}

    class StaleSim:
        def observe(self): return _bundle("s", [], expected=("next",))
        def act(self, d):
            acts["n"] += 1
            return ActOutcome("not_found", landed_state="s")     # stale-state outcome
    res = run_controller(StaleSim(), programs=programs_mod.ProgramStore(), session_id="r")
    assert res.status == loop_mod.STATUS_ESCALATED
    # acted twice (initial + one re-observe retry) before escalating
    assert acts["n"] == 2
    assert programs_mod.load_program("indeed_apply", "s").stale is True   # program flagged
