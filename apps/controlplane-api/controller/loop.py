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

from interaction.contract import STALE_STATE_OUTCOMES, Intent, Outcome
from interaction.decision import Bundle, Decision, DecisionRecord
from interaction.decision_journal import log_decision, record_for
from controller import programs as programs_mod
from controller.decide import DecisionReasoner, ProgramLookup, decide

MAX_STEPS = 40

#: Intents the loop NEVER auto-acts — it holds them for the operator. Submit ends an
#: application (irreversible, consequential); "apply = done only when SUBMITTED", and the
#: final Submit is the operator's, always (SESSION_02 DoD, and the apply preferences).
CONSEQUENTIAL_INTENTS = frozenset({Intent.SUBMIT.value})

_STALE = frozenset(o.value for o in STALE_STATE_OUTCOMES)


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


# Loop statuses — every one is a definite verdict, never an ambiguous "ran out".
STATUS_DONE = "done"
STATUS_MAX_STEPS = "max_steps"
STATUS_HUMAN = "human_required"
STATUS_BLOCKED = "blocked"
STATUS_ESCALATED = "escalated"
STATUS_CONSEQUENTIAL = "consequential_gate"


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
    last_bundle: Optional[Bundle] = None
    last_decision: Optional[Decision] = None

    def journal(dec: Decision, bundle: Bundle, *, outcome: Optional[str] = None,
                landed: Optional[str] = None, verified: Optional[bool] = None,
                cost: float = 0.0) -> None:
        rec = record_for(dec, bundle, outcome=outcome, landed_state=landed, verified=verified,
                         session_id=session_id, cost_usd=cost)
        saved = log_decision(rec)
        records.append(saved or rec)

    for step in range(max_steps):
        bundle = actuator.observe()
        last_bundle = bundle

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
        escalations_in_a_row = 0

        # --- a consequential intent: hold it for the operator, never auto-act
        if decision.intent in CONSEQUENTIAL_INTENTS:
            journal(decision, bundle, outcome=None)
            if on_consequential:
                on_consequential(bundle, decision)
            if on_step:
                on_step(bundle, decision, None)
            return LoopResult(STATUS_CONSEQUENTIAL, step,
                              f"{decision.intent} held for the operator (consequential gate)",
                              bundle, decision, records)

        # --- act through the Interaction API, then verify
        result = actuator.act(decision)
        verified = _verify(decision, result)
        journal(decision, bundle, outcome=result.outcome, landed=result.landed_state,
                verified=verified, cost=result.cost_usd)
        if on_step:
            on_step(bundle, decision, result)

        if result.outcome == Outcome.BLOCKED.value:
            if on_escalate:
                on_escalate(bundle, decision)
            return LoopResult(STATUS_BLOCKED, step, "BLOCKED — challenge/session, hand to human",
                              bundle, decision, records)

        if verified:
            stale_retry_used = False
            continue

        # verify failed. A stale-state outcome means "re-observe before retrying" — do it ONCE.
        if result.outcome in _STALE and not stale_retry_used:
            stale_retry_used = True
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
