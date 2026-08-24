"""The four control modes in the loop, and bounded teacher takeover.

The design's load-bearing sentence is not "there are four modes" — it is **"re-evaluate local
control after every teacher action"**. A takeover that runs to the end of the application is the
teacher replacing the driver; a takeover that returns the wheel at the next checkpoint is a
construction detour. `test_authority_returns_to_local_after_every_teacher_action` is that
sentence as a test, and it is the one to read first.

The other two that carry weight:

  * `test_a_park_that_expires_lands_exactly_where_the_old_loop_landed` — nobody listening must
    never be worse than nobody asking.
  * `test_orange_is_executed_locally_and_journaled_golden` — ORANGE's whole reason for existing.
    If the teacher's instruction were performed by the teacher, the step would vanish from the
    corpus and we would be back to invisible work (§8).
"""

from __future__ import annotations


from interaction.authority import (
    ActuationReach,
    ControlMode,
    Maturity,
    PromotionStanding,
    authority,
)
from interaction.contract import Outcome
from interaction.decision import Bundle, Decision

from controller.loop import (
    STATUS_ESCALATED,
    STATUS_PARK_EXPIRED,
    STATUS_TAKEOVER_BUDGET,
    ActOutcome,
    TakeoverResult,
    run_controller,
)
from controller.programs import IntentProgram

WHY = "the questionnaire asks about sponsorship, which maps to our work-authorisation answer"


# --- doubles -------------------------------------------------------------------------
def a_bundle(state="workday_questions", **over):
    kw = dict(task="apply", goal_text="", done=False, url="https://wd/x", route="/x",
              state=state, is_branch=False, human_required=False, ats="workday",
              ax_identities=("button|Continue", "combobox|Sponsorship"),
              unanswered=({"field": "sponsorship", "kind": "react_select"},))
    kw.update(over)
    return Bundle(**kw)


class FakeActuator:
    """Observes a scripted sequence and records what it was asked to do."""

    def __init__(self, bundles, outcome=None):
        self._bundles = list(bundles)
        self.acted: list[Decision] = []
        self._outcome = outcome or ActOutcome(outcome=Outcome.OK.value,
                                              landed_state="workday_review")

    def observe(self):
        return self._bundles.pop(0) if len(self._bundles) > 1 else self._bundles[0]

    def act(self, decision):
        self.acted.append(decision)
        return self._outcome


class ScriptedSeat:
    """A `TeacherSeat` that answers from a script — no files, no clocks, no waiting."""

    def __init__(self, *, instruction=None, takeover=None):
        self._instruction = instruction
        self._takeover = takeover or TakeoverResult(resumed=True, detail="checkpoint")
        self.instructed: list = []
        self.took_over: list = []

    def instruct(self, bundle, decision, verdict):
        self.instructed.append((bundle, decision, verdict))
        return self._instruction

    def takeover(self, bundle, decision, verdict):
        self.took_over.append((bundle, decision, verdict))
        return self._takeover


def fixed_authority(mode_maturity, *, reach=None, standing=None):
    """An AuthorityFn pinned to one maturity, so a test names the mode it is exercising.

    Defaults to a PROMOTED standing: these tests are about the MODE MACHINERY, not about the
    promotion gate, so the gate is held open and the maturity argument stays the single variable.
    The gate's own behaviour is pinned in `test_authority.py`.
    """
    probe = reach if reach is not None else ActuationReach(can_operate=True)
    stand = standing if standing is not None else PromotionStanding(
        measured=True, eligible=True, detail="gate held open for a mode-machinery test")

    def _fn(bundle, decision):
        return authority(maturity=mode_maturity, belief=bundle.belief, reach=probe,
                         consequential=False, standing=stand)
    return _fn


class Programs:
    def __init__(self, program=None):
        self._program = program

    def get(self, task, state):
        return self._program


CONTINUE_PROGRAM = IntentProgram(
    task="apply", state="workday_questions", guard_fields=("sponsorship",),
    expected_exit=("workday_review",),
    steps=({"intent": "select_option", "params": {"field": "sponsorship"}},))


def teacher_decision(intent="select_option", **params):
    return Decision(intent=intent, params=params, confidence=1.0, rung="teacher", rationale=WHY,
                    expected_next=("workday_review",))


# --- GREEN ---------------------------------------------------------------------------
def test_green_acts_without_asking_anyone():
    seat = ScriptedSeat()
    reviewed = []
    act = FakeActuator([a_bundle()])
    run_controller(act, programs=Programs(CONTINUE_PROGRAM), max_steps=1, seat=seat,
                   authority=fixed_authority(Maturity.CERTIFIED.value),
                   reviewer=lambda b, d: reviewed.append(d) or __import__(
                       "controller.teach", fromlist=["approve"]).approve())
    assert act.acted, "a certified transition should just run"
    assert not reviewed and not seat.instructed and not seat.took_over


# --- YELLOW --------------------------------------------------------------------------
def test_yellow_reviews_a_rung0_program_the_transition_has_not_earned():
    """The property the old rung-keyed gate could not express: a compiled program replaying a step
    nobody has ever verified is exactly as unproven as a model guess, and `PROPOSE_RUNGS` waved it
    straight through because the rung said 'recipe'."""
    from controller import teach
    seen = []

    def reviewer(bundle, decision):
        seen.append(decision)
        return teach.approve()

    act = FakeActuator([a_bundle()])
    run_controller(act, programs=Programs(CONTINUE_PROGRAM), max_steps=1, reviewer=reviewer,
                   authority=fixed_authority(Maturity.REPLAYABLE.value))
    assert [d.rung for d in seen] == ["recipe"]
    assert act.acted, "approved, so it still acts"


def test_the_old_rung_floor_still_applies_when_authority_says_green():
    """Nothing may get LESS review than it did before authority existed."""
    from controller import teach
    seen = []
    act = FakeActuator([a_bundle()])
    run_controller(act, programs=Programs(), max_steps=1,
                   model=lambda b: Decision(intent="click", params={"control": "Continue"},
                                            confidence=0.9, rung="model", rationale="advance"),
                   reviewer=lambda b, d: seen.append(d) or teach.approve(),
                   authority=fixed_authority(Maturity.CERTIFIED.value))
    assert [d.rung for d in seen] == ["model"]


# --- ORANGE --------------------------------------------------------------------------
def test_orange_is_executed_locally_and_journaled_golden():
    """ORANGE's entire reason for existing. If the teacher's instruction were performed BY the
    teacher, the step would vanish from the corpus and we would be back to invisible work (§8)."""
    seat = ScriptedSeat(instruction=teacher_decision(field="sponsorship"))
    act = FakeActuator([a_bundle()])
    result = run_controller(act, programs=Programs(), max_steps=1, seat=seat,
                            authority=fixed_authority(Maturity.UNSEEN.value))

    assert seat.instructed, "an unseen transition must ask"
    assert len(act.acted) == 1, "and the LOCAL actuator performs it"
    assert act.acted[0].rung == "teacher"

    row = result.records[-1]
    assert row.golden, "teacher answer + local prediction on one row is the densest training row"
    assert row.proposed_intent, "the local prediction must survive beside the teacher's answer"
    assert row.control_mode == ControlMode.ORANGE.value


def test_the_teacher_sees_the_local_prediction_it_is_correcting():
    seat = ScriptedSeat(instruction=teacher_decision(field="sponsorship"))
    run_controller(FakeActuator([a_bundle()]), programs=Programs(), max_steps=1, seat=seat,
                   authority=fixed_authority(Maturity.UNSEEN.value))
    _, proposed, verdict = seat.instructed[0]
    assert proposed.escalate and proposed.escalation_axis == "no_program"
    assert proposed.intent == "select_option"        # guessed from the react_select shape
    assert verdict.mode == ControlMode.ORANGE.value


def test_a_refused_instruction_hands_further_up_rather_than_inventing_one():
    seat = ScriptedSeat(instruction=None)
    act = FakeActuator([a_bundle()])
    result = run_controller(act, programs=Programs(), max_steps=4, seat=seat,
                            authority=fixed_authority(Maturity.UNSEEN.value))
    assert not act.acted
    assert result.status == STATUS_ESCALATED


# --- RED -----------------------------------------------------------------------------
def test_red_hands_the_wheel_over_and_takes_it_back():
    seat = ScriptedSeat(takeover=TakeoverResult(resumed=True, detail="signature entered"))
    act = FakeActuator([a_bundle()])
    run_controller(act, programs=Programs(CONTINUE_PROGRAM), max_steps=2, seat=seat,
                   authority=fixed_authority(
                       Maturity.CERTIFIED.value,
                       reach=ActuationReach(can_operate=False, gaps=("widget:signature_pad",))))
    assert seat.took_over
    assert seat.took_over[0][2].gaps == ("widget:signature_pad",)


def test_authority_returns_to_local_after_every_teacher_action():
    """THE sentence of the design, as a test.

    Turn 1 is RED (a widget we cannot drive). The teacher resolves it, and the page moves to a
    state we CAN operate — so turn 2 must be decided locally again, not handed over a second time.
    A takeover that runs to the end of the application is the teacher replacing the driver.
    """
    act = FakeActuator([a_bundle()])
    seat = ScriptedSeat(takeover=TakeoverResult(resumed=True, detail="signature entered"))

    turns = {"n": 0}

    def authority_fn(bundle, decision):
        # Turn 1: a widget the executor cannot drive. From turn 2 on, the teacher has cleared it.
        turns["n"] += 1
        unreachable = turns["n"] == 1
        return authority(
            maturity=Maturity.CERTIFIED.value, belief=None,
            reach=ActuationReach(can_operate=not unreachable,
                                 gaps=("widget:signature_pad",) if unreachable else ()))

    run_controller(act, programs=Programs(CONTINUE_PROGRAM), max_steps=3, seat=seat,
                   authority=authority_fn)

    assert len(seat.took_over) == 1, "the wheel was borrowed exactly once"
    assert act.acted, "and the very next turn was driven locally again"
    assert act.acted[0].rung == "recipe", "locally means the local rung, not another teacher turn"


def test_a_takeover_budget_stops_a_drive_that_is_being_done_for():
    seat = ScriptedSeat(takeover=TakeoverResult(resumed=True))
    act = FakeActuator([a_bundle()])
    result = run_controller(act, programs=Programs(CONTINUE_PROGRAM), max_steps=10, seat=seat,
                            max_takeovers=2,
                            authority=fixed_authority(
                                Maturity.CERTIFIED.value,
                                reach=ActuationReach(can_operate=False, gaps=("widget:x",))))
    assert result.status == STATUS_TAKEOVER_BUDGET
    assert len(seat.took_over) == 2


def test_an_aborted_takeover_stops_the_drive():
    seat = ScriptedSeat(takeover=TakeoverResult(resumed=False, aborted=True, detail="not safe"))
    result = run_controller(FakeActuator([a_bundle()]), programs=Programs(CONTINUE_PROGRAM),
                            max_steps=5, seat=seat,
                            authority=fixed_authority(
                                Maturity.CERTIFIED.value,
                                reach=ActuationReach(can_operate=False, gaps=("widget:x",))))
    assert result.status == "aborted" and "not safe" in result.reason


# --- the park-expiry contract --------------------------------------------------------
def test_a_park_that_expires_lands_exactly_where_the_old_loop_landed():
    """Nobody listening must never be WORSE than nobody asking."""
    escalated = []
    seat = ScriptedSeat(takeover=TakeoverResult(resumed=False, timed_out=True))
    result = run_controller(FakeActuator([a_bundle()]), programs=Programs(CONTINUE_PROGRAM),
                            max_steps=5, seat=seat,
                            on_escalate=lambda b, d: escalated.append(d),
                            authority=fixed_authority(
                                Maturity.CERTIFIED.value,
                                reach=ActuationReach(can_operate=False, gaps=("widget:x",))))
    assert result.status == STATUS_PARK_EXPIRED
    assert escalated, "the operator alert still fires, exactly as before"


def test_park_expiry_is_distinguishable_from_a_real_escalation():
    """'we had no seat' and 'we had a seat and it was empty' are different problems — one is a
    capability gap, the other is operator availability. They must not look alike in the numbers."""
    assert STATUS_PARK_EXPIRED != STATUS_ESCALATED


# --- the rails do not move with mode -------------------------------------------------
def test_submit_is_still_held_no_matter_the_mode():
    seat = ScriptedSeat(instruction=teacher_decision(intent="submit"))
    act = FakeActuator([a_bundle(unanswered=())])
    result = run_controller(act, programs=Programs(), max_steps=2, seat=seat,
                            authority=fixed_authority(Maturity.UNSEEN.value))
    assert result.status == "consequential_gate"
    assert not act.acted, "not even a teacher instruction may fire Submit"


def test_a_human_required_state_is_still_undriveable():
    seat = ScriptedSeat(instruction=teacher_decision(intent="set_text", field="password"))
    act = FakeActuator([a_bundle(human_required=True)])
    result = run_controller(act, programs=Programs(), max_steps=2, seat=seat,
                            authority=fixed_authority(Maturity.UNSEEN.value))
    assert result.status == "human_required"
    assert not act.acted and not seat.instructed


# --- journaling ----------------------------------------------------------------------
def test_every_row_records_who_was_allowed_to_decide_it():
    """Promotion reads `control_mode` back out of the journal, so a row that omits it contributes
    nothing to the ladder — which is exactly right, and must be visible rather than silent."""
    act = FakeActuator([a_bundle()])
    result = run_controller(act, programs=Programs(CONTINUE_PROGRAM), max_steps=1,
                            authority=fixed_authority(Maturity.CERTIFIED.value))
    row = result.records[-1]
    assert row.control_mode == ControlMode.GREEN.value
    assert row.transition_maturity == Maturity.CERTIFIED.value
    assert row.authority_reason


def test_without_authority_wired_nothing_changes():
    """The offline suite's contract: no authority, no seat, identical behaviour to before."""
    act = FakeActuator([a_bundle()])
    result = run_controller(act, programs=Programs(CONTINUE_PROGRAM), max_steps=1)
    assert act.acted
    assert result.records[-1].control_mode is None


# --- the production call site really gates -------------------------------------------
def test_the_live_route_wires_authority_and_a_seat():
    """`run_controller` defaults both to None so the offline suite can exercise pure control-flow.
    That default is a footgun unless the production path always overrides it — "gate immediately"
    has to be a fact about the running system, not a claim in a docstring."""
    import inspect
    from routers import controller as router

    src = inspect.getsource(router.run_live)
    assert "authority=authority_fn" in src and "seat=seat" in src
    assert "default_authority()" in src and "InboxSeat(" in src
    assert "progressive: bool = True" in inspect.getsource(router.RunBody)


def test_the_seam_uses_one_definition_of_consequential():
    """An observer applying the strict ceiling while authority does not (or the reverse) is a
    silent inconsistency at exactly the irreversible step."""
    import inspect
    from controller import authority_seam, loop

    assert "CONSEQUENTIAL_INTENTS" in inspect.getsource(authority_seam.default_authority)
    assert authority_seam.CONSEQUENTIAL_INTENTS is loop.CONSEQUENTIAL_INTENTS


# --- a takeover that moves the work to another tab (live gap, 2026-07-22) --------------
def test_a_takeover_can_report_that_the_work_moved_to_a_new_tab():
    """Clicking Apply opens the application in a NEW tab, and only the teacher saw it happen.

    Without this the loop kept observing the tab it was constructed with — the search results —
    so a successful takeover read as no progress, and the drive had to be aborted and the new tab
    addressed by hand. The retarget must land BEFORE the re-observation, or the loop photographs
    the tab it just left.
    """
    class _Retargetable(FakeActuator):
        def __init__(self, bundles):
            super().__init__(bundles)
            self.retargeted = []
            self.retargeted_when_observed = []

        def retarget(self, tab_id):
            self.retargeted.append(tab_id)
            return True

        def observe(self):
            self.retargeted_when_observed.append(list(self.retargeted))
            return super().observe()

    seat = ScriptedSeat(takeover=TakeoverResult(resumed=True, new_tab_id="TAB-B"))
    act = _Retargetable([a_bundle()])
    run_controller(act, programs=Programs(CONTINUE_PROGRAM), max_steps=2, seat=seat,
                   authority=fixed_authority(
                       Maturity.CERTIFIED.value,
                       reach=ActuationReach(can_operate=False, gaps=("widget:signature_pad",))))

    # This authority is permanently RED, so the loop hands over on every turn and reports the
    # same move each time — what matters is that it followed at all, and followed FIRST.
    assert act.retargeted[0] == "TAB-B", "the loop ignored where the teacher said the work went"
    assert act.retargeted_when_observed[-1][:1] == ["TAB-B"], \
        "re-observed before following the tab"


def test_a_takeover_without_a_new_tab_leaves_the_target_alone():
    """The common case must not touch the tab: only a teacher who SAW a window open reports one."""
    class _Retargetable(FakeActuator):
        def __init__(self, bundles):
            super().__init__(bundles)
            self.retargeted = []

        def retarget(self, tab_id):
            self.retargeted.append(tab_id)
            return True

    seat = ScriptedSeat(takeover=TakeoverResult(resumed=True, detail="signature entered"))
    act = _Retargetable([a_bundle()])
    run_controller(act, programs=Programs(CONTINUE_PROGRAM), max_steps=2, seat=seat,
                   authority=fixed_authority(
                       Maturity.CERTIFIED.value,
                       reach=ActuationReach(can_operate=False, gaps=("widget:signature_pad",))))
    assert act.retargeted == []


# --- attended mode: teacher-first economics (operator-directed 2026-08-09) -------------
def test_attended_demotes_haiku_at_the_model_wiring_line():
    """On an attended drive the rung above recipe is the session-Claude teacher — already paid
    for — so `assisted` must NOT wire the API rung unless the settings flag deliberately
    re-admits it. Enforced at model-wiring because authority grades AFTER decide() runs, by
    which point an injected model has already spent the call."""
    import inspect
    from routers import controller as router

    src = inspect.getsource(router.run_live)
    assert "body.attended" in src and "haiku_attended_allowed" in src
    # the gate must sit on the ONE line that constructs the reasoner
    wiring = src[src.index("model = None"):src.index("HaikuReasoner()") + 20]
    assert "not body.attended or settings.haiku_attended_allowed" in wiring
    assert "attended: bool = True" in inspect.getsource(router.RunBody)


def test_attended_routes_yellow_reviews_to_the_teacher_inbox():
    """"Not confident enough" is precisely the turn the operator wants the teacher to see —
    attended swaps the 0.85 auto-approve floor for the inbox, whose timeout ESCALATES rather
    than approving (nobody listening must never mean act unsupervised)."""
    import inspect
    from routers import controller as router

    src = inspect.getsource(router.run_live)
    assert "if body.attended:" in src
    assert "inbox_mod.inbox_reviewer(" in src


def test_a_park_announces_itself_when_it_opens_not_after_it_resolves():
    """`on_park` used to fire after `ask_and_wait` returned — a post-mortem, not a notification:
    nothing could learn a teacher was needed WHILE the teacher was needed. The seat now asks,
    announces, then waits; the callback must observe the request still OPEN."""
    from controller import inbox as inbox_mod
    from controller.authority_seam import InboxSeat

    seen_open: list[bool] = []

    def on_park(request):
        ids_open = {r.get("id") for r in inbox_mod.pending()}
        seen_open.append(request.id in ids_open)

    seat = InboxSeat(session_id="t-announce", timeout=0.0, on_park=on_park)
    answer = seat.instruct(a_bundle(), Decision("click", {}, 0.4, "recipe", WHY,
                                                escalate=True), None)
    assert answer is None, "timeout=0 must expire, exactly as before"
    assert seen_open == [True], "the park was already resolved by the time it was announced"


def test_the_live_route_writes_the_transition_corpus_it_reads():
    """The recorder wiring lived only in a dead module-level entrypoint, so the production
    route journaled decisions and wrote ZERO transition rows — the built-never-wired shape,
    caught in review before it shipped this time. One run key joins seat, reviewer, journal
    and corpus, so the cockpit Trace can follow a drive it launched."""
    import inspect
    from routers import controller as router

    src = inspect.getsource(router.run_live)
    assert "transition_recorder(run_key)" in src
    assert "on_supervise=rec_supervise" in src
    assert "on_step=_step_and_record" in src
    assert 'run_key = body.session_id or f"run-{body.task}"' in src
    assert "InboxSeat(session_id=run_key" in src
    assert "session_id=run_key" in src
