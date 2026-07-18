"""Rung 1 — the model reasoner: a Bundle in, a Decision out, behind the DecisionReasoner seam.

Two halves, deliberately separable so the untrustworthy one is the tiny one:

  - `parse_decision` — PURE and STRICT. Turns a model's JSON into a Decision, or REJECTS it.
    A rejected parse is not an error to swallow; it is training signal — it becomes a
    model-rung escalation whose rationale names exactly what was wrong (unknown intent, a
    selector smuggled into params, a missing confidence). Fully unit-tested.
  - `HaikuReasoner` — the IO half: budget-gate, call Haiku with the frozen bundle prompt +
    the closed vocabulary, record usage, hand the JSON to `parse_decision`. Untestable
    offline, but it contains no logic the parser doesn't already own.

Served behind HTTP (invariant #6): `HttpReasoner` POSTs the bundle to
`/api/controller/decide_model`, so the loop always talks to a model over the wire and
swapping Haiku -> a local L4 is a deployment change, never an edit to `decide()`.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from interaction.contract import Intent
from interaction.decision import (
    DECISION_CONFIDENCE_THRESHOLD,
    Bundle,
    Decision,
    looks_like_selector,
)

logger = logging.getLogger("controller.reason")

MODEL = "claude-haiku-4-5"
_INTENTS = frozenset(i.value for i in Intent)

#: The structured-output contract for the model — mirrors the Decision shape. `intent` is
#: enum-constrained so the model can't invent a verb; params/confidence/rationale required.
DECISION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": sorted(_INTENTS)},
        # params is the CLOSED param vocabulary — a strict json_schema needs every object to set
        # additionalProperties:false (Anthropic 400s otherwise), which also pins params to the
        # known semantic keys (a field label, a value, a control name, a date, a group) and keeps
        # a selector/id/xpath from ever being a valid key. Discovered live 2026-07-18.
        "params": {
            "type": "object",
            "description": "semantic references ONLY — never a selector/id/xpath",
            "properties": {
                "field": {"type": "string"},
                "value": {"type": "string"},
                "control": {"type": "string"},
                "month": {"type": "integer"},
                "year": {"type": "integer"},
                "values": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
        # No minimum/maximum: Anthropic structured output rejects numeric bounds. The [0,1] range
        # is enforced in parse_decision (the trustworthy, tested half) instead. (Live 2026-07-18.)
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
        # §10 (the Open Brain): the Bundle facts the rationale leans on — the receipts. Optional so
        # a terse call still parses, but requested in the system prompt so the backstop's "why" is
        # checkable and its proposal, when the teacher overrides it, carries its own cited reasoning.
        "evidence": {"type": "array", "items": {"type": "string"}},
        "expected_next": {"type": "array", "items": {"type": "string"}},
        "escalate": {"type": "boolean"},
    },
    "required": ["intent", "params", "confidence", "rationale"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You are the CONTROLLER of a supervised browser agent working a Career Search application. "
    "Given one observation (the Bundle below), emit exactly ONE next action as a Decision.\n\n"
    "Rules:\n"
    "- `intent` MUST be one of the closed vocabulary: "
    + ", ".join(sorted(_INTENTS)) + ".\n"
    "- `params` carries SEMANTIC references only — a `field` (the field's visible label) and/or a "
    "`control` (a button's visible text), plus a `value` when setting one. NEVER a CSS selector, "
    "id, xpath, or node id — addressing is not your job.\n"
    "- Fill the FIRST unanswered required field; when all are answered, click the control that "
    "advances the step (Continue/Next). Never invent an application answer.\n"
    "- Set `confidence` in [0,1]. If you are unsure, if the state looks novel, or if this needs a "
    "human (a login, a captcha, a Submit), set `escalate` true and a low confidence — asking is "
    "cheaper than a wrong action on someone's real job application.\n"
    "- `rationale` is one sentence saying WHY this action (not a restatement of it); `evidence` "
    "(optional) lists the Bundle facts it leans on — e.g. \"state\", \"unanswered[0].field\", "
    "\"recent[-1].outcome\" — so your reasoning is checkable, not asserted.\n"
    "- `expected_next` lists the state ids you expect to land on."
)


def build_messages(bundle: Bundle) -> tuple[str, list[dict[str, Any]]]:
    """(system, messages) for the Haiku call. The user turn is the FROZEN bundle serialization,
    so the prompt surface is identical to L4's future feature set."""
    from interaction.decision import bundle_to_prompt
    user = bundle_to_prompt(bundle) + "\n\nEmit one Decision as JSON."
    return _SYSTEM, [{"role": "user", "content": [{"type": "text", "text": user}]}]


def _reject(reason: str, bundle: Bundle) -> Decision:
    """A rejected model output becomes a model-rung escalation — journaled, so the malformed
    row is visible in the corpus as signal rather than dying silently."""
    return Decision(intent="observe", params={}, confidence=0.0, rung="model",
                    rationale=f"rejected model output: {reason}",
                    expected_next=tuple(bundle.expected_next), escalate=True)


def parse_decision(payload: Any, bundle: Bundle) -> Decision:
    """Strictly turn a model's JSON dict into a Decision (or a rejection escalation). PURE.

    Rejects, by design (each becomes a named escalation, not a silent guess):
      - an intent outside the closed vocabulary;
      - a params value that is not an object;
      - any params key OR value that looks like a selector (addressing smuggled in);
      - a missing or out-of-range confidence.
    """
    if not isinstance(payload, dict):
        return _reject("output was not a JSON object", bundle)

    intent = payload.get("intent")
    if intent not in _INTENTS:
        return _reject(f"intent {intent!r} is not in the closed vocabulary", bundle)

    params = payload.get("params", {})
    if not isinstance(params, dict):
        return _reject("params was not an object", bundle)
    for k, v in params.items():
        if looks_like_selector(k) or looks_like_selector(v):
            return _reject(f"selector-shaped param ({k!r}: {v!r}) — addressing is not the "
                           f"policy's job (invariant #10)", bundle)

    conf = payload.get("confidence")
    if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not (0.0 <= conf <= 1.0):
        return _reject(f"confidence {conf!r} missing or out of range [0,1]", bundle)

    return Decision(
        intent=str(intent),
        params={str(k): v for k, v in params.items()},
        confidence=float(conf),
        rung="model",
        rationale=str(payload.get("rationale", "") or ""),
        evidence=tuple(str(s) for s in (payload.get("evidence") or ())),
        expected_next=tuple(str(s) for s in (payload.get("expected_next") or ())),
        escalate=bool(payload.get("escalate", False)),
    )


class HaikuReasoner:
    """A DecisionReasoner backed by Haiku, budget-gated. Returns a Decision always — an escalation
    (with the reason) on budget/parse/transport failure, never a silent guess or a raise."""

    def __init__(self, *, budget_limit: Optional[float] = None, purpose: str = "controller_decide"):
        self.budget_limit = budget_limit
        self.purpose = purpose
        self.last_cost_usd = 0.0

    def __call__(self, bundle: Bundle) -> Decision:
        import anthropic_usage
        try:
            anthropic_usage.enforce_budget(self.budget_limit)   # BUDGET GATE — before any spend
        except anthropic_usage.BudgetExceededError as exc:
            logger.warning("controller decide over budget: %s", exc)
            return _reject(f"weekly budget exceeded — {exc}", bundle)

        try:
            import anthropic
            system, messages = build_messages(bundle)
            client = anthropic.Anthropic()
            resp = client.messages.create(
                model=MODEL,
                max_tokens=300,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=messages,
                output_config={"format": {"type": "json_schema", "schema": DECISION_JSON_SCHEMA}},
            )
            row = anthropic_usage.record_from_response(resp, purpose=self.purpose,
                                                       meta={"state": bundle.state, "ats": bundle.ats})
            self.last_cost_usd = float(row.get("cost_usd") or 0.0)
            text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "{}")
            payload = json.loads(text)
        except Exception as exc:  # noqa: BLE001 — a model failure escalates, never crashes the loop
            logger.warning("controller decide model call failed: %s", exc)
            return _reject(f"model call failed: {type(exc).__name__}: {exc}", bundle)

        return parse_decision(payload, bundle)


class HttpReasoner:
    """A DecisionReasoner that calls the model over HTTP (invariant #6). The loop injects THIS,
    so the boundary between the controller and the model is always the wire — a local L4 slots
    in behind the same endpoint with no change here."""

    def __init__(self, base_url: str, *, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def __call__(self, bundle: Bundle) -> Decision:
        import dataclasses

        import httpx
        try:
            payload = {"bundle": dataclasses.asdict(bundle)}
            with httpx.Client(timeout=self.timeout) as client:
                r = client.post(f"{self.base_url}/api/controller/decide_model", json=payload)
                r.raise_for_status()
                data = r.json()
        except Exception as exc:  # noqa: BLE001
            return _reject(f"decide_model endpoint unreachable: {type(exc).__name__}: {exc}", bundle)
        return parse_decision(data.get("decision") if isinstance(data, dict) else data, bundle)


DECISION_CONFIDENCE_FLOOR = DECISION_CONFIDENCE_THRESHOLD  # re-export for callers/tests
