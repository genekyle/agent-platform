"""The observer — two witnesses in, one BeliefState out (PLAN_perception_v1 §3.2).

The combination policy lives HERE, in one tested place, rather than as a threshold sprinkled
through callers. It is deliberately small and deliberately not clever:

  both agree                  -> the shared answer, uncertainty halved
  one speaks, one is silent   -> that answer, uncertainty unchanged (no free confidence)
  they SPLIT                  -> witness A (DOM) leads, and the belief is marked unsure
  either says "never seen it" -> novelty is raised regardless of how confident the labels look

Two of those rules are measurements, not taste (2026-07-22, leave-one-out over the labeled
corpus): rows where the witnesses agree are right **77.9%** of the time versus **48.2%** when
they split — so a split must never read as confident. And when they do split, the DOM witness is
right **48%** of the time against vision's **25%** — so the DOM leads and vision is the second
opinion, not a tie-break.

The observer never decides what to DO. It reports where we are and how unsure it is about what;
`unexpected.respond` and the supervisor's playbook still own the response.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from interaction.belief import NOVELTY_CEILING, BeliefState, WitnessView
from perception.dom_witness import TfidfCentroidWitness, extract_tokens
from perception.facets import facets_for
from perception.prototypes import Prediction, PrototypeBank

def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _uncertainty_from(pred: Optional[Prediction]) -> float:
    """Margin -> uncertainty, against THIS witness's own spread (`Prediction.clarity`).

    The LEVEL of a cosine or an NB posterior is not calibrated; the gap to the runner-up survives
    better. But the gap is only meaningful per witness — measured on real captures, a correct DOM
    call sits near a 0.37 margin and a correct visual call near 0.04, because every screenshot of
    a white form is cosine-similar to every other. A single shared threshold would have declared
    the visual witness permanently unsure. So each witness carries its own scale, fitted.
    """
    if pred is None or pred.label is None:
        return 1.0
    return _clamp(1.0 - pred.clarity)


@dataclass
class Observation:
    """The raw inputs one look at the page produces. Both halves are optional: a live turn may
    have page text and no screenshot, a replayed capture the reverse."""
    artifact: Optional[dict[str, Any]] = None
    page_text: str = ""
    screenshot_path: Optional[Path] = None
    url: str = ""
    domain_id: str = ""


class Observer:
    """Holds the two fitted witnesses and combines them. Cheap to construct, cheap to call."""

    def __init__(self, *, dom: Optional[TfidfCentroidWitness] = None,
                 visual: Optional[PrototypeBank] = None, encoder: Any = None,
                 dom_name: str = "dom:tfidf", visual_name: str = "visual") -> None:
        self.dom = dom
        self.visual = visual
        self.encoder = encoder
        self.dom_name = dom_name
        self.visual_name = visual_name

    # --- the two witnesses ------------------------------------------------------------
    def _ask_dom(self, obs: Observation) -> tuple[Optional[WitnessView], Optional[Prediction]]:
        if self.dom is None:
            return None, None
        tokens = extract_tokens(obs.artifact or {}, page_text=obs.page_text)
        if not tokens:
            return None, None
        pred = self.dom.predict(tokens)
        evidence = tuple(f for f, _ in self.dom.top_features(pred.label or "", k=3))
        return WitnessView(name=self.dom_name, label=pred.label, similarity=pred.similarity,
                           margin=pred.margin, novelty=pred.novelty, top_evidence=evidence), pred

    def _ask_visual(self, obs: Observation) -> tuple[Optional[WitnessView], Optional[Prediction]]:
        if self.visual is None or self.encoder is None or not obs.screenshot_path:
            return None, None
        vec = self.encoder.embed(obs.screenshot_path)
        if not vec:
            return None, None
        pred = self.visual.predict(vec)
        return WitnessView(name=self.visual_name, label=pred.label, similarity=pred.similarity,
                           margin=pred.margin, novelty=pred.novelty), pred

    # --- fusion ----------------------------------------------------------------------
    def observe(self, obs: Observation, *, prior: tuple[str, ...] = (),
                extra_uncertainty: Optional[dict[str, float]] = None) -> BeliefState:
        dom_view, dom_pred = self._ask_dom(obs)
        vis_view, vis_pred = self._ask_visual(obs)
        views = tuple(v for v in (dom_view, vis_view) if v)

        if not views:
            return BeliefState(state=None, agreement="no_evidence",
                               uncertainty={"state": 1.0, "novelty": 1.0},
                               rationale="no witness could read this page")

        both = dom_view is not None and vis_view is not None
        if both and dom_view.label == vis_view.label:
            agreement = "agree"
        elif both:
            agreement = "split"
        else:
            agreement = "one_sided"

        # Witness A leads. Measured: when they split, the DOM is right roughly twice as often.
        leader_view = dom_view or vis_view
        leader_pred = dom_pred if dom_view else vis_pred
        state = leader_view.label

        base = _uncertainty_from(leader_pred)
        if agreement == "agree":
            state_uncertainty = base * 0.5
            rationale = f"both witnesses say {state}"
        elif agreement == "split":
            # A split can never read as confident, however wide the leader's margin looked.
            state_uncertainty = _clamp(0.5 + base * 0.5, lo=0.5)
            rationale = (f"witnesses disagree — {dom_view.name} says {dom_view.label}, "
                         f"{vis_view.name} says {vis_view.label}; following {dom_view.name}")
        else:
            state_uncertainty = base
            rationale = f"only {leader_view.name} could read this page"

        prior_agrees = bool(prior) and state in prior
        if prior_agrees:
            # The recipe edge expected exactly this. A prior that agrees is evidence; a prior that
            # disagrees is NOT counter-evidence (the page may legitimately have branched), so it
            # narrows uncertainty and never widens it.
            state_uncertainty *= 0.8
            rationale += "; the recipe expected this"

        novelty = max((v.novelty for v in views), default=1.0)
        if novelty >= NOVELTY_CEILING:
            rationale += f"; but this looks unlike anything known (novelty {novelty:.2f})"

        uncertainty = {"state": round(_clamp(state_uncertainty), 4),
                       "novelty": round(_clamp(novelty), 4)}
        for axis, value in (extra_uncertainty or {}).items():
            uncertainty[axis] = round(_clamp(float(value)), 4)

        facets = facets_for(state or "", url=obs.url, domain_id=obs.domain_id)
        return BeliefState(
            state=state,
            facets=facets.as_dict(),
            prior=tuple(prior),
            prior_agrees=prior_agrees,
            witnesses=views,
            agreement=agreement,
            uncertainty=uncertainty,
            rationale=rationale,
        )
