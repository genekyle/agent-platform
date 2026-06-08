"""Provider-agnostic eval runner. The model changes; this contract does not.

Adding a new model means: register the row (`{target_id}__{impl}`), add an
entry to `IMPLEMENTATIONS`, and ship a wrapper module. No core changes.
"""
from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import ModelEvalRun, ModelRegistry, TrainingCapture
from training import _bbox_iou, _stable_split

import httpx

from model_lib import v0_florence
from model_lib.query_normalize import normalize_descriptive, normalize_element_query
from settings import settings

logger = logging.getLogger("controlplane.model_lib.eval")

REVIEWED_STATUSES = {"reviewed", "approved"}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _eval_dir(artifacts_root: Path, model_id: str, started_iso: str) -> Path:
    stamp = started_iso.replace(":", "-")
    out = artifacts_root / "models" / model_id / "eval_runs" / stamp
    out.mkdir(parents=True, exist_ok=True)
    return out


def _bbox_center(bbox: dict[str, float]) -> tuple[float, float]:
    return (bbox["x"] + bbox["width"] / 2.0, bbox["y"] + bbox["height"] / 2.0)


def _point_in_bbox(px: float, py: float, bbox: dict[str, float]) -> bool:
    return (
        bbox["x"] <= px <= bbox["x"] + bbox["width"]
        and bbox["y"] <= py <= bbox["y"] + bbox["height"]
    )


def _resolve_screenshot(capture: TrainingCapture, artifacts_root: Path) -> Optional[Path]:
    """Pick the viewport screenshot for this capture from screenshot_refs."""
    refs = capture.screenshot_refs or []
    if not refs:
        return None
    # Prefer viewport; fall back to first ref.
    viewport = next((r for r in refs if r.get("shot_type") == "viewport"), refs[0])
    raw = viewport.get("path") or viewport.get("image_path")
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = artifacts_root / "observer-screenshots" / p.name
    return p if p.exists() else None


def _eligible_captures(db: Session) -> list[TrainingCapture]:
    stmt = (
        select(TrainingCapture)
        .where(TrainingCapture.review_status.in_(REVIEWED_STATUSES))
        .where(TrainingCapture.element_query.is_not(None))
        .where(TrainingCapture.approved_bbox.is_not(None))
        .order_by(TrainingCapture.captured_at.asc())
    )
    return list(db.scalars(stmt).all())


def _aggregate(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    if not predictions:
        return {
            "record_count": 0,
            "mean_bbox_iou": 0.0,
            "iou_at_50_accuracy": 0.0,
            "center_in_target_accuracy": 0.0,
            "mean_latency_ms": 0,
            "per_scenario": {},
        }
    n = len(predictions)
    mean_iou = sum(p["bbox_iou"] for p in predictions) / n
    iou50 = sum(1 for p in predictions if p["bbox_iou"] >= 0.5) / n
    center_acc = sum(1 for p in predictions if p["center_in_target"]) / n
    mean_lat = int(sum(p["latency_ms"] for p in predictions) / n)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in predictions:
        grouped[p.get("scenario_id") or "unknown"].append(p)

    per_scenario: dict[str, dict[str, Any]] = {}
    for sid, items in sorted(grouped.items()):
        k = len(items)
        per_scenario[sid] = {
            "record_count": k,
            "mean_bbox_iou": round(sum(i["bbox_iou"] for i in items) / k, 4),
            "iou_at_50_accuracy": round(sum(1 for i in items if i["bbox_iou"] >= 0.5) / k, 4),
            "center_in_target_accuracy": round(sum(1 for i in items if i["center_in_target"]) / k, 4),
        }

    return {
        "record_count": n,
        "mean_bbox_iou": round(mean_iou, 4),
        "iou_at_50_accuracy": round(iou50, 4),
        "center_in_target_accuracy": round(center_acc, 4),
        "mean_latency_ms": mean_lat,
        "per_scenario": per_scenario,
    }


def _run_florence(
    *,
    db: Session,
    artifacts_root: Path,
    model: ModelRegistry,
    normalize_query: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Shared Florence-2 zero-shot loop. `normalize_query` toggles the only
    difference between the two registered implementations — input preprocessing.

    Returns (metrics, predictions, log_lines).
    """
    log: list[str] = []
    log.append(
        f"[{_iso_now()}] eval start model_id={model.id} normalize_query={normalize_query}"
    )

    captures = _eligible_captures(db)
    eval_captures = [c for c in captures if _stable_split(c.artifact_filename) == "eval"]
    log.append(
        f"[{_iso_now()}] eligible={len(captures)} eval_split={len(eval_captures)}"
    )

    handle = v0_florence.load_florence(model.model_name or v0_florence.FLORENCE_BASE)
    log.append(f"[{_iso_now()}] florence loaded on device={handle.device}")

    predictions: list[dict[str, Any]] = []
    for capture in eval_captures:
        screenshot = _resolve_screenshot(capture, artifacts_root)
        if screenshot is None:
            log.append(f"[skip] {capture.artifact_filename} — screenshot missing")
            continue

        original_query = capture.element_query or ""
        sent_query = normalize_element_query(original_query) if normalize_query else original_query

        result = v0_florence.predict_bbox(
            handle=handle,
            screenshot_path=str(screenshot),
            element_query=sent_query,
        )
        predicted = result["bbox"]
        target = capture.approved_bbox
        iou = _bbox_iou(predicted, target) if predicted else 0.0
        center_in = False
        if predicted and target:
            cx, cy = _bbox_center(predicted)
            center_in = _point_in_bbox(cx, cy, target)

        # Read screenshot dims from the capture refs so the UI can scale the overlay
        # without an extra HTTP roundtrip per image.
        viewport_ref = next(
            (r for r in (capture.screenshot_refs or []) if r.get("shot_type") == "viewport"),
            (capture.screenshot_refs or [{}])[0] if capture.screenshot_refs else {},
        )

        predictions.append({
            "artifact_filename": capture.artifact_filename,
            "screenshot_filename": screenshot.name,
            "screenshot_width": viewport_ref.get("width"),
            "screenshot_height": viewport_ref.get("height"),
            "scenario_id": capture.scenario_id,
            "domain_id": capture.domain_id,
            "goal_id": capture.goal_id,
            "observed_page_state": capture.observed_page_state,
            "element_query": original_query,
            "sent_query": sent_query,
            "predicted_bbox": predicted,
            "approved_bbox": target,
            "bbox_iou": round(iou, 4),
            "center_in_target": center_in,
            "latency_ms": result["latency_ms"],
            "raw_response": result.get("raw_response"),
        })

    metrics = _aggregate(predictions)
    log.append(
        f"[{_iso_now()}] done n={metrics['record_count']} "
        f"mean_iou={metrics['mean_bbox_iou']} iou@50={metrics['iou_at_50_accuracy']} "
        f"center_in={metrics['center_in_target_accuracy']}"
    )
    return metrics, predictions, log


def run_eval_florence_zero_shot(*, db, artifacts_root, model):
    return _run_florence(db=db, artifacts_root=artifacts_root, model=model, normalize_query=False)


def run_eval_florence_zero_shot_normalized(*, db, artifacts_root, model):
    return _run_florence(db=db, artifacts_root=artifacts_root, model=model, normalize_query=True)


def _run_florence_descriptive(
    *,
    db: Session,
    artifacts_root: Path,
    model: ModelRegistry,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Baseline B: short-form query + action-type element-type tag.

    Uses each capture's action_type_hint to enrich the noun phrase
    (e.g. "type" + "password" -> "password input field").
    """
    log: list[str] = [f"[{_iso_now()}] eval start model_id={model.id} (descriptive)"]
    captures = _eligible_captures(db)
    eval_captures = [c for c in captures if _stable_split(c.artifact_filename) == "eval"]
    log.append(f"[{_iso_now()}] eligible={len(captures)} eval_split={len(eval_captures)}")

    handle = v0_florence.load_florence(model.model_name or v0_florence.FLORENCE_BASE)
    log.append(f"[{_iso_now()}] florence loaded on device={handle.device}")

    predictions: list[dict[str, Any]] = []
    for capture in eval_captures:
        screenshot = _resolve_screenshot(capture, artifacts_root)
        if screenshot is None:
            log.append(f"[skip] {capture.artifact_filename} — screenshot missing")
            continue
        original = capture.element_query or ""
        sent = normalize_descriptive(original, capture.action_type_hint)
        result = v0_florence.predict_bbox(
            handle=handle, screenshot_path=str(screenshot), element_query=sent,
        )
        predicted = result["bbox"]
        target = capture.approved_bbox
        iou = _bbox_iou(predicted, target) if predicted else 0.0
        center_in = False
        if predicted and target:
            cx, cy = _bbox_center(predicted)
            center_in = _point_in_bbox(cx, cy, target)
        viewport_ref = next(
            (r for r in (capture.screenshot_refs or []) if r.get("shot_type") == "viewport"),
            (capture.screenshot_refs or [{}])[0] if capture.screenshot_refs else {},
        )
        predictions.append({
            "artifact_filename": capture.artifact_filename,
            "screenshot_filename": screenshot.name,
            "screenshot_width": viewport_ref.get("width"),
            "screenshot_height": viewport_ref.get("height"),
            "scenario_id": capture.scenario_id,
            "domain_id": capture.domain_id,
            "goal_id": capture.goal_id,
            "observed_page_state": capture.observed_page_state,
            "element_query": original,
            "sent_query": sent,
            "action_type_hint": capture.action_type_hint,
            "predicted_bbox": predicted,
            "approved_bbox": target,
            "bbox_iou": round(iou, 4),
            "center_in_target": center_in,
            "latency_ms": result["latency_ms"],
            "raw_response": result.get("raw_response"),
        })

    metrics = _aggregate(predictions)
    log.append(
        f"[{_iso_now()}] done n={metrics['record_count']} "
        f"mean_iou={metrics['mean_bbox_iou']} iou@50={metrics['iou_at_50_accuracy']} "
        f"center_in={metrics['center_in_target_accuracy']}"
    )
    return metrics, predictions, log


def _fetch_omniparser_proposals(screenshot_filename: str) -> list[dict[str, Any]]:
    """Ask mcp-mock for OmniParser proposals on this screenshot. Returns
    [{candidate_id, bbox, confidence, caption, ...}, ...]. Best-effort —
    returns [] on any HTTP failure so eval can still continue (logged below)."""
    try:
        with httpx.Client(timeout=120.0) as client:
            res = client.post(
                f"{settings.capture_server_url}/proposer/predict",
                json={"screenshot_filename": screenshot_filename},
            )
            res.raise_for_status()
            return res.json().get("proposals") or []
    except Exception:
        return []


def _run_omniparser_then_florence(
    *,
    db: Session,
    artifacts_root: Path,
    model: ModelRegistry,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Baseline E: two-stage detect→ground pipeline.

    1. OmniParser proposes ~10–30 candidate interactive regions (with confidences).
    2. Florence-2 runs on the FULL image with the (normalized) query to localize a
       rough bbox.
    3. Pick the OmniParser candidate that best overlaps Florence's rough bbox
       (highest IoU). If Florence returned nothing, fall back to the highest-
       confidence OmniParser candidate.

    Tests the hypothesis: "Florence can roughly find the right area but its
    boxes are sloppy. OmniParser knows precise interactive-element boundaries.
    Combining them should beat either alone."
    """
    log: list[str] = [f"[{_iso_now()}] eval start model_id={model.id} (omniparser+florence)"]
    captures = _eligible_captures(db)
    eval_captures = [c for c in captures if _stable_split(c.artifact_filename) == "eval"]
    log.append(f"[{_iso_now()}] eligible={len(captures)} eval_split={len(eval_captures)}")

    handle = v0_florence.load_florence(model.model_name or v0_florence.FLORENCE_BASE)
    log.append(f"[{_iso_now()}] florence loaded on device={handle.device}")

    predictions: list[dict[str, Any]] = []
    for capture in eval_captures:
        screenshot = _resolve_screenshot(capture, artifacts_root)
        if screenshot is None:
            log.append(f"[skip] {capture.artifact_filename} — screenshot missing")
            continue

        proposals = _fetch_omniparser_proposals(screenshot.name)
        original = capture.element_query or ""
        sent = normalize_element_query(original)
        florence_result = v0_florence.predict_bbox(
            handle=handle, screenshot_path=str(screenshot), element_query=sent,
        )
        florence_bbox = florence_result["bbox"]

        # Stage 2: snap to the OmniParser candidate that best overlaps Florence's box.
        snap_strategy = "florence_only"
        chosen_bbox = florence_bbox
        chosen_confidence: Optional[float] = None
        if proposals:
            if florence_bbox is not None:
                scored = sorted(
                    ((p, _bbox_iou(florence_bbox, p["bbox"])) for p in proposals),
                    key=lambda pair: pair[1], reverse=True,
                )
                best, best_iou = scored[0]
                if best_iou > 0:
                    chosen_bbox = best["bbox"]
                    chosen_confidence = best.get("confidence")
                    snap_strategy = f"snap_to_omniparser_iou={best_iou:.3f}"
            else:
                # Florence found nothing — fall back to OmniParser's most confident candidate.
                best = max(proposals, key=lambda p: p.get("confidence", 0))
                chosen_bbox = best["bbox"]
                chosen_confidence = best.get("confidence")
                snap_strategy = "omniparser_fallback_top_confidence"

        target = capture.approved_bbox
        iou = _bbox_iou(chosen_bbox, target) if chosen_bbox else 0.0
        center_in = False
        if chosen_bbox and target:
            cx, cy = _bbox_center(chosen_bbox)
            center_in = _point_in_bbox(cx, cy, target)

        viewport_ref = next(
            (r for r in (capture.screenshot_refs or []) if r.get("shot_type") == "viewport"),
            (capture.screenshot_refs or [{}])[0] if capture.screenshot_refs else {},
        )
        predictions.append({
            "artifact_filename": capture.artifact_filename,
            "screenshot_filename": screenshot.name,
            "screenshot_width": viewport_ref.get("width"),
            "screenshot_height": viewport_ref.get("height"),
            "scenario_id": capture.scenario_id,
            "domain_id": capture.domain_id,
            "goal_id": capture.goal_id,
            "observed_page_state": capture.observed_page_state,
            "element_query": original,
            "sent_query": sent,
            "predicted_bbox": chosen_bbox,
            "florence_bbox": florence_bbox,           # the rough localization
            "omniparser_proposal_count": len(proposals),
            "snap_strategy": snap_strategy,
            "predicted_confidence": chosen_confidence,
            "approved_bbox": target,
            "bbox_iou": round(iou, 4),
            "center_in_target": center_in,
            "latency_ms": florence_result["latency_ms"],
            "raw_response": florence_result.get("raw_response"),
        })

    metrics = _aggregate(predictions)
    log.append(
        f"[{_iso_now()}] done n={metrics['record_count']} "
        f"mean_iou={metrics['mean_bbox_iou']} iou@50={metrics['iou_at_50_accuracy']} "
        f"center_in={metrics['center_in_target_accuracy']}"
    )
    return metrics, predictions, log


# Dispatch table: the swap point. Two zero-shot baselines today —
# raw query vs. heuristically-normalized query. Same model, different input format.
IMPLEMENTATIONS: dict[str, Callable[..., tuple[dict[str, Any], list[dict[str, Any]], list[str]]]] = {
    "v0_zero_shot_florence2_base": run_eval_florence_zero_shot,
    "v0_zero_shot_florence2_base_short_query": run_eval_florence_zero_shot_normalized,
    "v0_zero_shot_florence2_base_descriptive_query": _run_florence_descriptive,
    "v0_two_stage_omniparser_then_florence": _run_omniparser_then_florence,
}


def run_eval(
    *,
    db: Session,
    artifacts_root: Path,
    model_id: str,
) -> ModelEvalRun:
    model = db.get(ModelRegistry, model_id)
    if model is None:
        raise ValueError(f"model not registered: {model_id}")
    runner = IMPLEMENTATIONS.get(model.implementation)
    if runner is None:
        raise ValueError(f"no implementation registered for {model.implementation}")

    run_id = uuid.uuid4().hex
    started_iso = _iso_now()
    run = ModelEvalRun(
        id=run_id,
        model_id=model.id,
        status="running",
        started_at=datetime.now(timezone.utc),
        record_count=0,
    )
    db.add(run)
    db.commit()

    try:
        metrics, predictions, log_lines = runner(
            db=db, artifacts_root=artifacts_root, model=model,
        )
    except Exception as exc:
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(run)
        logger.exception("eval failed for %s", model_id)
        return run

    out_dir = _eval_dir(artifacts_root, model.id, started_iso)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    with (out_dir / "predictions.jsonl").open("w", encoding="utf-8") as fh:
        for p in predictions:
            fh.write(json.dumps(p) + "\n")
    (out_dir / "run.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    run.status = "success"
    run.finished_at = datetime.now(timezone.utc)
    run.record_count = metrics["record_count"]
    run.metrics = metrics
    run.artifact_dir = str(out_dir)
    db.commit()
    db.refresh(run)
    return run


def read_predictions_sample(artifact_dir: Optional[str], *, limit: int = 25) -> list[dict[str, Any]]:
    if not artifact_dir:
        return []
    path = Path(artifact_dir) / "predictions.jsonl"
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
            if len(out) >= limit:
                break
    return out
