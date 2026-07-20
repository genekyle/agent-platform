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

from interaction.contract import Intent, Outcome
from interaction.decision import Bundle, Decision, DecisionRecord
from interaction.decision_journal import log_decision, record_for
from controller import programs as programs_mod
from controller import unexpected
from controller.decide import DecisionReasoner, ProgramLookup, decide
from controller.teach import PROPOSE_RUNGS, ReviewAction, Reviewer

MAX_STEPS = 40

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


class Actuator(Protocol):
    """The loop's window onto the live tab. observe() composes a Bundle (fetching page_text,
    scan_required, and the journal tail itself); act() drives one Decision through the
    Interaction API and reports the outcome + where it landed."""
    def observe(self) -> Bundle: ...
    def act(self, decision: Decision) -> ActOutcome: ...


#: Consecutive VERIFIED actions that leave the page byte-identical before we call it a stall.
#: One is normal (a field fill doesn't move the page); two in a row means the thing we keep
#: "successfully" doing is achieving nothing.
NO_PROGRESS_LIMIT = 2


def progress_signature(bundle: Bundle) -> tuple:
    """What "the page moved" means, mechanically: where we are, what state that is, and what is
    still unanswered. Compared between steps to tell PROGRESS from a treadmill."""
    return (bundle.url, bundle.state,
            tuple(sorted(str(u.get("field")) for u in (bundle.unanswered or ()))))


# Loop statuses — every one is a definite verdict, never an ambiguous "ran out".
STATUS_DONE = "done"
STATUS_STALLED = "stalled"
STATUS_MAX_STEPS = "max_steps"
STATUS_HUMAN = "human_required"
STATUS_BLOCKED = "blocked"
STATUS_ESCALATED = "escalated"
STATUS_CONSEQUENTIAL = "consequential_gate"
STATUS_ABORTED = "aborted"


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
) -> LoopResult:
    """Drive one task to a definite stop. Every step journals a DecisionRecord.

    Stops on: `done`, a human-required state, a consequential gate (Submit), BLOCKED,
    two escalations in a row, or `max_steps`. Never auto-solves a challenge — a BLOCKED
    outcome or a human_required state hands straight to the operator.
    """
    store = programs or programs_mod.ProgramStore()
    records: list[DecisionRecord] = []
    escalations_in_a_row = 0
    stale_retry_used = False
    no_progress = 0
    last_progress_sig: Optional[tuple] = None
    last_bundle: Optional[Bundle] = None
    last_decision: Optional[Decision] = None

    def journal(dec: Decision, bundle: Bundle, *, outcome: Optional[str] = None,
                landed: Optional[str] = None, verified: Optional[bool] = None,
                cost: float = 0.0, proposed: Optional[Decision] = None,
                golden: bool = False) -> None:
        rec = record_for(dec, bundle, outcome=outcome, landed_state=landed, verified=verified,
                         session_id=session_id, cost_usd=cost, proposed=proposed, golden=golden)
        saved = log_decision(rec)
        records.append(saved or rec)

    for step in range(max_steps):
        bundle = actuator.observe()
        last_bundle = bundle

        # NO-PROGRESS GUARD. `expected_next` legitimately contains the CURRENT state (Indeed's
        # questions module spans several pages that are all `indeed_apply_questions`), so a
        # self-loop verifies exactly like real progress: outcome ok + landed in expected_next.
        # Live on 2026-07-19 that let a blocked Continue be clicked 8 times and score 100%
        # autonomous / 100% verified while the page never moved — a perfect-looking treadmill.
        # Landing where you expected is NOT the same as getting somewhere.
        sig = progress_signature(bundle)
        if last_progress_sig is not None and sig == last_progress_sig:
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
        last_progress_sig = None      # only a VERIFIED action arms the comparison (set below)

        if bundle.done:
            return LoopResult(STATUS_DONE, step, "task complete", bundle, last_decision, records)
        if bundle.human_required:
            dec = decide(bundle, programs=store, model=model)  # yields the escalate decision
            journal(dec, bundle)
            if on_escalate:
                on_escalate(bundle, dec)
            return LoopResult(STATUS_HUMAN, step, bundle.branch_note or "human-required state",
                              bundle, dec, records)

        decision = decide(bundle, programs=store, model=model)
        last_decision = decision

        # --- an escalation: hand up, don't act
        if decision.escalate:
            journal(decision, bundle)
            if on_escalate:
                on_escalate(bundle, decision)
            if on_step:
                on_step(bundle, decision, None)
            escalations_in_a_row += 1
            if escalations_in_a_row >= 2:
                return LoopResult(STATUS_ESCALATED, step,
                                  "two consecutive escalations — handing to the teacher/operator",
                                  bundle, decision, records)
            continue
        # NB: escalations_in_a_row is reset only after a VERIFIED action (below), not merely on a
        # non-decide-escalate iteration — otherwise review-escalates and verify-fails could never
        # accumulate to the two-in-a-row stop (they land after this point in the loop).

        # --- propose-approve (DAgger): a non-recipe decision is reviewed before acting. The
        # decision that actually ACTS (`acting`) may be the proposal or the teacher's correction;
        # a correction journals a GOLDEN row carrying BOTH halves on the controller's own state.
        acting = decision
        proposed_for_golden: Optional[Decision] = None
        if reviewer is not None and decision.rung in propose_rungs:
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
        # decision — a correction could itself be a Submit).
        if acting.intent in CONSEQUENTIAL_INTENTS:
            journal(acting, bundle, outcome=None, proposed=proposed_for_golden,
                    golden=bool(proposed_for_golden))
            if on_consequential:
                on_consequential(bundle, acting)
            if on_step:
                on_step(bundle, acting, None)
            return LoopResult(STATUS_CONSEQUENTIAL, step,
                              f"{acting.intent} held for the operator (consequential gate)",
                              bundle, acting, records)

        # --- act through the Interaction API, then verify
        result = actuator.act(acting)
        verified = _verify(acting, result)
        journal(acting, bundle, outcome=result.outcome, landed=result.landed_state,
                verified=verified, cost=result.cost_usd, proposed=proposed_for_golden,
                golden=bool(proposed_for_golden))
        decision = acting          # downstream stale/escalation logic acts on what actually ran
        if on_step:
            on_step(bundle, decision, result)

        if result.outcome == Outcome.BLOCKED.value:
            if on_escalate:
                on_escalate(bundle, decision)
            return LoopResult(STATUS_BLOCKED, step, "BLOCKED — challenge/session, hand to human",
                              bundle, decision, records)

        # The unexpected-state policy — shared with the login drive so "not where we assumed"
        # is decided identically in both (controller/unexpected.py).
        response = unexpected.respond(result.outcome, verified=verified,
                                      already_retried=stale_retry_used)
        if response is unexpected.Response.CONTINUE:
            stale_retry_used = False
            escalations_in_a_row = 0        # a verified action breaks any escalation streak
            last_progress_sig = sig         # arm the treadmill check: this action claimed success
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
