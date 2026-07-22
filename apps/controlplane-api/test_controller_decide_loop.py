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


# --- the treadmill guard (found live 2026-07-19, Longroad) --------------------------
def test_verified_actions_that_never_move_the_page_stall_instead_of_spinning():
    """`expected_next` legitimately contains the CURRENT state (Indeed's questions module spans
    several pages that are all `indeed_apply_questions`), so a self-loop verifies exactly like
    progress. Live, that let a BLOCKED Continue be clicked 8 times and report 100% autonomous /
    100% verified while the page never moved. Landing where you expected != getting somewhere."""
    from controller.loop import STATUS_STALLED

    class Treadmill:
        """Always the same page; every click 'succeeds' and lands on the same state."""
        def observe(self):
            return Bundle(task="indeed_apply", goal_text="apply", done=False,
                          url="https://smartapply.indeed.com/questions/2",
                          route="smartapply/questions", state="indeed_apply_questions",
                          is_branch=False, human_required=False,
                          expected_next=("indeed_apply_questions", "indeed_apply_review"))

        def act(self, decision):
            return ActOutcome(outcome="ok", landed_state="indeed_apply_questions")

    # expected_exit INCLUDES the current state, exactly as the compiled program does: the recipe's
    # edges for indeed_apply_questions list indeed_apply_questions itself (the module spans several
    # pages). That self-inclusion is precisely what made the treadmill verify.
    store = DictStore({("indeed_apply", "indeed_apply_questions"): programs_mod.IntentProgram(
        task="indeed_apply", state="indeed_apply_questions", guard_fields=(),
        steps=({"intent": "click", "params": {"control": "Continue"}},),
        expected_exit=("indeed_apply_questions", "indeed_apply_review"))})

    res = run_controller(Treadmill(), programs=store, max_steps=20, session_id="t")
    assert res.status == STATUS_STALLED
    assert res.steps < 20                      # stopped early, did NOT burn the budget
    assert "unchanged" in res.reason


def test_observation_delta_distinguishes_moving_from_standing_still():
    """The delta replaces the old `progress_signature` 3-tuple. These are the three cases the
    signature already handled, kept as a regression: identical page, moved page, field answered."""
    from controller.loop import observation_delta

    def b(url, unanswered=(), ids=("button|continue",)):
        return Bundle(task="t", goal_text="g", done=False, url=url, route="r",
                      state="indeed_apply_questions", is_branch=False, human_required=False,
                      unanswered=tuple({"field": f} for f in unanswered), ax_identities=ids)

    assert observation_delta(b("/q/1"), b("/q/1")).moved is False
    assert observation_delta(b("/q/1"), b("/review")).moved is True          # moved pages
    # same url, but a field got answered -> that IS progress
    assert observation_delta(b("/q/1", ("a", "b")), b("/q/1", ("a",))).moved is True
    # and the first observation of a run is never a stall
    assert observation_delta(None, b("/q/1")).moved is True


def test_observation_delta_sees_what_the_old_signature_was_blind_to():
    """The upgrade, stated as a test. An overlay opening changes no url, no state and no
    unanswered-field set — `progress_signature` scored it identical, so a run could sit under a
    modal reporting "unchanged" with no idea why. The delta names the controls that appeared."""
    from controller.loop import observation_delta

    def b(ids):
        return Bundle(task="t", goal_text="g", done=False, url="/q/1", route="r",
                      state="indeed_apply_questions", is_branch=False, human_required=False,
                      unanswered=({"field": "a"},), ax_identities=ids)

    d = observation_delta(b(("button|continue",)),
                          b(("button|continue", "dialog|verify your identity", "button|close")))
    assert d.moved is True
    assert d.appeared == ("button|close", "dialog|verify your identity")


def test_treadmill_guard_needs_no_ax_identities_to_fire():
    """Back-compat: a Bundle built before `ax_identities` existed (and today's LiveActuator, which
    does not yet run an AX scan) carries an empty identity set. The delta must still fall back to
    url/state/unanswered and catch a stall — otherwise this upgrade would silently disarm the
    guard that commit c3d2904 added."""
    from controller.loop import observation_delta

    def b(url, unanswered=()):
        return Bundle(task="t", goal_text="g", done=False, url=url, route="r",
                      state="indeed_apply_questions", is_branch=False, human_required=False,
                      unanswered=tuple({"field": f} for f in unanswered))

    assert observation_delta(b("/q/1"), b("/q/1")).moved is False
    assert observation_delta(b("/q/1"), b("/review")).moved is True
    assert observation_delta(b("/q/1", ("a",)), b("/q/1")).moved is True


def test_a_same_route_step_advance_is_INVISIBLE_without_ax_or_a_scan_change():
    """A limitation recorded on purpose, not an accident (PLAN_supervisor §0a).

    Advancing `…/questions/1` -> `…/questions/2` changes neither the templated route nor the
    state. If the new step also happens to present the same NUMBER of unanswered fields and no AX
    scan ran, the delta sees nothing and the guard would call real progress a stall — a false
    escalation (the safe direction: it hands to the operator rather than spinning). This is the
    concrete reason `LiveActuator.observe()` owes an AX scan; when `ax_identities` is populated the
    control set turns over and the case resolves itself — which the second half asserts.
    """
    from controller.loop import observation_delta

    base = "https://smartapply.indeed.com/beta/indeedapply/form/questions-module/questions"

    def b(n, ids=()):
        return Bundle(task="t", goal_text="g", done=False, url=f"{base}/{n}", route="r",
                      state="indeed_apply_questions", is_branch=False, human_required=False,
                      unanswered=({"field": "q"},), ax_identities=ids)

    assert observation_delta(b(1), b(2)).moved is False          # blind, today
    assert observation_delta(b(1, ("radio|work authorization",)),
                             b(2, ("textbox|why this role",))).moved is True   # sighted, with AX


# --- the supervisor seam (S12, PLAN_supervisor §3) ----------------------------------
def _supervised_run(actuator, *, store=None, max_steps=6):
    """Run a drive and collect every verdict the loop emitted."""
    seen = []
    res = run_controller(actuator, programs=store, max_steps=max_steps, session_id="t",
                         on_supervise=lambda b, d, v: seen.append(v))
    return res, seen


class _Advancing:
    """Each click actually moves the page — the nominal path."""
    def __init__(self):
        self.n = 0

    def observe(self):
        return _bundle(f"s{self.n}", expected=(f"s{self.n + 1}",))

    def act(self, decision):
        self.n += 1
        return ActOutcome(outcome="ok", landed_state=f"s{self.n}",
                          ax_identities=(f"button|step {self.n}",), unanswered_after=0)


def test_every_acting_step_gets_a_verdict_journaled_on_its_own_row():
    """The verdict must land on the row of the action it judges — that is what makes it a
    training label rather than a note. It is why act() reports the after-picture instead of the
    loop waiting for the next observe() (which would arrive one turn late)."""
    store = DictStore({("indeed_apply", f"s{i}"): _prog(
        "indeed_apply", f"s{i}", [{"intent": "click", "params": {"control": "Continue"}}],
        guard=[], exit_states=[f"s{i + 1}"]) for i in range(4)})

    res, verdicts = _supervised_run(_Advancing(), store=store, max_steps=3)

    acting = [r for r in res.records if r.outcome is not None]
    assert acting, "no acting rows"
    for row in acting:
        assert row.supervisor_class == "none"          # every one of these advanced
        assert row.supervisor_rung == "deterministic"  # and cost nothing to say so
        assert row.supervisor_rationale
        assert row.delta_moved is True
    assert len(verdicts) == len(acting)


#: The real treadmill's shape: a program whose fields are all satisfied, so rung 0 clicks the
#: control that should advance — and nothing happens.
_TREADMILL_STORE = DictStore({("indeed_apply", "indeed_apply_questions"): _prog(
    "indeed_apply", "indeed_apply_questions",
    [{"intent": "click", "params": {"control": "Continue"}}],
    guard=[], exit_states=["indeed_apply_questions"])})


def test_the_treadmill_gets_a_SHARPER_name_than_the_guard_could_give_it():
    """The 2026-07-19 Longroad incident, and the point of the taxonomy in one test.

    The guard already STOPPED this — it could say "2 verified actions left the page unchanged".
    What it could never say is WHY. Here the form scans as complete (`unanswered=0`) and the
    advance still no-ops, which is not generic no-progress: it is the lone required control the
    scanner never saw (the acknowledgment checkbox, LEARNINGS 2026-07-18). So the verdict is
    `missed_required_control` -> `rescan_required`, an actionable play, rather than "it's stuck".
    """
    class Treadmill:
        def observe(self):
            return _bundle("indeed_apply_questions", expected=("indeed_apply_questions",))

        def act(self, decision):
            # 'succeeds', lands where expected, and changes nothing at all
            return ActOutcome(outcome="ok", landed_state="indeed_apply_questions",
                              ax_identities=("button|continue",), unanswered_after=0)

    res, verdicts = _supervised_run(Treadmill(), store=_TREADMILL_STORE, max_steps=6)

    assert res.status == loop_mod.STATUS_STALLED       # the guard still stops it
    assert verdicts[0].failure_class == "missed_required_control"
    assert verdicts[0].proposed_recovery == "rescan_required"
    assert verdicts[0].stuck_signal > 0.5
    assert res.records[0].delta_moved is False


def test_control_identities_are_diffed_only_when_BOTH_sides_have_them():
    """An empty identity set is ambiguous — "no controls" or "nobody looked" — and reading a
    populated `after` against an empty `before` as "the whole page appeared" makes EVERY action
    look like progress, silently disarming the treadmill check. (Caught by the test above failing
    when act() reported identities that observe() never did.)"""
    from controller.loop import action_effect

    seen = _bundle("s", expected=("s",))                       # no ax_identities
    with_ids = loop_mod.Bundle(**{**seen.__dict__, "ax_identities": ("button|continue",)})
    acted = ActOutcome(outcome="ok", landed_state="s",
                       ax_identities=("button|continue",), unanswered_after=0)

    assert action_effect(seen, acted).moved is False           # one side blind -> fall back
    assert action_effect(with_ids, acted).moved is False        # both sides, unchanged
    assert action_effect(with_ids, ActOutcome(
        outcome="ok", landed_state="s", ax_identities=("button|next",),
        unanswered_after=0)).moved is True                      # both sides, really changed


# NB: the generic `no_progress` class cannot arise from a rung-0 drive — rung 0 only clicks once
# every guard field is satisfied, and a satisfied form that no-ops is the sharper
# `missed_required_control` above. It is exercised directly in
# packages/interaction/tests/test_supervision.py, where the classifier's inputs can be posed
# freely. Do not contort a loop fixture to reach it.


def test_an_actuator_that_cannot_look_still_diagnoses_the_stall():
    """Back-compat and the anti-footgun: an actuator with no post-act AX scan (every existing
    fake, and any future one) must degrade to state/unanswered — NOT read as 'first observation',
    which counts as moved and would silently disarm the whole check."""
    class Blind:
        def observe(self):
            return _bundle("indeed_apply_questions", expected=("indeed_apply_questions",))

        def act(self, decision):
            return ActOutcome(outcome="ok", landed_state="indeed_apply_questions")

    _res, verdicts = _supervised_run(Blind(), store=_TREADMILL_STORE, max_steps=6)
    assert verdicts[0].failure_class != "none"
    assert verdicts[0].failure_class == "missed_required_control"


def test_the_supervisor_narrates_the_loudest_stop_rather_than_going_silent():
    """A BLOCKED outcome ends the drive. The verdict must be emitted BEFORE that return, or the
    commentary goes quiet at exactly the moment the operator most needs it."""
    class Blocked:
        def observe(self):
            return _bundle("indeed_apply_questions", expected=("indeed_apply_review",))

        def act(self, decision):
            return ActOutcome(outcome="blocked", landed_state="indeed_apply_questions")

    store = DictStore({("indeed_apply", "indeed_apply_questions"): _prog(
        "indeed_apply", "indeed_apply_questions",
        [{"intent": "click", "params": {"control": "Continue"}}],
        guard=[], exit_states=["indeed_apply_review"])})

    res, verdicts = _supervised_run(Blocked(), store=store)
    assert res.status == loop_mod.STATUS_BLOCKED
    assert verdicts and verdicts[0].failure_class == "challenge"
    assert verdicts[0].proposed_recovery == "escalate"


def test_the_verdict_does_not_change_what_the_loop_does_yet():
    """Stage 1 is SHADOW (PLAN_supervisor §6): the supervisor journals and narrates, and
    influences nothing. `unexpected.respond` keeps the final say over continue/re-observe/escalate.
    This test is the guard against the supervisor quietly acquiring authority it has not earned."""
    class NotFound:
        def observe(self):
            return _bundle("indeed_apply_questions", fields=("Q1",), expected=("indeed_apply_review",))

        def act(self, decision):
            return ActOutcome(outcome="not_found", landed_state="indeed_apply_questions")

    store = DictStore({("indeed_apply", "indeed_apply_questions"): _prog(
        "indeed_apply", "indeed_apply_questions",
        [{"intent": "set_text", "params": {"field": "Q1"}}],
        guard=["Q1"], exit_states=["indeed_apply_review"])})

    res, verdicts = _supervised_run(NotFound(), store=store, max_steps=6)

    # the supervisor says RE_OBSERVE...
    assert verdicts[0].failure_class == "control_not_found"
    assert verdicts[0].proposed_recovery == "re_observe"
    # ...and the loop's own policy is what actually produced the outcome, unchanged: one
    # re-observe, then escalate (STATUS_ESCALATED), exactly as before the supervisor existed.
    assert res.status == loop_mod.STATUS_ESCALATED


# --- S12b: the loop fills the prescription, but only for a graduated class ------------
class _StuckOnRace:
    """A step that keeps failing the way a settle would fix."""
    def __init__(self):
        self.acts = 0

    def observe(self):
        return _bundle("indeed_apply_questions", expected=("indeed_apply_review",),
                       task="indeed_apply")

    def act(self, decision):
        self.acts += 1
        return ActOutcome(outcome="ok", landed_state="indeed_apply_questions",
                          ax_identities=("button|continue",), unanswered_after=0)


class _Recorder:
    def __init__(self, *, tab=True, missed=()):
        self.calls = []
        self._tab, self._missed = tab, missed

    def settle(self): self.calls.append("settle")
    def re_resolve_tab(self): self.calls.append("re_resolve_tab"); return self._tab
    def rescan_required(self): self.calls.append("rescan_required"); return self._missed
    def commit_widget(self, f, v): self.calls.append(("commit", f, v)); return True


def test_the_loop_runs_no_play_in_shadow_mode():
    """The default drive must behave EXACTLY as it did before S12b existed."""
    rec = _Recorder()
    res = run_controller(_StuckOnRace(), programs=_TREADMILL_STORE, max_steps=6,
                         session_id="t", recovery_actuator=rec)
    assert rec.calls == []
    assert res.status == loop_mod.STATUS_STALLED       # unchanged outcome


def test_a_graduated_class_gets_its_play_run_and_the_drive_continues():
    """`missed_required_control` enabled: the loop rescans, finds the control the ordinary scan
    hid, and carries on rather than escalating."""
    rec = _Recorder(missed=({"role": "checkbox", "name": "I acknowledge"},))
    plays = []
    run_controller(_StuckOnRace(), programs=_TREADMILL_STORE, max_steps=4, session_id="t",
                   recovery_actuator=rec,
                   autonomous_classes=frozenset({"missed_required_control"}),
                   on_recover=lambda b, v, p: plays.append(p))
    assert "rescan_required" in rec.calls
    assert plays[0].attempted and plays[0].retry is True


def test_the_recovery_latch_stops_a_second_attempt_on_the_same_step():
    """One attempt per step. Without the latch a play that keeps 'succeeding' while the page never
    moves is a treadmill wearing the supervisor's badge."""
    rec = _Recorder(missed=({"role": "checkbox", "name": "I acknowledge"},))
    plays = []
    run_controller(_StuckOnRace(), programs=_TREADMILL_STORE, max_steps=8, session_id="t",
                   recovery_actuator=rec,
                   autonomous_classes=frozenset({"missed_required_control"}),
                   on_recover=lambda b, v, p: plays.append(p))
    assert rec.calls.count("rescan_required") == 1
    assert any(p.skipped and "treadmill" in p.detail for p in plays[1:])


def test_a_verified_step_clears_the_recovery_latch():
    """Otherwise one recovery early in a drive would disable recovery for the whole rest of it."""
    class Alternating:
        def __init__(self): self.n = 0

        def observe(self):
            return _bundle(f"s{self.n}", expected=(f"s{self.n + 1}",))

        def act(self, decision):
            self.n += 1
            return ActOutcome(outcome="ok", landed_state=f"s{self.n}",
                              ax_identities=(f"button|step {self.n}",), unanswered_after=0)

    store = DictStore({("indeed_apply", f"s{i}"): _prog(
        "indeed_apply", f"s{i}", [{"intent": "click", "params": {"control": "Continue"}}],
        guard=[], exit_states=[f"s{i + 1}"]) for i in range(6)})
    res = run_controller(Alternating(), programs=store, max_steps=4, session_id="t",
                         recovery_actuator=_Recorder(),
                         autonomous_classes=frozenset({"missed_required_control"}))
    # every step verified, so no play ever needed to run and the latch stayed clear
    assert res.status in (loop_mod.STATUS_MAX_STEPS, loop_mod.STATUS_DONE)


# --- the one rail with a documented release (operator-authorised 2026-07-22) -----------
def test_submit_is_held_by_default_even_when_a_program_asks_for_it(monkeypatch, tmp_path):
    """The default must not drift. Everything else here is opt-in; this is the regression guard."""
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    sim = ApplySim()
    res = run_controller(sim, programs=_apply_programs(), session_id="held")
    assert res.status == loop_mod.STATUS_CONSEQUENTIAL
    assert "submit" not in [d.intent for d in sim.acted]


def test_releasing_held_intents_lets_the_system_press_submit_itself(monkeypatch, tmp_path):
    """`held_intents=frozenset()` is how an operator authorises ONE measured drive to finish.

    Per-run and explicit on purpose: an operator authorising a single submit must never become a
    system that always submits, so this lives in the call signature rather than in a setting.
    """
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    sim = ApplySim()
    res = run_controller(sim, programs=_apply_programs(), session_id="released",
                         held_intents=frozenset())
    assert res.status != loop_mod.STATUS_CONSEQUENTIAL
    assert "submit" in [d.intent for d in sim.acted], "the release did not reach the actuator"
