"""Tests for what may become a rung-0 program — the guard that matters most.

Rung 0 is the rung that replays WITHOUT asking anyone, so a bad program is worse than no
program: it acts unattended and confidently. These pin the rejections.
"""

from __future__ import annotations

import apply_recipe
from controller import programs as P


def _row(state, intent, params=None, *, outcome="ok", verified=True, escalate=False,
         task="indeed_apply", expected_next=()):
    return {"task": task, "state": state, "intent": intent, "params": params or {},
            "outcome": outcome, "verified": verified, "escalate": escalate,
            "expected_next": list(expected_next), "bundle_digest": "d1"}


def test_target_parameterised_states_never_compile():
    """indeed_job_posting's action is "click the job we're pursuing" — the control name is the
    job title, so a compiled program would click whatever job now sits in that slot."""
    rows = [_row("indeed_job_posting", "click",
                 {"control": "full details of Analyst - Actuarial Financial Reporting"})]
    assert P.rejection_reason(rows) is not None
    assert "target-parameterised" in P.rejection_reason(rows)
    assert P.compile_from_journal(rows) is None


def test_a_program_of_only_no_ops_is_not_a_program():
    rows = [_row("workday_job_posting", "observe", {})]
    assert "no-op" in P.rejection_reason(rows)
    assert P.compile_from_journal(rows) is None


def test_unverified_or_escalated_rows_do_not_compile():
    assert P.rejection_reason([_row("indeed_apply_questions", "click", escalate=True)])
    assert P.rejection_reason([_row("indeed_apply_questions", "click", outcome="not_found")])
    assert P.rejection_reason([_row("indeed_apply_questions", "click", verified=False)])


def test_a_real_spine_state_compiles_and_drops_no_ops():
    rows = [
        _row("indeed_apply_questions", "observe", {}),                      # dropped
        _row("indeed_apply_questions", "select_option",
             {"field": "SMS recruiting-text consent", "value": "No"}),      # value stripped
        _row("indeed_apply_questions", "click", {"control": "Continue"}),
    ]
    prog = P.compile_from_journal(rows)
    assert prog is not None
    assert [s["intent"] for s in prog.steps] == ["select_option", "click"]
    assert "value" not in prog.steps[0]["params"]        # never store the answer itself
    assert prog.guard_fields == ("SMS recruiting-text consent",)


def test_missing_exit_inherits_the_recipe_edges_but_is_never_invented():
    rows = [_row("indeed_apply_demographics", "click", {"control": "Review your application"})]
    res = P.compile_all_from_journal(rows, save=False,
                                     expected_exit_for=apply_recipe.expected_next_for)
    assert res["compiled"][0]["expected_exit"] == list(
        apply_recipe.expected_next_for("indeed_apply_demographics"))

    # A state the recipe knows nothing about inherits nothing — no fabricated landing state.
    unknown = [_row("some_unmapped_state", "click", {"control": "Next"})]
    res2 = P.compile_all_from_journal(unknown, save=False,
                                      expected_exit_for=apply_recipe.expected_next_for)
    assert res2["compiled"][0]["expected_exit"] == []


def test_rejections_are_reported_not_silently_skipped():
    """"nothing compiled" and "everything was rejected for a reason" must not look alike."""
    rows = [_row("indeed_job_posting", "click", {"control": "full details of X"})]
    res = P.compile_all_from_journal(rows, save=False)
    assert res["compiled"] == []
    assert len(res["rejected"]) == 1 and res["rejected"][0]["reason"]


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROLLER_PROGRAMS_DIR", str(tmp_path))
    rows = [_row("indeed_apply_questions", "click", {"control": "Continue"})]
    P.compile_all_from_journal(rows, save=False)
    assert list(tmp_path.glob("*.json")) == []
    P.compile_all_from_journal(rows, save=True)
    assert len(list(tmp_path.glob("*.json"))) == 1


# --- the automatic pardon: staleness is a one-way door no longer (2026-08-20) -------------------

def test_new_evidence_compiles_where_no_program_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROLLER_PROGRAMS_DIR", str(tmp_path))
    rows = [dict(_row("indeed_apply_questions", "click", {"control": "Continue"}),
                 ts="2026-08-20T10:00:00+00:00")]
    out = P.recompile_from_new_evidence(rows)
    assert [c["state"] for c in out["compiled"]] == ["indeed_apply_questions"]
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_a_stale_program_is_pardoned_only_by_newer_proof(tmp_path, monkeypatch):
    """The measured cost of the one-way door: 26 of 45 teacher parks on one state whose program
    sat condemned while the journal held fresh verified teacher steps. The pardon must come from
    NEW evidence — recompiling the same rows would resurrect the exact program the world just
    proved wrong, on the one rung that replays without asking anyone."""
    monkeypatch.setenv("CONTROLLER_PROGRAMS_DIR", str(tmp_path))
    old = [dict(_row("indeed_apply_questions", "click", {"control": "Continue"}),
                ts="2026-08-19T10:00:00+00:00")]
    P.recompile_from_new_evidence(old)
    P.mark_stale("indeed_apply", "indeed_apply_questions")
    assert P.load_program("indeed_apply", "indeed_apply_questions").stale is True

    # The same old rows again: staleness STANDS.
    out = P.recompile_from_new_evidence(old)
    assert out["compiled"] == []
    assert "staleness stands" in out["skipped"][0]["reason"]
    assert P.load_program("indeed_apply", "indeed_apply_questions").stale is True

    # A teacher answered the escalations — rows newer than the condemned compile pardon it.
    fresh = old + [dict(_row("indeed_apply_questions", "click", {"control": "Continue"}),
                        ts="2126-01-01T00:00:00+00:00")]
    out = P.recompile_from_new_evidence(fresh)
    assert [c["state"] for c in out["compiled"]] == ["indeed_apply_questions"]
    assert P.load_program("indeed_apply", "indeed_apply_questions").stale is False


def test_a_fresh_program_with_nothing_newer_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROLLER_PROGRAMS_DIR", str(tmp_path))
    rows = [dict(_row("indeed_apply_questions", "click", {"control": "Continue"}),
                 ts="2026-08-20T10:00:00+00:00")]
    P.recompile_from_new_evidence(rows)
    before = P.load_program("indeed_apply", "indeed_apply_questions").verified_at
    out = P.recompile_from_new_evidence(rows)
    assert out["compiled"] == [] and out["skipped"]
    assert P.load_program("indeed_apply", "indeed_apply_questions").verified_at == before
