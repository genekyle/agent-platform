"""State-transition model (the planner's look-ahead edge-model).

Given (from_state, action) predict the resulting to_state — i.e. "if I do this here,
where do I land?" This is the probabilistic, queryable version of the state graph's
INTENDED edges (observed_page_state --action_type_hint--> post_action_state), and it
is the substrate the planner searches over for look-ahead without firing anything.

v0 is deliberately a smoothed transition TABLE, not a neural net: with a handful of
labeled pairs a frequency table with a (from_state)-only backoff is both the honest
model and exactly what graph-search needs. It grows into something richer as the
(before, after) corpus grows. Same artifact/split conventions as the other trainers.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from training import _stable_split, utcnow_iso

MODEL_TYPE = "state_transition_table_v1"
_KEY_SEP = ""  # joins (from_state, action) into one JSON-safe key


def _key(from_state: str, action: Optional[str]) -> str:
    return f"{from_state}{_KEY_SEP}{action or ''}"


def build_transition_dataset(artifacts_root: Path, *, captures: list[Any]) -> dict[str, Any]:
    """Records = labeled (from_state, action, to_state) triples. A capture qualifies only
    if it has BOTH observed_page_state (from) and post_action_state (to) — the same pair
    the state graph reads as an intended edge."""
    records: list[dict[str, Any]] = []
    skipped = {"no_transition_pair": 0}
    for cap in captures:
        from_state = getattr(cap, "observed_page_state", None)
        to_state = getattr(cap, "post_action_state", None)
        if not from_state or not to_state:
            skipped["no_transition_pair"] += 1
            continue
        records.append({
            "filename": cap.artifact_filename,
            "from_state": from_state,
            "action": getattr(cap, "action_type_hint", None) or "any",
            "to_state": to_state,
            "domain_id": getattr(cap, "domain_id", None),
            "split": _stable_split(cap.artifact_filename),
        })

    dataset_dir = artifacts_root / "datasets"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    dataset_id = f"{utcnow_iso().replace(':', '-')}__state_transition"
    path = dataset_dir / f"{dataset_id}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")

    return {
        "dataset_id": dataset_id,
        "path": str(path),
        "record_count": len(records),
        "distinct_from_states": len({r["from_state"] for r in records}),
        "distinct_transitions": len({(r["from_state"], r["action"], r["to_state"]) for r in records}),
        "skipped": skipped,
    }


def _train_table(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Two nested frequency tables: the precise (from,action)->to and a (from)->to
    backoff for when the exact action hasn't been seen at that state yet."""
    by_state_action: dict[str, Counter] = defaultdict(Counter)
    by_state: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        by_state_action[_key(r["from_state"], r["action"])][r["to_state"]] += 1
        by_state[r["from_state"]][r["to_state"]] += 1
    return {
        "model_type": MODEL_TYPE,
        "by_state_action": {k: dict(v) for k, v in by_state_action.items()},
        "by_state": {k: dict(v) for k, v in by_state.items()},
    }


def predict(model: dict[str, Any], from_state: str, action: Optional[str] = None) -> dict[str, Any]:
    """Rank likely next states. Tries the exact (from,action) edge, then backs off to
    all transitions seen from `from_state`. `backoff` flags which path answered; an
    unknown from_state returns label=None (the planner treats that as 'needs exploration')."""
    exact = model.get("by_state_action", {}).get(_key(from_state, action))
    dist = exact
    backoff = False
    if not dist:
        dist = model.get("by_state", {}).get(from_state)
        backoff = True
    if not dist:
        return {"label": None, "confidence": 0.0, "ranked": [], "backoff": backoff,
                "reason": "unknown_from_state"}
    total = sum(dist.values()) or 1
    ranked = sorted(dist.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "label": ranked[0][0],
        "confidence": round(ranked[0][1] / total, 4),
        "ranked": [{"to_state": s, "prob": round(n / total, 4), "count": n} for s, n in ranked],
        "backoff": backoff,
    }


def _evaluate(model: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    correct = backoff_used = 0
    samples: list[dict[str, Any]] = []
    for r in records:
        pred = predict(model, r["from_state"], r["action"])
        ok = pred["label"] == r["to_state"]
        correct += 1 if ok else 0
        backoff_used += 1 if pred["backoff"] else 0
        samples.append({"from_state": r["from_state"], "action": r["action"],
                        "gold": r["to_state"], "pred": pred["label"],
                        "confidence": pred["confidence"], "backoff": pred["backoff"], "correct": ok})
    n = len(records)
    return {
        "eval_count": n,
        "top1_accuracy": round(correct / n, 4) if n else None,
        "backoff_rate": round(backoff_used / n, 4) if n else None,
        "samples": samples,
    }


def train_transition_model(artifacts_root: Path, *, captures: list[Any]) -> dict[str, Any]:
    """Build the dataset, fit the table on the train split, eval on held-out, persist."""
    manifest = build_transition_dataset(artifacts_root, captures=captures)
    records = [json.loads(line) for line in Path(manifest["path"]).read_text().splitlines() if line.strip()]
    if len(records) < 2:
        return {"ok": False, "reason": "insufficient_transition_pairs", **manifest}

    train_records = [r for r in records if r["split"] == "train"]
    eval_records = [r for r in records if r["split"] == "eval"]
    if not train_records:
        train_records = records
    model = _train_table(train_records)
    evaluation = _evaluate(model, eval_records or train_records)

    model_dir = artifacts_root / "models" / f"{utcnow_iso().replace(':', '-')}__{MODEL_TYPE}"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model.json").write_text(json.dumps({**model, "dataset_id": manifest["dataset_id"],
                                                      "created_at": utcnow_iso()}, indent=2), encoding="utf-8")
    metrics = {
        "train_count": len(train_records),
        "eval_count": evaluation["eval_count"],
        "eval_on": "held_out" if eval_records else "train_fallback",
        "top1_accuracy": evaluation["top1_accuracy"],
        "backoff_rate": evaluation["backoff_rate"],
        "distinct_from_states": manifest["distinct_from_states"],
        "distinct_transitions": manifest["distinct_transitions"],
    }
    (model_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (model_dir / "prediction_samples.json").write_text(json.dumps(evaluation["samples"][:25], indent=2), encoding="utf-8")

    return {
        "ok": True,
        "dataset_id": manifest["dataset_id"],
        "model_dir": str(model_dir),
        "record_count": manifest["record_count"],
        "skipped": manifest["skipped"],
        "metrics": metrics,
    }
