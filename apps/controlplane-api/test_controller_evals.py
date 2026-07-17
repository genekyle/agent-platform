"""The controller regression suite — what `make controller-evals` runs.

Deterministic, offline, free: the curated eval cases (escalation ladder + short-circuits), a
rung-0 fill/advance replay against a temp program, the golden/shadow journal replay, and shadow.py.
No model rung here — it spends and is non-deterministic; its replay is gated behind an explicit flag.
"""

from __future__ import annotations

import json
from pathlib import Path

from controller import programs as programs_mod
from controller.programs import IntentProgram
from controller.replay import replay_journal, run_cases
from controller.shadow import shadow_step
from interaction.decision import Bundle, Decision

CASES = json.loads((Path(__file__).parent / "controller" / "eval_cases.json").read_text())["cases"]


def _bundle(state, fields=(), **over) -> Bundle:
    base = dict(task="indeed_apply", goal_text="apply", done=False,
                url="https://smartapply.indeed.com/x", route="smartapply.indeed.com/x",
                state=state, is_branch=False, human_required=False, ats="indeed_quick_apply",
                expected_next=("indeed_apply_review",),
                unanswered=tuple({"field": f, "kind": "text", "required_via": "required-attr",
                                  "answered": False, "valid": True} for f in fields))
    base.update(over)
    return Bundle(**base)


class DictStore:
    def __init__(self, progs): self.progs = progs
    def get(self, task, state): return self.progs.get((task, state))


# --- the curated cases (the file make controller-evals is anchored on) -------
def test_eval_cases_all_pass():
    report = run_cases(CASES, programs=DictStore({}))
    failed = [r for r in report["results"] if not r["ok"]]
    assert report["failed"] == 0, f"decide() regressed on: {failed}"
    assert report["total"] == len(CASES) >= 5


# --- rung-0 replay against a program (fill then advance) ---------------------
def test_rung0_fill_and_advance_replay():
    store = DictStore({("indeed_apply", "indeed_apply_questions"): IntentProgram(
        task="indeed_apply", state="indeed_apply_questions",
        guard_fields=("Work authorization",),
        steps=({"intent": "set_text", "params": {"field": "Work authorization"}},
               {"intent": "click", "params": {"control": "Continue"}}),
        expected_exit=("indeed_apply_review",))})
    cases = [
        {"name": "fill", "bundle": {"task": "indeed_apply", "state": "indeed_apply_questions",
                                    "route": "r", "expected_next": ["indeed_apply_review"],
                                    "unanswered": [{"field": "Work authorization"}]},
         "expect": {"intent": "set_text", "rung": "recipe", "field": "Work authorization"}},
        {"name": "advance", "bundle": {"task": "indeed_apply", "state": "indeed_apply_questions",
                                       "route": "r", "expected_next": ["indeed_apply_review"],
                                       "unanswered": []},
         "expect": {"intent": "click", "rung": "recipe", "escalate": False}},
    ]
    report = run_cases(cases, programs=store)
    assert report["failed"] == 0, report["results"]


# --- shadow.py journals a paired row, and replay reproduces it ---------------
def test_shadow_and_journal_replay(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    store = DictStore({("indeed_apply", "indeed_apply_questions"): IntentProgram(
        task="indeed_apply", state="indeed_apply_questions", guard_fields=("Q1",),
        steps=({"intent": "set_text", "params": {"field": "Q1"}},), expected_exit=("x",))})

    teacher = Decision("set_text", {"field": "Q1"}, 1.0, "teacher", "teacher filled Q1")
    controller = shadow_step(teacher, _bundle("indeed_apply_questions", ["Q1"]),
                             programs=store, session_id="shadow-1", outcome="ok")
    assert controller.intent == "set_text" and controller.rung == "recipe"   # controller agrees

    from interaction import decision_journal
    rows = decision_journal.read_rows()
    assert len(rows) == 1 and rows[0]["shadow"] is True
    assert rows[0]["bundle_snapshot"] is not None                            # replay case stored

    # the snapshot replays deterministically to the same rung-0 decision
    rep = replay_journal(rows, programs=store)
    assert rep["checked"] == 1 and rep["reproduced"] == 1 and rep["reproduce_rate"] == 1.0
