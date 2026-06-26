"""Coarse page-state observer (L3 v0) — the agent's "where am I?" model.

This is the cheapest useful state model: a 3-way auth-stage classifier
(authenticated / unauthenticated / neutral) over CHEAP features — the URL route
plus AX role/label tokens — with a pure-Python multinomial Naive Bayes. No vision
model, no GPU: it proves the L3 pipeline end-to-end on data we already have and
gives the planner its single most important signal (am I logged in?).

The fine 37-way page_state_classifier is data-gated (needs ~10-30 examples/state);
this coarse model trains today on ~48 labels across 3 classes. Same artifact/split
conventions as the grounding trainer (model.json / metrics.json, _stable_split eval).

Labels come from each capture's `observed_page_state` mapped through the page-state
registry's lifecycle `stage`. Features are presence tokens, so the model is fully
inspectable (you can read why it predicted a class from the per-feature weights).
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from training import _stable_split, utcnow_iso

MODEL_TYPE = "stage_observer_nb_v1"
STAGE_LABELS = ("authenticated", "unauthenticated", "neutral")
_ALPHA = 1.0          # Laplace smoothing
_MAX_LABEL_TOKENS = 6  # cap tokens per AX candidate label (keep features sparse)
_MAX_CANDIDATES = 40   # cap candidates considered per capture
_WORD = re.compile(r"[a-z0-9]{2,}")


def _route_template(url: str) -> str:
    """Route template for the URL (reuse the selector's canonicalizer if present)."""
    try:
        from select_stage.fingerprint import route_template
        return route_template(url or "")
    except Exception:
        return (url or "").split("?")[0][:120]


def extract_features(artifact: dict[str, Any]) -> list[str]:
    """Cheap presence-features for one capture: route + AX roles + label word tokens.

    Pulled straight from the capture artifact (no DB, no sidecar) so the trainer is
    decoupled. Discriminative for auth-stage because login pages surface 'log in' /
    'password' tokens while authed pages surface 'account' / 'sign out' / inbox roles."""
    acq = artifact.get("acquisition") or {}
    url = ((acq.get("page_identity") or {}).get("url")) or artifact.get("url") or ""
    feats: list[str] = [f"route:{_route_template(url)}"]

    candidates = (artifact.get("ranked_candidates") or [])[:_MAX_CANDIDATES]
    seen_tokens = 0
    for cand in candidates:
        target = cand.get("target") or {}
        role = (target.get("role") or target.get("tag") or "").strip().lower()
        if role:
            feats.append(f"role:{role}")
        label = (target.get("label") or target.get("name") or "").lower()
        for word in _WORD.findall(label)[:_MAX_LABEL_TOKENS]:
            feats.append(f"tok:{word}")
            seen_tokens += 1
            if seen_tokens > 200:  # hard cap per capture
                break
    return feats


def build_stage_dataset(artifacts_root: Path, *, captures: list[Any],
                        stage_by_state: dict[str, str]) -> dict[str, Any]:
    """Assemble (features, stage_label) records from labeled captures.

    A capture is included only if it has an `observed_page_state` that maps to one of
    the three lifecycle stages. The deterministic _stable_split keeps the eval set
    stable across runs (same contract as the grounding dataset)."""
    traces_dir = artifacts_root / "observer-traces"
    records: list[dict[str, Any]] = []
    skipped = {"no_observed_state": 0, "no_stage_mapping": 0, "missing_artifact": 0}

    for cap in captures:
        state = getattr(cap, "observed_page_state", None)
        if not state:
            skipped["no_observed_state"] += 1
            continue
        stage = stage_by_state.get(state)
        if stage not in STAGE_LABELS:
            skipped["no_stage_mapping"] += 1
            continue
        fn = cap.artifact_filename
        path = traces_dir / fn
        if not path.exists():
            skipped["missing_artifact"] += 1
            continue
        try:
            artifact = json.loads(path.read_text())
        except Exception:
            skipped["missing_artifact"] += 1
            continue
        records.append({
            "filename": fn,
            "observed_page_state": state,
            "label": stage,
            "features": extract_features(artifact),
            "split": _stable_split(fn),
        })

    dataset_dir = artifacts_root / "datasets"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    dataset_id = f"{utcnow_iso().replace(':', '-')}__stage_observer"
    path = dataset_dir / f"{dataset_id}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")

    return {
        "dataset_id": dataset_id,
        "path": str(path),
        "record_count": len(records),
        "label_counts": dict(Counter(r["label"] for r in records)),
        "skipped": skipped,
    }


def _train_nb(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Multinomial Naive Bayes with Laplace smoothing. Returns a self-contained,
    human-readable model artifact (log-priors + per-class feature log-likelihoods)."""
    class_feat_counts: dict[str, Counter] = defaultdict(Counter)
    class_doc_counts: Counter = Counter()
    class_total_feats: Counter = Counter()
    vocab: set[str] = set()

    for rec in records:
        label = rec["label"]
        class_doc_counts[label] += 1
        for feat in rec["features"]:
            class_feat_counts[label][feat] += 1
            class_total_feats[label] += 1
            vocab.add(feat)

    n_docs = sum(class_doc_counts.values())
    v = len(vocab)
    log_prior = {c: math.log(class_doc_counts[c] / n_docs) for c in class_doc_counts}
    feature_loglik: dict[str, dict[str, float]] = {}
    default_loglik: dict[str, float] = {}
    for c in class_doc_counts:
        denom = class_total_feats[c] + _ALPHA * v
        feature_loglik[c] = {
            f: math.log((class_feat_counts[c][f] + _ALPHA) / denom) for f in vocab
        }
        default_loglik[c] = math.log(_ALPHA / denom)  # unseen-feature fallback

    return {
        "model_type": MODEL_TYPE,
        "classes": sorted(class_doc_counts),
        "log_prior": log_prior,
        "feature_loglik": feature_loglik,
        "default_loglik": default_loglik,
        "vocab_size": v,
    }


def predict(model: dict[str, Any], features: list[str]) -> dict[str, Any]:
    """Score features under each class; return the argmax label + normalized scores."""
    scores: dict[str, float] = {}
    for c in model["classes"]:
        ll = model["feature_loglik"].get(c, {})
        default = model["default_loglik"].get(c, -20.0)
        s = model["log_prior"].get(c, -20.0)
        for f in features:
            s += ll.get(f, default)
        scores[c] = s
    best = max(scores, key=scores.get)
    # Softmax over log-scores for a calibrated-ish confidence.
    mx = max(scores.values())
    exps = {c: math.exp(s - mx) for c, s in scores.items()}
    z = sum(exps.values()) or 1.0
    probs = {c: round(exps[c] / z, 4) for c in exps}
    return {"label": best, "confidence": probs[best], "probs": probs}


def _evaluate(model: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Accuracy + per-class precision/recall + confusion, over the eval records."""
    confusion: dict[str, Counter] = defaultdict(Counter)
    correct = 0
    samples: list[dict[str, Any]] = []
    for rec in records:
        pred = predict(model, rec["features"])
        gold = rec["label"]
        confusion[gold][pred["label"]] += 1
        if pred["label"] == gold:
            correct += 1
        samples.append({"filename": rec["filename"], "observed_page_state": rec["observed_page_state"],
                        "gold": gold, "pred": pred["label"], "confidence": pred["confidence"],
                        "correct": pred["label"] == gold})

    per_class: dict[str, dict[str, float]] = {}
    for c in model["classes"]:
        tp = confusion[c][c]
        fn = sum(confusion[c][o] for o in confusion[c] if o != c)
        fp = sum(confusion[g][c] for g in confusion if g != c)
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        per_class[c] = {
            "precision": round(precision, 3) if precision is not None else None,
            "recall": round(recall, 3) if recall is not None else None,
            "support": sum(confusion[c].values()),
        }
    accuracy = correct / len(records) if records else None
    return {
        "eval_count": len(records),
        "accuracy": round(accuracy, 4) if accuracy is not None else None,
        "per_class": per_class,
        "confusion": {g: dict(confusion[g]) for g in confusion},
        "samples": samples,
    }


def train_stage_observer(artifacts_root: Path, *, captures: list[Any],
                         stage_by_state: dict[str, str]) -> dict[str, Any]:
    """Build the dataset, train NB, evaluate on the held-out split, persist artifacts."""
    manifest = build_stage_dataset(artifacts_root, captures=captures, stage_by_state=stage_by_state)
    records = [json.loads(line) for line in Path(manifest["path"]).read_text().splitlines() if line.strip()]
    if len(records) < 2 or len(manifest["label_counts"]) < 2:
        return {"ok": False, "reason": "insufficient_labeled_data", **manifest}

    train_records = [r for r in records if r["split"] == "train"]
    eval_records = [r for r in records if r["split"] == "eval"]
    if not train_records:
        train_records = records
    # Train on the train split; if there is no held-out eval, fall back to train (small data).
    model = _train_nb(train_records)
    evaluation = _evaluate(model, eval_records or train_records)

    model_dir = artifacts_root / "models" / f"{utcnow_iso().replace(':', '-')}__{MODEL_TYPE}"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model.json").write_text(json.dumps({**model, "dataset_id": manifest["dataset_id"],
                                                      "created_at": utcnow_iso()}, indent=2), encoding="utf-8")
    metrics = {
        "train_count": len(train_records),
        "eval_count": evaluation["eval_count"],
        "eval_on": "held_out" if eval_records else "train_fallback",
        "accuracy": evaluation["accuracy"],
        "per_class": evaluation["per_class"],
        "confusion": evaluation["confusion"],
        "label_counts": manifest["label_counts"],
        "vocab_size": model["vocab_size"],
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
