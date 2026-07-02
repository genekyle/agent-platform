"""Tests for the handoff record: build (pure), persist/list/resolve (round-trip), notify.

Persistence is redirected to a tmp artifacts dir by monkeypatching the path helper;
the macOS banner is monkeypatched to a no-op so tests never shell out.
"""

from __future__ import annotations

from runtime import handoff as handoff_mod
from runtime.loop import LoopResult, LoopStatus, Observation, StepRecord


def _step(idx, action="click", layer="cache", conf=1.0, status="executed_verified",
          reason="cache_hit", verify=None, gate=None):
    return StepRecord(step_index=idx, retry=0, ts="t", route="ex.com/p", fingerprint="fp",
                      task_goal="g", candidate_count=3, action_id=action,
                      target_backend_node_id=5, confidence=conf, reason_code=reason,
                      layer=layer, cost_usd=0.0, needs_human=False, executed=True,
                      driver="humanized", status=status, verify=verify, gate=gate)


def _escalated_result(reason="low_confidence"):
    steps = [
        _step(0),  # a practiced (cache) step that worked
        _step(1, layer="som_haiku", conf=0.2, status="escalated", reason=reason, verify={"ok": False}),
    ]
    return LoopResult(LoopStatus.ESCALATED, steps,
                      reason=f"select escalated: {reason}", escalation_reason=reason)


def _obs():
    return Observation(url="https://x.com/apply", page_text="", ax_candidates=[],
                       screenshot_path="/shots/x.png")


def test_build_handoff_captures_why_and_trace():
    h = handoff_mod.build_handoff(_escalated_result(), task_goal="click apply",
                                  training_session_id=7, last_observation=_obs(), tab_url="x.com")
    assert h.escalation_reason == "low_confidence"
    assert "confident" in h.why.lower()
    assert h.suggestion                       # a concrete next step
    assert h.url == "https://x.com/apply"
    assert h.screenshot_path == "/shots/x.png"
    assert h.training_session_id == 7
    assert len(h.tried) == 2
    # the trace shows the practiced free step AND the paid escalating step
    assert h.tried[0]["layer"] == "cache"
    assert h.tried[1]["layer"] == "som_haiku"
    assert h.tried[1]["verified"] is False
    assert h.id.startswith("hoff_")


def test_build_handoff_surfaces_gate_block_reason():
    """A gate-blocked stop should read as the specific reason (needs approval), not generic."""
    blocked = _step(1, action="submit", status="escalated", reason="som_pick",
                    gate={"reason": "needs_approval", "action": "submit",
                          "target": "Submit application",
                          "guidance": "The operator must approve it."})
    r = LoopResult(LoopStatus.ESCALATED, [_step(0), blocked],
                   reason="gate blocked: needs_approval", escalation_reason="gate_blocked")
    h = handoff_mod.build_handoff(r, task_goal="apply")
    assert "approval" in h.why.lower()
    assert "Submit application" in h.why
    assert h.suggestion == "The operator must approve it."


def test_build_handoff_captcha_diagnostic_becomes_headline():
    """A post-failure captcha probe that came back blocking should headline the handoff."""
    r = LoopResult(LoopStatus.ESCALATED, [_step(0, status="escalated", reason="verifier_failed",
                                                 verify={"ok": False})],
                   reason="verify failed after retries", escalation_reason="verifier_failed")
    diag = {"reason": "captcha", "guidance": "Solve the captcha, then resume."}
    h = handoff_mod.build_handoff(r, task_goal="apply", diagnostic=diag)
    assert "captcha" in h.why.lower()
    assert h.suggestion == "Solve the captcha, then resume."
    assert h.diagnostic == diag


def test_build_handoff_max_steps_reads_as_incomplete():
    r = LoopResult(LoopStatus.MAX_STEPS, [_step(0)], reason="reached max_steps=12",
                   escalation_reason=None)
    h = handoff_mod.build_handoff(r, task_goal="apply")
    assert "incomplete" in h.why.lower()
    assert h.loop_status == "max_steps"


def test_build_handoff_unknown_reason_has_generic_guidance():
    r = LoopResult(LoopStatus.ESCALATED, [_step(0)], reason="weird", escalation_reason="something_new")
    h = handoff_mod.build_handoff(r, task_goal="g")
    assert h.why and h.suggestion            # never empty, even for an unmapped reason


def test_persist_list_resolve_roundtrip(monkeypatch, tmp_path):
    path = tmp_path / "handoffs.jsonl"
    monkeypatch.setattr(handoff_mod, "_handoffs_path", lambda: path)

    h = handoff_mod.build_handoff(_escalated_result(), task_goal="click apply")
    handoff_mod.persist(h)

    rows = handoff_mod.list_handoffs()
    assert len(rows) == 1
    assert rows[0]["id"] == h.id
    assert rows[0]["status"] == "open"

    assert handoff_mod.resolve(h.id) is True
    assert handoff_mod.resolve("hoff_unknown") is False

    open_rows = handoff_mod.list_handoffs(open_only=True)
    assert open_rows == []                    # the only one is now resolved
    all_rows = handoff_mod.list_handoffs()
    assert all_rows[0]["status"] == "resolved"
    assert all_rows[0]["resolved_at"]


def test_list_newest_first(monkeypatch, tmp_path):
    path = tmp_path / "handoffs.jsonl"
    monkeypatch.setattr(handoff_mod, "_handoffs_path", lambda: path)
    h1 = handoff_mod.build_handoff(_escalated_result(), task_goal="first")
    h2 = handoff_mod.build_handoff(_escalated_result(), task_goal="second")
    handoff_mod.persist(h1)
    handoff_mod.persist(h2)
    rows = handoff_mod.list_handoffs()
    assert [r["task_goal"] for r in rows] == ["second", "first"]


def test_emit_persists_and_notifies(monkeypatch, tmp_path):
    path = tmp_path / "handoffs.jsonl"
    monkeypatch.setattr(handoff_mod, "_handoffs_path", lambda: path)
    banners = []
    monkeypatch.setattr(handoff_mod, "_macos_banner",
                        lambda title, message: banners.append((title, message)))
    monkeypatch.setattr(handoff_mod, "_notify_channel", lambda: "macos")

    h = handoff_mod.emit(_escalated_result(), task_goal="click apply", training_session_id=1)
    assert handoff_mod.list_handoffs()[0]["id"] == h.id     # persisted
    assert len(banners) == 1                                 # notified
    assert "click apply" in banners[0][0]


def test_notify_off_channel_is_silent(monkeypatch):
    banners = []
    monkeypatch.setattr(handoff_mod, "_macos_banner",
                        lambda title, message: banners.append(1))
    monkeypatch.setattr(handoff_mod, "_notify_channel", lambda: "off")
    handoff_mod.notify(handoff_mod.build_handoff(_escalated_result(), task_goal="g"))
    assert banners == []
