"""Witness A — the DOM/AX witness (PLAN_perception_v1 §3.2).

This is the existing Naive Bayes **assisted**, not replaced. Two upgrades, both cheap:

**1. It gets to see more of the page.** `state_observer.extract_features` reads the route plus
`ranked_candidates[].target.{role,label}` and nothing else — so it has been judging Workday's six
near-identical form phases without ever reading the page title, the field placeholders, the
dialog flag, or the `actionable_elements` text that actually distinguishes them. Those are all
sitting in the same capture artifact, already written, never read. (Same shape as every finding
since 2026-07-16: the work is connection, not construction.)

**2. It gets a second model family over the identical features.** Multinomial NB assumes feature
independence, and page tokens are heavily correlated — "Sign In" co-occurs with "Password" every
single time — so NB's posteriors are famously overconfident exactly where a state is ambiguous.
A TF-IDF centroid over the same tokens does not make that assumption, costs the same nothing, and
reports a *margin* and a *calibrated novelty* in the same shape witness B does, which is what
makes late fusion a two-line operation instead of a fudge factor.

Which one wins is a measurement, not a preference — `bench.py` runs both on one split.
"""

from __future__ import annotations

import math
import re
from bisect import bisect_right
from collections import Counter, defaultdict
from typing import Any, Iterable, Optional

from perception.prototypes import Prediction, _median

#: Bump when `extract_tokens` changes what it emits — a mid-corpus feature drift silently
#: invalidates every row trained before it (the `bundle_to_prompt` discipline, one floor down).
FEATURE_SET_VERSION = "v2"

_WORD = re.compile(r"[a-z0-9]{2,}")
_MAX_ELEMENTS = 60        # cap elements read per capture — keeps the vector sparse and cheap
_MAX_TOKENS_PER_TEXT = 8
_MAX_TOTAL_TOKENS = 400


def _tokens(text: str, limit: int = _MAX_TOKENS_PER_TEXT) -> list[str]:
    return _WORD.findall((text or "").lower())[:limit]


def _normalize(vec: dict[str, float]) -> dict[str, float]:
    norm = math.sqrt(sum(x * x for x in vec.values())) or 1.0
    return {t: x / norm for t, x in vec.items()}


def _route(url: str) -> str:
    try:
        from select_stage.fingerprint import route_template
        return route_template(url or "")
    except Exception:
        return (url or "").split("?")[0][:120]


def extract_tokens(artifact: dict[str, Any], *, page_text: str = "") -> list[str]:
    """Presence-features for one observation. Namespaced so a caller can read WHY a state won.

    Sources, in order of how much they cost us to get (all zero — they are already in the
    artifact): the route, the page title, the AX roles/tags, accessible names and labels, the
    element text and placeholders, and the dialog flag. `page_text` is accepted separately so the
    live path (`LiveActuator.observe()`, which has page text but no artifact) can use the same
    featurizer as the trainer — one featurizer, two callers, no drift.
    """
    acq = artifact.get("acquisition") or {}
    identity = acq.get("page_identity") or {}
    url = identity.get("url") or artifact.get("url") or ""

    feats: list[str] = [f"route:{_route(url)}"]
    for tok in _tokens(identity.get("title") or artifact.get("title") or "", 12):
        feats.append(f"title:{tok}")

    frame = acq.get("frame_state") or {}
    if frame.get("dialog_present"):
        feats.append("flag:dialog")
    if (frame.get("frame_count") or 0) > 0:
        feats.append("flag:frames")

    seen = 0
    for cand in (artifact.get("ranked_candidates") or [])[:_MAX_ELEMENTS]:
        target = cand.get("target") or {}
        role = (target.get("role") or target.get("tag") or "").strip().lower()
        if role:
            feats.append(f"role:{role}")
        for tok in _tokens(target.get("label") or target.get("name") or ""):
            feats.append(f"tok:{tok}")
            seen += 1
        if seen > _MAX_TOTAL_TOKENS:
            break

    for el in (acq.get("actionable_elements") or [])[:_MAX_ELEMENTS]:
        tag = (el.get("role") or el.get("tag") or "").strip().lower()
        if tag:
            feats.append(f"role:{tag}")
        for source, prefix in (("name", "tok"), ("label", "tok"), ("text", "txt"),
                               ("placeholder", "ph")):
            for tok in _tokens(el.get(source) or ""):
                feats.append(f"{prefix}:{tok}")
                seen += 1
        if seen > _MAX_TOTAL_TOKENS:
            break

    for tok in _tokens(page_text, 120):
        feats.append(f"txt:{tok}")

    return feats


# --------------------------------------------------------------------------------------
# Model family 1 — TF-IDF centroid (no independence assumption, margin + calibrated novelty)
# --------------------------------------------------------------------------------------
class TfidfCentroidWitness:
    """Cosine to a per-label TF-IDF centroid. The sparse twin of `PrototypeBank`."""

    name = "tfidf_centroid"

    def __init__(self) -> None:
        self.idf: dict[str, float] = {}
        self.centroids: dict[str, dict[str, float]] = {}
        self.counts: dict[str, int] = {}
        self._calibration: list[float] = []
        self._calibration_by_label: dict[str, list[float]] = {}
        self.margin_scale: float = 0.1

    def _vectorize(self, tokens: Iterable[str]) -> dict[str, float]:
        counts = Counter(tokens)
        vec = {t: (1.0 + math.log(c)) * self.idf.get(t, self._oov_idf) for t, c in counts.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    @property
    def _oov_idf(self) -> float:
        """An unseen token is maximally informative — but capped, so one novel word cannot
        dominate a vector built from four hundred familiar ones."""
        return self._max_idf

    def fit(self, examples: Iterable[tuple[str, list[str]]]) -> "TfidfCentroidWitness":
        rows = [(lab, list(toks)) for lab, toks in examples if lab and toks]
        n_docs = len(rows) or 1
        df: Counter = Counter()
        for _lab, toks in rows:
            df.update(set(toks))
        self.idf = {t: math.log((1 + n_docs) / (1 + c)) + 1.0 for t, c in df.items()}
        self._max_idf = math.log(1 + n_docs) + 1.0

        grouped: dict[str, list[dict[str, float]]] = defaultdict(list)
        for lab, toks in rows:
            grouped[lab].append(self._vectorize(toks))
        self.centroids = {}
        self.counts = {}
        sums: dict[str, dict[str, float]] = {}
        for lab, vecs in grouped.items():
            acc: dict[str, float] = defaultdict(float)
            for v in vecs:
                for t, val in v.items():
                    acc[t] += val
            sums[lab] = dict(acc)
            self.centroids[lab] = _normalize(acc)
            self.counts[lab] = len(vecs)

        # Leave-one-out, singletons excluded — same reasoning as `PrototypeBank.fit`, and the
        # same bug it fixes: a document inside its own centroid is not evidence of familiarity,
        # and 59 states over 174 rows means most centroids would otherwise be a copy of their
        # single example scoring a perfect 1.0.
        sims: list[float] = []
        margins: list[float] = []
        by_label: dict[str, list[float]] = {}
        for lab, vecs in grouped.items():
            if len(vecs) < 2:
                continue
            for v in vecs:
                loo = _normalize({t: x - v.get(t, 0.0) for t, x in sums[lab].items()})
                by_label.setdefault(lab, []).append(self._cos(v, loo))
                scored = sorted((self._cos(v, loo if other == lab else cent)
                                 for other, cent in self.centroids.items()), reverse=True)
                sims.append(scored[0])
                margins.append(scored[0] - scored[1] if len(scored) > 1 else scored[0])
        self._calibration = sorted(sims)
        self._calibration_by_label = {lab: sorted(v) for lab, v in by_label.items()}
        self.margin_scale = _median(margins) or 0.1
        return self

    @staticmethod
    def _cos(a: dict[str, float], b: dict[str, float]) -> float:
        if len(a) > len(b):
            a, b = b, a
        return sum(v * b.get(t, 0.0) for t, v in a.items())

    def novelty(self, similarity: float, label: Optional[str] = None) -> float:
        """Percentile against in-distribution familiarity — see `PrototypeBank.novelty`.
        Class-conditional where the data allows, global pool otherwise."""
        pool = self._calibration_by_label.get(label or "") or self._calibration
        if not pool:
            return 0.0
        idx = bisect_right(pool, similarity)
        return round(1.0 - idx / len(pool), 4)

    def predict(self, tokens: list[str]) -> Prediction:
        if not self.centroids or not tokens:
            return Prediction(label=None, similarity=0.0, margin=0.0, novelty=1.0)
        vec = self._vectorize(tokens)
        scores = {lab: self._cos(vec, cent) for lab, cent in self.centroids.items()}
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        best_label, best = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
        return Prediction(label=best_label, similarity=best, margin=best - runner_up,
                          novelty=self.novelty(best, best_label), scores=scores,
                          margin_scale=self.margin_scale)

    # --- persistence ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {"model_type": "dom_tfidf_centroid_v1", "feature_set": FEATURE_SET_VERSION,
                "idf": self.idf, "max_idf": getattr(self, "_max_idf", 1.0),
                "centroids": self.centroids, "counts": self.counts,
                "calibration": self._calibration, "margin_scale": self.margin_scale,
                "calibration_by_label": self._calibration_by_label}

    @classmethod
    def from_dict(cls, blob: dict[str, Any]) -> "TfidfCentroidWitness":
        witness = cls()
        witness.idf = dict(blob.get("idf") or {})
        witness._max_idf = float(blob.get("max_idf") or 1.0)
        witness.centroids = {k: dict(v) for k, v in (blob.get("centroids") or {}).items()}
        witness.counts = dict(blob.get("counts") or {})
        witness._calibration = sorted(blob.get("calibration") or [])
        witness.margin_scale = float(blob.get("margin_scale") or 0.1)
        witness._calibration_by_label = {k: sorted(v) for k, v
                                         in (blob.get("calibration_by_label") or {}).items()}
        return witness

    def top_features(self, label: str, k: int = 8) -> list[tuple[str, float]]:
        """The tokens that make this label what it is — the witness's own explanation, faithful
        to what it actually computed (PRINCIPLES §10: rationale with receipts)."""
        cent = self.centroids.get(label) or {}
        return sorted(cent.items(), key=lambda kv: kv[1], reverse=True)[:k]


# --------------------------------------------------------------------------------------
# Model family 2 — multinomial Naive Bayes over the identical features
# --------------------------------------------------------------------------------------
class NaiveBayesWitness:
    """The incumbent, wrapped so the bench compares model families and not featurizers.

    Reuses `state_observer`'s NB math verbatim — the point is to change ONE variable at a time.
    """

    name = "naive_bayes"

    def __init__(self) -> None:
        self.model: Optional[dict[str, Any]] = None

    def fit(self, examples: Iterable[tuple[str, list[str]]]) -> "NaiveBayesWitness":
        import state_observer
        records = [{"label": lab, "features": list(toks)} for lab, toks in examples if lab and toks]
        self.model = state_observer._train_nb(records) if records else None
        return self

    def predict(self, tokens: list[str]) -> Prediction:
        import state_observer
        if not self.model or not tokens:
            return Prediction(label=None, similarity=0.0, margin=0.0, novelty=1.0)
        out = state_observer.predict(self.model, tokens)
        probs = out.get("probs") or {}
        ranked = sorted(probs.values(), reverse=True)
        margin = (ranked[0] - ranked[1]) if len(ranked) > 1 else ranked[0] if ranked else 0.0
        # NB has no notion of "far from everything" — by construction its posteriors sum to 1
        # over the KNOWN classes. Reporting 0.0 is the honest answer, not a modelling gap to
        # paper over: that blind spot is precisely why witness B exists.
        return Prediction(label=out.get("label"), similarity=float(out.get("confidence") or 0.0),
                          margin=float(margin), novelty=0.0, scores=probs)
