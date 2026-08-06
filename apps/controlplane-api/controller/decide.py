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

from collections.abc import Sequence
from typing import Any, Optional, Protocol

from interaction.contract import Intent
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


#: What `/scan_required` actually puts in a row's `kind` — a react-select, a lowercased tagName,
#: or a synthesised group. Taken from the scanner's own JS (`protocols.SCAN_REQUIRED_JS`) rather
#: than from imagination, so a guess made here is a guess about a shape that really occurs.
_KIND_TO_INTENT: dict[str, str] = {
    "react_select": Intent.SELECT_OPTION.value,
    "select": Intent.SELECT_OPTION.value,
    "checkbox_group": Intent.CHECK_GROUP.value,
    "radio_group": Intent.CHECK_GROUP.value,
    "textarea": Intent.SET_TEXT.value,
    "input": Intent.SET_TEXT.value,
}

#: Control names that ADVANCE a form, cheapest-first. Used only to form a guess on a state with
#: nothing left unanswered. `Submit` is deliberately absent: the guess must never propose the one
#: irreversible action, even marked escalate — a proposal is a thing a teacher can approve.
_ADVANCE_CONTROLS = ("Continue", "Save and Continue", "Next", "Review your application", "Save")

#: How much a guess made from form shape alone is worth. Below `DECISION_CONFIDENCE_THRESHOLD` by
#: construction — this is a PREDICTION TO BE SCORED, never a bid to act. Calibration is the whole
#: point: a hand-up that claimed 0.9 would be a liar, and one that claims 0.0 (the old behaviour)
#: is unscoreable. These say "here is my guess, and here is how little I'd stake on it".
PREDICTION_CONFIDENCE_FIELD = 0.35
PREDICTION_CONFIDENCE_ADVANCE = 0.30
PREDICTION_CONFIDENCE_NONE = 0.0


def local_prediction(bundle: Bundle) -> tuple[str, dict, float, str, tuple[str, ...]]:
    """The best guess the LOCAL layers can make with no program and no model — `(intent, params,
    confidence, why, evidence)`.

    This exists so that no escalation is information-free. Before it, `decide()` handed up
    `observe`/0.0 with no hypothesis, which meant the inner system was never scored on the turns
    the teacher was paid for: `shadow_agreement` had nothing to compare. Now every hand-up carries
    a real proposal, so an escalation answers four separate questions — did we name the state? did
    we pick the right field? the right verb? or did we only fail to ground it? — and the answers
    are exactly the DAgger signal.

    It guesses from FORM SHAPE only, never from meaning. It will not invent an answer value: the
    `answer` axis belongs to `resolve_answer` and, past that, to the human.
    """
    unanswered = [u for u in bundle.unanswered if u.get("field")]
    if unanswered:
        first = unanswered[0]
        field = first.get("field")
        kind = str(first.get("kind") or "").lower()
        intent = _KIND_TO_INTENT.get(kind, Intent.DESCRIBE.value)
        why = (f"{field!r} is the first unanswered required field and scans as {kind or 'unknown'}"
               f" — so {intent} is the shape of the next step"
               + ("" if intent != Intent.DESCRIBE.value
                  else "; the shape is unfamiliar, so describe it before touching it"))
        return (intent, {"field": field}, PREDICTION_CONFIDENCE_FIELD, why,
                ("state", "unanswered[0].field", "unanswered[0].kind"))

    actual = advance_control(bundle.ax_identities)
    if actual:
        return (Intent.CLICK.value, {"control": actual}, PREDICTION_CONFIDENCE_ADVANCE,
                f"no required field is unanswered and {actual!r} is on the page — the step "
                f"looks complete and this is the control that advances it",
                ("state", "unanswered", "ax_identities"))

    return (Intent.OBSERVE.value, {}, PREDICTION_CONFIDENCE_NONE,
            "nothing unanswered and no advance control found — no local guess is available",
            ("state", "unanswered"))


def advance_control(ax_identities: Sequence[str]) -> str:
    """The control on this page that advances the form, AS THE PAGE RENDERS IT — or "".

    Public because the apply ladder's tail needs the same answer this module's prediction needs,
    and a second lexicon in the router would be a second opinion about which button moves a form
    on. `Submit` stays out of it by construction (see `_ADVANCE_CONTROLS`): the one irreversible
    control is never something a lexicon match may reach for.

    Accepts `role|name` identities or bare names, so both a Bundle and a raw AX scan can ask.

    Two rules, both learned the hard way:
      * **Return the label the page renders**, not the lexicon entry that matched it — "Continue"
        is a substring of "Save and Continue", and proposing the lexicon hands back a control name
        the page does not have.
      * **Longest match wins.** On a page carrying both, the specific one is the real advance
        control and the bare one is a substring of it.
    """
    names = [ident.partition("|")[2].strip() if "|" in ident else ident.strip()
             for ident in (ax_identities or ())]
    names = [n for n in names if n]
    for control in _ADVANCE_CONTROLS:
        matches = [n for n in names if control.lower() in n.lower()]
        if matches:
            return max(matches, key=len)
    return ""


class Orienter(Protocol):
    """`(bundle) -> Decision | None` — a richer local prediction for a landing page than form
    shape can give. `controller/orientation.py` is the live one; injected so this module keeps its
    no-IO, no-registry purity and a test can pass a lambda."""
    def __call__(self, bundle: Bundle) -> Optional[Decision]: ...


def _handup(rung: str, axis: str, rationale: str, bundle: Bundle,
            orient: Optional[Orienter] = None) -> Decision:
    """Hand up the ladder — carrying the local system's best PREDICTION, not a blank.

    `escalate=True` still means nothing is acted. What changes is that the row is now scoreable:
    the teacher's answer lands beside a real proposal, so a correction teaches a boundary instead
    of filling a vacuum (§10 — the contrast on a correction is the densest signal we have).

    `human_required` and `branch` are the exception and deliberately so: on a sign-in wall or an
    off-spine page a "best guess at the next action" is not a prediction, it is a suggestion to do
    something the agent must never do. Those hand up empty, and that emptiness is correct.
    """
    if axis in ("human_required", "branch", "task_complete"):
        return Decision(intent=Intent.OBSERVE.value, params={}, confidence=0.0, rung=rung,
                        rationale=rationale, expected_next=tuple(bundle.expected_next),
                        escalate=True, escalation_axis=axis,
                        evidence=("state",))
    # A landing page is the one case where something better than form shape is available: which
    # SITE is this, and where is Apply. Tried first because on the deep end there is usually no
    # form to read shape from yet, so the shape guess would return `observe` and teach nothing.
    if orient is not None:
        oriented = orient(bundle)
        if oriented is not None:
            return Decision(intent=oriented.intent, params=oriented.params,
                            confidence=oriented.confidence, rung=rung,
                            rationale=f"{rationale}; local guess: {oriented.rationale}",
                            expected_next=tuple(bundle.expected_next), escalate=True,
                            escalation_axis=axis, evidence=oriented.evidence)
    intent, params, confidence, why, evidence = local_prediction(bundle)
    return Decision(intent=intent, params=params, confidence=confidence, rung=rung,
                    rationale=f"{rationale}; local guess: {why}",
                    expected_next=tuple(bundle.expected_next), escalate=True,
                    escalation_axis=axis, evidence=evidence)


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
           model: Optional[DecisionReasoner] = None,
           orient: Optional[Orienter] = None) -> Decision:
    """One Decision for one Bundle. Cheapest confident rung wins; below confidence, ask."""
    # Short-circuits — these are not decisions, they are stops/hand-ups.
    if bundle.done:
        return _handup("recipe", "task_complete", "task is complete — nothing to decide", bundle)
    if bundle.human_required:
        return _handup("human", "human_required",
                       bundle.branch_note or "human-required state", bundle)
    if bundle.is_branch:
        return _handup("human", "branch",
                       f"branch state ({bundle.branch_note or 'off-spine'})", bundle)
    if not bundle.state:
        return _handup("teacher", "unknown_state",
                       "unknown state — no recipe to map, teach it", bundle, orient)

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
            return _handup("model", "model_declined",
                           "reasoner returned no confident decision", bundle, orient)
        if proposed.confidence < DECISION_CONFIDENCE_THRESHOLD:
            # Keep the proposal visible AND acting-shaped, but escalate — ask, don't guess. The
            # model's own guess beats a shape-based one, so it is what the teacher gets to score.
            return Decision(intent=proposed.intent, params=proposed.params,
                            confidence=proposed.confidence, rung="model",
                            rationale=f"{proposed.rationale} (confidence "
                                      f"{proposed.confidence:.2f} < "
                                      f"{DECISION_CONFIDENCE_THRESHOLD} — ask, don't guess)",
                            expected_next=proposed.expected_next, escalate=True,
                            escalation_axis="low_confidence", evidence=proposed.evidence)
        return proposed

    # No program, no model — a novel state the teacher must resolve (which produces a program).
    stale = program is not None and program.stale
    return _handup("teacher", "stale_program" if stale else "no_program",
                   "no compiled program for this state"
                   + (" (program stale — recompile)" if stale else " and no model wired"),
                   bundle, orient)
