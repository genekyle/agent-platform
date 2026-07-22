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

#: Below this DOM clarity, ask the eyes. Not chosen — measured (2026-07-22, 151 leave-one-out
#: rows): above the cut the DOM witness is right **80.2%** of the time; at or below it, **40.0%**.
#: One number splits the corpus into a half we can act on and a third we cannot, which makes it
#: the natural trigger. In that low band a split with the visual witness drops accuracy to **20%**
#: — the strongest single "do not act" signal in the system.
VISION_CLARITY_FLOOR = 0.6

#: …and ask the eyes when the ears think this is somewhere new, because OR-fusion is the only
#: novelty configuration that beats one witness alone (recall at a 10% false-flag budget: dom
#: 48.3%, visual 44.4%, AND 39.7%, **OR 50.3%**). Slightly below the acting ceiling so the eyes
#: get a say BEFORE the belief is declared novel, not after.
VISION_NOVELTY_FLOOR = 0.80

#: What a lone visual witness is worth as evidence of state. Deliberately a constant and not its
#: margin: the visual margin separates its right answers from its wrong ones at **AUROC 0.503** —
#: chance. Its LABEL carries information; its confidence carries none, and reading the latter as
#: certainty is precisely the "confidently wrong where it should have raised its hand" failure.
VISUAL_ONLY_UNCERTAINTY = 0.75


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

    # --- the cascade ------------------------------------------------------------------
    def should_consult_eyes(self, dom: Optional[Prediction], *,
                            consequential: bool = False) -> tuple[bool, str]:
        """Is this a turn where a second opinion can change the answer? Returns (yes, why).

        A committee asks both witnesses every turn and averages. A cascade asks the cheap one and
        escalates — the same rule as every other layer here. Which is right was measured, not
        assumed: agreement predicts error WORSE than the DOM's own margin on average (0.656 vs
        0.774), and better than anything in the band where the DOM is unclear (agree 60% / split
        20%). So: ask the eyes where they pay, skip them where they only re-derive.
        """
        if dom is None or dom.label is None:
            return True, "the ears could not read the page"
        if consequential:
            return True, "the action is consequential — the strict bar applies"
        if dom.clarity <= VISION_CLARITY_FLOOR:
            return True, f"the ears are unclear (clarity {dom.clarity:.2f})"
        if dom.novelty >= VISION_NOVELTY_FLOOR:
            return True, f"the ears think this may be new (novelty {dom.novelty:.2f})"
        return False, ""

    # --- fusion ----------------------------------------------------------------------
    def observe(self, obs: Observation, *, prior: tuple[str, ...] = (),
                consequential: bool = False,
                extra_uncertainty: Optional[dict[str, float]] = None) -> BeliefState:
        dom_view, dom_pred = self._ask_dom(obs)
        consult, why_consult = self.should_consult_eyes(dom_pred, consequential=consequential)
        vis_view, vis_pred = self._ask_visual(obs) if consult else (None, None)
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
        elif dom_view is not None and not consult:
            agreement = "not_consulted"
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
            # A split can never read as confident, however wide the leader's margin looked — and
            # in the low-clarity band where the eyes get consulted, a split means the leader is
            # right one time in five.
            state_uncertainty = _clamp(0.5 + base * 0.5, lo=0.5)
            rationale = (f"witnesses disagree — {dom_view.name} says {dom_view.label}, "
                         f"{vis_view.name} says {vis_view.label}; following {dom_view.name}")
        elif agreement == "not_consulted":
            state_uncertainty = base
            rationale = f"{dom_view.name} is clear ({state}); the eyes were not needed"
        elif dom_view is None:
            # Vision alone. Its label is worth having; its CONFIDENCE is chance (AUROC 0.503), so
            # a lone visual witness is never allowed to read as sure however wide its margin.
            state_uncertainty = max(base, VISUAL_ONLY_UNCERTAINTY)
            rationale = f"only {leader_view.name} could read this page, and its confidence is not informative"
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
