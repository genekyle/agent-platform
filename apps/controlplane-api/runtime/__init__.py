"""The runtime loop — drives the five per-step stages end-to-end.

The stages exist as independent units (classify=escalation_rules, propose=mcp
AX proposer, select=select_stage.selector, act=mcp executor, verify=
select_stage.verifier). This package is the orchestrator that runs them as a loop:

    classify → propose → select → act → verify → repeat

It is deliberately PORT-BASED: the side-effecting stages (observe the live page,
perform an action) are injected as `Proposer` / `Actor` so the loop logic is pure
and fully testable without a browser. The default `Actor` is record-only — it logs
the decided intent and executes nothing — which is the safe mode for the first
live runs and for supervised data collection.
"""

from .loop import (  # noqa: F401
    Actor,
    Gate,
    LoopResult,
    LoopStatus,
    Observation,
    Proposer,
    RecordOnlyActor,
    StepRecord,
    StepStatus,
    run_loop,
)
from .live import (  # noqa: F401
    LiveActor,
    LiveProposer,
    PrimedProposer,
    observation_from_trace,
)
from . import gate as gate  # noqa: F401
from . import handoff as handoff  # noqa: F401
