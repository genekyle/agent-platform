"""RuntimeLoop — drives classify → propose → select → act → verify → repeat.

The loop is a pure orchestrator over PORTS:

  * `Proposer`  — observe the current page → an `Observation` (url, page text, AX
                  candidates, screenshot, a verifier `Snapshot`). The live adapter
                  drives mcp's AX proposer; tests inject a fake.
  * `Actor`     — perform (or record) the decided action. The default
                  `RecordOnlyActor` logs the intent and executes NOTHING — the safe
                  mode for first live runs and supervised data collection.

The select + verify + classify stages are called in-process (same app). Because the
side effects are injected, the entire loop is unit-testable without a browser.

Safety contract: in record-only mode the action is not executed, so the verify gate
is SKIPPED (status `recorded`), not failed — expecting a page delta when nothing was
clicked would be wrong. The loop then stops, because there is no autonomous progress
to verify; a human performs the recorded intent under supervision.

Every iteration appends a `StepRecord` to `<artifacts>/cache/loop_steps.jsonl` — the
per-step trajectory corpus the L4 selector / state-transition models will train on.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, runtime_checkable

import escalation_rules
from settings import settings

from select_stage import fingerprint, selector, verifier
from select_stage.schema import (
    ActionId,
    Candidate,
    ReasonCode,
    SelectionResult,
    candidates_from_ax,
)

logger = logging.getLogger("controlplane.runtime.loop")
_lock = threading.Lock()


# --- Ports -------------------------------------------------------------------
@dataclass
class Observation:
    """A single look at the current page — the input to one loop step."""
    url: str
    page_text: str
    ax_candidates: list[dict[str, Any]]
    screenshot_path: str
    viewport: dict[str, Any] = field(default_factory=dict)
    dom_clickables: list[dict[str, Any]] = field(default_factory=list)
    snapshot: Optional[verifier.Snapshot] = None


# A Proposer observes the current page. Called once per step (and again after an
# executed action, to produce the AFTER snapshot for verification).
Proposer = Callable[[], Observation]

# A Gate inspects a DECIDED action against the live invariants BEFORE it fires (the
# Layer-3 code-enforced check). It returns a block dict (e.g. {reason, blockers}) to
# refuse the action and escalate to a human, or None to allow it through. This is what
# makes the loop structurally unable to e.g. submit a form with an empty required field,
# regardless of how confident the selector was.
Gate = Callable[["SelectionResult", Observation], Optional[dict[str, Any]]]


@dataclass
class ActResult:
    executed: bool
    driver: str = ""
    detail: str = ""


@runtime_checkable
class Actor(Protocol):
    """Performs (or records) a decided action against the live page."""

    def perform(
        self,
        *,
        action_id: str,
        target_backend_node_id: Optional[int],
        target_bbox: dict[str, float],
        value: Optional[str],
        observation: Observation,
    ) -> ActResult: ...


class RecordOnlyActor:
    """Default-safe Actor: records the decided intent, executes nothing.

    This is the controlplane-side record of the loop's DECISION (distinct from the
    mcp executor, which performs real CDP input once autonomous action is
    trusted). It writes the intent to the loop-step corpus implicitly via the
    `StepRecord`; here it only marks the action un-executed so the loop skips verify.
    """

    name = "record_only"

    def perform(self, *, action_id, target_backend_node_id, target_bbox, value, observation) -> ActResult:
        logger.info("record_only: would %s on backend_node_id=%s (value=%r) — NOT executed",
                    action_id, target_backend_node_id, value)
        return ActResult(executed=False, driver=self.name, detail="recorded intent; not executed")


# --- Records -----------------------------------------------------------------
class StepStatus(str, Enum):
    EXECUTED_VERIFIED = "executed_verified"   # action fired and the page changed as predicted
    RECORDED = "recorded"                     # record-only: intent logged, not executed
    ESCALATED = "escalated"                   # handed to a human (stop/budget/low-conf/no-match/verify-fail)
    RETRY = "retry"                           # verify failed, retrying (bounded)


class LoopStatus(str, Enum):
    COMPLETED = "completed"       # is_done() satisfied
    RECORDED = "recorded"         # record-only intent logged; stopped for human
    ESCALATED = "escalated"       # stopped — needs a human
    MAX_STEPS = "max_steps"       # ran out of step budget


@dataclass
class StepRecord:
    step_index: int
    retry: int
    ts: str
    route: str
    fingerprint: str
    task_goal: str
    candidate_count: int
    action_id: str
    target_backend_node_id: Optional[int]
    confidence: float
    reason_code: str
    layer: str
    cost_usd: float
    needs_human: bool
    executed: bool
    driver: str
    status: str
    verify: Optional[dict[str, Any]] = None
    gate: Optional[dict[str, Any]] = None


@dataclass
class LoopResult:
    status: LoopStatus
    steps: list[StepRecord]
    reason: str = ""
    escalation_reason: Optional[str] = None

    @property
    def total_cost_usd(self) -> float:
        return round(sum(s.cost_usd for s in self.steps), 6)


# --- Corpus ------------------------------------------------------------------
def _steps_path() -> Path:
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parents[1] / base).resolve()
    p = base / "cache" / "loop_steps.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _log_step(record: StepRecord) -> None:
    """Append a step row to the trajectory corpus. Best-effort — never raises."""
    try:
        with _lock:
            with _steps_path().open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(record)) + "\n")
    except Exception:
        pass


# --- Helpers -----------------------------------------------------------------
def _resolve_candidate(result: SelectionResult, candidates: list[Candidate]) -> Optional[Candidate]:
    return next((c for c in candidates if c.backend_node_id == result.target_backend_node_id), None)


def _device_scale_factor(viewport: dict[str, Any]) -> float:
    return float(viewport.get("device_scale_factor") or viewport.get("dpr") or 1.0)


# --- The loop ----------------------------------------------------------------
def run_loop(
    *,
    task_goal: str,
    proposer: Proposer,
    actor: Optional[Actor] = None,
    max_steps: int = 10,
    is_done: Optional[Callable[[Observation], bool]] = None,
    value_for: Optional[Callable[[SelectionResult, Observation], Optional[str]]] = None,
    gate: Optional[Gate] = None,
    max_retries: int = verifier.MAX_RETRIES,
    log_corpus: bool = True,
) -> LoopResult:
    """Run the per-step loop until done, escalation, a recorded intent, or budget.

    `actor` defaults to `RecordOnlyActor` (safe). `is_done(observation)` lets the
    caller declare task success. `value_for(result, observation)` supplies the text
    for `type`/`select` actions (used both as the input value and the verify oracle).
    `gate(result, observation)` is the Layer-3 invariant check: it runs AFTER select and
    BEFORE act, and if it returns a block dict the action is refused and the loop escalates
    to a human — the structural stop that keeps a confident-but-wrong submit from firing.
    """
    actor = actor or RecordOnlyActor()
    steps: list[StepRecord] = []
    observation = proposer()
    retry = 0

    def record(result: SelectionResult, observation: Observation, candidate_count: int,
               executed: bool, driver: str, status: StepStatus, step_index: int,
               verify_info: Optional[dict[str, Any]] = None,
               gate_info: Optional[dict[str, Any]] = None) -> StepRecord:
        rec = StepRecord(
            step_index=step_index, retry=retry,
            ts=datetime.now(timezone.utc).isoformat(),
            route=fingerprint.route_template(observation.url),
            fingerprint=result.fingerprint,
            task_goal=task_goal, candidate_count=candidate_count,
            action_id=result.action_id.value,
            target_backend_node_id=result.target_backend_node_id,
            confidence=result.confidence, reason_code=result.reason_code.value,
            layer=result.layer, cost_usd=result.cost_usd, needs_human=result.needs_human,
            executed=executed, driver=driver, status=status.value, verify=verify_info,
            gate=gate_info,
        )
        steps.append(rec)
        if log_corpus:
            _log_step(rec)
        return rec

    step_index = 0
    while step_index < max_steps:
        if is_done is not None and is_done(observation):
            return LoopResult(LoopStatus.COMPLETED, steps, reason="is_done satisfied")

        candidates = candidates_from_ax(observation.ax_candidates)

        # --- CLASSIFY: the cheap $0 stop-state gate, before paying for select ---
        stop = escalation_rules.check(
            url=observation.url, page_text=observation.page_text,
            ax_candidate_count=len(candidates),
        )
        if stop is not None:
            fp = fingerprint.compute(url=observation.url, viewport=observation.viewport,
                                     candidates=observation.ax_candidates, task_goal=task_goal,
                                     dom_clickables=observation.dom_clickables)
            r = SelectionResult(ActionId.NONE, None, 1.0, True,
                                ReasonCode.STOP_STATE, layer="classify",
                                cost_usd=0.0, fingerprint=fp)
            record(r, observation, len(candidates), False, "", StepStatus.ESCALATED, step_index)
            return LoopResult(LoopStatus.ESCALATED, steps, reason=f"stop-state: {stop['rule']}",
                              escalation_reason="stop_state")

        # --- SELECT: cheapest-first cascade (cache → Haiku → escalate) ----------
        # A supervised agent must never crash on an unexpected stage failure — any
        # error escalates to a human (the catchall the whole design relies on).
        try:
            result = selector.select(
                url=observation.url, task_goal=task_goal,
                ax_candidates=observation.ax_candidates, screenshot_path=observation.screenshot_path,
                viewport=observation.viewport, page_text=observation.page_text,
                dom_clickables=observation.dom_clickables,
                meta={"step_index": step_index, "retry": retry},
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("select stage errored — escalating")
            fp = fingerprint.compute(url=observation.url, viewport=observation.viewport,
                                     candidates=observation.ax_candidates, task_goal=task_goal,
                                     dom_clickables=observation.dom_clickables)
            r = SelectionResult(ActionId.NONE, None, 0.0, True, ReasonCode.UNKNOWN_STATE,
                                layer="select", cost_usd=0.0, fingerprint=fp)
            record(r, observation, len(candidates), False, "", StepStatus.ESCALATED, step_index)
            return LoopResult(LoopStatus.ESCALATED, steps,
                              reason=f"select stage error: {exc}", escalation_reason="stage_error")

        if result.needs_human:
            record(result, observation, len(candidates), False, "", StepStatus.ESCALATED, step_index)
            return LoopResult(LoopStatus.ESCALATED, steps,
                              reason=f"select escalated: {result.reason_code.value}",
                              escalation_reason=result.reason_code.value)

        # --- GATE: Layer-3 invariant check before the action fires --------------
        # Even a high-confidence pick is refused if it would violate an invariant (e.g.
        # proceed past a form with an empty required field). The action never runs.
        if gate is not None:
            block = gate(result, observation)
            if block is not None:
                record(result, observation, len(candidates), False, "",
                       StepStatus.ESCALATED, step_index, gate_info=block)
                return LoopResult(LoopStatus.ESCALATED, steps,
                                  reason=f"gate blocked: {block.get('reason', 'invariant')}",
                                  escalation_reason="gate_blocked")

        cand = _resolve_candidate(result, candidates)
        target_bbox = cand.bbox if cand else {}
        value = value_for(result, observation) if value_for else None

        # --- ACT: perform or record the decided intent -------------------------
        act = actor.perform(
            action_id=result.action_id.value,
            target_backend_node_id=result.target_backend_node_id,
            target_bbox=target_bbox, value=value, observation=observation,
        )

        if not act.executed:
            # Record-only: intent logged, nothing fired → verify is N/A. Stop here;
            # a human performs the intent and the next observation comes from them.
            record(result, observation, len(candidates), False, act.driver,
                   StepStatus.RECORDED, step_index)
            return LoopResult(LoopStatus.RECORDED, steps,
                              reason="record-only: intent logged, awaiting human action")

        # --- VERIFY: did the page change the way the action predicted? ---------
        after = proposer()
        vres = verifier.verify(
            action_id=result.action_id.value,
            before=observation.snapshot or verifier.Snapshot(),
            after=after.snapshot or verifier.Snapshot(),
            target_backend_node_id=result.target_backend_node_id,
            expected_value=value,
        )
        verify_info = {"ok": vres.ok, "predicted": vres.predicted, "observed": vres.observed}
        decision = verifier.next_step(vres, retry, max_retries)

        if decision == "ok":
            record(result, observation, len(candidates), True, act.driver,
                   StepStatus.EXECUTED_VERIFIED, step_index, verify_info)
            observation = after
            retry = 0
            step_index += 1
            continue

        if decision == "retry":
            record(result, observation, len(candidates), True, act.driver,
                   StepStatus.RETRY, step_index, verify_info)
            observation = after
            retry += 1
            continue  # same step_index — re-select against the (unchanged) page

        # escalate
        record(result, observation, len(candidates), True, act.driver,
               StepStatus.ESCALATED, step_index, verify_info)
        return LoopResult(LoopStatus.ESCALATED, steps,
                          reason=f"verify failed after retries: {vres.reason}",
                          escalation_reason="verifier_failed")

    return LoopResult(LoopStatus.MAX_STEPS, steps, reason=f"reached max_steps={max_steps}")
