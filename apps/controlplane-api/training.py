from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# Legacy structural dataset — kept for backwards compatibility with existing reviews
DATASET_VERSION = "grounding_v1"

# Vision grounding dataset — the primary target format going forward
VISION_DATASET_VERSION = "grounding_vision_v1"

# The three models this platform is building toward.
# Ordered by the training pipeline dependency: grounding feeds state classification,
# both feed task outcome determination.
TRAINING_TARGETS = [
    {
        "target_id": "vision_element_grounding",
        "label": "Vision element grounding",
        "description": (
            "Given a screenshot and a natural-language element query "
            "(e.g. 'click the Apply Now button'), predict the bounding box "
            "of the target element. Primary model — feeds all downstream tasks."
        ),
        "stage": "primary",
        "inputs": ["screenshot", "element_query"],
        "output": "bbox",
        "requires": ["element_query on scenario", "approved_bbox on capture"],
    },
    {
        "target_id": "page_state_classifier",
        "label": "Page state classifier",
        "description": (
            "Given a screenshot and domain context, identify which named page state "
            "is shown (e.g. 'search_results', 'login_wall'). Enables the agent to "
            "know where it is before deciding what to do."
        ),
        "stage": "primary",
        "inputs": ["screenshot", "domain_id"],
        "output": "page_state_id",
        "requires": ["observed_page_state annotation on capture"],
    },
    {
        "target_id": "state_transition",
        "label": "State transition model",
        "description": (
            "Given a (page_state_before, action, element_grounding) triple, predict "
            "the resulting page state. Enables look-ahead planning and task-progress "
            "estimation without executing the action."
        ),
        "stage": "secondary",
        "inputs": ["page_state_before", "element_query", "approved_bbox"],
        "output": "page_state_after",
        "requires": ["observed_page_state + post_action_state annotation on capture"],
    },
    {
        "target_id": "task_outcome",
        "label": "Task outcome classifier",
        "description": (
            "Given a sequence of (state, action) pairs from a training session, "
            "classify whether the overall task succeeded, failed, or is in progress. "
            "Built on top of vision grounding and state classification."
        ),
        "stage": "tertiary",
        "inputs": ["session_trace"],
        "output": "outcome_label",
        "requires": ["full session traces with state and action annotations"],
    },
]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_meta(traces_dir: Path, filename: str) -> dict[str, Any]:
    meta_path = traces_dir / f"{filename}.meta.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text())
    except Exception:
        return {}


def write_meta(traces_dir: Path, filename: str, meta: dict[str, Any]) -> None:
    meta_path = traces_dir / f"{filename}.meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def merge_training_annotation(existing: Optional[dict[str, Any]], patch: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if patch is None:
        return existing

    merged = dict(existing or {})
    merged.update({k: v for k, v in patch.items() if v is not None})

    labels = merged.get("candidate_labels") or {}
    labels = {str(k): str(v) for k, v in labels.items() if v in {"approve", "reject"}}
    positive_ids = [candidate_id for candidate_id, label in labels.items() if label == "approve"]
    positive_candidate_id = positive_ids[0] if positive_ids else merged.get("positive_candidate_id")
    if positive_candidate_id:
        labels = {
            candidate_id: ("approve" if candidate_id == positive_candidate_id else label)
            for candidate_id, label in labels.items()
            if candidate_id == positive_candidate_id or label == "reject"
        }
    rejected_ids = sorted(
        set(merged.get("rejected_candidate_ids") or []).union(
            candidate_id for candidate_id, label in labels.items() if label == "reject"
        )
    )
    rejected_ids = [candidate_id for candidate_id in rejected_ids if candidate_id != positive_candidate_id]

    bbox = merged.get("approved_bbox")
    if isinstance(bbox, dict):
        bbox = {
            "x": float(bbox.get("x", 0)),
            "y": float(bbox.get("y", 0)),
            "width": float(bbox.get("width", 0)),
            "height": float(bbox.get("height", 0)),
        }
    else:
        bbox = None

    review_status = "reviewed" if positive_candidate_id else "draft"

    return {
        "version": DATASET_VERSION,
        "review_status": review_status,
        "domain_id": str(merged.get("domain_id", "") or ""),
        "goal_id": str(merged.get("goal_id", "") or ""),
        "task_id": str(merged.get("task_id", "") or "") or None,
        "action_type_hint": str(merged.get("action_type_hint", "any") or "any"),
        "capture_profile": str(merged.get("capture_profile", "viewport") or "viewport"),
        "notes": merged.get("notes"),
        "browser_session_id": str(merged.get("browser_session_id", "") or ""),
        "positive_candidate_id": positive_candidate_id,
        "rejected_candidate_ids": rejected_ids,
        "candidate_labels": labels,
        "approved_bbox": bbox,
        "reviewed_at": utcnow_iso() if positive_candidate_id else merged.get("reviewed_at"),
        "updated_at": utcnow_iso(),
    }


def build_grounding_dataset(artifacts_root: Path, *, captures: list[Any]) -> dict[str, Any]:
    datasets_dir = artifacts_root / "datasets"
    datasets_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    included = 0
    skipped = 0

    for capture in captures:
        trace_path = artifacts_root / "observer-traces" / capture.artifact_filename
        if not trace_path.exists():
            skipped += 1
            continue
        try:
            artifact = json.loads(trace_path.read_text())
        except Exception:
            skipped += 1
            continue

        record = _build_dataset_record(capture, artifact)
        if record is None:
            skipped += 1
            continue

        records.append(record)
        included += 1

    dataset_id = f"{utcnow_iso().replace(':', '-')}__{DATASET_VERSION}"
    dataset_dir = datasets_dir / dataset_id
    dataset_dir.mkdir(parents=True, exist_ok=True)

    dataset_path = dataset_dir / "grounding_dataset.jsonl"
    with dataset_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")

    manifest = {
        "dataset_id": dataset_id,
        "version": DATASET_VERSION,
        "created_at": utcnow_iso(),
        "record_count": len(records),
        "included_records": included,
        "skipped_records": skipped,
        "path": str(dataset_path),
        "scenario_counts": dict(sorted(Counter(record["training_context"].get("scenario_id") or "unknown" for record in records).items())),
        "review_status_filter": ["reviewed", "approved"],
    }
    (dataset_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def build_vision_dataset(artifacts_root: Path, *, captures: list[Any]) -> dict[str, Any]:
    """Build a vision-grounding dataset for end-to-end vision model training.

    Each JSONL record contains:
      - query          : natural-language element description (from scenario.element_query)
      - screenshot     : {path, width, height, shot_type}
      - bbox           : approved ground-truth bounding box {x, y, width, height}
      - bbox_normalized: bbox values divided by screenshot dimensions (0-1 range for model input)
      - page_state     : observed_page_state annotation (if set by annotator)
      - post_action_state: state the agent lands on after this interaction (if annotated)
      - context        : {domain_id, scenario_id, goal_id, task_id, action_type_hint, difficulty}
      - split          : "train" | "eval"

    Only captures that have BOTH element_query AND approved_bbox are included —
    those are the minimum requirements for vision model supervision.
    """
    datasets_dir = artifacts_root / "datasets"
    datasets_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    included = skipped_no_query = skipped_no_bbox = skipped_no_artifact = 0

    for capture in captures:
        # Skip captures without the vision training requirements
        if not capture.element_query:
            skipped_no_query += 1
            continue
        if not capture.approved_bbox:
            skipped_no_bbox += 1
            continue

        trace_path = artifacts_root / "observer-traces" / capture.artifact_filename
        if not trace_path.exists():
            skipped_no_artifact += 1
            continue

        try:
            artifact = json.loads(trace_path.read_text())
        except Exception:
            skipped_no_artifact += 1
            continue

        record = _build_vision_record(capture, artifact)
        if record is not None:
            records.append(record)
            included += 1

    dataset_id = f"{utcnow_iso().replace(':', '-')}__{VISION_DATASET_VERSION}"
    dataset_dir = datasets_dir / dataset_id
    dataset_dir.mkdir(parents=True, exist_ok=True)

    dataset_path = dataset_dir / "vision_grounding_dataset.jsonl"
    with dataset_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")

    scenario_counts = dict(sorted(Counter(
        r["context"].get("scenario_id") or "unknown" for r in records
    ).items()))

    state_annotation_count = sum(1 for r in records if r.get("page_state"))
    transition_annotation_count = sum(1 for r in records if r.get("post_action_state"))

    manifest = {
        "dataset_id": dataset_id,
        "version": VISION_DATASET_VERSION,
        "created_at": utcnow_iso(),
        "record_count": len(records),
        "included": included,
        "skipped_no_query": skipped_no_query,
        "skipped_no_bbox": skipped_no_bbox,
        "skipped_no_artifact": skipped_no_artifact,
        "state_annotation_coverage": f"{state_annotation_count}/{len(records)}",
        "transition_annotation_coverage": f"{transition_annotation_count}/{len(records)}",
        "path": str(dataset_path),
        "scenario_counts": scenario_counts,
        "training_target": "vision_element_grounding",
        "review_status_filter": ["reviewed", "approved"],
    }
    (dataset_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _build_vision_record(capture: Any, artifact: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Build a single vision training record from a reviewed capture."""
    acquisition = artifact.get("acquisition") or {}
    screenshots = acquisition.get("screenshots") or []
    screenshot = (capture.screenshot_refs or screenshots or [{}])[0]

    bbox = capture.approved_bbox
    if not isinstance(bbox, dict):
        return None

    # Normalize bbox to [0, 1] range relative to screenshot dimensions
    sw = screenshot.get("width") or capture.viewport_width or 0
    sh = screenshot.get("height") or capture.viewport_height or 0
    bbox_normalized: Optional[dict[str, float]] = None
    if sw and sh:
        bbox_normalized = {
            "x": round(float(bbox.get("x", 0)) / sw, 6),
            "y": round(float(bbox.get("y", 0)) / sh, 6),
            "w": round(float(bbox.get("width", 0)) / sw, 6),
            "h": round(float(bbox.get("height", 0)) / sh, 6),
        }

    return {
        "version": VISION_DATASET_VERSION,
        "artifact_filename": capture.artifact_filename,
        "timestamp": capture.captured_at.isoformat() if hasattr(capture.captured_at, "isoformat") else str(capture.captured_at),
        # The natural-language prompt the vision model receives at inference
        "query": capture.element_query,
        "screenshot": {
            "path": screenshot.get("path") or screenshot.get("image_path"),
            "width": sw or None,
            "height": sh or None,
            "shot_type": screenshot.get("shot_type", "viewport"),
        },
        # Ground truth
        "bbox": {
            "x": float(bbox.get("x", 0)),
            "y": float(bbox.get("y", 0)),
            "width": float(bbox.get("width", 0)),
            "height": float(bbox.get("height", 0)),
        },
        "bbox_normalized": bbox_normalized,
        # State annotation (set by human annotator during review)
        "page_state": capture.observed_page_state,
        "post_action_state": capture.post_action_state,
        # Training context for stratification and filtering
        "context": {
            "domain_id": capture.domain_id,
            "scenario_id": capture.scenario_id,
            "goal_id": capture.goal_id,
            "task_id": capture.task_id,
            "action_type_hint": capture.action_type_hint,
            "capture_profile": capture.capture_profile,
            "url": capture.url,
            "title": capture.title,
            "viewport": {
                "width": capture.viewport_width,
                "height": capture.viewport_height,
                "device_scale_factor": capture.device_scale_factor,
                "scroll_x": capture.scroll_x,
                "scroll_y": capture.scroll_y,
            },
        },
        "split": _stable_split(capture.artifact_filename),
        "review_status": capture.review_status,
    }


def train_grounding_model(artifacts_root: Path, *, dataset_manifest: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    manifest = dataset_manifest or build_grounding_dataset(artifacts_root, captures=[])
    dataset_path = Path(manifest["path"])
    records = [json.loads(line) for line in dataset_path.read_text().splitlines() if line.strip()]

    if not records:
        return {
            "ok": False,
            "reason": "no_reviewed_records",
            "dataset_id": manifest["dataset_id"],
            "record_count": 0,
        }

    train_records = [record for record in records if record["split"] == "train"]
    eval_records = [record for record in records if record["split"] == "eval"]
    if not train_records:
        train_records = records
        eval_records = []

    weights = train_weight_map(train_records)
    evaluation_records = eval_records or train_records
    predictions = [predict_record(record, weights) for record in evaluation_records]
    accuracy = sum(1 for prediction in predictions if prediction["correct_target"]) / len(predictions)
    mean_iou = sum(prediction["bbox_iou"] for prediction in predictions) / len(predictions)
    per_scenario = evaluate_predictions_by_scenario(evaluation_records, predictions)

    model_dir = artifacts_root / "models" / f"{utcnow_iso().replace(':', '-')}__grounding_linear_v1"
    model_dir.mkdir(parents=True, exist_ok=True)

    model_artifact = {
        "model_type": "grounding_linear_v1",
        "created_at": utcnow_iso(),
        "dataset_id": manifest["dataset_id"],
        "weights": weights,
    }
    metrics = {
        "train_count": len(train_records),
        "eval_count": len(evaluation_records),
        "target_accuracy": round(accuracy, 4),
        "mean_bbox_iou": round(mean_iou, 4),
        "mean_candidate_rank": round(sum(prediction["target_rank"] for prediction in predictions) / len(predictions), 2),
        "per_scenario": per_scenario,
    }

    (model_dir / "model.json").write_text(json.dumps(model_artifact, indent=2), encoding="utf-8")
    (model_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (model_dir / "prediction_samples.json").write_text(json.dumps(predictions[:25], indent=2), encoding="utf-8")

    return {
        "ok": True,
        "dataset_id": manifest["dataset_id"],
        "model_dir": str(model_dir),
        "metrics": metrics,
    }


def _build_dataset_record(capture: Any, artifact: dict[str, Any]) -> Optional[dict[str, Any]]:
    candidates = artifact.get("ranked_candidates") or []
    positive_candidate_id = capture.positive_candidate_id
    positive_candidate = next((candidate for candidate in candidates if candidate.get("candidate_id") == positive_candidate_id), None)
    if positive_candidate is None:
        return None

    approved_bbox = capture.approved_bbox or _candidate_bbox(positive_candidate, artifact.get("acquisition") or {})
    if approved_bbox is None:
        return None

    acquisition = artifact.get("acquisition") or {}
    metadata = artifact.get("metadata") or {}
    training_metadata = acquisition.get("training_metadata") or {}
    screenshot = (capture.screenshot_refs or acquisition.get("screenshots") or [{}])[0]
    scenario_id = getattr(capture, "scenario_id", None) or training_metadata.get("scenario_id") or metadata.get("scenario")
    page_state = training_metadata.get("page_state") or metadata.get("page_state") or metadata.get("scenario")

    return {
        "version": DATASET_VERSION,
        "artifact_filename": capture.artifact_filename,
        "timestamp": capture.captured_at.isoformat() if hasattr(capture.captured_at, "isoformat") else str(capture.captured_at),
        "page": {"url": capture.url, "title": capture.title},
        "training_context": {
            "domain_id": capture.domain_id,
            "scenario_id": scenario_id,
            "goal_id": capture.goal_id,
            "task_id": capture.task_id,
            "browser_session_id": capture.browser_session_id,
            "action_type_hint": capture.action_type_hint,
            "capture_profile": capture.capture_profile,
            "notes": capture.notes,
            "page_state": page_state,
            "viewport": {
                "width": capture.viewport_width,
                "height": capture.viewport_height,
                "device_scale_factor": capture.device_scale_factor,
                "scroll_x": capture.scroll_x,
                "scroll_y": capture.scroll_y,
                "tab_id": capture.tab_id,
            },
        },
        "screenshot": {
            "path": screenshot.get("path") or screenshot.get("image_path"),
            "width": screenshot.get("width"),
            "height": screenshot.get("height"),
            "shot_type": screenshot.get("shot_type", "viewport"),
        },
        "candidate_snapshot": [
            {
                "candidate_id": candidate.get("candidate_id"),
                "action_type": candidate.get("action_type"),
                "target": candidate.get("target"),
                "score": candidate.get("score"),
                "confidence": candidate.get("confidence"),
                "bbox": _candidate_bbox(candidate, acquisition),
            }
            for candidate in candidates
        ],
        "approved_target_candidate_id": positive_candidate_id,
        "approved_bbox": approved_bbox,
        "rejected_candidate_ids": capture.rejected_candidate_ids or [],
        "split": _stable_split(capture.artifact_filename),
        "review_status": capture.review_status,
    }


def _candidate_bbox(candidate: dict[str, Any], acquisition: dict[str, Any]) -> Optional[dict[str, float]]:
    bbox = ((candidate.get("grounding") or {}).get("bbox") if isinstance(candidate.get("grounding"), dict) else None)
    if bbox is None:
        for element in acquisition.get("actionable_elements", []):
            if element.get("uid") == candidate.get("element_id"):
                bbox = element.get("rect")
                break
    if not isinstance(bbox, dict):
        return None
    return {
        "x": float(bbox.get("x", 0)),
        "y": float(bbox.get("y", 0)),
        "width": float(bbox.get("width", 0)),
        "height": float(bbox.get("height", 0)),
    }


def _stable_split(filename: str) -> str:
    digest = hashlib.sha256(filename.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 10
    return "eval" if bucket < 2 else "train"


def train_weight_map(records: list[dict[str, Any]]) -> dict[str, float]:
    weights: Counter[str] = Counter()
    for record in records:
        positive_id = record["approved_target_candidate_id"]
        for candidate in record["candidate_snapshot"]:
            features = candidate_features(candidate, record["training_context"])
            direction = 1.0 if candidate["candidate_id"] == positive_id else -0.35
            for feature in features:
                weights[feature] += direction
    return {feature: round(weight, 4) for feature, weight in weights.items() if abs(weight) >= 0.2}


def candidate_features(candidate: dict[str, Any], training_context: dict[str, Any]) -> list[str]:
    features = [
        f"action:{candidate.get('action_type') or 'unknown'}",
        f"hint:{training_context.get('action_type_hint') or 'any'}",
        f"domain:{training_context.get('domain_id') or 'unknown'}",
        f"goal:{training_context.get('goal_id') or 'unknown'}",
    ]
    if training_context.get("task_id"):
        features.append(f"task:{training_context['task_id']}")
    if training_context.get("capture_profile"):
        features.append(f"profile:{training_context['capture_profile']}")

    target = candidate.get("target") or {}
    for key in ("tag", "role"):
        value = str(target.get(key, "") or "").strip().lower()
        if value:
            features.append(f"{key}:{value}")

    text_blob = " ".join(
        filter(
            None,
            [
                str(target.get("label", "") or ""),
                str(training_context.get("goal_id", "") or ""),
                str(training_context.get("task_id", "") or ""),
                str(training_context.get("domain_id", "") or ""),
            ],
        )
    ).lower()
    for token in _tokens(text_blob):
        features.append(f"tok:{token}")

    bbox = candidate.get("bbox") or {}
    if isinstance(bbox, dict):
        width = float(bbox.get("width", 0))
        height = float(bbox.get("height", 0))
        area = width * height
        if area > 0:
            if area < 1200:
                features.append("bbox:small")
            elif area < 10000:
                features.append("bbox:medium")
            else:
                features.append("bbox:large")
    return features


def predict_record(record: dict[str, Any], weights: dict[str, float]) -> dict[str, Any]:
    scored_candidates = []
    for candidate in record["candidate_snapshot"]:
        score = sum(weights.get(feature, 0.0) for feature in candidate_features(candidate, record["training_context"]))
        scored_candidates.append((score, candidate))
    scored_candidates.sort(key=lambda item: item[0], reverse=True)
    predicted_candidate = scored_candidates[0][1]
    predicted_bbox = predicted_candidate.get("bbox")
    target_bbox = record["approved_bbox"]
    target_candidate_id = record["approved_target_candidate_id"]
    target_rank = next(
        (index + 1 for index, (_, candidate) in enumerate(scored_candidates) if candidate["candidate_id"] == target_candidate_id),
        len(scored_candidates) + 1,
    )

    return {
        "artifact_filename": record["artifact_filename"],
        "scenario_id": record["training_context"].get("scenario_id") or "unknown",
        "predicted_candidate_id": predicted_candidate["candidate_id"],
        "target_candidate_id": target_candidate_id,
        "correct_target": predicted_candidate["candidate_id"] == target_candidate_id,
        "target_rank": target_rank,
        "bbox_iou": round(_bbox_iou(predicted_bbox, target_bbox), 4),
    }


def evaluate_predictions_by_scenario(records: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for prediction in predictions:
        grouped[prediction.get("scenario_id") or "unknown"].append(prediction)

    records_by_scenario = Counter(record["training_context"].get("scenario_id") or "unknown" for record in records)
    result: dict[str, dict[str, Any]] = {}
    for scenario_id, scenario_predictions in sorted(grouped.items()):
        count = len(scenario_predictions)
        result[scenario_id] = {
            "record_count": records_by_scenario[scenario_id],
            "grounding_accuracy": round(sum(1 for item in scenario_predictions if item["correct_target"]) / count, 4),
            "mean_bbox_iou": round(sum(item["bbox_iou"] for item in scenario_predictions) / count, 4),
            "mean_candidate_rank": round(sum(item["target_rank"] for item in scenario_predictions) / count, 2),
        }
    return result


def compare_training_targets(artifacts_root: Path, *, captures: list[Any]) -> dict[str, Any]:
    scenario_counts: Counter[str] = Counter()
    reviewed_count = 0
    labeled_count = 0
    total_candidates = 0
    rejected_count = 0
    missing_artifacts = 0
    page_states: Counter[str] = Counter()

    for capture in captures:
        trace_path = artifacts_root / "observer-traces" / capture.artifact_filename
        artifact: dict[str, Any] = {}
        if trace_path.exists():
            try:
                artifact = json.loads(trace_path.read_text())
            except Exception:
                artifact = {}
        else:
            missing_artifacts += 1

        acquisition = artifact.get("acquisition") or {}
        metadata = artifact.get("metadata") or {}
        training_metadata = acquisition.get("training_metadata") or {}
        scenario_id = getattr(capture, "scenario_id", None) or training_metadata.get("scenario_id") or metadata.get("scenario") or "unknown"
        page_state = training_metadata.get("page_state") or metadata.get("page_state") or metadata.get("scenario") or "unknown"

        scenario_counts[scenario_id] += 1
        page_states[page_state] += 1
        total_candidates += int(getattr(capture, "candidate_count", 0) or len(artifact.get("ranked_candidates") or []))
        rejected_count += len(getattr(capture, "rejected_candidate_ids", []) or [])
        if getattr(capture, "review_status", None) in {"reviewed", "approved"}:
            reviewed_count += 1
        if getattr(capture, "positive_candidate_id", None):
            labeled_count += 1

    target_rows = []
    for target in TRAINING_TARGETS:
        score_values = target["scores"]
        weighted_score = round(sum(score_values.values()) / len(score_values), 2)
        readiness = _target_readiness(target["target_id"], captures, reviewed_count, labeled_count, len(scenario_counts))
        target_rows.append({
            **target,
            "weighted_score": weighted_score,
            "readiness": readiness,
        })

    target_rows.sort(key=lambda row: (row["readiness"]["ready"], row["weighted_score"]), reverse=True)

    return {
        "recommended_target": "element_grounding",
        "recommendation_reason": "Element grounding has the clearest direct payoff for browser automation and can be evaluated from the same reviewed capture labels.",
        "capture_summary": {
            "capture_count": len(captures),
            "reviewed_count": reviewed_count,
            "labeled_count": labeled_count,
            "scenario_count": len(scenario_counts),
            "total_candidates": total_candidates,
            "rejected_candidate_count": rejected_count,
            "missing_artifact_count": missing_artifacts,
            "scenario_counts": dict(sorted(scenario_counts.items())),
            "page_state_counts": dict(sorted(page_states.items())),
        },
        "targets": target_rows,
    }


def _target_readiness(target_id: str, captures: list[Any], reviewed_count: int, labeled_count: int, scenario_count: int) -> dict[str, Any]:
    if target_id == "element_grounding":
        ready = labeled_count > 0
        blocker = None if ready else "Needs reviewed captures with an approved candidate."
    elif target_id == "action_classification":
        ready = reviewed_count > 0
        blocker = None if ready else "Needs reviewed captures with action hints from scenario goals."
    else:
        ready = scenario_count > 1 and len(captures) > 1
        blocker = None if ready else "Needs captures spread across multiple scenarios or page states."
    return {"ready": ready, "blocker": blocker}


def _bbox_iou(predicted: Optional[dict], target: Optional[dict]) -> float:
    if not isinstance(predicted, dict) or not isinstance(target, dict):
        return 0.0
    pred_x2 = predicted["x"] + predicted["width"]
    pred_y2 = predicted["y"] + predicted["height"]
    target_x2 = target["x"] + target["width"]
    target_y2 = target["y"] + target["height"]

    inter_x1 = max(predicted["x"], target["x"])
    inter_y1 = max(predicted["y"], target["y"])
    inter_x2 = min(pred_x2, target_x2)
    inter_y2 = min(pred_y2, target_y2)
    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    intersection = inter_w * inter_h

    pred_area = predicted["width"] * predicted["height"]
    target_area = target["width"] * target["height"]
    union = pred_area + target_area - intersection
    return 0.0 if union <= 0 else intersection / union


def _tokens(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", text.lower()) if token][:24]
