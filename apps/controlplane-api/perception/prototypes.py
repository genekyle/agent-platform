"""The prototype bank — witness B's memory (PLAN_perception_v1 §2, rung 1).

Not a trained model: a per-label centroid over frozen embeddings, compared by cosine. ~100 lines,
no sklearn, no training loop, no GPU. That is deliberate and it is the right tool for our data
shape, not a compromise:

  * it works at n=3 examples per class, which is the regime we are actually in (18 of 33 labeled
    states have >= 2 examples; the median is 3);
  * an update is an average, so an operator correction lands INSTANTLY — the same property that
    made Naive Bayes the right first witness (counts) rather than a nicety;
  * distance to the nearest prototype is a NOVELTY score, and novelty is the thing the sparse
    witness structurally cannot produce. NB can only be unsure *between known classes*; it can
    never be unsure of *everything*.

**Novelty is calibrated, never thresholded on raw cosine.** Measured 2026-07-22, Apple Vision's
same-state median cosine is 0.897 against a different-state median of 0.811 — a band far too
narrow for a hand-picked cut-off to survive a new tenant. So the bank stores the in-distribution
distribution of "how similar was a known example to its own prototype" and reports novelty as the
fraction of that distribution this observation is LESS similar than. 0.0 = utterly familiar,
1.0 = further from everything we know than any training example ever was. Threshold-free,
comparable across encoders, and it degrades honestly when the encoder is weak (everything scores
mid-range) instead of pretending.
"""

from __future__ import annotations

import json
import math
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA_VERSION = "v1"


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    return dot / denom if denom else 0.0


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _centroid(vectors: list[list[float]]) -> list[float]:
    n = len(vectors)
    dim = len(vectors[0])
    out = [0.0] * dim
    for vec in vectors:
        for i, v in enumerate(vec):
            out[i] += v
    return [v / n for v in out]


@dataclass
class Prediction:
    """One witness's answer. `margin` — not raw confidence — is what a caller should gate on:
    Naive Bayes taught us posteriors lie when features correlate, and cosine-to-centroid is no
    better calibrated. The gap to the runner-up is the honest quantity.

    `margin_scale` travels WITH the prediction because a margin is only meaningful against its own
    witness's spread. Found by running the observer on real captures: the DOM witness's correct
    calls sit around a 0.37 margin while the visual witness's sit around 0.04 — every cosine is
    ~0.9 when every screenshot is a white form. One shared threshold would have read the visual
    witness as permanently unsure and the DOM witness as permanently certain.
    """
    label: Optional[str]
    similarity: float
    margin: float
    novelty: float
    scores: dict[str, float] = field(default_factory=dict)
    margin_scale: float = 0.1

    @property
    def clarity(self) -> float:
        """Margin as a fraction of what a confident call looks like for THIS witness. [0,1]."""
        if self.label is None or self.margin_scale <= 0:
            return 0.0
        return max(0.0, min(1.0, self.margin / self.margin_scale))

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "similarity": round(self.similarity, 4),
                "margin": round(self.margin, 4), "novelty": round(self.novelty, 4),
                "clarity": round(self.clarity, 4)}


class PrototypeBank:
    """label -> centroid, plus the calibration curve novelty is read against."""

    def __init__(self, encoder_name: str = "") -> None:
        self.encoder_name = encoder_name
        self.prototypes: dict[str, list[float]] = {}
        self.counts: dict[str, int] = {}
        self._calibration: list[float] = []   # sorted in-distribution nearest-prototype sims
        self._calibration_by_label: dict[str, list[float]] = {}
        self.margin_scale: float = 0.1

    # --- construction ----------------------------------------------------------------
    def fit(self, examples: Iterable[tuple[str, list[float]]]) -> "PrototypeBank":
        grouped: dict[str, list[list[float]]] = {}
        for label, vec in examples:
            if not label or not vec:
                continue
            grouped.setdefault(label, []).append(vec)
        self.prototypes = {lab: _centroid(vecs) for lab, vecs in grouped.items()}
        self.counts = {lab: len(vecs) for lab, vecs in grouped.items()}

        # Calibrate LEAVE-ONE-OUT, and never on a singleton class.
        #
        # Found 2026-07-22 by running the fitted observer on real captures: a genuine
        # `workday_questions` page scored novelty 0.93 — "less familiar than 93% of what we
        # know" — about a page in its own training set. Two causes, both fixed here. (a) An
        # example is INSIDE its own centroid, so measuring familiarity against it is measuring
        # nothing. (b) With 59 states over 174 rows, most classes are singletons whose centroid
        # IS their one example, so they score a perfect 1.0 and shove the whole percentile curve
        # up until every multi-example state reads as an outlier. Excluding them costs us the
        # states we know least about and buys a calibration that means what it says.
        sims: list[float] = []
        margins: list[float] = []
        by_label: dict[str, list[float]] = {}
        for label, vecs in grouped.items():
            n = len(vecs)
            if n < 2:
                continue
            proto = self.prototypes[label]
            for vec in vecs:
                loo = [(p * n - v) / (n - 1) for p, v in zip(proto, vec)]
                own = cosine(vec, loo)
                scored = sorted(
                    (cosine(vec, loo if lab == label else other)
                     for lab, other in self.prototypes.items()), reverse=True)
                sims.append(scored[0])
                by_label.setdefault(label, []).append(own)
                margins.append(scored[0] - scored[1] if len(scored) > 1 else scored[0])
        self._calibration = sorted(sims)
        self._calibration_by_label = {lab: sorted(v) for lab, v in by_label.items()}
        self.margin_scale = _median(margins) or 0.1
        return self

    def update(self, label: str, vec: list[float]) -> None:
        """Fold one new example into its centroid — the instant-correction path. Running mean, so
        the bank never needs the original vectors kept around."""
        if not label or not vec:
            return
        n = self.counts.get(label, 0)
        if n == 0 or label not in self.prototypes:
            self.prototypes[label] = list(vec)
            self.counts[label] = 1
            return
        proto = self.prototypes[label]
        self.prototypes[label] = [(p * n + v) / (n + 1) for p, v in zip(proto, vec)]
        self.counts[label] = n + 1

    # --- inference -------------------------------------------------------------------
    def novelty(self, best_similarity: float, label: Optional[str] = None) -> float:
        """How unfamiliar is this, given what we think it is?

        Calibrated **per predicted label** where we have the data, global pool otherwise. The
        third fix the real captures forced (2026-07-22): against one global pool, a 20-example
        state scored *more novel* than a 2-example state, because a centroid over twenty varied
        screenshots sits further from each of them than a centroid over two. That measures class
        TIGHTNESS, not novelty. Asking "given we think this is `workday_questions`, how typical is
        it *for* `workday_questions`?" is the question we actually meant — one class-conditional
        step short of conformal prediction, which is as far as 2-6 examples per class will carry.

        `bisect_right`, not `bisect_left`: a tie is not "more similar". With a small bank the
        calibration values bunch, and left-bisection reads an exact duplicate of a training
        example as maximally novel — the one thing it demonstrably is not.

        NB the score is a PERCENTILE, so in-distribution observations are spread roughly uniformly
        over [0,1] by construction. That is a feature (it is comparable across encoders and needs
        no threshold tuning per corpus) with one consequence a caller must respect: the novelty
        cut-off IS the false-flag rate. See `interaction.belief.NOVELTY_CEILING`.
        """
        pool = self._calibration_by_label.get(label or "") or self._calibration
        if not pool:
            return 0.0
        idx = bisect_right(pool, best_similarity)
        return round(1.0 - idx / len(pool), 4)

    def predict(self, vec: list[float]) -> Prediction:
        if not self.prototypes or not vec:
            return Prediction(label=None, similarity=0.0, margin=0.0, novelty=1.0)
        scores = {lab: cosine(vec, proto) for lab, proto in self.prototypes.items()}
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        best_label, best = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
        return Prediction(label=best_label, similarity=best, margin=best - runner_up,
                          novelty=self.novelty(best, best_label), scores=scores,
                          margin_scale=self.margin_scale)

    # --- persistence -----------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "encoder": self.encoder_name,
                "counts": self.counts, "calibration": self._calibration,
                "margin_scale": self.margin_scale, "prototypes": self.prototypes,
                "calibration_by_label": self._calibration_by_label}

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict()))
        return path

    @classmethod
    def from_dict(cls, blob: dict[str, Any]) -> "PrototypeBank":
        bank = cls(encoder_name=blob.get("encoder", ""))
        bank.prototypes = {k: list(v) for k, v in (blob.get("prototypes") or {}).items()}
        bank.counts = dict(blob.get("counts") or {})
        bank._calibration = sorted(blob.get("calibration") or [])
        bank.margin_scale = float(blob.get("margin_scale") or 0.1)
        bank._calibration_by_label = {k: sorted(v) for k, v
                                      in (blob.get("calibration_by_label") or {}).items()}
        return bank

    @classmethod
    def load(cls, path: Path) -> "PrototypeBank":
        return cls.from_dict(json.loads(Path(path).read_text()))
