"""Tests for the authority contract — who owns the turn.

The two that matter most are at the bottom:

  * `test_unseen_transition_can_never_be_green` — the safety property the whole "gate immediately"
    decision rests on. Absence of evidence must never read as permission, under ANY belief or
    reach. If this ever goes red, an unproven transition can act unwatched on a live application.
  * `test_dict_belief_matches_object_belief` — belief arrives as an object live and as a dict from
    a journaled row, and the two must decide identically or replay/eval silently disagrees with
    the drive it is scoring.
"""

from __future__ import annotations

import itertools

import pytest

from interaction.authority import (
    AUTHORITY_SCHEMA_VERSION,
    GREEN_AT,
    LOCALLY_EXECUTED,
    MATURITY_ORDER,
    MODE_ORDER,
    NEEDS_TEACHER,
    ActuationReach,
    ControlMode,
    Maturity,
    PromotionStanding,
    TransitionKey,
    authority,
    authority_to_prompt,
    maturity_rank,
    weakest,
)
from interaction.belief import BeliefState

REACHABLE = ActuationReach(can_operate=True)
UNREACHABLE = ActuationReach(can_operate=False, gaps=("widget:signature_pad", "field:eeo_race"))

#: A scenario that HAS cleared the two-bar promotion gate. Supplied explicitly by every test that
#: expects GREEN, because since 2026-08-22 a track record alone does not grant autonomy — the
#: controller must also have been measured to agree with the teacher on that scenario. Omitting it
#: is what `test_an_unmeasured_scenario_can_never_be_green` pins.
PROMOTED = PromotionStanding(measured=True, eligible=True,
                             detail="loose 95% over 30, exact 90% over 28")

SURE = BeliefState(state="workday_my_information",
                   uncertainty={"state": 0.05, "novelty": 0.10})
UNSURE_STATE = BeliefState(state="workday_my_information",
                           uncertainty={"state": 0.60, "novelty": 0.10})
NOVEL = BeliefState(state="workday_my_information",
                    uncertainty={"state": 0.05, "novelty": 0.97})


# --- the ladder ---------------------------------------------------------------------
def test_maturity_order_covers_every_member():
    assert set(MATURITY_ORDER) == {m.value for m in Maturity}


def test_unknown_maturity_ranks_lowest():
    """A typo or a value from a future schema must fail SAFE, not open."""
    assert maturity_rank("certifiedd") == 0
    assert maturity_rank("") == 0


def test_regressed_ranks_below_replayable():
    """A transition that broke is worth about one old demonstration, not what it was worth."""
    assert maturity_rank(Maturity.REGRESSED.value) < maturity_rank(Maturity.REPLAYABLE.value)
    assert maturity_rank(Maturity.REGRESSED.value) > maturity_rank(Maturity.UNSEEN.value)


def test_weakest_caps_never_averages():
    assert weakest("green", "orange") == "orange"
    assert weakest("yellow", "red", "green") == "red"
    assert weakest("green") == "green"


def test_mode_order_covers_every_member():
    assert set(MODE_ORDER) == {m.value for m in ControlMode}


# --- the truth table ----------------------------------------------------------------
def test_certified_and_sure_and_reachable_and_promoted_is_green():
    v = authority(maturity=Maturity.CERTIFIED.value, belief=SURE, reach=REACHABLE,
                  standing=PROMOTED)
    assert v.mode == ControlMode.GREEN.value
    assert v.locally_executed and not v.needs_teacher


@pytest.mark.parametrize("maturity", [
    Maturity.DEMONSTRATED.value, Maturity.REPLAYABLE.value,
    Maturity.TESTING.value, Maturity.REGRESSED.value,
])
def test_known_but_unproven_is_yellow(maturity):
    v = authority(maturity=maturity, belief=SURE, reach=REACHABLE)
    assert v.mode == ControlMode.YELLOW.value
    assert v.locally_executed and not v.needs_teacher
    assert GREEN_AT in v.reason


def test_unsure_on_a_workable_page_is_orange_not_red():
    """The distinction the whole design rests on: we don't know what this MEANS, but we can still
    click things — so the teacher supplies meaning and the local executor acts."""
    v = authority(maturity=Maturity.CERTIFIED.value, belief=UNSURE_STATE, reach=REACHABLE)
    assert v.mode == ControlMode.ORANGE.value
    assert v.blocking_axis == "state"
    assert v.locally_executed and v.needs_teacher


def test_unreachable_page_is_red_even_when_certified():
    """Reach outranks everything: a perfect label on a page we cannot touch buys nothing."""
    v = authority(maturity=Maturity.CERTIFIED.value, belief=SURE, reach=UNREACHABLE)
    assert v.mode == ControlMode.RED.value
    assert v.gaps == UNREACHABLE.gaps
    assert not v.locally_executed and v.needs_teacher
    assert "endpoint" in v.reason        # the gap is a work item, not a licence to free-hand


def test_novelty_on_a_reachable_page_is_orange_not_red():
    """Corrected 2026-07-22 after the first live drive; this test asserted RED until then.

    RED is the CAPABILITY verdict and belongs to the reach branch alone. Novelty on a page our
    tools can operate is the purest KNOWLEDGE gap there is — exactly the split
    `reach.BLOCKING_GAP_PREFIXES` already draws — so the teacher answers it with meaning while the
    local actuator performs and verifies the step, which is what keeps the step journaled.

    Grading it RED was not merely conservative, it was unrunnable: `authority_seam.takeover`
    accepts only `takeover_done` or `abort` because it assumes RED means the executor could not
    reach the page. Live, every novelty-RED carried `reach_gaps == []`, so the teaching path had
    no way back into the loop and the drive died on turn one. Plan falsifier #4, observed.
    """
    v = authority(maturity=Maturity.CERTIFIED.value, belief=NOVEL, reach=REACHABLE)
    assert v.mode == ControlMode.ORANGE.value
    assert v.blocking_axis == "novelty"
    assert v.needs_teacher, "the teacher must still be asked — this loosens who ACTS, not whether"


def test_red_is_reserved_for_pages_the_executor_cannot_operate():
    """The invariant the change above buys: nothing but reach produces RED."""
    modes = {
        authority(maturity=m, belief=b, reach=REACHABLE).mode
        for m in (Maturity.UNSEEN.value, Maturity.CERTIFIED.value)
        for b in (SURE, NOVEL)
    }
    assert ControlMode.RED.value not in modes


def test_reach_outranks_novelty():
    """Both fire; the reason the operator reads must be the actionable one (we can't act here)."""
    v = authority(maturity=Maturity.CERTIFIED.value, belief=NOVEL, reach=UNREACHABLE)
    assert v.mode == ControlMode.RED.value
    assert v.gaps and v.blocking_axis == ""


def test_unprobed_reach_caps_a_certified_transition_at_yellow():
    v = authority(maturity=Maturity.CERTIFIED.value, belief=SURE, reach=None,
                  standing=PROMOTED)
    assert v.mode == ControlMode.YELLOW.value
    assert "probed" in v.reason


def test_absent_belief_falls_back_to_maturity():
    """No observer fitted is still the default. An unassessed axis does not block — the
    deterministic state mapping that produced the maturity key is itself the evidence."""
    assert authority(maturity=Maturity.CERTIFIED.value, belief=None, reach=REACHABLE,
                     standing=PROMOTED).mode == ControlMode.GREEN.value
    assert authority(maturity=Maturity.TESTING.value, belief=None, reach=REACHABLE,
                     standing=PROMOTED).mode == ControlMode.YELLOW.value


def test_consequential_tightens_the_ceiling():
    """0.20 uncertainty is fine normally (ceiling 0.25) and blocks on an irreversible step
    (ceiling 0.10) — one flag, two answers, which is why it must be threaded and not guessed."""
    shaky = BeliefState(state="indeed_apply_review", uncertainty={"state": 0.20, "novelty": 0.1})
    assert authority(maturity=Maturity.CERTIFIED.value, belief=shaky, reach=REACHABLE,
                     standing=PROMOTED).mode == ControlMode.GREEN.value
    strict = authority(maturity=Maturity.CERTIFIED.value, belief=shaky, reach=REACHABLE,
                       consequential=True, standing=PROMOTED)
    assert strict.mode == ControlMode.ORANGE.value
    assert strict.consequential and strict.blocking_axis == "state"


# --- the safety property ------------------------------------------------------------
def test_unseen_transition_can_never_be_green():
    """Exhaustive over every belief/reach/consequential combination we can construct.

    This is the property the operator's "gate immediately" decision rests on: with 45 journal rows
    and 3 compiled programs, almost everything is UNSEEN on day one, and UNSEEN acting unwatched
    on a real job application is the failure mode that would make this whole plan a liability.
    """
    beliefs = [None, SURE, UNSURE_STATE, NOVEL, SURE.as_dict(), NOVEL.as_dict()]
    reaches = [None, REACHABLE, UNREACHABLE, ActuationReach.unprobed()]
    for belief, reach, consequential in itertools.product(beliefs, reaches, (False, True)):
        v = authority(maturity=Maturity.UNSEEN.value, belief=belief, reach=reach,
                      consequential=consequential)
        assert v.mode != ControlMode.GREEN.value, (belief, reach, consequential)
        assert v.needs_teacher, (belief, reach, consequential)


def test_an_unmeasured_scenario_can_never_be_green():
    """The promotion gate's safety property, and the twin of the one above.

    A transition can earn CERTIFIED on its ACTION history — `maturity.key_for_row` derives that
    from acted rows and skips shadow rows entirely — while the controller, asked to choose for
    itself on that page, has never once been measured against the teacher. Autonomy depends on
    the second fact, so a missing standing must read as a refusal and never as permission.

    Exhaustive over every belief/reach/consequential combination, exactly like the UNSEEN
    property, because a gate with one unguarded corner is not a gate.
    """
    beliefs = [None, SURE, SURE.as_dict()]
    reaches = [REACHABLE, ActuationReach.unprobed(), None]
    for belief, reach, consequential in itertools.product(beliefs, reaches, (False, True)):
        for standing in (None, PromotionStanding.unmeasured(),
                         PromotionStanding(measured=True, eligible=False, detail="loose 66%")):
            v = authority(maturity=Maturity.CERTIFIED.value, belief=belief, reach=reach,
                          consequential=consequential, standing=standing)
            assert v.mode != ControlMode.GREEN.value, (belief, reach, consequential, standing)


def test_a_refusal_names_which_bar_failed_and_its_number():
    """"Not promoted" without a number is not actionable. The refusal is what an operator reads
    when a transition they expected to run free did not, so it must say why and how far off."""
    v = authority(maturity=Maturity.CERTIFIED.value, belief=SURE, reach=REACHABLE,
                  standing=PromotionStanding(
                      measured=True, eligible=False,
                      detail="exact agreement 60% over 163, needs 85%"))
    assert v.mode == ControlMode.YELLOW.value
    assert "promotion gate is not cleared" in v.reason
    assert "exact agreement 60% over 163, needs 85%" in v.reason
    assert v.locally_executed and not v.needs_teacher      # YELLOW's usual partition, unchanged


def test_the_gate_does_not_override_a_stronger_refusal():
    """Ordering: an unreachable page is still RED and an unsure belief is still ORANGE, even with
    no standing. The gate caps autonomy; it does not get to relabel a capability or knowledge gap
    as a promotion problem."""
    assert authority(maturity=Maturity.CERTIFIED.value, belief=SURE,
                     reach=UNREACHABLE).mode == ControlMode.RED.value
    assert authority(maturity=Maturity.CERTIFIED.value, belief=UNSURE_STATE,
                     reach=REACHABLE).mode == ControlMode.ORANGE.value
    # and a sub-certified maturity still names the maturity bar, not the gate
    v = authority(maturity=Maturity.TESTING.value, belief=SURE, reach=REACHABLE)
    assert v.mode == ControlMode.YELLOW.value and GREEN_AT in v.reason


def test_every_mode_is_reachable():
    """A four-mode design with an unreachable mode is a three-mode design with extra prose."""
    produced = {
        authority(maturity=Maturity.CERTIFIED.value, belief=SURE, reach=REACHABLE,
                  standing=PROMOTED).mode,
        authority(maturity=Maturity.TESTING.value, belief=SURE, reach=REACHABLE,
                  standing=PROMOTED).mode,
        authority(maturity=Maturity.UNSEEN.value, belief=SURE, reach=REACHABLE,
                  standing=PROMOTED).mode,
        authority(maturity=Maturity.CERTIFIED.value, belief=SURE, reach=UNREACHABLE,
                  standing=PROMOTED).mode,
    }
    assert produced == {m.value for m in ControlMode}


def test_locally_executed_and_needs_teacher_partition_correctly():
    """ORANGE is in BOTH sets, and that is the point of it: the teacher decides, the system acts,
    so the step lands in the journal like every other step."""
    assert ControlMode.ORANGE.value in LOCALLY_EXECUTED
    assert ControlMode.ORANGE.value in NEEDS_TEACHER
    assert ControlMode.RED.value not in LOCALLY_EXECUTED
    assert ControlMode.GREEN.value not in NEEDS_TEACHER


# --- the two serialisations belief arrives in ---------------------------------------
@pytest.mark.parametrize("belief", [SURE, UNSURE_STATE, NOVEL])
@pytest.mark.parametrize("maturity", list(MATURITY_ORDER))
@pytest.mark.parametrize("consequential", [False, True])
def test_dict_belief_matches_object_belief(belief, maturity, consequential):
    """Live drives pass a BeliefState; replay and evals pass the journaled dict. If these two ever
    disagree, every offline number is scoring a different policy than the one that ran."""
    as_obj = authority(maturity=maturity, belief=belief, reach=REACHABLE,
                       consequential=consequential)
    as_dict = authority(maturity=maturity, belief=belief.as_dict(), reach=REACHABLE,
                        consequential=consequential)
    assert as_obj.mode == as_dict.mode
    assert as_obj.blocking_axis == as_dict.blocking_axis


# --- the frozen surfaces ------------------------------------------------------------
def test_transition_key_is_stable_and_greppable():
    k = TransitionKey(from_state="indeed_apply_questions", intent="click", ref="Continue")
    assert k.as_str() == "indeed_apply_questions|click|Continue"
    assert k == TransitionKey(**k.as_dict())


def test_transition_key_excludes_the_destination():
    """Indeed skips prefilled steps, so one action legitimately lands in several places (the
    recipe's `expect` is a LIST). Keying on the landing would shatter one history into three thin
    ones and nothing would ever accumulate enough evidence to certify."""
    assert "expected_state" not in TransitionKey.__dataclass_fields__


def test_transition_key_excludes_the_task_label():
    """Found running the registry over the real corpus: one twelve-success history was split in
    two because some rows say task='indeed' and others task='indeed_quick_apply'. A free-text
    label drifting across sessions must not reset a track record."""
    assert "task" not in TransitionKey.__dataclass_fields__


def test_transition_key_separates_two_controls_on_one_page():
    """'click Continue' and 'click Back' are different track records; one must not certify the
    other, which is why `ref` is part of the key."""
    page = dict(from_state="indeed_apply_questions", intent="click")
    assert TransitionKey(**page, ref="Continue") != TransitionKey(**page, ref="Back")


def test_authority_to_prompt_is_the_frozen_format():
    v = authority(maturity=Maturity.CERTIFIED.value, belief=UNSURE_STATE, reach=REACHABLE,
                  consequential=True)
    text = authority_to_prompt(v)
    assert text.splitlines()[:3] == ["# AUTHORITY", "mode: orange", "maturity: certified"]
    assert "blocked_on: state" in text
    assert "consequential: yes (strict ceiling applied)" in text


def test_verdict_dict_carries_the_schema_version():
    d = authority(maturity=Maturity.UNSEEN.value, reach=REACHABLE).as_dict()
    assert d["schema_version"] == AUTHORITY_SCHEMA_VERSION
    assert d["mode"] == ControlMode.ORANGE.value


def test_summary_reads_without_a_model():
    v = authority(maturity=Maturity.TESTING.value, belief=SURE, reach=REACHABLE)
    assert v.summary().startswith("yellow [testing] — ")
