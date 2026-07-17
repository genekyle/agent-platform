"""The Interaction API's shared contract.

Imported by BOTH `apps/controlplane-api` and `apps/mcp`, which is the whole point:
the two processes previously each kept their own copy of shared logic, and the copies
DRIFTED silently (the autofill question-matcher in `mcp/app/main_server.py` diverged
from its Python twin in `controlplane-api/application_answers.py` — different stop-word
lists, so the same question scored differently on each side). A contract that lives in
one place cannot drift.

What belongs here: things both the actor and the trainer must agree on.
  - `contract`     — the intent vocabulary + outcome taxonomy + widget types (frozen)
  - `journal`      — the append-only intent corpus (the actor writes, the trainer reads)
  - `fingerprint`  — the page-state join key (was select_stage/fingerprint.py)
  - `decision`     — the controller's frozen I/O (Bundle in, Decision out) + serialization
  - `decision_journal` — the append-only decision corpus (the reasoner writes)

What does NOT belong here: recipes (per-ATS data, control-plane-owned), CDP mechanism
(mcp-owned), models/DB (control-plane-owned).

NB: the writer is `log_intent`, not `journal` — the submodule is called `journal`, and a
re-exported function of the same name SHADOWS it (`import interaction.journal` would hand
you the function, not the module). It also matches the siblings it sits beside:
`telemetry.log_selection`, `event_log.log_event`.
"""

from interaction.contract import (
    INTENT_SCHEMA_VERSION,
    Intent,
    Outcome,
    WidgetType,
    intent_expands_to,
    redact,
)
from interaction.decision import (
    DECISION_CONFIDENCE_THRESHOLD,
    DECISION_SCHEMA_VERSION,
    RUNGS,
    Bundle,
    Decision,
    DecisionRecord,
    bundle_digest,
    bundle_to_prompt,
    looks_like_selector,
    sanitize_unanswered,
)
from interaction.decision_journal import log_decision, record_for
from interaction.journal import IntentRecord, log_intent, read_rows

__all__ = [
    "INTENT_SCHEMA_VERSION",
    "Intent",
    "IntentRecord",
    "Outcome",
    "WidgetType",
    "intent_expands_to",
    "log_intent",
    "read_rows",
    "redact",
    # controller contract
    "DECISION_SCHEMA_VERSION",
    "DECISION_CONFIDENCE_THRESHOLD",
    "RUNGS",
    "Bundle",
    "Decision",
    "DecisionRecord",
    "bundle_digest",
    "bundle_to_prompt",
    "looks_like_selector",
    "sanitize_unanswered",
    "log_decision",
    "record_for",
]
