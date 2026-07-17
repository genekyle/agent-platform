"""Controller routes — the reasoner served over HTTP, and the read surfaces the cockpit shows.

`POST /api/controller/decide_model` is the invariant-#6 boundary: the model behind an endpoint,
so a local L4 replaces Haiku as a deployment change. The GET routes expose the decision corpus,
the compiled programs, and the scoreboard so the operator can SEE the controller working — what
it decided, at which rung, what it escalated, and how well it agrees with the teacher.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

import apply_recipe
from controller import metrics as controller_metrics
from controller import programs as programs_mod
from controller.decide import decide
from controller.reason import HaikuReasoner, parse_decision
from interaction import decision_journal
from interaction.decision import Bundle

router = APIRouter()


_SEQ_FIELDS = ("expected_next", "unanswered", "recent")


def _bundle_from_dict(d: dict[str, Any]) -> Bundle:
    """Reconstruct a Bundle from a posted/journaled dict, coercing the sequence fields back to
    tuples and ignoring unknown keys (schema-forward-compatible)."""
    known = {f for f in Bundle.__dataclass_fields__}          # noqa: SLF001 (public enough)
    clean = {k: v for k, v in (d or {}).items() if k in known}
    for k in _SEQ_FIELDS:
        if k in clean and clean[k] is not None:
            clean[k] = tuple(tuple(x) if isinstance(x, list) else x for x in clean[k]) \
                if k == "expected_next" else tuple(clean[k])
    # required positional-ish fields with safe defaults if a partial bundle was posted
    clean.setdefault("task", "")
    clean.setdefault("goal_text", "")
    clean.setdefault("done", False)
    clean.setdefault("url", "")
    clean.setdefault("route", "")
    clean.setdefault("state", None)
    clean.setdefault("is_branch", False)
    clean.setdefault("human_required", False)
    return Bundle(**clean)


class DecideModelBody(BaseModel):
    bundle: dict[str, Any]
    budget_limit: Optional[float] = None


@router.post("/api/controller/decide_model")
def decide_model(body: DecideModelBody) -> dict[str, Any]:
    """Rung 1: a Bundle in, a Decision out, via Haiku (budget-gated). The response's `decision`
    is already strict-parsed; a rejected/over-budget call returns an escalation, never a raise."""
    bundle = _bundle_from_dict(body.bundle)
    reasoner = HaikuReasoner(budget_limit=body.budget_limit)
    decision = reasoner(bundle)
    return {
        "decision": {
            "intent": decision.intent, "params": decision.params,
            "confidence": decision.confidence, "rung": decision.rung,
            "rationale": decision.rationale, "expected_next": list(decision.expected_next),
            "escalate": decision.escalate,
        },
        "cost_usd": reasoner.last_cost_usd,
    }


@router.post("/api/controller/decide")
def decide_cascade(body: DecideModelBody) -> dict[str, Any]:
    """The FULL cascade for a posted bundle — rung 0 (programs) then, only if asked, the model.
    Deterministic and free by default (model=None): a 'what would the controller do here?' probe
    the cockpit uses without spending. Set `budget_limit` present to allow the Haiku rung."""
    bundle = _bundle_from_dict(body.bundle)
    reasoner = HaikuReasoner(budget_limit=body.budget_limit) if body.budget_limit is not None else None
    decision = decide(bundle, programs=programs_mod.ProgramStore(), model=reasoner)
    return {"decision": {
        "intent": decision.intent, "params": decision.params, "confidence": decision.confidence,
        "rung": decision.rung, "rationale": decision.rationale,
        "expected_next": list(decision.expected_next), "escalate": decision.escalate}}


@router.get("/api/controller/summary")
def summary() -> dict[str, Any]:
    """The scoreboard: corpus size, per-rung / per-outcome, verified & escalation rates, program
    count, and per-scenario shadow agreement — everything the cockpit's controller panel needs."""
    js = decision_journal.summarize()
    progs = programs_mod.list_programs()
    return {
        **js,
        "programs": [{"task": p.task, "state": p.state, "steps": len(p.steps),
                      "guard_fields": list(p.guard_fields), "expected_exit": list(p.expected_exit),
                      "stale": p.stale, "verified_at": p.verified_at} for p in progs],
        "program_count": len(progs),
        "agreement": controller_metrics.shadow_agreement(decision_journal.read_rows()),
    }


@router.get("/api/controller/decisions")
def decisions(limit: int = 100, session_id: Optional[str] = None) -> dict[str, Any]:
    """The recent decision feed (newest first) — what the reasoner decided, at which rung, and
    whether it verified. Optionally scoped to one run."""
    rows = decision_journal.read_rows()
    if session_id:
        rows = [r for r in rows if r.get("session_id") == session_id]
    rows = list(reversed(rows))[:limit]
    return {"decisions": rows, "count": len(rows)}


@router.get("/api/controller/programs")
def programs() -> dict[str, Any]:
    """The compiled intent programs — the $0 rung-0 library, growing as the teacher drives."""
    progs = programs_mod.list_programs()
    return {"programs": [{"task": p.task, "state": p.state, "steps": list(p.steps),
                          "guard_fields": list(p.guard_fields),
                          "expected_exit": list(p.expected_exit), "stale": p.stale,
                          "compiled_from": list(p.compiled_from), "verified_at": p.verified_at}
                         for p in progs], "count": len(progs)}
