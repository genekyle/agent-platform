"""Offline tests for the LiveActuator — proving the dispatch table is correct against a FAKE
capture server, so the seam is trustworthy before it ever drives a real job application.

The one seam that makes this possible: `transport` is injected. The real one POSTs to the mcp
capture server; here it's a recorder that returns canned responses and lets us assert exactly
which endpoint each Decision hits and with what payload.
"""

from __future__ import annotations

import pytest

from controller.live_actuator import LiveActuator
from interaction.contract import Outcome
from interaction.decision import Decision

_INDEED = "https://smartapply.indeed.com/questions"
_GREENHOUSE = "https://job-boards.greenhouse.io/acme/jobs/123"


class FakeTransport:
    """Records (path, payload) and returns canned responses; can be told to raise on a path."""

    def __init__(self, *, url, unanswered=None, logged_in=True, responses=None, raise_on=None):
        self.calls = []
        self._url = url
        self._unanswered = unanswered or []
        self._logged_in = logged_in
        self._responses = responses or {}
        self._raise_on = set(raise_on or ())

    def __call__(self, path, payload):
        self.calls.append((path, dict(payload)))
        if path in self._raise_on:
            raise RuntimeError("transport boom")
        if path == "/auth_state":
            return {"ok": True, "logged_in": self._logged_in, "url": self._url}
        if path == "/scan_required":
            return {"ok": True, "outcome": "ok", "unanswered": self._unanswered}
        return self._responses.get(path, {"ok": True, "outcome": "ok"})

    def paths(self):
        return [p for p, _ in self.calls]

    def payload_for(self, path):
        return next(pl for p, pl in self.calls if p == path)


def _actuator(fake, *, task="indeed_apply"):
    return LiveActuator(base_url="http://x", browser_url="http://localhost:9328",
                        tab_id="TAB", task=task, transport=fake)


# --- observe ---------------------------------------------------------------------------
def test_observe_builds_bundle_and_keeps_raw_scan():
    raw = [{"field": "work_authorization", "selector": "#q1", "kind": "radio"}]
    fake = FakeTransport(url=_INDEED, unanswered=raw)
    act = _actuator(fake)

    bundle = act.observe()

    # smartapply classifies as `indeed_quick_apply` — which is exactly why Indeed's dynamic
    # fields fall through apply_fields (keyed under `indeed`) to the live scan below.
    assert bundle.ats == "indeed_quick_apply"
    assert bundle.url == _INDEED
    # The bundle carries the SANITIZED unanswered (no selector leaks to decide()).
    assert all("selector" not in u for u in bundle.unanswered)
    assert any(u.get("field") == "work_authorization" for u in bundle.unanswered)
    # ...but the actuator kept the RAW scan (with selectors) for act().
    assert act._last_scan == raw
    assert "/auth_state" in fake.paths() and "/scan_required" in fake.paths()


def test_observe_logged_out_escalates_to_human():
    fake = FakeTransport(url=_INDEED, logged_in=False)
    bundle = _actuator(fake).observe()
    assert bundle.human_required is True
    assert "logged in" in bundle.branch_note or "authenticate" in bundle.branch_note


# --- act: field addressing -------------------------------------------------------------
def test_set_text_uses_the_live_scan_selector_for_indeed_dynamic_field():
    # Indeed apply questions aren't in apply_fields (INDEED_FIELDS is ~empty) — their selector
    # comes from the live scan. This is the fallback path the resolver can't cover.
    raw = [{"field": "years_experience", "selector": "#input-42", "kind": "text"}]
    fake = FakeTransport(url=_INDEED, unanswered=raw)
    act = _actuator(fake)
    act.observe()

    out = act.act(Decision(intent="set_text", params={"field": "years_experience", "value": "5"},
                           confidence=1.0, rung="model", rationale="", expected_next=()))

    exe = fake.payload_for("/execute")
    assert exe["action_id"] == "type"
    assert exe["selector"] == "#input-42"      # from the live scan, not a guess
    assert exe["value"] == "5"
    assert exe["tab_id"] == "TAB"              # addressed by stable tab id, not tab_url
    assert out.outcome == Outcome.OK.value


def test_select_option_resolves_static_greenhouse_selector():
    fake = FakeTransport(url=_GREENHOUSE, responses={"/select_option": {"ok": True, "outcome": "ok"}})
    act = _actuator(fake, task="greenhouse_apply")
    act.observe()
    assert act._ats == "greenhouse"

    act.act(Decision(intent="select_option", params={"field": "school", "value": "Other"},
                     confidence=1.0, rung="model", rationale="", expected_next=()))

    body = fake.payload_for("/select_option")
    assert body["selector"] == "#school--0"    # from apply_fields.resolve, the static recipe
    assert body["value"] == "Other"
    assert body["ats"] == "greenhouse" and body["field"] == "school"


def test_click_drives_execute_by_control_name():
    fake = FakeTransport(url=_INDEED)
    act = _actuator(fake)
    act.observe()

    act.act(Decision(intent="click", params={"control": "Continue"},
                     confidence=1.0, rung="recipe", rationale="", expected_next=("next",)))

    exe = fake.payload_for("/execute")
    assert exe["action_id"] == "click"
    assert exe["target_name"] == "Continue"


def test_unaddressable_field_is_not_found_not_a_guess():
    fake = FakeTransport(url=_INDEED, unanswered=[])   # empty scan, no static entry
    act = _actuator(fake)
    act.observe()
    out = act.act(Decision(intent="set_text", params={"field": "ghost_field", "value": "x"},
                           confidence=1.0, rung="model", rationale="", expected_next=()))
    assert out.outcome == Outcome.NOT_FOUND.value
    assert "/execute" not in fake.paths()          # never fired a guessed action


# --- act: the SUBMIT gate --------------------------------------------------------------
def test_submit_is_refused_never_driven():
    fake = FakeTransport(url=_INDEED)
    act = _actuator(fake)
    act.observe()
    n_before = len(fake.calls)
    out = act.act(Decision(intent="submit", params={}, confidence=1.0, rung="model",
                           rationale="", expected_next=()))
    assert out.outcome == Outcome.BLOCKED.value
    # Nothing was posted for a submit — the actuator refuses before any endpoint call.
    assert len(fake.calls) == n_before


# --- resilience: never raise into the loop ---------------------------------------------
def test_transport_error_becomes_error_outcome_not_a_crash():
    raw = [{"field": "years_experience", "selector": "#input-42", "kind": "text"}]
    fake = FakeTransport(url=_INDEED, unanswered=raw, raise_on={"/execute"})
    act = _actuator(fake)
    act.observe()
    out = act.act(Decision(intent="set_text", params={"field": "years_experience", "value": "5"},
                           confidence=1.0, rung="model", rationale="", expected_next=()))
    assert out.outcome == Outcome.ERROR.value       # a clean handoff, not an exception


def test_observe_survives_a_scan_transport_failure():
    fake = FakeTransport(url=_INDEED, raise_on={"/scan_required"})
    bundle = _actuator(fake).observe()              # must not raise
    assert bundle.url == _INDEED
    assert bundle.unanswered == ()                  # degraded to no known unanswered fields
