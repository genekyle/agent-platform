"""The controller loop — observe() -> decide() -> act() -> verify(), thin and boring.

PLAN_controller_v1 §5. This is NOT a rewrite of `runtime/loop.py` (an explicit non-goal —
the wheel turns first); it is a standalone harness that imports the same modules, and
`runtime/loop.py` adopts `decide()` later. Kept deliberately small so its behaviour is
obvious and testable.

The one seam is the `Actuator`: it OBSERVES (builds a Bundle from the live tab) and ACTS
(drives one Decision through the Interaction API — the only action surface, journaled by
construction). Everything else — the cascade, verify, the escalation triggers, the stop
conditions, the consequential gate — is pure control-flow here, exercised offline with a
fake actuator. Acting through anything but the Interaction API is a failed session
(SESSION_02 non-negotiable).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from interaction import delta as delta_mod
from interaction import supervision
from interaction.authority import AuthorityVerdict, ControlMode
from interaction.contract import Intent, Outcome
from interaction.decision import Bundle, Decision, DecisionRecord
from interaction.decision_journal import log_decision, record_for
from controller import programs as programs_mod
from controller import recovery
from controller import unexpected
from controller.decide import DecisionReasoner, Orienter, ProgramLookup, decide
from controller.teach import PROPOSE_RUNGS, ReviewAction, Reviewer

MAX_STEPS = 40

#: How many bounded teacher takeovers one drive may spend before it stops and asks for a human
#: with the whole picture. A takeover is a CONSTRUCTION DETOUR, not a second driver: a drive that
#: keeps handing the wheel over is not being taught, it is being done for — which is the exact
#: pattern this plan exists to end.
MAX_TAKEOVERS = 3

#: Intents the loop NEVER auto-acts — it holds them for the operator. Submit ends an
#: application (irreversible, consequential); "apply = done only when SUBMITTED", and the
#: final Submit is the operator's, always (SESSION_02 DoD, and the apply preferences).
CONSEQUENTIAL_INTENTS = frozenset({Intent.SUBMIT.value})


@dataclass
class ActOutcome:
    """What an actuator reports after driving one Decision."""
    outcome: str                          # an Outcome value
    landed_state: Optional[str] = None    # the state observed AFTER acting (for verify)
    cost_usd: float = 0.0
    detail: str = ""
    #: The control-set identities read AFTER the action. The supervisor's verdict is about what
    #: THIS action did, so it needs the after-picture at the moment of acting — not the one the
    #: next observe() happens to find. Empty means the actuator could not (or did not) look, and
    #: the delta degrades to url/state/unanswered rather than lying about churn.
    ax_identities: tuple[str, ...] = ()
    unanswered_after: Optional[int] = None


class Actuator(Protocol):
    """The loop's window onto the live tab. observe() composes a Bundle (fetching page_text,
    scan_required, and the journal tail itself); act() drives one Decision through the
    Interaction API and reports the outcome + where it landed."""
    def observe(self) -> Bundle: ...
    def act(self, decision: Decision) -> ActOutcome: ...


class AuthorityFn(Protocol):
    """`(bundle, decision) -> AuthorityVerdict`. Injected so the loop stays pure control-flow: the
    real one reads the maturity registry (which reads the journal) and runs the reach probe, and a
    test passes a lambda. `controller.authority_seam.default_authority()` builds the live one."""
    def __call__(self, bundle: Bundle, decision: Decision) -> AuthorityVerdict: ...


@dataclass
class TakeoverResult:
    """What came back from a bounded RED handoff."""
    #: The teacher reached a stop condition and control returns to the loop.
    resumed: bool = True
    #: The teacher gave up / aborted the drive.
    aborted: bool = False
    #: Nobody answered before the park expired — the drive must land exactly where it landed
    #: before the inbox existed.
    timed_out: bool = False
    detail: str = ""


class TeacherSeat(Protocol):
    """The teacher's seat in a RUNNING drive — the seam this whole plan is built around.

    Before it, an escalation returned `STATUS_ESCALATED` and the drive was over: there was
    nowhere for the local Claude agent to answer, so sessions fell back to hand-rolled scripts
    around the Interaction API. That was a missing seam, not indiscipline (PRINCIPLES §11).

    Two methods, because ORANGE and RED are genuinely different requests:

      `instruct` — "I cannot choose here; give me ONE bounded semantic action." The LOCAL actuator
                   then performs and verifies it, so the step is journaled like every other step.
                   Returns a Decision, or None on timeout/refusal.
      `takeover` — "I cannot operate this at all; drive until a stop condition is met." Bounded by
                   construction: the ticket carries its exit conditions.

    Injected, like `Actuator` and `Reviewer`, so `run_controller` performs no IO and the whole
    park/resume path is testable offline in microseconds.
    """
    def instruct(self, bundle: Bundle, decision: Decision,
                 verdict: AuthorityVerdict) -> Optional[Decision]: ...
    def takeover(self, bundle: Bundle, decision: Decision,
                 verdict: AuthorityVerdict) -> TakeoverResult: ...


#: Consecutive VERIFIED actions that leave the page byte-identical before we call it a stall.
#: One is normal (a field fill doesn't move the page); two in a row means the thing we keep
#: "successfully" doing is achieving nothing.
NO_PROGRESS_LIMIT = 2


def observation_delta(before: Optional[Bundle], after: Bundle) -> delta_mod.StateDelta:
    """Diff two consecutive observations — "did the page move, and how?"

    Replaces the old `progress_signature` 3-tuple (`url`, `state`, unanswered field names), which
    answered only "did it move" and was blind to everything that actually goes wrong: an overlay
    that opened, an error banner that appeared, a Continue that went disabled. It was also blind
    in the other direction — real progress through Indeed's questions module changes NEITHER the
    templated route (`…/questions/{id}`) nor the state (`indeed_apply_questions`), so movement
    there is visible only in the control set turning over.

    `before=None` (the first observation of a run) yields a delta that counts as moved: there is
    no prior to have failed to move from.
    """
    return delta_mod.compute(
        before=None if before is None else before.ax_identities,
        after=after.ax_identities,
        url_before=None if before is None else before.url,
        url_after=after.url,
        state_before=None if before is None else before.state,
        state_after=after.state,
        unanswered_before=None if before is None else len(before.unanswered),
        unanswered_after=len(after.unanswered),
    )


def action_effect(before: Bundle, result: ActOutcome) -> delta_mod.StateDelta:
    """What ONE action did — the supervisor's input.

    Distinct from `observation_delta`, and the distinction is the whole reason the supervisor can
    be synchronous. A between-observations delta at the top of the loop describes the PREVIOUS
    action, so a verdict built from it would always arrive one turn late (and could never be
    journaled on the row of the action it judges). `Actuator.act()` reports the after-picture at
    the moment of acting instead, so cause and effect sit on the same record.

    Control identities are diffed only when BOTH sides have them. An empty set is ambiguous — it
    means either "a page with no controls" or "nobody looked" — and guessing wrong is expensive in
    both directions: `before=()` against a populated `after` reports the entire page as newly
    appeared (every action looks like progress, disarming the treadmill check), while a populated
    `before` against `after=()` reports the whole page vanishing. When either side is empty the
    delta falls back to state + unanswered, which is what we had before S11 — under-reporting
    movement, so it fails toward a false STALL and an escalation rather than false progress.

    Note also what is NOT passed: `before=None`. That means "first observation" and counts as
    moved, which would silently disarm the check on exactly the actuators that cannot look.
    """
    looked = bool(result.ax_identities) and bool(before.ax_identities)
    return delta_mod.compute(
        before=before.ax_identities if looked else (),
        after=result.ax_identities if looked else (),
        state_before=before.state,
        state_after=result.landed_state,
        unanswered_before=len(before.unanswered),
        unanswered_after=result.unanswered_after,
    )


# Loop statuses — every one is a definite verdict, never an ambiguous "ran out".
STATUS_DONE = "done"
STATUS_STALLED = "stalled"
STATUS_MAX_STEPS = "max_steps"
STATUS_HUMAN = "human_required"
STATUS_BLOCKED = "blocked"
STATUS_ESCALATED = "escalated"
STATUS_CONSEQUENTIAL = "consequential_gate"
STATUS_ABORTED = "aborted"
#: The teacher was asked and nobody answered before the park expired. Distinct from
#: STATUS_ESCALATED so "we had no seat" and "we had a seat and it was empty" never look alike in
#: the numbers — the second is an operator-availability problem, not a capability one.
STATUS_PARK_EXPIRED = "park_expired"
#: A drive that spent its takeover budget. Stops with the whole picture rather than handing the
#: wheel over indefinitely.
STATUS_TAKEOVER_BUDGET = "takeover_budget"


@dataclass
class LoopResult:
    status: str
    steps: int
    reason: str
    last_bundle: Optional[Bundle] = None
    last_decision: Optional[Decision] = None
    records: list[DecisionRecord] = field(default_factory=list)


def _verify(decision: Decision, result: ActOutcome) -> bool:
    """Two-part verify (PLAN §5): the landed state is one the decision expected, AND the
    tier-2 Outcome is a verified ok. When no expectation was given (a field-fill that stays
    put), the Outcome alone decides."""
    if result.outcome != Outcome.OK.value:
        return False
    if decision.expected_next:
        return result.landed_state in decision.expected_next
    return True


def run_controller(
    actuator: Actuator,
    *,
    programs: Optional[ProgramLookup] = None,
    model: Optional[DecisionReasoner] = None,
    session_id: str = "",
    max_steps: int = MAX_STEPS,
    reviewer: Optional[Reviewer] = None,
    propose_rungs: frozenset = PROPOSE_RUNGS,
    on_escalate: Optional[Callable[[Bundle, Decision], None]] = None,
    on_consequential: Optional[Callable[[Bundle, Decision], None]] = None,
    on_step: Optional[Callable[[Bundle, Decision, Optional[ActOutcome]], None]] = None,
    on_supervise: Optional[Callable[[Bundle, Decision, "supervision.SupervisorVerdict"], None]] = None,
    recovery_actuator: Optional["recovery.RecoveryActuator"] = None,
    autonomous_classes: frozenset = recovery.AUTONOMOUS_CLASSES,
    on_recover: Optional[Callable[[Bundle, "supervision.SupervisorVerdict",
                                   "recovery.PlayResult"], None]] = None,
    authority: Optional[AuthorityFn] = None,
    seat: Optional[TeacherSeat] = None,
    orient: Optional[Orienter] = None,
    max_takeovers: int = MAX_TAKEOVERS,
    on_authority: Optional[Callable[[Bundle, Decision, AuthorityVerdict], None]] = None,
    held_intents: frozenset = CONSEQUENTIAL_INTENTS,
) -> LoopResult:
    """Drive one task to a definite stop. Every step journals a DecisionRecord.

    Stops on: `done`, a human-required state, a consequential gate (Submit), BLOCKED,
    two escalations in a row, or `max_steps`. Never auto-solves a challenge — a BLOCKED
    outcome or a human_required state hands straight to the operator.

    --------------------------------------------------------------------------------
    Authority (`authority=` + `seat=`) — who owns each turn
    --------------------------------------------------------------------------------
    With `authority` wired, every turn is graded GREEN / YELLOW / ORANGE / RED from what the
    TRANSITION has earned, how unsure the witnesses are, and whether the executor can operate the
    page at all. With `seat` wired too, ORANGE and RED park the drive for the teacher instead of
    ending it, and **control returns to the local loop on the very next turn** — the design's
    load-bearing line is "re-evaluate local control after every teacher action", not at the end of
    the application.

    **Both default to None, which reproduces the pre-authority behaviour exactly.** That is for
    the offline suite (a fake actuator with an empty journal would grade every transition UNSEEN
    and park forever); the LIVE call site always wires both — see `/api/controller/run` and
    `controller/authority_seam.py`. If you are adding a new production call site, wire them.

    Nothing about the safety rails moves with mode: SUBMIT is still held for the operator,
    `human_required` states are still structurally undriveable, BLOCKED still hands straight over,
    and a challenge is still never auto-solved. Mode decides who CHOOSES, never what is allowed.

    The one rail with a documented release is `held_intents`, and releasing it is an OPERATOR act,
    never a mode's: passing `frozenset()` lets the loop press Submit itself, per run, because the
    operator asked to measure whether the system can finish an application unaided. It stays a
    parameter rather than a setting so the authorisation lives at one call site and expires with
    the drive, and it does not touch `human_required`, BLOCKED, or the challenge rule at all.
    """
    store = programs or programs_mod.ProgramStore()
    records: list[DecisionRecord] = []
    escalations_in_a_row = 0
    stale_retry_used = False
    #: One recovery attempt until the drive makes real progress again, latched here. Cleared only
    #: by a step that verifies AND the supervisor calls nominal — NOT by the next iteration,
    #: or a play that keeps "succeeding" while the page never moves would be a treadmill wearing
    #: the supervisor's badge (recovery.py's second discipline).
    recovery_used = False
    no_progress = 0
    #: The observation the treadmill check compares against. Set ONLY after a VERIFIED action —
    #: an action that never claimed success has no business being judged for not moving the page.
    armed_bundle: Optional[Bundle] = None
    last_bundle: Optional[Bundle] = None
    last_decision: Optional[Decision] = None

    takeovers_used = 0
    #: The authority verdict for the turn being journaled. Held here rather than threaded through
    #: `journal(...)`'s signature at eight call sites, because a row that records WHAT was decided
    #: without recording WHO WAS ALLOWED to decide it cannot feed promotion back into the maturity
    #: registry — the feedback that makes the ladder climb instead of being hand-maintained.
    current_authority: Optional[AuthorityVerdict] = None

    def journal(dec: Decision, bundle: Bundle, *, outcome: Optional[str] = None,
                landed: Optional[str] = None, verified: Optional[bool] = None,
                cost: float = 0.0, proposed: Optional[Decision] = None,
                golden: bool = False, verdict=None, delta=None) -> None:
        rec = record_for(dec, bundle, outcome=outcome, landed_state=landed, verified=verified,
                         session_id=session_id, cost_usd=cost, proposed=proposed, golden=golden,
                         verdict=verdict, delta=delta, authority=current_authority)
        saved = log_decision(rec)
        records.append(saved or rec)

    for step in range(max_steps):
        bundle = actuator.observe()
        last_bundle = bundle
        # Cleared every turn so a stale verdict from the previous step can never be journaled
        # onto this one. An authority column that silently lags by a turn is worse than an empty
        # one: promotion reads it back, so a lagged row would credit the wrong transition.
        current_authority = None

        # NO-PROGRESS GUARD. `expected_next` legitimately contains the CURRENT state (Indeed's
        # questions module spans several pages that are all `indeed_apply_questions`), so a
        # self-loop verifies exactly like real progress: outcome ok + landed in expected_next.
        # Live on 2026-07-19 that let a blocked Continue be clicked 8 times and score 100%
        # autonomous / 100% verified while the page never moved — a perfect-looking treadmill.
        # Landing where you expected is NOT the same as getting somewhere.
        step_delta = observation_delta(armed_bundle, bundle) if armed_bundle is not None else None
        if step_delta is not None and not step_delta.moved:
            no_progress += 1
            if no_progress >= NO_PROGRESS_LIMIT:
                if on_escalate and last_decision is not None:
                    on_escalate(bundle, last_decision)
                return LoopResult(STATUS_STALLED, step,
                                  f"{no_progress} verified actions left the page unchanged "
                                  f"({bundle.state}) — acting without progressing, handing up",
                                  bundle, last_decision, records)
        else:
            no_progress = 0
        armed_bundle = None           # only a VERIFIED action arms the comparison (set below)

        if bundle.done:
            return LoopResult(STATUS_DONE, step, "task complete", bundle, last_decision, records)
        if bundle.human_required:
            dec = decide(bundle, programs=store, model=model,
                         orient=orient)             # yields the escalate decision
            journal(dec, bundle)
            if on_escalate:
                on_escalate(bundle, dec)
            return LoopResult(STATUS_HUMAN, step, bundle.branch_note or "human-required state",
                              bundle, dec, records)

        decision = decide(bundle, programs=store, model=model, orient=orient)
        last_decision = decision

        # --- WHO owns this turn. Computed BEFORE anything acts or hands up, because the answer
        # changes both: an escalation with a seat becomes a park, not a stop.
        current_authority = authority(bundle, decision) if authority is not None else None
        if current_authority is not None and on_authority:
            on_authority(bundle, decision, current_authority)
        mode = current_authority.mode if current_authority else ""

        # --- RED: the executor cannot work this page. Hand the wheel over, BOUNDED.
        if mode == ControlMode.RED.value and seat is not None:
            if takeovers_used >= max_takeovers:
                journal(decision, bundle)
                if on_escalate:
                    on_escalate(bundle, decision)
                return LoopResult(STATUS_TAKEOVER_BUDGET, step,
                                  f"{takeovers_used} bounded takeovers already spent — a drive "
                                  f"that keeps handing the wheel over is being done for, not "
                                  f"taught",
                                  bundle, decision, records)
            takeovers_used += 1
            journal(decision, bundle)
            result = seat.takeover(bundle, decision, current_authority)
            if on_step:
                on_step(bundle, decision, None)
            if result.timed_out:
                if on_escalate:
                    on_escalate(bundle, decision)
                return LoopResult(STATUS_PARK_EXPIRED, step,
                                  f"parked for a takeover and nobody answered: "
                                  f"{current_authority.reason}",
                                  bundle, decision, records)
            if result.aborted:
                return LoopResult(STATUS_ABORTED, step,
                                  result.detail or "teacher aborted during takeover",
                                  bundle, decision, records)
            # Re-OBSERVE and re-decide. The teacher changed the page — that is why they drove —
            # so the decision we were holding is itself stale, and the mode must be recomputed
            # from what is on screen NOW. This is "re-evaluate local control after every teacher
            # action", and it is the line that keeps a detour a detour.
            escalations_in_a_row = 0
            continue

        # --- an escalation: park for a bounded instruction if there is a seat, else hand up
        if decision.escalate:
            instructed: Optional[Decision] = None
            if seat is not None and (current_authority is None
                                     or current_authority.mode != ControlMode.RED.value):
                # ORANGE, or an escalation with no authority wired: ask for ONE bounded action.
                # The teacher supplies the MEANING; the local actuator still performs and verifies
                # it, so the step lands in the journal exactly like every other step (§8).
                instructed = seat.instruct(bundle, decision, current_authority)
            if instructed is None:
                journal(decision, bundle)
                if on_escalate:
                    on_escalate(bundle, decision)
                if on_step:
                    on_step(bundle, decision, None)
                escalations_in_a_row += 1
                if escalations_in_a_row >= 2:
                    return LoopResult(STATUS_ESCALATED, step,
                                      "two consecutive escalations — handing to the "
                                      "teacher/operator",
                                      bundle, decision, records)
                continue
            # A teacher instruction on a state the local system could not resolve is the densest
            # training row there is: the local PREDICTION and the teacher's answer, side by side
            # on the state the student actually reached (§10, and DAgger's whole point). Journal
            # it golden, carrying both halves.
            proposed_for_instruction = decision
            decision = instructed
            last_decision = decision
        else:
            proposed_for_instruction = None
        # NB: escalations_in_a_row is reset only after a VERIFIED action (below), not merely on a
        # non-decide-escalate iteration — otherwise review-escalates and verify-fails could never
        # accumulate to the two-in-a-row stop (they land after this point in the loop).

        # --- propose-approve (DAgger): a decision is reviewed before acting. The decision that
        # actually ACTS (`acting`) may be the proposal or the teacher's correction; a correction
        # journals a GOLDEN row carrying BOTH halves on the controller's own state.
        acting = decision
        proposed_for_golden: Optional[Decision] = proposed_for_instruction
        # WHICH turns get reviewed. `propose_rungs` (who proposed) is kept as a FLOOR so nothing
        # ever gets less review than it did before authority existed; YELLOW adds the turns the
        # TRANSITION has not earned yet, which is the property that actually matters — a compiled
        # rung-0 program replaying a step nobody has ever verified is exactly as unproven as a
        # model guess, and the old rung-keyed gate waved it straight through.
        needs_review = decision.rung in propose_rungs or mode == ControlMode.YELLOW.value
        if reviewer is not None and needs_review:
            review = reviewer(bundle, decision)
            if review.action == ReviewAction.ABORT:
                journal(decision, bundle)
                return LoopResult(STATUS_ABORTED, step, "operator aborted the drive",
                                  bundle, decision, records)
            if review.action == ReviewAction.ESCALATE:
                journal(decision, bundle)
                if on_escalate:
                    on_escalate(bundle, decision)
                escalations_in_a_row += 1
                if escalations_in_a_row >= 2:
                    return LoopResult(STATUS_ESCALATED, step, "escalated at review", bundle,
                                      decision, records)
                continue
            if review.action == ReviewAction.CORRECT and review.correction is not None:
                acting = review.correction
                proposed_for_golden = decision
            last_decision = acting

        # --- a consequential intent: hold it for the operator, never auto-act (gate the ACTING
        # decision — a correction could itself be a Submit). `held_intents` is the ONE way to
        # narrow this, it is per-run, and it defaults to the full set — an operator authorising a
        # submit for one measured drive must not quietly become a system that always submits.
        if acting.intent in held_intents:
            journal(acting, bundle, outcome=None, proposed=proposed_for_golden,
                    golden=bool(proposed_for_golden))
            if on_consequential:
                on_consequential(bundle, acting)
            if on_step:
                on_step(bundle, acting, None)
            return LoopResult(STATUS_CONSEQUENTIAL, step,
                              f"{acting.intent} held for the operator (consequential gate)",
                              bundle, acting, records)

        # --- act through the Interaction API, then verify, then SUPERVISE
        result = actuator.act(acting)
        verified = _verify(acting, result)

        # The supervisor turns `verified: true|false` into a NAMED diagnosis (PLAN_supervisor §3).
        # Rung 0 is pure and free, so it runs on every acting step including the nominal ones —
        # that is what makes the operator commentary cost nothing. It runs BEFORE the BLOCKED
        # check on purpose: a challenge is the loudest thing that can happen, and the narration
        # would be worthless if it went silent exactly there.
        action_delta = action_effect(bundle, result)
        verdict = supervision.classify(
            outcome=result.outcome, verified=verified, delta=action_delta,
            intent=acting.intent, field_name=(acting.params or {}).get("field"),
            state=bundle.state, human_required=bundle.human_required,
            expected_next=tuple(acting.expected_next), landed_state=result.landed_state,
            unanswered_count=len(bundle.unanswered),
        )
        journal(acting, bundle, outcome=result.outcome, landed=result.landed_state,
                verified=verified, cost=result.cost_usd, proposed=proposed_for_golden,
                golden=bool(proposed_for_golden), verdict=verdict, delta=action_delta)
        decision = acting          # downstream stale/escalation logic acts on what actually ran
        if on_supervise:
            on_supervise(bundle, decision, verdict)
        if on_step:
            on_step(bundle, decision, result)

        if result.outcome == Outcome.BLOCKED.value:
            if on_escalate:
                on_escalate(bundle, decision)
            return LoopResult(STATUS_BLOCKED, step, "BLOCKED — challenge/session, hand to human",
                              bundle, decision, records)

        # --- the supervisor's prescription, filled ONLY for a graduated class (S12b). In shadow
        # mode `autonomous_classes` is empty, every play is refused, and this whole block is a
        # no-op that journals why — which is the behaviour until a class earns its promotion.
        # Gated on the VERDICT, not on `verified` — the treadmill is the reason. A blocked
        # Continue verifies (ok + landed in expected_next) while achieving nothing, so
        # `if not verified` would never fire on precisely the failure that motivated all of this.
        # The supervisor's judgment is now the thing that decides something went wrong.
        if not verdict.nominal:
            play = recovery.apply_play(verdict, decision, recovery_actuator,
                                       enabled_classes=autonomous_classes,
                                       already_recovered=recovery_used)
            if on_recover:
                on_recover(bundle, verdict, play)
            if play.retry:
                recovery_used = True
                # Re-OBSERVE and re-DECIDE rather than replaying `decision`: the page changed
                # under us (that is why we recovered), so the old decision is itself stale.
                continue

        # The unexpected-state policy — shared with the login drive so "not where we assumed"
        # is decided identically in both (controller/unexpected.py).
        response = unexpected.respond(result.outcome, verified=verified,
                                      already_retried=stale_retry_used)
        if response is unexpected.Response.CONTINUE:
            stale_retry_used = False
            # Real progress clears the one-shot recovery latch — and "real progress" is the
            # SUPERVISOR's nominal, not `verified`. Keying this on `verified` re-opened the latch
            # on every treadmill turn (the treadmill verifies), which let a play re-fire forever:
            # the same mistake as the gate above, one level down.
            if verdict.nominal:
                recovery_used = False
            escalations_in_a_row = 0        # a verified action breaks any escalation streak
            armed_bundle = bundle           # arm the treadmill check: this action claimed success
            continue
        if response is unexpected.Response.RE_OBSERVE:
            stale_retry_used = True         # re-observe once; a second miss escalates
            continue
        stale_retry_used = False

        # A program step that didn't land marks the program stale so the next visit recompiles.
        if decision.rung == "recipe" and bundle.state:
            programs_mod.mark_stale(bundle.task, bundle.state)
        if on_escalate:
            on_escalate(bundle, decision)
        escalations_in_a_row += 1
        if escalations_in_a_row >= 2:
            return LoopResult(STATUS_ESCALATED, step,
                              "two consecutive verify-fails — handing up", bundle, decision, records)

    return LoopResult(STATUS_MAX_STEPS, max_steps,
                      f"reached max_steps={max_steps} without a terminal state",
                      last_bundle, last_decision, records)
