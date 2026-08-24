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


# --- progressive autonomy: the mode mix and the ladder are part of the regression ------
def test_authority_truth_table_is_pinned():
    """The four modes decide who acts on a live application, so a silent change to the ordering
    is exactly the kind of regression this suite exists to catch."""
    from interaction.authority import ActuationReach, ControlMode, Maturity, authority
    from interaction.authority import PromotionStanding
    from interaction.belief import BeliefState

    reachable = ActuationReach(can_operate=True)
    sure = BeliefState(state="s", uncertainty={"state": 0.05, "novelty": 0.1})
    novel = BeliefState(state="s", uncertainty={"state": 0.05, "novelty": 0.97})
    blocked = ActuationReach(can_operate=False, gaps=("widget:x",))
    # Since 2026-08-22 a track record alone does not grant autonomy: the scenario must also have
    # cleared the two-bar promotion gate. Supplied explicitly wherever GREEN is asserted, and
    # pinned by `test_an_unmeasured_scenario_can_never_be_green` in the interaction suite.
    promoted = PromotionStanding(measured=True, eligible=True, detail="loose 95%, exact 90%")

    assert authority(maturity=Maturity.CERTIFIED.value, belief=sure, reach=reachable,
                     standing=promoted).mode == ControlMode.GREEN.value
    assert authority(maturity=Maturity.TESTING.value, belief=sure,
                     reach=reachable).mode == ControlMode.YELLOW.value
    assert authority(maturity=Maturity.UNSEEN.value, belief=sure,
                     reach=reachable).mode == ControlMode.ORANGE.value
    # Novelty on a page we CAN operate is a knowledge gap, so ORANGE — the teacher supplies the
    # meaning and the local executor still acts. Corrected 2026-07-22: grading it RED meant every
    # novelty block demanded a takeover, and a takeover accepts no instruction, so live drives had
    # no way to be taught at all. RED is the capability verdict now, and reach is what earns it.
    assert authority(maturity=Maturity.CERTIFIED.value, belief=novel, reach=reachable,
                     standing=promoted).mode == ControlMode.ORANGE.value
    assert authority(maturity=Maturity.CERTIFIED.value, belief=sure, reach=blocked,
                     standing=promoted).mode == ControlMode.RED.value
    assert authority(maturity=Maturity.CERTIFIED.value, belief=novel, reach=blocked,
                     standing=promoted).mode == ControlMode.RED.value
    # …and the new row of the table: everything else perfect, gate not cleared -> YELLOW.
    assert authority(maturity=Maturity.CERTIFIED.value, belief=sure,
                     reach=reachable).mode == ControlMode.YELLOW.value


def test_an_empty_registry_can_never_produce_green():
    """The safety property the "gate immediately" decision rests on, pinned in the regression
    suite as well as the unit tests — with 45 journal rows most transitions are UNSEEN, and UNSEEN
    acting unwatched on a real job application is what would make this a liability."""
    import itertools

    from interaction.authority import ActuationReach, ControlMode, Maturity, authority
    from interaction.belief import BeliefState

    beliefs = [None, BeliefState(state="s", uncertainty={"state": 0.01, "novelty": 0.01})]
    reaches = [None, ActuationReach(can_operate=True), ActuationReach.unprobed()]
    for belief, reach, conseq in itertools.product(beliefs, reaches, (False, True)):
        assert authority(maturity=Maturity.UNSEEN.value, belief=belief, reach=reach,
                         consequential=conseq).mode != ControlMode.GREEN.value


def test_the_real_corpus_still_certifies_nothing():
    """The honest day-one baseline. When this starts failing it is GOOD NEWS — a transition has
    earned certification off real supervised, reviewed drives — but it must be a deliberate,
    noticed change rather than a threshold quietly drifting."""
    import json as _json

    from controller import maturity as maturity_mod

    path = (Path(__file__).resolve().parents[1] / "mcp" / "output" / "cache"
            / "decision_journal.jsonl")
    if not path.exists():
        return
    rows = [_json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    stats = maturity_mod.derive(rows, programs_mod.list_programs())
    certified = [s.key.as_str() for s in stats.values() if s.maturity == "certified"]
    assert not certified, f"newly certified transitions — confirm they earned it: {certified}"


def test_escalations_carry_a_scoreable_prediction():
    """`shadow_agreement` can only measure the local layers on the turns the teacher is paid for
    if those turns carry a proposal. An escalation that reverts to `observe`/0.0 silently stops
    the measurement, which is how the student stopped taking the exam in the first place."""
    from controller.decide import decide

    scoreable = decide(_bundle("indeed_apply_questions", ["Work authorization"]),
                       programs=DictStore({}))
    assert scoreable.escalate
    assert scoreable.intent != "observe" and scoreable.params
    assert scoreable.escalation_axis == "no_program"
