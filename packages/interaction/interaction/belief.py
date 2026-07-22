"""BeliefState — five uncertainties, kept apart (PLAN_perception_v1 §3.3).

Today the controller carries `Bundle.state: str | None` and one `Decision.confidence: float`. The
system is uncertain about **five different things**, and collapsing them into one number destroys
exactly the information the recovery ladder needs to choose a rung:

  state    "I don't know where I am"          -> re-observe, or ask the second witness
  element  "I don't know which control"       -> re-scan, or ground visually
  answer   "I don't know what value is right" -> resolve_answer rungs, then the human
  effect   "I don't know if that worked"      -> the supervisor's job (StateDelta + verdict)
  novelty  "I've never been anywhere like this" -> retrieve episodes, then escalate

"Confidence 0.6" cannot tell those apart, so a loop reading it can only do one generic thing.
`RecoveryPlay` already speaks five different remedies; this is the input that lets it pick.

Frozen and versioned like `contract.py` and `decision.py`: it is a prompt surface today and a
training feature set tomorrow, so its serialization is part of the contract, not a convenience.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

BELIEF_SCHEMA_VERSION = "v1"

#: The axes, in the order they are rendered. CLOSED — extend the way `Outcome` was extended.
AXES = ("state", "element", "answer", "effect", "novelty")

#: How two witnesses ended up relative to each other. `split` is the interesting one: measured
#: 2026-07-22, agreement rows are right 77.9% of the time and split rows 48.2% — a 30-point gap,
#: which is what earns the second witness its place (PLAN_perception_v1 §8's cleanest falsifier).
AGREEMENT = ("agree", "split", "one_sided", "no_evidence")

#: Uncertainty above this on the `state` axis means the loop must not take a consequential action.
#: Same floor as `DECISION_CONFIDENCE_THRESHOLD` (0.75 confidence == 0.25 uncertainty), stated as
#: uncertainty so every axis reads the same direction: 0 = sure, 1 = no idea.
UNCERTAINTY_CEILING = 0.25

#: Irreversible actions (Submit, account creation) get a much stricter bar, and it does not move.
#: The human's rail, expressed as a number so a caller cannot forget it.
CONSEQUENTIAL_CEILING = 0.10

#: Above this on the `novelty` axis, the page is treated as somewhere we have never been.
#: NOT 0.5, and the reason is structural rather than tuned: novelty is a PERCENTILE against how
#: familiar known pages looked, so in-distribution observations spread roughly uniformly over
#: [0,1] — a 0.5 cut-off would flag half of every page we know. **The cut-off IS the false-flag
#: rate**: 0.90 means "less familiar than 90% of everything we have seen", i.e. ~10% of known
#: pages get a second look. Cheap insurance; tighten it only against measured novelty AUROC.
NOVELTY_CEILING = 0.90


@dataclass(frozen=True)
class WitnessView:
    """One witness's answer, kept SEPARATE from the other's on purpose.

    Late fusion, never concatenation: two witnesses with different failure modes are only worth
    having while you can still see them disagree. `margin` — the gap to the runner-up — is what a
    caller should gate on rather than `similarity`; neither Naive Bayes posteriors nor cosine
    scores are calibrated, and the gap survives that better than the level does.
    """
    name: str                       # "dom:tfidf" | "visual:apple" | …
    label: Optional[str]
    similarity: float = 0.0
    margin: float = 0.0
    novelty: float = 0.0
    top_evidence: tuple[str, ...] = ()   # the features that drove it — receipts, per §10

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.label, "similarity": round(self.similarity, 4),
                "margin": round(self.margin, 4), "novelty": round(self.novelty, 4),
                "top_evidence": list(self.top_evidence)}


@dataclass(frozen=True)
class BeliefState:
    """What we believe about where we are, and how unsure we are about what, separately."""
    state: Optional[str]
    facets: dict[str, str] = field(default_factory=dict)
    prior: tuple[str, ...] = ()            # `expected_next` — the recipe edge's transition prior
    prior_agrees: bool = False             # did the fused answer land inside the prior?
    witnesses: tuple[WitnessView, ...] = ()
    agreement: str = "no_evidence"
    uncertainty: dict[str, float] = field(default_factory=dict)
    rationale: str = ""

    # --- reading it ---------------------------------------------------------------
    def unsure_about(self, axis: str) -> float:
        """1.0 (no idea) for an axis nobody has spoken to — silence is not confidence."""
        return float(self.uncertainty.get(axis, 1.0))

    @property
    def is_novel(self) -> bool:
        return self.unsure_about("novelty") >= NOVELTY_CEILING

    def blocks(self, *, consequential: bool = False) -> Optional[str]:
        """The axis that should stop a caller, or None. Checked in escalation order.

        `novelty` outranks `state`: a page we have never seen can still produce a confident
        nearest-neighbour label, and acting on that is the failure mode the second witness was
        bought to prevent.

        An axis **nobody assessed** does not block — the observer speaks to `state` and `novelty`,
        while `element`/`answer`/`effect` are other subsystems' answers and are simply absent
        until they run. (`unsure_about` still reports 1.0 for them, because silence is not
        confidence; the difference is that a caller cannot be blocked by a question no one asked.)
        """
        ceiling = CONSEQUENTIAL_CEILING if consequential else UNCERTAINTY_CEILING
        if "novelty" in self.uncertainty and self.is_novel:
            return "novelty"
        for axis in ("state", "element", "answer"):
            if axis in self.uncertainty and self.uncertainty[axis] > ceiling:
                return axis
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BELIEF_SCHEMA_VERSION,
            "state": self.state,
            "facets": dict(self.facets),
            "prior": list(self.prior),
            "prior_agrees": self.prior_agrees,
            "agreement": self.agreement,
            "uncertainty": {a: round(self.unsure_about(a), 4) for a in AXES},
            #: WHICH axes anybody actually spoke to. `uncertainty` above renders all five for
            #: legibility, filling the silent ones with 1.0 — which is right for a human reading a
            #: row and WRONG for a machine reading it back, because `blocks()` draws its central
            #: distinction between "no idea" (1.0, blocks) and "nobody asked" (absent, does not).
            #: Without this key the two are indistinguishable after a round-trip, and a replayed
            #: row blocks on `element`/`answer` that no subsystem ever assessed. Found by
            #: `test_dict_belief_matches_object_belief` the first time anything read a belief back.
            "assessed": [a for a in AXES if a in self.uncertainty],
            "witnesses": [w.as_dict() for w in self.witnesses],
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BeliefState":
        """Rebuild a belief from its journaled form — the inverse of `as_dict`.

        Exists so that everything reading a belief back (authority, replay, the evals) runs the
        SAME `blocks()` as the live drive instead of re-deriving the ceilings locally. Two
        implementations of a policy is two policies.

        `assessed` is honoured when present; a row written before it existed falls back to the
        keys the dict actually carries, which is the literal reading of that older format.
        """
        unc_raw = dict(d.get("uncertainty") or {})
        assessed = d.get("assessed")
        keys = list(assessed) if assessed is not None else list(unc_raw)
        return cls(
            state=d.get("state"),
            facets=dict(d.get("facets") or {}),
            prior=tuple(d.get("prior") or ()),
            prior_agrees=bool(d.get("prior_agrees", False)),
            witnesses=tuple(
                WitnessView(name=w.get("name", ""), label=w.get("label"),
                            similarity=float(w.get("similarity", 0.0)),
                            margin=float(w.get("margin", 0.0)),
                            novelty=float(w.get("novelty", 0.0)),
                            top_evidence=tuple(w.get("top_evidence") or ()))
                for w in (d.get("witnesses") or ()) if isinstance(w, dict)
            ),
            agreement=d.get("agreement", "no_evidence"),
            uncertainty={a: float(unc_raw[a]) for a in keys if a in unc_raw},
            rationale=d.get("rationale", ""),
        )


def belief_to_prompt(belief: BeliefState) -> str:
    """The serialization a reasoner reads and a policy trains on — frozen like
    `bundle_to_prompt`. One line per fact, no prose, cheap to tokenize."""
    lines = [f"state: {belief.state or 'unknown'}"]
    if belief.facets:
        lines.append("facets: " + " ".join(f"{k}={v}" for k, v in sorted(belief.facets.items())))
    if belief.prior:
        mark = "yes" if belief.prior_agrees else "NO"
        lines.append(f"expected: {', '.join(belief.prior)} (matched: {mark})")
    lines.append("unsure: " + " ".join(f"{a}={belief.unsure_about(a):.2f}" for a in AXES))
    lines.append(f"witnesses ({belief.agreement}):")
    for w in belief.witnesses:
        ev = f" [{', '.join(w.top_evidence[:3])}]" if w.top_evidence else ""
        lines.append(f"  - {w.name}: {w.label or 'none'} "
                     f"(margin {w.margin:.2f}, novelty {w.novelty:.2f}){ev}")
    if belief.rationale:
        lines.append(f"why: {belief.rationale}")
    return "\n".join(lines)
