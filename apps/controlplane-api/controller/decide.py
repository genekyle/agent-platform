"""decide(bundle) -> Decision — the cost-ordered cascade, shaped like `resolve_answer`.

A PURE function: no IO, no HTTP. The model rung is injected as a callable (the `DecisionReasoner`
seam, mirroring `resolve_answer(model=...)`), so swapping Haiku -> local L4 is a deployment
change, never an edit here. Rungs, cheapest first (PLAN_controller_v1 §2):

  rung 0  recipe/program   $0        a compiled IntentProgram for (task, state) -> replay a step
  rung 1  model (Haiku)    ~$0.002   no program: prompt the serialized Bundle, parse a Decision
  rung 2  teacher (Claude) escalate  novel state / stale recipe / low confidence
  rung 3  human            escalate  branch / BLOCKED / consequential gate / floor breached twice

This milestone (M2) implements rung 0 and the escalation surface; rung 1 lands in M3 as a
drop-in behind the `model` seam. The escalation TRIGGERS (outcome-driven) live in the loop.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol

from interaction.decision import (
    DECISION_CONFIDENCE_THRESHOLD,
    Bundle,
    Decision,
)
from controller.programs import IntentProgram


class ProgramLookup(Protocol):
    """What `decide` needs from the program store: get (task, state) -> program|None."""
    def get(self, task: str, state: str) -> Optional[IntentProgram]: ...


class DecisionReasoner(Protocol):
    """Rung 1. A bounded Haiku call, a local L4 model, or a test double — same shape.
    MUST be allowed to return None (no confident decision) rather than guess; a reasoner that
    always answers is a plausible-guess generator, which is worse than an honest escalation."""
    def __call__(self, bundle: Bundle) -> Optional[Decision]: ...


def _escalate(rung: str, rationale: str, bundle: Bundle) -> Decision:
    """A hand-up-the-ladder decision — no action, carries the current state's expectation so a
    downstream verify still has something to check against."""
    return Decision(intent="observe", params={}, confidence=0.0, rung=rung,
                    rationale=rationale, expected_next=tuple(bundle.expected_next),
                    escalate=True)


def _field_of(step: dict[str, Any]) -> Optional[str]:
    p = step.get("params") if isinstance(step.get("params"), dict) else {}
    return p.get("field")


def _rung0(bundle: Bundle, program: IntentProgram) -> Optional[Decision]:
    """Replay the next program step for this state, reading LIVE form truth so the choice is
    re-entrant to Indeed skipping prefilled fields. Returns None (=> escalate) on a guard miss."""
    unanswered = [u.get("field") for u in bundle.unanswered if u.get("field")]

    # Guard: every currently-unanswered field must be one the program knows how to fill. An
    # unanswered field the program never saw means the FORM CHANGED — recompile, don't guess.
    unknown = [f for f in unanswered if f not in set(program.guard_fields)]
    if unknown:
        return None

    # Next field: the first unanswered field with a matching program step. A field-fill stays
    # in the same state, so its expectation is the state itself (verify = the field now took).
    for f in unanswered:
        step = next((s for s in program.steps if _field_of(s) == f), None)
        if step:
            return Decision(intent=step["intent"], params=dict(step.get("params") or {}),
                            confidence=1.0, rung="recipe",
                            rationale=f"program replay: fill {f!r}",
                            expected_next=(bundle.state,) if bundle.state else ())

    # All program fields satisfied -> emit the advance (control) step, which exits the state.
    advance = next((s for s in program.steps if not _field_of(s)), None)
    if advance:
        return Decision(intent=advance["intent"], params=dict(advance.get("params") or {}),
                        confidence=1.0, rung="recipe",
                        rationale="program fields satisfied — advance",
                        expected_next=tuple(program.expected_exit))
    return None


def decide(bundle: Bundle, *, programs: ProgramLookup,
           model: Optional[DecisionReasoner] = None) -> Decision:
    """One Decision for one Bundle. Cheapest confident rung wins; below confidence, ask."""
    # Short-circuits — these are not decisions, they are stops/hand-ups.
    if bundle.done:
        return _escalate("recipe", "task is complete — nothing to decide", bundle)
    if bundle.human_required:
        return _escalate("human", bundle.branch_note or "human-required state", bundle)
    if bundle.is_branch:
        return _escalate("human", f"branch state ({bundle.branch_note or 'off-spine'})", bundle)
    if not bundle.state:
        return _escalate("teacher", "unknown state — no recipe to map, teach it", bundle)

    # Rung 0 — a compiled program for this state.
    program = programs.get(bundle.task, bundle.state)
    if program is not None and not program.stale:
        decision = _rung0(bundle, program)
        if decision is not None:
            return decision
        # Guard miss / no coverage: fall through to the model, then escalation.

    # Rung 1 — the model seam (M3). No model wired => rung 2, honestly.
    if model is not None:
        proposed = model(bundle)
        if proposed is None:
            return _escalate("model", "reasoner returned no confident decision", bundle)
        if proposed.confidence < DECISION_CONFIDENCE_THRESHOLD:
            # Keep the proposal visible in the rationale, but escalate — ask, don't guess.
            return Decision(intent=proposed.intent, params=proposed.params,
                            confidence=proposed.confidence, rung="model",
                            rationale=f"{proposed.rationale} (confidence "
                                      f"{proposed.confidence:.2f} < {DECISION_CONFIDENCE_THRESHOLD} "
                                      f"— ask, don't guess)",
                            expected_next=proposed.expected_next, escalate=True)
        return proposed

    # No program, no model — a novel state the teacher must resolve (which produces a program).
    reason = "no compiled program for this state" + (
        " (program stale — recompile)" if (program is not None and program.stale)
        else " and no model wired")
    return _escalate("teacher", reason, bundle)
