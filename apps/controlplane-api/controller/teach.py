"""Propose-approve teaching — the DAgger loop (PLAN_controller_v1 §4).

Why propose-approve and not "watch Claude drive": pure behaviour cloning from teacher logs fails
on distribution shift — the student visits states the teacher never did and errors compound. The
proven fix is DAgger: the STUDENT drives, the teacher CORRECTS at the point of disagreement, and
the corrections are collected on the student's OWN state distribution. That is the only data that
fixes the shift, and it is exactly what this gate produces.

The gate as a loop policy: rung-0 (`recipe`) decisions act freely — they are verified programs.
Every `model`/`teacher` decision is PROPOSED to a `Reviewer` first: approve → act; correct → act on
the correction AND journal a GOLDEN row carrying BOTH the proposal and the correction; escalate →
hand up; abort → stop. Golden rows are the highest-value rows in the corpus: mechanically
distinguishable (`golden=True`), and the proposed half is never overwritten.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol

from interaction.decision import Bundle, Decision

#: The rungs whose decisions must be reviewed before acting. Rung 0 (recipe) is trusted;
#: everything non-deterministic is proposed.
PROPOSE_RUNGS = frozenset({"model", "teacher"})


class ReviewAction(str, Enum):
    APPROVE = "approve"     # act on the proposed decision as-is
    CORRECT = "correct"     # act on `correction` instead; journal a golden {proposed, corrected}
    ESCALATE = "escalate"   # don't act — hand up the ladder
    ABORT = "abort"         # stop the drive


@dataclass
class Review:
    action: ReviewAction
    correction: Optional[Decision] = None   # required when action == CORRECT


class Reviewer(Protocol):
    """Decides the fate of a proposed decision. The operator, Claude driving the cockpit, or a
    scripted test double — same shape."""
    def __call__(self, bundle: Bundle, decision: Decision) -> Review: ...


# --- convenience constructors -------------------------------------------------------
def approve() -> Review:
    return Review(ReviewAction.APPROVE)


def correct(new_decision: Decision) -> Review:
    """A correction is a FULL replacement Decision, teacher-authored through the same contract —
    never a side-channel hand-fix in the browser (that is the event-log mistake again). It is
    re-stamped rung='teacher' so the corpus shows who really decided."""
    from dataclasses import replace
    return Review(ReviewAction.CORRECT, correction=replace(new_decision, rung="teacher"))


def escalate() -> Review:
    return Review(ReviewAction.ESCALATE)


def abort() -> Review:
    return Review(ReviewAction.ABORT)


def scripted_reviewer(script: list[Review]) -> Reviewer:
    """A reviewer that returns pre-programmed reviews in order (tests). Runs out -> APPROVE."""
    seq = list(script)
    idx = {"i": 0}

    def _review(bundle: Bundle, decision: Decision) -> Review:
        i = idx["i"]
        idx["i"] += 1
        return seq[i] if i < len(seq) else approve()

    return _review


def cli_reviewer(*, input_fn=input, print_fn=print) -> Reviewer:
    """A terminal propose-approve prompt — the surface that ships this session (a cockpit panel is
    optional polish; the contract is the deliverable). Renders the proposal, reads a verdict:

        [enter]/a = approve   c = correct   e = escalate   q = abort

    A correction reads the replacement intent + field/value/control on the same contract, so the
    teacher's fix is a Decision, not a hand-edit.
    """
    def _review(bundle: Bundle, decision: Decision) -> Review:
        print_fn("\n── PROPOSED ─────────────────────────────────────────────")
        print_fn(f"  state:      {bundle.state}  ({bundle.ats})")
        print_fn(f"  rung:       {decision.rung}   confidence: {decision.confidence:.2f}")
        print_fn(f"  intent:     {decision.intent}  {decision.params}")
        print_fn(f"  rationale:  {decision.rationale}")
        print_fn(f"  expects →   {', '.join(decision.expected_next) or '(unknown)'}")
        verdict = (input_fn("  approve [enter] · (c)orrect · (e)scalate · (q)abort > ") or "a").strip().lower()
        if verdict in ("", "a", "approve"):
            return approve()
        if verdict in ("e", "escalate"):
            return escalate()
        if verdict in ("q", "abort", "quit"):
            return abort()
        if verdict in ("c", "correct"):
            intent = input_fn("    corrected intent > ").strip()
            field = input_fn("    field (blank if none) > ").strip() or None
            value = input_fn("    value (blank if none) > ").strip() or None
            control = input_fn("    control name (blank if none) > ").strip() or None
            # §10 — the Open Brain: capture the teacher's REAL reasoning, not a stub. This used to be
            # hardcoded "operator correction", so every human correction taught WHAT to do with no
            # WHY — the highest-value rows in the corpus arrived reasoning-blank. Pass what's typed
            # verbatim (an empty "why" journals empty and gets flagged by the reasoned_rate metric —
            # honest beats a placeholder that masquerades as content).
            why = input_fn("    why? (one line — this is the training signal) > ").strip()
            params: dict = {}
            if field:
                params["field"] = field
            if value:
                params["value"] = value
            if control:
                params["control"] = control
            return correct(Decision(intent=intent, params=params, confidence=1.0, rung="teacher",
                                    rationale=why, expected_next=decision.expected_next))
        return approve()

    return _review
