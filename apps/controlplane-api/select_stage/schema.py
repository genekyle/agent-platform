"""Frozen contract for the SELECT stage (V1).

This module is the single source of truth for the selector's enums, dataclasses,
the Haiku output JSON schema, and the schema version. Keep it STABLE: the Haiku
output schema must stay byte-identical across requests to preserve the 24h
structured-output schema cache and prompt caching. Bump SELECTOR_SCHEMA_VERSION
only on an intentional, cache-invalidating change.

The model returns a `mark` (the number shown on the screenshot), not a raw
backend node id — that keeps the schema constant per request (a per-request id
enum would recompile the schema every call). The server resolves
mark -> Candidate.backend_node_id to produce the frozen SelectionResult.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# Bumping this invalidates the SelectionCache and recompiles the Haiku schema.
SELECTOR_SCHEMA_VERSION = "v1"


class ActionId(str, Enum):
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    SCROLL = "scroll"
    SUBMIT = "submit"
    CLEAR = "clear"
    NONE = "none"


class ReasonCode(str, Enum):
    # Set by the MODEL (must match _MODEL_REASON_CODES below):
    SOM_PICK = "som_pick"
    LOW_CONFIDENCE = "low_confidence"
    NO_MATCH = "no_match"
    UNKNOWN_STATE = "unknown_state"
    # Set by the SYSTEM (never emitted by the model):
    CACHE_HIT = "cache_hit"
    BUDGET_EXCEEDED = "budget_exceeded"
    STOP_STATE = "stop_state"
    VERIFIER_FAILED = "verifier_failed"


# Subset the model is allowed to emit. The rest are system-assigned.
_MODEL_REASON_CODES = ("som_pick", "low_confidence", "no_match", "unknown_state")
_ACTION_IDS = tuple(a.value for a in ActionId)


# --- Dataclasses -------------------------------------------------------------
@dataclass(frozen=True)
class Candidate:
    """One actionable element. `mark` is the 1-based number drawn on the
    screenshot and shown to Haiku; `backend_node_id` is the stable AX identity
    used everywhere downstream (cache key, result, executor handoff)."""
    mark: int
    backend_node_id: int
    role: str
    name: str
    bbox: dict[str, float]  # screenshot pixels: {x, y, width, height}


@dataclass(frozen=True)
class SelectionResult:
    """The frozen 5-field selector contract + non-contract metadata."""
    action_id: ActionId
    target_backend_node_id: Optional[int]
    confidence: float
    needs_human: bool
    reason_code: ReasonCode
    # metadata (NOT part of the frozen contract; for telemetry/debug):
    layer: str = ""
    cost_usd: float = 0.0
    fingerprint: str = ""

    @property
    def status(self) -> str:
        return "escalate" if self.needs_human else "resolved"


@dataclass(frozen=True)
class TrajectoryRequest:
    """Selector -> executor handoff. Carries intent + target ONLY — no pointer
    path. The executor (mcp-mock) decides center-click vs synthesized path."""
    backend_node_id: int
    target_bbox: dict[str, float]
    action_id: ActionId
    value: Optional[str] = None


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    predicted: str
    observed: str
    reason: str = ""


# --- Frozen Haiku output schema ---------------------------------------------
# Built from the enums so they can't drift. Treat the resulting dict as
# immutable — any change is a SELECTOR_SCHEMA_VERSION bump.
HAIKU_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action_id", "mark", "confidence", "needs_human", "reason_code"],
    "properties": {
        "action_id": {"type": "string", "enum": list(_ACTION_IDS)},
        "mark": {"type": "integer"},  # 0 = none match
        "confidence": {"type": "number"},
        "needs_human": {"type": "boolean"},
        "reason_code": {"type": "string", "enum": list(_MODEL_REASON_CODES)},
    },
}


# --- Helpers (mark <-> candidate, model output -> SelectionResult) -----------
def candidates_from_ax(ax_candidates: list[dict[str, Any]]) -> list[Candidate]:
    """Convert proposer AX candidates into numbered Candidates. Requires the
    top-level `backend_node_id` field (promoted in ax_proposer). Candidates
    without a backend id are skipped — they can't be a stable target."""
    out: list[Candidate] = []
    mark = 1
    for c in ax_candidates:
        bid = c.get("backend_node_id")
        if bid is None:
            bid = (c.get("_debug") or {}).get("backend_node_id")  # back-compat
        if bid is None:
            continue
        out.append(Candidate(
            mark=mark,
            backend_node_id=int(bid),
            role=str(c.get("role", "")),
            name=(c.get("caption") or c.get("name") or "").strip(),
            bbox=c.get("bbox") or {},
        ))
        mark += 1
    return out


def resolve_mark(mark: int, candidates: list[Candidate]) -> Optional[Candidate]:
    """Map a model `mark` (1-based) back to its Candidate. 0 / out-of-range -> None."""
    if 1 <= mark <= len(candidates):
        return candidates[mark - 1]
    return None
