"""Shadow mode — the controller decides SILENTLY beside the teacher, for the agreement metric.

On every teacher step, compute `decide(bundle)` WITHOUT acting and journal it paired with what
the teacher actually did (`shadow=True`, teacher = the row's decision, controller = `proposed_*`).
`metrics.shadow_agreement` reads exactly those pairs. Rung-0 shadows are FREE (deterministic);
the model rung costs money, so it is OFF by default here — a shadow drive must not spend unless
the caller explicitly opts in and accepts the sampling.
"""

from __future__ import annotations

from typing import Optional

from controller.decide import DecisionReasoner, ProgramLookup, decide
from controller import programs as programs_mod
from interaction.decision import Bundle, Decision
from interaction.decision_journal import log_decision, record_for


def _free_default_model() -> Optional[DecisionReasoner]:
    """The precedent rung as the shadow's default seat-holder (PLAN_inhouse_reasoner_v1 §11
    item 2). $0 by construction — retrieval over vectors.db, no API call — so it does NOT
    violate this module's spend rule the way Haiku would; it is the free rung the rule was
    protecting. `settings.precedent_shadow` turns it off; any failure means None, honestly."""
    try:
        from settings import settings

        if not settings.precedent_shadow:
            return None
        from precedent.engine import reasoner

        return reasoner()
    except Exception:  # noqa: BLE001 — a dead seat shadows as an empty seat, never an error
        return None


def shadow_decision(bundle: Bundle, *, programs: Optional[ProgramLookup] = None,
                    model: Optional[DecisionReasoner] = None) -> Decision:
    """What the controller WOULD decide here — computed, never acted. With no model passed,
    the FREE precedent rung fills the seat (the paid Haiku rung still requires opting in)."""
    if model is None:
        model = _free_default_model()
    return decide(bundle, programs=programs or programs_mod.ProgramStore(), model=model)


def shadow_step(teacher_decision: Decision, bundle: Bundle, *,
                programs: Optional[ProgramLookup] = None,
                model: Optional[DecisionReasoner] = None,
                session_id: str = "", outcome: Optional[str] = None) -> Decision:
    """Journal one shadow comparison beside a teacher step, and return the controller's decision.

    The row records the TEACHER's decision as what happened and the CONTROLLER's as the proposal,
    so it is a paired agreement row by construction. `model=None` (default) keeps it free.
    """
    controller = shadow_decision(bundle, programs=programs, model=model)
    rec = record_for(teacher_decision, bundle, proposed=controller, shadow=True,
                     outcome=outcome, session_id=session_id)
    log_decision(rec)
    return controller
