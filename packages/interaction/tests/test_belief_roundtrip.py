"""The belief round-trip — `as_dict()` -> `from_dict()` must preserve what `blocks()` reads.

Why this file exists. `as_dict()` renders all five axes for legibility, filling the ones nobody
spoke to with 1.0. That is right for a human reading a journal row and wrong for a machine reading
one back, because `blocks()` draws its whole distinction between **"no idea"** (1.0 — blocks) and
**"nobody asked"** (absent — does not). Before `assessed` existed, a journaled belief came back
blocking on `element` and `answer` that no subsystem had ever assessed, so a replayed row escalated
where the live drive had acted.

Nothing read a belief back until `authority()` did, which is why the loss went unnoticed. These
tests are the guard so it cannot come back.
"""

from __future__ import annotations

import pytest

from interaction.belief import (
    AXES,
    CONSEQUENTIAL_CEILING,
    NOVELTY_CEILING,
    BeliefState,
    WitnessView,
)

FULL = BeliefState(
    state="workday_my_information",
    facets={"platform": "workday", "phase": "personal_information"},
    prior=("workday_my_experience",),
    prior_agrees=True,
    witnesses=(
        WitnessView(name="dom:tfidf", label="workday_my_information", similarity=0.91,
                    margin=0.34, novelty=0.08, top_evidence=("tok:legal", "route:/apply")),
        WitnessView(name="visual:apple", label="workday_my_information", similarity=0.88,
                    margin=0.12, novelty=0.19),
    ),
    agreement="agree",
    uncertainty={"state": 0.09, "novelty": 0.12},
    rationale="both witnesses say workday_my_information",
)


def test_round_trip_preserves_every_field():
    back = BeliefState.from_dict(FULL.as_dict())
    assert back.state == FULL.state
    assert back.facets == FULL.facets
    assert back.prior == FULL.prior
    assert back.prior_agrees == FULL.prior_agrees
    assert back.agreement == FULL.agreement
    assert back.rationale == FULL.rationale
    assert back.witnesses == FULL.witnesses


def test_round_trip_preserves_which_axes_were_assessed():
    """The property the whole file is about: silence must survive serialisation as silence."""
    back = BeliefState.from_dict(FULL.as_dict())
    assert set(back.uncertainty) == {"state", "novelty"}
    for axis in ("element", "answer", "effect"):
        assert axis not in back.uncertainty
        assert back.unsure_about(axis) == 1.0        # still reports 1.0 — but does not BLOCK


@pytest.mark.parametrize("consequential", [False, True])
def test_round_trip_preserves_the_blocking_decision(consequential):
    assert BeliefState.from_dict(FULL.as_dict()).blocks(consequential=consequential) == \
        FULL.blocks(consequential=consequential)


def test_a_genuine_one_point_zero_still_blocks():
    """`assessed` must not become a way to launder a real 'no idea' into 'nobody asked'. An axis a
    subsystem explicitly set to 1.0 is present in `uncertainty`, so it survives and it blocks."""
    hopeless = BeliefState(state="workday_questions",
                           uncertainty={"state": 0.05, "novelty": 0.1, "answer": 1.0})
    back = BeliefState.from_dict(hopeless.as_dict())
    assert "answer" in back.uncertainty
    assert back.blocks() == "answer"


def test_legacy_row_without_assessed_reads_literally():
    """A belief journaled before `assessed` existed: fall back to the keys the dict carries, which
    is that format's literal meaning. No such rows exist in the corpus yet — this is here so the
    fallback is a decision on the record rather than an accident."""
    legacy = FULL.as_dict()
    legacy.pop("assessed")
    legacy["uncertainty"] = {"state": 0.09, "novelty": 0.12}
    assert set(BeliefState.from_dict(legacy).uncertainty) == {"state", "novelty"}


def test_novelty_survives_the_round_trip_at_the_ceiling():
    novel = BeliefState(state="acme_unknown_form",
                        uncertainty={"state": 0.2, "novelty": NOVELTY_CEILING})
    assert BeliefState.from_dict(novel.as_dict()).is_novel
    assert BeliefState.from_dict(novel.as_dict()).blocks() == "novelty"


def test_consequential_ceiling_survives_the_round_trip():
    shaky = BeliefState(state="indeed_apply_review",
                        uncertainty={"state": CONSEQUENTIAL_CEILING + 0.05, "novelty": 0.1})
    back = BeliefState.from_dict(shaky.as_dict())
    assert back.blocks(consequential=False) is None
    assert back.blocks(consequential=True) == "state"


def test_empty_belief_round_trips_to_an_empty_belief():
    empty = BeliefState(state=None)
    back = BeliefState.from_dict(empty.as_dict())
    assert back.state is None and back.uncertainty == {} and back.blocks() is None


def test_as_dict_still_renders_all_axes_for_the_reader():
    """The legibility half of the contract — `assessed` is ADDITIVE, it did not narrow the row."""
    d = FULL.as_dict()
    assert set(d["uncertainty"]) == set(AXES)
    assert d["assessed"] == ["state", "novelty"]
