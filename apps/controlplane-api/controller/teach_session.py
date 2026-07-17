"""TeachSession — the teacher in the loop, one turn at a time (the §9-aligned teaching drive).

`run_controller` runs to completion with an injected reviewer — right for autonomous and scripted
runs, but a LIVE teaching drive needs the TEACHER (Claude / the operator / a cockpit panel) to
author each non-program decision turn by turn, and `run_controller`'s `cli_reviewer` blocks on
stdin (a human terminal) which the teacher-as-Claude can't drive. This exposes the loop BODY as two
calls so the teacher sits in the seat:

  propose() -> TeachStep : observe the live tab, run decide() (a rung-0 program step, or an escalate
                           = "teacher, you're up"), and compute the Haiku BACKSTOP's shadow (what the
                           cheap model would do — PRINCIPLES §9: Haiku shadows, it does not act here).
                           Nothing is acted.
  commit(decision)       : act the teacher-authored (or approved) decision through the actuator,
                           verify, and journal the paired row (teacher = what happened, shadow =
                           `proposed_*`) so `metrics.shadow_agreement` reads it directly. A SUBMIT /
                           consequential intent is HELD for the operator, never acted.

This is the DAgger loop with the STUDENT's teacher in the seat and Haiku demoted to the shadow
baseline — the §9-aligned default (teacher-demonstrates / Haiku-shadows), not Haiku-proposes. It is
also the backend a cockpit propose-approve panel calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from interaction.decision import Bundle, Decision
from interaction.decision_journal import log_decision, record_for
from controller import programs as programs_mod
from controller.decide import DecisionReasoner, ProgramLookup, decide
from controller.loop import CONSEQUENTIAL_INTENTS, ActOutcome, _verify


class _ActuatorLike(Protocol):
    def observe(self) -> Bundle: ...
    def act(self, decision: Decision) -> ActOutcome: ...


@dataclass
class TeachStep:
    """What propose() shows the teacher — the state, decide()'s call, and the backstop's shadow."""
    bundle: Bundle
    decision: Decision            # decide(): a rung-0 program step, or an escalate = "teacher, you're up"
    shadow: Optional[Decision]    # the Haiku backstop's would-be decision (None if no real backstop)
    needs_review: bool            # False only for a trusted rung-0 program step


@dataclass
class CommitResult:
    held: bool                    # True = a consequential intent (Submit) held for the operator
    outcome: Optional[str] = None
    landed_state: Optional[str] = None
    verified: Optional[bool] = None
    agreed: Optional[bool] = None # did the Haiku shadow match what the teacher did? (feeds the metric)
    golden: bool = False
    detail: str = ""


class TeachSession:
    """Drive a live teaching turn. §9: the TEACHER decides; Haiku SHADOWS (a free-to-collect
    baseline once the caller opts into the ~$0.002/step model call). The journaled row is paired by
    construction (teacher = what happened, shadow = `proposed_*`). SUBMIT is never acted here."""

    def __init__(self, actuator: _ActuatorLike, *, programs: Optional[ProgramLookup] = None,
                 shadow_model: Optional[DecisionReasoner] = None, session_id: str = ""):
        self.actuator = actuator
        self.programs = programs or programs_mod.ProgramStore()
        self.shadow_model = shadow_model   # a HaikuReasoner (or any DecisionReasoner); SHADOW ONLY
        self.session_id = session_id
        self._bundle: Optional[Bundle] = None
        self._shadow: Optional[Decision] = None

    def propose(self) -> TeachStep:
        bundle = self.actuator.observe()
        self._bundle = bundle
        # No acting model: rung-0 program if one exists for this state, else an escalate — the
        # teacher's cue to demonstrate. The teacher, not Haiku, is the acting reasoner (§9).
        decision = decide(bundle, programs=self.programs, model=None)

        shadow: Optional[Decision] = None
        if not bundle.done and not bundle.human_required:
            # The backstop's take: Haiku if the caller opted in, else the free deterministic shadow.
            # Only a REAL decision is a useful comparison — an escalate placeholder (no program, no
            # Haiku) is not journaled as a proposal, or every row would "disagree" against `observe`.
            s = decide(bundle, programs=self.programs, model=self.shadow_model)
            shadow = s if not s.escalate else None
        self._shadow = shadow

        needs_review = decision.escalate or decision.rung != "recipe"
        return TeachStep(bundle=bundle, decision=decision, shadow=shadow, needs_review=needs_review)

    def commit(self, decision: Decision, *, golden_over: Optional[Decision] = None) -> CommitResult:
        """Act the teacher-authored (or approved) `decision`, verify, and journal the paired row.

        The Haiku shadow captured in propose() is journaled as `proposed_*` (shadow=True). Pass
        `golden_over` to mark an explicit correction of a shown proposal (golden=True) — a
        disagreement by construction, and the highest-value row in the corpus. SUBMIT is HELD."""
        if self._bundle is None:
            raise RuntimeError("commit() before propose() — observe first")
        bundle, shadow = self._bundle, self._shadow
        proposed = golden_over or shadow

        if decision.intent in CONSEQUENTIAL_INTENTS:
            self._journal(decision, bundle, proposed=proposed, golden=bool(golden_over), outcome=None)
            return CommitResult(held=True, golden=bool(golden_over),
                                detail=f"{decision.intent} held for the operator (consequential gate)")

        result = self.actuator.act(decision)
        verified = _verify(decision, result)
        agreed = None
        if proposed is not None:
            agreed = (proposed.intent == decision.intent and
                      (proposed.params or {}).get("field") == (decision.params or {}).get("field"))
        self._journal(decision, bundle, proposed=proposed, golden=bool(golden_over),
                      outcome=result.outcome, landed=result.landed_state, verified=verified,
                      cost=result.cost_usd)
        return CommitResult(held=False, outcome=result.outcome, landed_state=result.landed_state,
                            verified=verified, agreed=agreed, golden=bool(golden_over),
                            detail=result.detail)

    def _journal(self, decision: Decision, bundle: Bundle, *, proposed: Optional[Decision] = None,
                 golden: bool = False, outcome: Optional[str] = None, landed: Optional[str] = None,
                 verified: Optional[bool] = None, cost: float = 0.0) -> None:
        # A passive comparison is a shadow row; an explicit correction is a golden row. Both carry
        # `proposed_*`, so both feed shadow_agreement; the flags only categorise (summarize()).
        rec = record_for(decision, bundle, proposed=proposed,
                         shadow=(proposed is not None and not golden), golden=golden,
                         outcome=outcome, landed_state=landed, verified=verified,
                         session_id=self.session_id, cost_usd=cost)
        log_decision(rec)


def open_live_teach_session(*, browser_url: str, tab_id: str, task: str = "indeed_apply",
                            capture_server_url: Optional[str] = None, shadow_haiku: bool = False,
                            budget_limit: Optional[float] = None, record_only: bool = False,
                            session_id: str = "", transport=None) -> TeachSession:
    """Open a live teaching session (the §9-aligned entrypoint): a `LiveActuator` + optionally Haiku
    as the SHADOW backstop. The teacher drives turn by turn via `propose()` / `commit()`.

    `shadow_haiku=True` opts into the ~$0.002/step baseline (Haiku shadows — computes a decision for
    the agreement metric, never acts). Default off keeps a teaching drive free and Haiku-silent."""
    from settings import settings
    from controller.live_actuator import LiveActuator

    base = capture_server_url or settings.capture_server_url
    actuator = LiveActuator(base_url=base, browser_url=browser_url, tab_id=tab_id, task=task,
                            driver=("record_only" if record_only else "direct"), transport=transport)
    shadow_model = None
    if shadow_haiku:
        from controller.reason import HaikuReasoner
        shadow_model = HaikuReasoner(budget_limit=budget_limit, purpose="controller_shadow")
    return TeachSession(actuator, shadow_model=shadow_model, session_id=session_id)
