"""Tests for the apply-flow state store — blackboard, gates, deterministic reconcile.

These exercise the part the whole design hinges on: the invariant gate makes the store
structurally unable to mark a form subtask done while a required field is empty/invalid,
and reconcile folds a fresh observation in deterministically (no model, no browser)."""

import apply_state_store as store
from apply_state_store import FieldState


# --- form scanner reducer + gate ---------------------------------------------------
def test_build_form_state_defaults():
    fs = store.build_form_state([
        {"field_id": "q1", "label": "Salary", "kind": "text", "required": True, "filled": True},
        {"label": "State", "kind": "select", "required": True},  # no id, not filled
    ])
    assert fs[0].field_id == "q1" and fs[0].satisfied is True
    assert fs[1].field_id == "field_1"          # synthesized id
    assert fs[1].required and not fs[1].filled
    assert fs[1].satisfied is False             # required + empty => blocks


def test_gate_blocks_on_empty_required_field():
    fs = store.build_form_state([
        {"field_id": "country", "label": "Country", "required": True, "filled": True},
        {"field_id": "state", "label": "State", "required": True, "filled": False},  # the classic miss
    ])
    gate = store.form_complete_gate(fs)
    assert gate.ok is False
    assert gate.satisfied == ["country"]
    assert gate.unsatisfied[0]["field_id"] == "state"
    assert gate.unsatisfied[0]["reason"] == "empty"


def test_gate_blocks_on_invalid_required_field():
    fs = [FieldState("email", "Email", "text", required=True, filled=True, valid=False)]
    gate = store.form_complete_gate(fs)
    assert gate.ok is False
    assert gate.unsatisfied[0]["reason"] == "invalid"


def test_gate_ignores_optional_empty_fields():
    fs = store.build_form_state([
        {"field_id": "opt", "label": "LinkedIn", "required": False, "filled": False},
    ])
    assert store.form_complete_gate(fs).ok is True


# --- blackboard lifecycle + reconcile ----------------------------------------------
def _apply_tab(state, **kw):
    base = {"url": f"https://smartapply.indeed.com/{state}", "state": state, "role": "apply",
            "human_required": False, "branch_note": None}
    base.update(kw)
    return base


def test_default_plan_gates_form_steps_only():
    plan = store.default_plan()
    gated = {s.id for s in plan if s.gate == "form_complete"}
    assert "indeed_apply_demographics" in gated
    assert "indeed_apply_review" not in gated  # navigation step, no form gate


def test_reconcile_advances_and_blocks_on_form():
    bb = store.new_blackboard(session_id=7)
    # On demographics with an empty required field -> subtask BLOCKED, gate not ok.
    bb = store.reconcile(
        bb,
        tabs=[_apply_tab("indeed_apply_demographics")],
        form_fields=[{"field_id": "gender", "label": "Gender", "required": True, "filled": False}],
        last_action="open demographics", last_result="ok",
    )
    cur = next(s for s in bb.plan if s.id == bb.current_subtask_id)
    assert cur.id == "indeed_apply_demographics"
    assert cur.status == "blocked"
    assert any(b.kind == "required_field" for b in bb.blockers)
    assert bb.to_dict()["gate_ok"] is False

    # Fill it -> gate passes, subtask becomes active (not blocked).
    bb = store.reconcile(
        bb,
        tabs=[_apply_tab("indeed_apply_demographics")],
        form_fields=[{"field_id": "gender", "label": "Gender", "required": True, "filled": True}],
    )
    cur = next(s for s in bb.plan if s.id == bb.current_subtask_id)
    assert cur.status == "active"
    assert bb.to_dict()["gate_ok"] is True


def test_reconcile_marks_earlier_steps_done():
    bb = store.new_blackboard(session_id=1)
    bb = store.reconcile(bb, tabs=[_apply_tab("indeed_apply_review")])
    by_id = {s.id: s for s in bb.plan}
    assert by_id["indeed_apply_resume_selection"].status == "done"
    assert by_id["indeed_apply_review"].status == "active"
    assert by_id["indeed_apply_submitted"].status == "pending"


def test_reconcile_lifts_human_branch_blocker():
    bb = store.new_blackboard(session_id=2)
    bb = store.reconcile(bb, tabs=[
        _apply_tab("captcha", human_required=True, branch_note="reCAPTCHA box")])
    assert bb.to_dict()["needs_human"] is True
    assert any(b.human_required and b.kind == "human_branch" for b in bb.blockers)


def test_state_change_is_logged():
    bb = store.new_blackboard(session_id=3)
    store.reconcile(bb, tabs=[_apply_tab("indeed_apply_questions")])
    store.reconcile(bb, tabs=[_apply_tab("indeed_apply_review")])
    kinds = [e.kind for e in bb.events]
    assert "state_change" in kinds


def test_roundtrip_serialization():
    bb = store.new_blackboard(session_id=42)
    store.reconcile(bb, tabs=[_apply_tab("indeed_apply_questions")],
                    form_fields=[{"field_id": "q", "label": "Q", "required": True, "filled": True}])
    back = store.Blackboard.from_dict(bb.to_dict())
    assert back.session_id == 42
    assert back.current_subtask_id == bb.current_subtask_id
    assert [f.field_id for f in back.form_state] == [f.field_id for f in bb.form_state]
    assert [s.status for s in back.plan] == [s.status for s in bb.plan]


def test_proceed_decision_blocks_on_empty_required_field():
    bb = store.new_blackboard(session_id=11)
    store.reconcile(bb, tabs=[_apply_tab("indeed_apply_demographics")],
                    form_fields=[{"field_id": "gender", "label": "Gender",
                                  "required": True, "filled": False}])
    d = store.proceed_decision(bb)
    assert d["ok"] is False and d["reason"] == "form_incomplete"
    assert d["blockers"][0]["field_id"] == "gender"


def test_proceed_decision_clear_when_form_complete():
    bb = store.new_blackboard(session_id=12)
    store.reconcile(bb, tabs=[_apply_tab("indeed_apply_demographics")],
                    form_fields=[{"field_id": "gender", "label": "Gender",
                                  "required": True, "filled": True}])
    d = store.proceed_decision(bb)
    assert d["ok"] is True and d["reason"] == "clear"


def test_block_frame_surfaces_as_human_blocker_even_without_captcha_tab():
    """The live gap: a search tab + an active captcha iframe → needs_human MUST be true,
    even though no tab URL maps to a captcha state."""
    bb = store.new_blackboard(session_id=21)
    store.reconcile(bb, tabs=[_apply_tab("indeed_search_results", role="search")],
                    block={"provider": "recaptcha", "strength": "active",
                           "reason": "recaptcha challenge frame present"})
    d = bb.to_dict()
    assert d["needs_human"] is True
    assert any(b.kind == "captcha" and b.human_required for b in bb.blockers)
    assert store.proceed_decision(bb)["ok"] is False


def test_passive_block_is_advisory_not_human_required():
    bb = store.new_blackboard(session_id=22)
    store.reconcile(bb, tabs=[_apply_tab("indeed_search_results", role="search")],
                    block={"provider": "recaptcha_checkbox", "strength": "passive",
                           "reason": "passive widget"})
    assert bb.to_dict()["needs_human"] is False
    assert any(b.kind == "captcha_passive" and not b.human_required for b in bb.blockers)


def test_proceed_decision_human_branch_outranks_form():
    bb = store.new_blackboard(session_id=13)
    store.reconcile(bb, tabs=[_apply_tab("captcha", human_required=True,
                                         branch_note="reCAPTCHA box")])
    d = store.proceed_decision(bb)
    assert d["ok"] is False and d["reason"] == "human_required"


def test_make_proceed_gate_blocks_a_real_loop():
    """End-to-end: the store's gate, handed to run_loop, refuses the action when a
    required field is empty — the action never fires, the loop escalates."""
    from runtime.loop import LoopStatus, Observation, run_loop
    from runtime import loop as loop_mod
    from select_stage.schema import ActionId, ReasonCode, SelectionResult

    bb = store.new_blackboard(session_id=14)
    store.reconcile(bb, tabs=[_apply_tab("indeed_apply_demographics")],
                    form_fields=[{"field_id": "state", "label": "State",
                                  "required": True, "filled": False}])

    fired = {"n": 0}

    class _Actor:
        def perform(self, **kw):
            fired["n"] += 1
            from runtime.loop import ActResult
            return ActResult(executed=True, driver="fake")

    obs = Observation(url="https://smartapply.indeed.com/demographic-questions",
                      page_text="demographics", ax_candidates=[],
                      screenshot_path="/tmp/x.png", viewport={}, dom_clickables=[])
    loop_mod.selector.select = lambda **kw: SelectionResult(  # type: ignore[assignment]
        ActionId.CLICK, 5, 0.95, False, ReasonCode.SOM_PICK, layer="x",
        cost_usd=0.0, fingerprint="fp")

    res = run_loop(task_goal="submit application", proposer=lambda: obs,
                   actor=_Actor(), gate=store.make_proceed_gate(bb),
                   max_steps=2, log_corpus=False)

    assert res.status is LoopStatus.ESCALATED
    assert res.escalation_reason == "gate_blocked"
    assert fired["n"] == 0  # confident pick, but the empty required field stopped it


# --- session-spanning: search phase + phase transitions ----------------------------
def _search_tab(state, **kw):
    base = {"url": f"https://www.indeed.com/{state}", "state": state, "role": "search",
            "human_required": False, "branch_note": None}
    base.update(kw)
    return base


def test_new_blackboard_starts_in_search_with_target_seeded():
    bb = store.new_blackboard(session_id=30, query="reporting analyst", location="Nashua, NH")
    assert bb.phase == "search"
    assert bb.search_state.query == "reporting analyst"
    assert bb.search_state.location == "Nashua, NH"
    # the plan is the search spine, not the apply spine
    assert [s.id for s in bb.plan] == [s.id for s in store.search_plan()]
    assert any(s.id == "indeed_search_results" for s in bb.plan)
    assert all(s.gate is None for s in bb.plan)  # no form gates in search


def test_reconcile_tracks_search_progress_and_advances_search_plan():
    bb = store.new_blackboard(session_id=31, query="reporting analyst", location="Nashua, NH")
    bb = store.reconcile(bb, tabs=[_search_tab("indeed_search_results")],
                         search_update={"page": 2, "observed_count": 17})
    assert bb.phase == "search"
    assert bb.search_state.page == 2 and bb.search_state.observed_count == 17
    cur = next(s for s in bb.plan if s.id == bb.current_subtask_id)
    assert cur.id == "indeed_search_results" and cur.status == "active"
    by_id = {s.id: s for s in bb.plan}
    assert by_id["indeed_home"].status == "done"


def test_phase_flips_to_triage_on_job_posting_then_apply_on_apply_tab():
    bb = store.new_blackboard(session_id=32, query="reporting analyst", location="Nashua, NH")
    bb = store.reconcile(bb, tabs=[_search_tab("indeed_job_posting")])
    assert bb.phase == "triage"  # still the search-family spine
    assert any(s.id == "indeed_job_posting" for s in bb.plan)
    # opening the apply flow swaps the spine to the apply plan
    bb = store.reconcile(bb, tabs=[_apply_tab("indeed_apply_questions")])
    assert bb.phase == "apply"
    assert {s.id for s in bb.plan} >= {"indeed_apply_questions", "indeed_apply_submitted"}
    assert any(e.kind == "phase_change" for e in bb.events)


def test_captcha_during_search_halts_proceed():
    """Captcha-understanding is cross-phase: an active challenge frame while we're still on the
    search results page must flip needs_human + block proceed, same as one at submit."""
    bb = store.new_blackboard(session_id=33, query="reporting analyst", location="Nashua, NH")
    bb = store.reconcile(bb, tabs=[_search_tab("indeed_search_results")],
                         block={"provider": "recaptcha", "strength": "active",
                                "reason": "recaptcha challenge frame present"})
    assert bb.phase == "search"
    assert bb.to_dict()["needs_human"] is True
    assert any(b.kind == "captcha" and b.human_required for b in bb.blockers)
    assert store.proceed_decision(bb)["ok"] is False


def test_search_state_survives_roundtrip_and_reload():
    bb = store.new_blackboard(session_id=34, query="reporting analyst", location="Nashua, NH")
    store.reconcile(bb, tabs=[_search_tab("indeed_search_results")],
                    search_update={"page": 3, "observed_count": 25, "shortlist": ["vjk1", "vjk2"]})
    back = store.Blackboard.from_dict(bb.to_dict())
    assert back.phase == "search"
    assert back.search_state.page == 3 and back.search_state.observed_count == 25
    assert back.search_state.shortlist == ["vjk1", "vjk2"]


def test_persistence_load_or_create(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_store_dir", lambda: tmp_path)
    bb = store.load_or_create(99)
    store.reconcile(bb, tabs=[_apply_tab("indeed_apply_review")])
    store.save(bb)
    again = store.load_or_create(99)
    assert again.current_subtask_id == "indeed_apply_review"
    # a fresh session id has no file yet -> brand new plan, nothing done
    fresh = store.load_or_create(100)
    assert all(s.status == "pending" for s in fresh.plan)
