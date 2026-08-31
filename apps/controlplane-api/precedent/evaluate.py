"""Leave-one-SESSION-out replay of the precedent engine (PLAN_inhouse_reasoner_v1 §8 P1-P3).

For every (situation -> intent) pair in the store, retrieve neighbors from OTHER sessions only
(P2: same-drive neighbors are near-duplicates; a random split is reported alongside, never
alone), vote distance-weighted over the closed intent vocabulary, and score:

- intent agreement (the loose bar's analogue), overall and per scenario (ats:state)
- selective accuracy at confidence thresholds — the number that matters for a cascade rung
  that is allowed to ABSTAIN (abstention -> next rung, exactly like every other rung)
- exact-ref agreement on the correct-intent subset that has a scoreable ref
- per-block ablation (text / vision / facets alone) so the weights follow measurement

Usage: python -m precedent.evaluate --db <data-root>/vectors.db
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from .embedder import FACET_SLICE, TEXT_SLICE, VISION_SLICE
from .store import VectorStore

K = 15
_EPS = 0.1


def _normalize_rows(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms < 1e-12] = 1.0
    return mat / norms


def _fold(ref: str) -> str:
    return " ".join((ref or "").lower().split())


def _evaluate(metas: list[dict], mat: np.ndarray, *, split: str) -> dict:
    """split: 'session' (honest) or 'random' (leakage check — excludes only self)."""
    n = len(metas)
    sims = mat @ mat.T
    sessions = np.array([m["session"] for m in metas])
    intents = [m["intent"] for m in metas]

    correct = 0
    scored = 0
    confidences: list[tuple[float, bool, int]] = []  # (confidence, correct, idx)
    per_scenario: dict[str, list[bool]] = defaultdict(list)
    exact_ok = exact_n = 0

    for i in range(n):
        row = sims[i].copy()
        if split == "session":
            row[sessions == sessions[i]] = -np.inf
        else:
            row[i] = -np.inf
        top = np.argpartition(-row, min(K, n - 1))[:K]
        top = top[np.argsort(-row[top])]
        votes: Counter = Counter()
        best_ref_by_intent: dict[str, str] = {}
        for j in top:
            if row[j] == -np.inf:
                continue
            dist = float(np.sqrt(max(0.0, 2.0 - 2.0 * row[j])))
            weight = 1.0 / (dist + _EPS)
            votes[intents[j]] += weight
            if intents[j] not in best_ref_by_intent and metas[j]["ref"]:
                best_ref_by_intent[intents[j]] = metas[j]["ref"]
        if not votes:
            continue
        scored += 1
        predicted, top_w = votes.most_common(1)[0]
        conf = top_w / sum(votes.values())
        is_correct = predicted == intents[i]
        correct += is_correct
        confidences.append((conf, is_correct, i))
        scenario = f"{metas[i]['ats'] or '?'}:{metas[i]['state'] or '?'}"
        per_scenario[scenario].append(is_correct)
        if is_correct and metas[i]["ref"] and predicted in best_ref_by_intent:
            exact_n += 1
            exact_ok += _fold(best_ref_by_intent[predicted]) == _fold(metas[i]["ref"])

    result = {
        "split": split,
        "n": scored,
        "agreement": round(correct / scored, 4) if scored else None,
        "exact_ref_agreement": round(exact_ok / exact_n, 4) if exact_n else None,
        "exact_ref_n": exact_n,
    }
    # selective accuracy: if the rung only acts when confident, what does it get?
    for floor in (0.5, 0.7, 0.9):
        kept = [(c, ok) for c, ok, _ in confidences if c >= floor]
        result[f"selective@{floor}"] = {
            "coverage": round(len(kept) / scored, 3) if scored else None,
            "accuracy": round(sum(ok for _, ok in kept) / len(kept), 4) if kept else None,
        }
    result["top_scenarios"] = sorted(
        (
            {"scenario": s, "n": len(v), "agreement": round(sum(v) / len(v), 3)}
            for s, v in per_scenario.items()
            if len(v) >= 10
        ),
        key=lambda d: -d["n"],
    )[:8]
    return result


def run(db_path: Path) -> dict:
    store = VectorStore(db_path)
    metas, vecs = store.all_vectors()
    store.close()
    mat = np.asarray(vecs, dtype=np.float32)

    report: dict = {"corpus": len(metas)}
    for kind, label in (("decision", "decisions"), ("transition_before", "transitions")):
        idx = [i for i, m in enumerate(metas) if m["kind"] == kind and m["intent"]]
        if len(idx) < 20:
            report[label] = {"skipped": f"only {len(idx)} labeled rows"}
            continue
        sub_meta = [metas[i] for i in idx]
        sub = _normalize_rows(mat[idx])
        majority = Counter(m["intent"] for m in sub_meta).most_common(1)[0]
        block: dict = {
            "labeled": len(idx),
            "majority_baseline": {
                "intent": majority[0],
                "agreement": round(majority[1] / len(idx), 4),
            },
            "session_split": _evaluate(sub_meta, sub, split="session"),
            "random_split_for_leakage_check": _evaluate(sub_meta, sub, split="random"),
        }
        ablations = {}
        for name, sl in (("text", TEXT_SLICE), ("vision", VISION_SLICE), ("facets", FACET_SLICE)):
            sliced = mat[idx][:, sl]
            live = np.linalg.norm(sliced, axis=1) > 1e-9
            if live.sum() < 20:
                ablations[name] = {"skipped": f"only {int(live.sum())} rows carry this block"}
                continue
            live_meta = [m for m, keep in zip(sub_meta, live) if keep]
            res = _evaluate(live_meta, _normalize_rows(sliced[live]), split="session")
            ablations[name] = {"n": res["n"], "agreement": res["agreement"]}
        block["ablation_session_split"] = ablations
        report[label] = block
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    args = ap.parse_args()
    print(json.dumps(run(args.db), indent=2))


if __name__ == "__main__":
    main()
