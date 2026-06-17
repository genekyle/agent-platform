"""Unit tests for the runtime loop orchestration.

We test the LOOP's control flow — classify gate, select-escalation, record-only
stop, executed+verify advance, verify-fail retry→escalate, is_done completion, and
the step budget — with fake Proposer/Actor and a monkeypatched `selector.select`.
The selector internals are covered by select_stage's own tests; here the boundary
is deliberately the loop logic alone.
"""

from __future__ import annotations

import pytest

from runtime import loop
from runtime.loop import (
    ActResult,
    LoopStatus,
    Observation,
    RecordOnlyActor,
    StepStatus,
    run_loop,
)
from select_stage import verifier
from select_stage.schema import ActionId, ReasonCode, SelectionResult


# --- fakes -------------------------------------------------------------------
def _snap(body_text: str = "hello", route: str = "ex.com/page") -> verifier.Snapshot:
    return verifier.Snapshot(url=f"https://{route}", route=route,
                             ax=frozenset({("button", "Go")}), body_text=body_text)


def _obs(*, page_text: str = "hello", ax: list | None = None, snap=None,
         url: str = "https://ex.com/page") -> Observation:
    if ax is None:
        ax = [{"role": "button", "name": "Go", "backend_node_id": 5,
               "bbox": {"x": 0, "y": 0, "width": 10, "height": 10}}]
    return Observation(url=url, page_text=page_text, ax_candidates=ax,
                       screenshot_path="/tmp/x.png", viewport={"device_scale_factor": 1.0},
                       dom_clickables=[], snapshot=snap or _snap(page_text))


class SequenceProposer:
    """Yields observations in order; repeats the last one forever."""

    def __init__(self, observations: list[Observation]):
        self._obs = observations
        self._i = 0
        self.calls = 0

    def __call__(self) -> Observation:
        self.calls += 1
        o = self._obs[min(self._i, len(self._obs) - 1)]
        self._i += 1
        return o


class ExecutingActor:
    def __init__(self):
        self.calls = 0

    def perform(self, **kwargs) -> ActResult:
        self.calls += 1
        return ActResult(executed=True, driver="fake")


def _resolved(action=ActionId.CLICK, backend=5, conf=0.9) -> SelectionResult:
    return SelectionResult(action, backend, conf, False, ReasonCode.SOM_PICK,
                           layer="som_haiku", cost_usd=0.002, fingerprint="fp")


def _patch_select(monkeypatch, result_or_fn):
    fn = result_or_fn if callable(result_or_fn) else (lambda **kw: result_or_fn)
    monkeypatch.setattr(loop.selector, "select", fn)


# --- tests -------------------------------------------------------------------
def test_classify_stop_state_escalates_before_select(monkeypatch):
    """A captcha URL trips the $0 classify gate; select is never called."""
    called = {"select": False}

    def boom(**kw):
        called["select"] = True
        raise AssertionError("select must not run on a stop-state")

    _patch_select(monkeypatch, boom)
    proposer = SequenceProposer([_obs(url="https://site.com/checkpoint/challenge")])
    res = run_loop(task_goal="x", proposer=proposer, max_steps=3, log_corpus=False)

    assert res.status is LoopStatus.ESCALATED
    assert res.escalation_reason == "stop_state"
    assert called["select"] is False
    assert res.steps[-1].status == StepStatus.ESCALATED.value


def test_select_escalation_stops_loop(monkeypatch):
    esc = SelectionResult(ActionId.NONE, None, 0.2, True, ReasonCode.LOW_CONFIDENCE,
                          layer="som_haiku", cost_usd=0.002, fingerprint="fp")
    _patch_select(monkeypatch, esc)
    res = run_loop(task_goal="x", proposer=SequenceProposer([_obs()]),
                   max_steps=3, log_corpus=False)

    assert res.status is LoopStatus.ESCALATED
    assert res.escalation_reason == "low_confidence"
    assert len(res.steps) == 1


def test_record_only_logs_intent_and_stops(monkeypatch):
    """Default actor records the intent, does not execute, and verify is skipped."""
    _patch_select(monkeypatch, _resolved())
    proposer = SequenceProposer([_obs()])
    res = run_loop(task_goal="click go", proposer=proposer, actor=RecordOnlyActor(),
                   max_steps=3, log_corpus=False)

    assert res.status is LoopStatus.RECORDED
    step = res.steps[-1]
    assert step.status == StepStatus.RECORDED.value
    assert step.executed is False
    assert step.verify is None
    # proposer called exactly once — no AFTER snapshot is taken when nothing fired.
    assert proposer.calls == 1


def test_executed_and_verified_then_done(monkeypatch):
    """Action fires, page text changes (verify ok), is_done ends the loop."""
    _patch_select(monkeypatch, _resolved())
    before = _obs(page_text="hello", snap=_snap("hello"))
    after = _obs(page_text="world", snap=_snap("world"))
    proposer = SequenceProposer([before, after])
    actor = ExecutingActor()

    seen = {"n": 0}

    def is_done(o: Observation) -> bool:
        # done once we observe the post-click page
        return o.page_text == "world"

    res = run_loop(task_goal="x", proposer=proposer, actor=actor, is_done=is_done,
                   max_steps=5, log_corpus=False)

    assert res.status is LoopStatus.COMPLETED
    assert actor.calls == 1
    assert res.steps[0].status == StepStatus.EXECUTED_VERIFIED.value
    assert res.steps[0].verify["ok"] is True
    assert res.total_cost_usd == pytest.approx(0.002)


def test_verify_fail_retries_then_escalates(monkeypatch):
    """No page change after a click → retry once → escalate (verifier_failed)."""
    _patch_select(monkeypatch, _resolved())
    same = _snap("hello")
    # every observation is identical → click produces no delta → verify always fails
    proposer = SequenceProposer([_obs(snap=same)])
    actor = ExecutingActor()
    res = run_loop(task_goal="x", proposer=proposer, actor=actor, max_steps=5, log_corpus=False)

    assert res.status is LoopStatus.ESCALATED
    assert res.escalation_reason == "verifier_failed"
    statuses = [s.status for s in res.steps]
    assert StepStatus.RETRY.value in statuses
    assert statuses[-1] == StepStatus.ESCALATED.value
    # MAX_RETRIES=1 → one retry record + one escalate record
    assert statuses.count(StepStatus.RETRY.value) == 1


def test_max_steps_budget(monkeypatch):
    """An always-progressing page with no is_done terminates at max_steps."""
    _patch_select(monkeypatch, lambda **kw: _resolved())
    # each call returns a fresh changed page so verify always passes and we keep going
    counter = {"n": 0}

    def proposer() -> Observation:
        counter["n"] += 1
        return _obs(page_text=f"page{counter['n']}", snap=_snap(f"page{counter['n']}"))

    actor = ExecutingActor()
    res = run_loop(task_goal="x", proposer=proposer, actor=actor, max_steps=3, log_corpus=False)

    assert res.status is LoopStatus.MAX_STEPS
    executed = [s for s in res.steps if s.status == StepStatus.EXECUTED_VERIFIED.value]
    assert len(executed) == 3


def test_select_stage_exception_escalates_not_crashes(monkeypatch):
    """An unexpected error inside select escalates to a human, never propagates."""
    def boom(**kw):
        raise RuntimeError("model exploded")

    _patch_select(monkeypatch, boom)
    res = run_loop(task_goal="x", proposer=SequenceProposer([_obs()]),
                   max_steps=3, log_corpus=False)

    assert res.status is LoopStatus.ESCALATED
    assert res.escalation_reason == "stage_error"
    assert "model exploded" in res.reason
    assert res.steps[-1].status == StepStatus.ESCALATED.value


def test_type_action_uses_value_for_oracle(monkeypatch):
    """value_for supplies the typed text; verify confirms it appears in the page."""
    _patch_select(monkeypatch, _resolved(action=ActionId.TYPE))
    before = _obs(page_text="empty", snap=_snap("empty"))
    after = _obs(page_text="now contains banana", snap=_snap("now contains banana"))
    proposer = SequenceProposer([before, after])

    res = run_loop(task_goal="x", proposer=proposer, actor=ExecutingActor(),
                   value_for=lambda r, o: "banana",
                   is_done=lambda o: "banana" in o.page_text, max_steps=3, log_corpus=False)

    assert res.status is LoopStatus.COMPLETED
    assert res.steps[0].verify["ok"] is True
