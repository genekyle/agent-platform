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


# --- emit_escalation: the alert for loops that have no LoopResult -------------------------------
def _quiet(monkeypatch, tmp_path):
    """Redirect persistence to tmp and capture banners instead of shelling out."""
    monkeypatch.setattr(handoff_mod, "_handoffs_path", lambda: tmp_path / "handoffs.jsonl")
    banners = []
    monkeypatch.setattr(handoff_mod, "_macos_banner",
                        lambda title, message: banners.append((title, message)))
    monkeypatch.setattr(handoff_mod, "_notify_channel", lambda: "macos")
    return banners


def test_emit_escalation_alerts_without_a_loop_result(monkeypatch, tmp_path):
    """The login drive and the controller produce no LoopResult — before this they had no alert
    at all. A stale tab must now reach the same handoffs log and the same banner."""
    banners = _quiet(monkeypatch, tmp_path)
    h = handoff_mod.emit_escalation(reason="stale_tab", task_goal="ATS login — acme",
                                    detail="the tab went away", url="https://acme.wd1.com/login")
    assert h is not None
    rows = handoff_mod.list_handoffs()
    assert rows[0]["id"] == h.id and rows[0]["escalation_reason"] == "stale_tab"
    assert "no longer exists" in h.why           # plain-language guidance, not a raw status
    assert h.suggestion and len(banners) == 1


def test_emit_escalation_maps_login_statuses_to_plain_language(monkeypatch, tmp_path):
    _quiet(monkeypatch, tmp_path)
    for reason, needle in [("captcha", "captcha"), ("mfa", "2fa"),
                           ("bad_credentials", "rejected"), ("unexpected_state", "recognise")]:
        h = handoff_mod.emit_escalation(reason=reason, task_goal="g")
        assert needle in h.why.lower(), reason

    # An unmapped status still produces a usable alert rather than an empty one.
    h = handoff_mod.emit_escalation(reason="something_new", task_goal="g")
    assert h.why and h.suggestion


def test_emit_escalation_never_raises_into_the_drive(monkeypatch, tmp_path):
    """An alert must never break the drive it is reporting on."""
    _quiet(monkeypatch, tmp_path)

    def boom(_):
        raise OSError("disk gone")

    monkeypatch.setattr(handoff_mod, "persist", boom)
    assert handoff_mod.emit_escalation(reason="stale_tab", task_goal="g") is None


def test_escalation_callback_shapes_a_controller_escalation(monkeypatch, tmp_path):
    """The on_escalate seam: (bundle, decision) -> an alert carrying the state + rung."""
    _quiet(monkeypatch, tmp_path)

    class B:
        goal_text, task, url, state = "apply to acme", "apply", "https://acme/apply", "acme_questions"

    class D:
        rationale, intent, rung, confidence = "no program for this state", "click", "model", 0.4

    handoff_mod.escalation_callback(task_goal="apply run")(B(), D())
    row = handoff_mod.list_handoffs()[0]
    assert row["escalation_reason"] == "unexpected_state"
    assert row["detail"] == "no program for this state"
    assert row["tried"][0]["state"] == "acme_questions"
    assert row["tried"][0]["layer"] == "model"


def test_escalation_callback_carries_context_from_the_bundle(monkeypatch, tmp_path):
    """The record should say WHERE it was and WHAT the page wanted, not just 'unrecognised'."""
    _quiet(monkeypatch, tmp_path)

    class B:
        goal_text, task, url, state = "apply", "apply", "https://acme/apply", "acme_questions"
        next_action = "click Continue"
        unanswered = ({"field": "Job title", "kind": "input"}, {"field": "Company", "kind": "input"})
        belief = {"state": "acme_submitted", "agreement": "split"}

    class D:
        rationale, intent, rung, confidence = "no program", "set_text", "teacher", 0.3
        escalation_axis = "unknown_state"

    handoff_mod.escalation_callback(task_goal="apply run")(B(), D())
    ctx = handoff_mod.list_handoffs()[0]["context"]
    assert ctx["state"] == "acme_questions"
    assert ctx["needs"] == ["Job title", "Company"]        # names only, never values
    assert ctx["observer"] == {"state": "acme_submitted", "agreement": "split"}
    assert ctx["stuck_on"] == "unknown_state"


# --- dedup: one situation, one open handoff (live 2026-07-23) ---------------------------
def test_the_same_situation_updates_one_handoff_instead_of_stacking(monkeypatch, tmp_path):
    """Four drives parking on one work-experience page had made four identical handoffs. A repeat
    of the same page+state+reason must bump the open record, not open a fifth."""
    monkeypatch.setattr(handoff_mod, "_handoffs_path", lambda: tmp_path / "handoffs.jsonl")
    monkeypatch.setenv("HANDOFF_NOTIFY", "off")

    url = "https://smartapply.indeed.com/beta/indeedapply/form/resume-module/profile-work-experience/append"
    first = handoff_mod.emit_escalation(reason="unexpected_state", task_goal="finish the application",
                                        url=url, state="indeed_apply_resume_review")
    second = handoff_mod.emit_escalation(reason="unexpected_state",
                                         task_goal="finish and submit the application",
                                         url=url + "?vjk=abc", state="indeed_apply_resume_review")

    assert first.id == second.id, "a repeat opened a new record instead of updating the open one"
    assert second.occurrences == 2
    openh = handoff_mod.list_handoffs(open_only=True)
    assert len(openh) == 1, "the inbox should hold ONE handoff for one situation"
    assert openh[0]["occurrences"] == 2


def test_a_different_page_is_a_different_handoff(monkeypatch, tmp_path):
    monkeypatch.setattr(handoff_mod, "_handoffs_path", lambda: tmp_path / "handoffs.jsonl")
    monkeypatch.setenv("HANDOFF_NOTIFY", "off")
    a = handoff_mod.emit_escalation(reason="unexpected_state", task_goal="g",
                                    url="https://x/apply/step-1", state="s1")
    b = handoff_mod.emit_escalation(reason="unexpected_state", task_goal="g",
                                    url="https://x/apply/step-2", state="s2")
    assert a.id != b.id
    assert len(handoff_mod.list_handoffs(open_only=True)) == 2


def test_a_resolved_situation_can_be_raised_fresh(monkeypatch, tmp_path):
    """Dedup only collapses OPEN handoffs. Once the operator clears one, the same page stopping
    again is genuinely new — it must not silently re-open the resolved record."""
    monkeypatch.setattr(handoff_mod, "_handoffs_path", lambda: tmp_path / "handoffs.jsonl")
    monkeypatch.setenv("HANDOFF_NOTIFY", "off")
    a = handoff_mod.emit_escalation(reason="unexpected_state", task_goal="g",
                                    url="https://x/apply/step", state="s")
    handoff_mod.resolve(a.id)
    b = handoff_mod.emit_escalation(reason="unexpected_state", task_goal="g",
                                    url="https://x/apply/step", state="s")
    assert b.id != a.id
    assert len(handoff_mod.list_handoffs(open_only=True)) == 1


def test_context_is_carried_so_the_record_is_not_generic(monkeypatch, tmp_path):
    monkeypatch.setattr(handoff_mod, "_handoffs_path", lambda: tmp_path / "handoffs.jsonl")
    monkeypatch.setenv("HANDOFF_NOTIFY", "off")
    ctx = {"state": "indeed_apply_resume_review", "needs": ["Job title", "Company"],
           "observer": "disagrees with the recipe"}
    h = handoff_mod.emit_escalation(reason="unexpected_state", task_goal="g",
                                    url="https://x/apply", state="indeed_apply_resume_review",
                                    context=ctx)
    got = handoff_mod.list_handoffs(open_only=True)[0]
    assert got["context"]["needs"] == ["Job title", "Company"]
