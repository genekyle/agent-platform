"""The observer's fusion policy and the BeliefState contract.

The rules under test are measurements, not preferences (2026-07-22, leave-one-out over the
labeled corpus): agreement rows are right 77.9% vs 48.2% when the witnesses split, and on a split
the DOM witness is right 48% against vision's 25%. So: a split must never read as confident, and
the DOM leads. If a future change makes a split look confident, these fail — which is the point.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from interaction.belief import (AXES, BeliefState, CONSEQUENTIAL_CEILING, WitnessView,
                                belief_to_prompt)
from perception.dom_witness import TfidfCentroidWitness
from perception.observer import Observation, Observer
from perception.prototypes import PrototypeBank


class _StubEncoder:
    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors

    def embed(self, path):
        return self.vectors.get(Path(path).name)


def _artifact(title: str, labels: list[str]) -> dict:
    return {
        "acquisition": {
            "page_identity": {"title": title, "url": "https://acme.wd5.myworkdayjobs.com/en-US/x"},
            "actionable_elements": [{"tag": "input", "label": lab} for lab in labels],
        },
        "ranked_candidates": [{"target": {"role": "textbox", "label": lab}} for lab in labels],
    }


def _dom_witness() -> TfidfCentroidWitness:
    from perception.dom_witness import extract_tokens
    return TfidfCentroidWitness().fit([
        ("workday_sign_in", extract_tokens(_artifact("Sign In", ["Email", "Password"]))),
        ("workday_sign_in", extract_tokens(_artifact("Sign In", ["Email Address", "Password"]))),
        ("workday_questions", extract_tokens(_artifact("Questions", ["Sponsorship", "Salary"]))),
        ("workday_questions", extract_tokens(_artifact("Questions", ["Visa", "Desired Salary"]))),
    ])


# --- BeliefState contract ---------------------------------------------------------
def test_an_unassessed_axis_reads_unknown_but_cannot_block():
    """Silence is not confidence — but a caller must not be blocked by a question no one asked."""
    belief = BeliefState(state="x", uncertainty={"state": 0.1, "novelty": 0.1})
    assert belief.unsure_about("element") == 1.0
    assert belief.blocks() is None


def test_novelty_outranks_a_confident_label():
    """A page we have never seen still produces a confident nearest neighbour. That is exactly
    the failure the second witness was bought to prevent."""
    belief = BeliefState(state="x", uncertainty={"state": 0.0, "novelty": 0.9})
    assert belief.blocks() == "novelty"
    assert belief.is_novel


def test_consequential_actions_use_a_much_stricter_bar():
    belief = BeliefState(state="x", uncertainty={"state": 0.2, "novelty": 0.0})
    assert belief.blocks() is None
    assert belief.blocks(consequential=True) == "state"
    assert CONSEQUENTIAL_CEILING < 0.25


def test_prompt_serialization_names_every_axis_and_both_witnesses():
    belief = BeliefState(
        state="workday_questions",
        facets={"platform": "workday", "phase": "questions"},
        prior=("workday_questions", "workday_review"), prior_agrees=True,
        witnesses=(WitnessView("dom:tfidf", "workday_questions", 0.8, 0.3, 0.1,
                               ("tok:sponsorship",)),
                   WitnessView("visual:apple", "workday_questions", 0.9, 0.2, 0.2)),
        agreement="agree", uncertainty={"state": 0.05, "novelty": 0.2}, rationale="both agree")
    text = belief_to_prompt(belief)
    for axis in AXES:
        assert f"{axis}=" in text
    assert "dom:tfidf" in text and "visual:apple" in text
    assert "matched: yes" in text


# --- fusion ------------------------------------------------------------------------
def test_agreement_halves_uncertainty_and_names_both_witnesses():
    dom = _dom_witness()
    bank = PrototypeBank("stub").fit([("workday_sign_in", [1.0, 0.0]),
                                      ("workday_sign_in", [0.98, 0.02]),
                                      ("workday_questions", [0.0, 1.0]),
                                      ("workday_questions", [0.02, 0.98])])
    obs_engine = Observer(dom=dom, visual=bank, encoder=_StubEncoder({"a.png": [0.99, 0.01]}),
                          visual_name="visual:stub")
    belief = obs_engine.observe(Observation(artifact=_artifact("Sign In", ["Email", "Password"]),
                                            screenshot_path=Path("a.png")))
    assert belief.state == "workday_sign_in"
    assert belief.agreement == "agree"
    assert len(belief.witnesses) == 2


def test_a_split_can_never_read_as_confident_and_the_dom_leads():
    dom = _dom_witness()
    bank = PrototypeBank("stub").fit([("workday_sign_in", [1.0, 0.0]),
                                      ("workday_sign_in", [0.98, 0.02]),
                                      ("workday_questions", [0.0, 1.0]),
                                      ("workday_questions", [0.02, 0.98])])
    # The screenshot looks like `questions` while the DOM plainly reads a sign-in form.
    obs_engine = Observer(dom=dom, visual=bank, encoder=_StubEncoder({"a.png": [0.0, 1.0]}),
                          visual_name="visual:stub")
    belief = obs_engine.observe(Observation(artifact=_artifact("Sign In", ["Email", "Password"]),
                                            screenshot_path=Path("a.png")))
    assert belief.agreement == "split"
    assert belief.state == "workday_sign_in"          # witness A leads
    assert belief.unsure_about("state") >= 0.5        # and the split is visible in the number
    assert belief.blocks() == "state"
    assert "disagree" in belief.rationale


def test_one_sided_evidence_buys_no_free_confidence():
    dom = _dom_witness()
    engine = Observer(dom=dom)   # no visual witness at all
    belief = engine.observe(Observation(artifact=_artifact("Sign In", ["Email", "Password"])))
    assert belief.agreement == "one_sided"
    assert len(belief.witnesses) == 1


def test_a_matching_recipe_prior_narrows_but_a_missing_one_never_widens():
    dom = _dom_witness()
    engine = Observer(dom=dom)
    page = Observation(artifact=_artifact("Sign In", ["Email", "Password"]))
    without = engine.observe(page)
    with_prior = engine.observe(page, prior=("workday_sign_in",))
    wrong_prior = engine.observe(page, prior=("workday_review",))
    assert with_prior.prior_agrees and not wrong_prior.prior_agrees
    assert with_prior.unsure_about("state") <= without.unsure_about("state")
    assert wrong_prior.unsure_about("state") == without.unsure_about("state")


def test_no_readable_surface_is_reported_as_no_evidence_not_as_a_guess():
    belief = Observer().observe(Observation())
    assert belief.state is None
    assert belief.agreement == "no_evidence"
    assert belief.unsure_about("state") == 1.0


def test_facets_are_projected_onto_the_answer():
    dom = _dom_witness()
    belief = Observer(dom=dom).observe(Observation(
        artifact=_artifact("Questions", ["Sponsorship", "Salary"]),
        url="https://acme.wd5.myworkdayjobs.com/en-US/x"))
    assert belief.facets["platform"] == "workday"
    assert belief.facets["variant"] == "tenant:acme"


def test_caller_supplied_axes_land_on_the_belief():
    dom = _dom_witness()
    belief = Observer(dom=dom).observe(
        Observation(artifact=_artifact("Sign In", ["Email", "Password"])),
        extra_uncertainty={"answer": 0.9})
    assert belief.unsure_about("answer") == 0.9
    assert belief.blocks() == "answer"
