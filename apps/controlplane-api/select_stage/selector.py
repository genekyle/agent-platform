"""Selector orchestrator — the SELECT-stage cascade (classify → cache → Haiku).

Cheapest-first. Each layer answers confidently or escalates. Haiku is a GATED
catchall, never primary. Every path logs a telemetry row (the training corpus).
Human escalation on: stop-state, over-budget, low confidence, no match.

    classify  (escalation_rules — stop-states)   FREE → escalate
    cache     (SelectionCacheV1)                  FREE
    haiku     (HaikuSelectorV1, budget-gated)     ~$0.002
    escalate  (over-budget / low-conf / no-match) → human
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import anthropic_usage
import escalation_rules

from . import cache, fingerprint, haiku_selector, telemetry
from .schema import ActionId, ReasonCode, SelectionResult, candidates_from_ax

logger = logging.getLogger("controlplane.select.selector")

MIN_CONFIDENCE = 0.5


def select(
    *,
    url: str,
    task_goal: str,
    ax_candidates: list[dict[str, Any]],
    screenshot_path: str | Path,
    viewport: Optional[dict[str, Any]] = None,
    page_text: str = "",
    dom_clickables: Optional[list[dict[str, Any]]] = None,
    meta: Optional[dict[str, Any]] = None,
) -> SelectionResult:
    viewport = viewport or {}
    route = fingerprint.route_template(url)
    candidates = candidates_from_ax(ax_candidates)
    fp = fingerprint.compute(url=url, viewport=viewport, candidates=ax_candidates,
                             task_goal=task_goal, dom_clickables=dom_clickables)
    budget = anthropic_usage.budget_status()

    def finish(result: SelectionResult, cache_status: str, tokens: dict) -> SelectionResult:
        telemetry.log_selection(result=result, route=route, task_goal=task_goal,
                                candidate_count=len(candidates), cache_status=cache_status,
                                tokens=tokens, budget=anthropic_usage.budget_status(), meta=meta)
        return result

    # --- CLASSIFY: STOP-AND-INVOLVE-HUMAN gate (captcha/checkpoint) -----------
    stop = escalation_rules.check(url=url, page_text=page_text, ax_candidate_count=len(candidates))
    if stop is not None:
        r = SelectionResult(ActionId.NONE, None, 1.0, True, ReasonCode.STOP_STATE,
                            layer="classify", cost_usd=0.0, fingerprint=fp)
        logger.info("classify STOP: %s", stop["rule"])
        return finish(r, "n/a", {})

    if not candidates:
        r = SelectionResult(ActionId.NONE, None, 0.0, True, ReasonCode.UNKNOWN_STATE,
                            layer="propose", cost_usd=0.0, fingerprint=fp)
        return finish(r, "miss", {})

    # --- Layer 2: cache (free) ----------------------------------------------
    hit = cache.lookup(fingerprint=fp, task_goal=task_goal, candidates=candidates)
    if hit is not None:
        c = hit["candidate"]
        r = SelectionResult(hit["action_id"], c.backend_node_id, 1.0, False, ReasonCode.CACHE_HIT,
                            layer="cache", cost_usd=0.0, fingerprint=fp)
        logger.info("cache HIT: goal=%r -> %s", task_goal, c.name)
        return finish(r, "hit", {})

    # --- Layer 5: Haiku SoM (budget-gated) ----------------------------------
    try:
        pick = haiku_selector.pick(screenshot_path=screenshot_path, candidates=candidates,
                                   task_goal=task_goal, meta=meta)
    except anthropic_usage.BudgetExceededError as exc:
        r = SelectionResult(ActionId.NONE, None, 0.0, True, ReasonCode.BUDGET_EXCEEDED,
                            layer="som_haiku", cost_usd=0.0, fingerprint=fp)
        logger.warning("budget exceeded — escalate: %s", exc)
        return finish(r, "miss", {})

    cost = pick["cost_usd"]
    cand = pick["candidate"]
    conf = pick["confidence"]
    tokens = {}  # token detail is in anthropic_usage; budget row carries spend

    if cand is None:
        r = SelectionResult(ActionId.NONE, None, conf, True, ReasonCode.NO_MATCH,
                            layer="som_haiku", cost_usd=cost, fingerprint=fp)
        return finish(r, "miss", tokens)
    if pick["needs_human"] or conf < MIN_CONFIDENCE:
        r = SelectionResult(pick["action_id"], cand.backend_node_id, conf, True,
                            ReasonCode.LOW_CONFIDENCE, layer="som_haiku", cost_usd=cost, fingerprint=fp)
        return finish(r, "miss", tokens)

    # Confident → seed the cache so this state is FREE next time.
    cache.store(fingerprint=fp, task_goal=task_goal, chosen=cand,
                action_id=pick["action_id"], source="som_haiku")
    r = SelectionResult(pick["action_id"], cand.backend_node_id, conf, False, ReasonCode.SOM_PICK,
                        layer="som_haiku", cost_usd=cost, fingerprint=fp)
    return finish(r, "miss", tokens)
