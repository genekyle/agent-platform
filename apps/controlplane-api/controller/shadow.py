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


def shadow_decision(bundle: Bundle, *, programs: Optional[ProgramLookup] = None,
                    model: Optional[DecisionReasoner] = None) -> Decision:
    """What the controller WOULD decide here — computed, never acted."""
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
