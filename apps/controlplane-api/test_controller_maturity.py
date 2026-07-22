"""Tests for the transition maturity registry.

Three groups, in the order they matter:

  1. **The ladder** — each rung is reachable, and the floors are floors (you climb them, they do
     not climb to you).
  2. **The safety properties** — nothing certifies without positive supervisor evidence and a
     clean reviewed run; a stale program demotes; failure resets a streak.
  3. **The real corpus** — `derive()` run over the 45 rows actually on disk. Not a smoke test: it
     pins the honest day-one answer (nothing is certified, Indeed leads, Workday is unseen), which
     is what the "gate immediately" decision was taken against.
"""

from __future__ import annotations

import pytest

from interaction.authority import ControlMode, Maturity, TransitionKey
from interaction.supervision import FailureClass

from controller import maturity as maturity_mod
from controller.programs import IntentProgram

QUESTIONS = dict(task="indeed_apply", state="indeed_apply_questions", ats="indeed")


def row(intent="click", *, ref="Continue", outcome="ok", verified=True, rung="recipe",
        supervisor=FailureClass.NONE.value, mode=None, golden=False, landed=None,
        escalate=False, shadow=False, **over):
    """One journal row, with the fields `derive` actually reads."""
    params = {}
    if ref:
        params["control" if intent == "click" else "field"] = ref
    base = {**QUESTIONS, "intent": intent, "params": params, "outcome": outcome,
            "verified": verified, "rung": rung, "supervisor_class": supervisor,
            "control_mode": mode, "golden": golden, "landed_state": landed,
            "escalate": escalate, "shadow": shadow}
    base.update(over)
    return base


def graded(rows, programs=()):
    stats = maturity_mod.derive(rows, programs)
    assert len(stats) == 1, f"expected one transition, got {list(stats)}"
    return next(iter(stats.values()))


# --- 1. the ladder ------------------------------------------------------------------
def test_no_rows_is_unseen():
    assert maturity_mod.derive([], ()) == {}


def test_one_teacher_success_is_demonstrated():
    s = graded([row(rung="teacher")])
    assert s.maturity == Maturity.DEMONSTRATED.value
    assert s.teacher_oks == 1


def test_two_successes_are_replayable():
    s = graded([row(rung="teacher"), row(rung="teacher")])
    assert s.maturity == Maturity.REPLAYABLE.value


def test_a_compiled_program_alone_is_replayable():
    """A proven program is evidence even with no journal rows under the key — otherwise a
    hand-compiled program is silently ignored and the drive pays full price for a proven step."""
    program = IntentProgram(task="indeed_apply", state="indeed_apply_questions",
                            guard_fields=(), expected_exit=("indeed_apply_review",),
                            steps=({"intent": "click", "params": {"control": "Continue"}},))
    stats = maturity_mod.derive([], [program])
    key = TransitionKey(from_state="indeed_apply_questions", intent="click", ref="Continue")
    assert stats[key.as_str()].maturity == Maturity.REPLAYABLE.value


def test_three_successes_driven_locally_are_testing():
    s = graded([row(rung="teacher"), row(rung="recipe"), row(rung="recipe")])
    assert s.maturity == Maturity.TESTING.value
    assert s.local_oks == 2


def test_five_clean_supervised_and_reviewed_successes_certify():
    rows = [row(rung="teacher")] * 2 + [row(rung="recipe", mode=ControlMode.YELLOW.value)] * 3
    s = graded(rows)
    assert s.maturity == Maturity.CERTIFIED.value
    assert s.clean_reviews == 3


# --- 2. the safety properties -------------------------------------------------------
def test_certification_requires_positive_supervisor_evidence():
    """`supervisor_class = None` means the supervisor never ran. Silence is not a clean bill of
    health — every row journaled before S12 has no verdict, so history alone certifies nothing."""
    rows = [row(rung="recipe", mode=ControlMode.YELLOW.value, supervisor=None)] * 6
    s = graded(rows)
    assert s.maturity != Maturity.CERTIFIED.value
    assert s.supervised_nominal_tail == 0


def test_certification_requires_a_reviewed_run_that_was_not_corrected():
    """Plenty of verified runs, never once reviewed → TESTING, not CERTIFIED."""
    s = graded([row(rung="recipe")] * 8)
    assert s.maturity == Maturity.TESTING.value
    assert s.clean_reviews == 0


def test_a_correction_is_not_a_clean_review():
    """A golden row is re-stamped rung='teacher', so counting corrections by rung would read every
    correction as a teacher success. It is counted off the `golden` flag instead."""
    rows = ([row(rung="recipe", mode=ControlMode.YELLOW.value)] * 4
            + [row(rung="teacher", mode=ControlMode.YELLOW.value, golden=True)])
    s = graded(rows)
    assert s.corrections == 1
    assert s.clean_reviews == 4


def test_one_bad_row_resets_the_clean_tail():
    """The tail is CONSECUTIVE and most-recent — a transition must not certify on an old streak
    it has since broken."""
    rows = ([row(rung="recipe", mode=ControlMode.YELLOW.value)] * 5
            + [row(rung="recipe", mode=ControlMode.YELLOW.value,
                   supervisor=FailureClass.NO_PROGRESS.value)])
    s = graded(rows)
    assert s.supervised_nominal_tail == 0
    assert s.maturity != Maturity.CERTIFIED.value


def test_a_recent_failure_regresses_a_proven_transition():
    rows = [row(rung="recipe", mode=ControlMode.YELLOW.value)] * 5 + [
        row(outcome="not_found", verified=False, supervisor=FailureClass.CONTROL_NOT_FOUND.value)]
    s = graded(rows)
    assert s.maturity == Maturity.REGRESSED.value
    assert s.reason == "5 verified attempts, then the most recent one failed"


def test_a_stale_program_regresses_rather_than_unseeing():
    """REGRESSED, not UNSEEN: we hold prior knowledge that is now suspect, and the remedy is to
    inspect what changed, not to teach it from scratch."""
    program = IntentProgram(task="indeed_apply", state="indeed_apply_questions",
                            guard_fields=(), expected_exit=(), stale=True,
                            steps=({"intent": "click", "params": {"control": "Continue"}},))
    s = graded([row(rung="teacher"), row(rung="teacher")], [program])
    assert s.maturity == Maturity.REGRESSED.value
    assert s.program_stale


def test_failures_alone_never_promote():
    s = graded([row(outcome="not_found", verified=False)] * 9)
    assert s.maturity == Maturity.UNSEEN.value
    assert s.failures == 9 and s.oks == 0


def test_a_program_for_the_state_does_not_certify_a_different_action_on_it():
    """A program covering 'click Continue' is not evidence about 'click Back'."""
    program = IntentProgram(task="indeed_apply", state="indeed_apply_questions",
                            guard_fields=(), expected_exit=(),
                            steps=({"intent": "click", "params": {"control": "Continue"}},))
    stats = maturity_mod.derive([row(ref="Back", rung="teacher")], [program])
    back = stats[TransitionKey(from_state="indeed_apply_questions",
                               intent="click", ref="Back").as_str()]
    assert not back.has_program
    assert back.maturity == Maturity.DEMONSTRATED.value


# --- what is not evidence -----------------------------------------------------------
@pytest.mark.parametrize("bad", [
    pytest.param(dict(escalate=True), id="escalation — nothing was attempted"),
    pytest.param(dict(shadow=True), id="shadow — nothing happened"),
    pytest.param(dict(intent="observe", ref=""), id="no-op intent — looking is not acting"),
    pytest.param(dict(state=None), id="unplaceable row"),
    pytest.param(dict(intent=""), id="row with no intent"),
])
def test_non_evidence_rows_are_ignored(bad):
    assert maturity_mod.derive([row(**bad)], ()) == {}


def test_a_field_fill_with_no_expectation_still_counts():
    """A fill that stays on the same page journals `verified=None`. Refusing those would make
    every form-filling transition permanently unprovable."""
    s = graded([row(intent="set_text", ref="phone", verified=None)] * 3)
    assert s.oks == 3


def test_landings_are_recorded_but_are_not_identity():
    """Indeed skips prefilled steps, so one action lands in several places. All of them belong to
    ONE transition — fragmenting by landing is how nothing ever accumulates enough to certify."""
    s = graded([row(rung="teacher", landed="indeed_apply_review"),
                row(rung="teacher", landed="indeed_apply_demographics")])
    assert set(s.landings) == {"indeed_apply_review", "indeed_apply_demographics"}


# --- key derivation is shared between lookup and evidence ---------------------------
def test_key_for_and_key_for_row_agree():
    """If the live lookup and the journal derivation disagreed about identity, a transition would
    accumulate evidence under one key and be looked up under another — forever UNSEEN."""
    r = row(intent="select_option", ref="work_authorization")
    assert maturity_mod.key_for_row(r) == maturity_mod.key_for(
        r["state"], r["intent"], r["params"])


# --- 3. the real corpus -------------------------------------------------------------
def _real_journal_rows():
    """The corpus actually on disk, read by PATH rather than through `decision_journal`.

    `conftest.py` redirects `INTERACTION_ARTIFACTS_DIR` at a temp dir for the whole session — the
    right thing, since the suite once wrote 237 fixture rows into the live corpus. But it also
    means `read_rows()` here returns [] and a test asserting over "the real journal" passes
    vacuously, which is how this test read on its first run. Resolve the path explicitly, and
    read-only: nothing in this file ever writes to it.
    """
    import json
    from pathlib import Path
    path = (Path(__file__).resolve().parents[1] / "mcp" / "output" / "cache"
            / "decision_journal.jsonl")
    if not path.exists():
        pytest.skip("no real corpus on this machine")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_derive_over_the_real_journal_is_honest():
    """Run over whatever is actually on disk. This pins the day-one answer the "gate immediately"
    decision was taken against: real evidence exists for Indeed, and NOTHING is certified, because
    certification needs supervisor verdicts and reviewed runs that only new drives can produce.
    """
    rows = _real_journal_rows()
    stats = maturity_mod.derive(rows, ())
    assert rows, "the corpus is not empty — if this fires, the fixture is reading the wrong path"
    assert stats, "45 real rows should place at least one transition"

    assert all(s.maturity != Maturity.CERTIFIED.value for s in stats.values()), \
        "nothing in the historical corpus should certify — it predates the supervisor columns"
    for s in stats.values():
        assert s.maturity in {m.value for m in Maturity}
        assert s.reason, f"{s.key.as_str()} graded with no explanation"


def test_coverage_groups_and_counts():
    rows = [row(rung="teacher"), row(rung="teacher"),
            row(state="workday_my_information", ats="workday", ref="Save and Continue",
                rung="teacher")]
    cov = maturity_mod.coverage(rows, ())
    assert cov["total"] == 2
    assert set(cov["by_platform"]) == {"indeed", "workday"}
    assert cov["counts"][Maturity.REPLAYABLE.value] == 1
    assert cov["counts"][Maturity.DEMONSTRATED.value] == 1
    assert cov["certified_share"] == 0.0


def test_coverage_sorts_strongest_first():
    rows = [row(rung="teacher"), row(rung="teacher"), row(ref="Back", rung="teacher")]
    entries = maturity_mod.coverage(rows, ())["by_platform"]["indeed"]
    assert entries[0]["maturity"] == Maturity.REPLAYABLE.value
    assert entries[-1]["maturity"] == Maturity.DEMONSTRATED.value


def test_target_parameterised_states_are_capped_below_autonomy():
    """`indeed_job_posting/click/<a specific job title>` must never climb the ladder: the action
    is chosen by which prospect we are pursuing, so its evidence can never recur. Capping it also
    lands the right policy on entering an ATS — that click stays reviewed."""
    posting = dict(task="indeed_apply", state="indeed_job_posting", ats="indeed")
    rows = [row(rung="recipe", mode=ControlMode.YELLOW.value, ref="Apply on company site",
                **posting)] * 9
    s = graded(rows)
    assert s.oks == 9
    assert s.maturity == Maturity.DEMONSTRATED.value
    assert "which item is being pursued" in s.reason


def test_the_cap_reuses_the_compilers_own_list():
    """One list, two consumers: a state too target-dependent to compile a program from is too
    target-dependent to certify a transition on. Re-listing them would let the two drift."""
    from controller.programs import NON_COMPILABLE_STATES
    assert maturity_mod.TARGET_PARAMETERISED_STATES is NON_COMPILABLE_STATES


def test_one_history_is_not_split_by_a_drifting_task_label():
    """The bug the real corpus surfaced: twelve successes on one Indeed transition were split in
    two because the rows carry different free-text task names."""
    rows = ([row(rung="teacher", task="indeed")] * 2
            + [row(rung="recipe", task="indeed_quick_apply")] * 2)
    s = graded(rows)          # `graded` asserts there is exactly ONE transition
    assert s.oks == 4
