"""Provider-agnostic eval runner.

This module owns the durable contract:
  - Eligible captures: reviewed/approved with element_query + approved_bbox.
  - Eval split: stable-hash 20% holdout (training._stable_split). Same set every run.
  - Metrics: mean_bbox_iou / iou_at_50_accuracy / center_in_target_accuracy /
    mean_latency_ms, with per-scenario breakdown.
  - Storage: output/models/{model_id}/eval_runs/{iso}/metrics.json +
    predictions.jsonl + run.log.

Long-running evals are checkpointed: predictions are appended to disk one
line at a time, the DB row's `progress` column is updated after every
capture, and the runner checks `cancel_requested` between captures so the
UI can stop it cleanly. If the worker is killed mid-run (uvicorn reload,
system sleep), no work is lost — a `resume` re-runs only the missing
captures and merges with what was already on disk.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import SessionLocal
from models import ModelEvalRun, ModelRegistry, TrainingCapture
from training import _bbox_iou, _stable_split

from model_lib import v0_florence, v0_uground
from model_lib.query_normalize import normalize_descriptive, normalize_element_query
from settings import settings

logger = logging.getLogger("controlplane.model_lib.eval")

REVIEWED_STATUSES = {"reviewed", "approved"}

# How often to flush the DB row's progress JSON. Done every capture.
# (DB write is cheap; per-capture inference dwarfs it.)
_PROGRESS_FLUSH_EVERY = 1


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


def _viewport_ref(capture: TrainingCapture) -> dict[str, Any]:
    refs = capture.screenshot_refs or []
    return next((r for r in refs if r.get("shot_type") == "viewport"), refs[0] if refs else {})


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


# ---------------------------------------------------------------------------
# EvalRunContext: a tiny per-run helper that owns:
#   - the output directory + predictions.jsonl append handle
#   - the DB row + a function to flush progress
#   - the cancellation check
#
# All runner implementations call ctx.write(prediction) after each capture
# and check ctx.cancelled before starting the next one.
# ---------------------------------------------------------------------------

class EvalRunContext:
    def __init__(
        self,
        *,
        db: Session,
        run_id: str,
        model_id: str,
        artifacts_root: Path,
        out_dir: Path,
        existing_predictions: Optional[list[dict[str, Any]]] = None,
    ):
        self.db = db
        self.run_id = run_id
        self.model_id = model_id
        self.artifacts_root = artifacts_root
        self.out_dir = out_dir
        self.predictions_path = out_dir / "predictions.jsonl"
        self.log_path = out_dir / "run.log"
        self._predictions: list[dict[str, Any]] = list(existing_predictions or [])
        self._done_filenames: set[str] = {p["artifact_filename"] for p in self._predictions}
        # Append mode — we may be resuming.
        self._pred_fh = self.predictions_path.open("a", encoding="utf-8")
        self._log_fh = self.log_path.open("a", encoding="utf-8")
        self.total: int = 0
        self.started_iso = _iso_now()

    def already_done(self, capture: TrainingCapture) -> bool:
        return capture.artifact_filename in self._done_filenames

    def log(self, msg: str) -> None:
        line = f"[{_iso_now()}] {msg}\n"
        self._log_fh.write(line)
        self._log_fh.flush()
        logger.info("[%s] %s", self.run_id[:8], msg)

    def write_prediction(self, prediction: dict[str, Any]) -> None:
        self._predictions.append(prediction)
        self._done_filenames.add(prediction["artifact_filename"])
        self._pred_fh.write(json.dumps(prediction) + "\n")
        self._pred_fh.flush()

    @property
    def predictions(self) -> list[dict[str, Any]]:
        return self._predictions

    def update_progress(self, *, current_capture: Optional[str], current_step: str) -> None:
        """Refresh the DB row's progress JSON. Re-queries to pick up any external
        cancel flag changes from a separate request."""
        row = self.db.get(ModelEvalRun, self.run_id)
        if row is None:
            return
        row.progress = {
            "completed": len(self._predictions),
            "total": self.total,
            "current_capture": current_capture,
            "current_step": current_step,
            "started_at": self.started_iso,
            "last_update_at": _iso_now(),
        }
        # Snapshot mid-run metrics so the UI can show progressing numbers.
        row.metrics = _aggregate(self._predictions)
        row.record_count = len(self._predictions)
        self.db.commit()

    def is_cancelled(self) -> bool:
        """Check the DB row's cancel_requested flag — set by a separate HTTP call."""
        row = self.db.get(ModelEvalRun, self.run_id)
        if row is None:
            return False
        self.db.refresh(row)
        return bool(row.cancel_requested)

    def finalize(self, *, status: str, error: Optional[str] = None) -> ModelEvalRun:
        self._pred_fh.close()
        self._log_fh.close()
        metrics = _aggregate(self._predictions)
        (self.out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        row = self.db.get(ModelEvalRun, self.run_id)
        if row is not None:
            row.status = status
            row.error = error
            row.finished_at = datetime.now(timezone.utc)
            row.record_count = len(self._predictions)
            row.metrics = metrics
            row.artifact_dir = str(self.out_dir)
            row.progress = {
                **(row.progress or {}),
                "completed": len(self._predictions),
                "total": self.total,
                "current_step": status,
                "last_update_at": _iso_now(),
                "current_capture": None,
            }
            self.db.commit()
            self.db.refresh(row)
        return row


# ---------------------------------------------------------------------------
# Runner implementations. Each takes the ctx + model and returns nothing —
# results are written into ctx.predictions as work progresses.
# ---------------------------------------------------------------------------

def _florence_loop(*, ctx: EvalRunContext, model: ModelRegistry, normalize_query: bool, descriptive: bool) -> None:
    """Shared Florence-2 grounding loop. Three modes:
      - normalize_query=False, descriptive=False → raw query
      - normalize_query=True,  descriptive=False → short noun-phrase
      - normalize_query=False, descriptive=True  → noun-phrase + element-type tag
    """
    eval_captures = _select_eval_captures(ctx)
    ctx.log(f"florence loop: total={len(eval_captures)} normalize_query={normalize_query} descriptive={descriptive}")

    # MPS only has room for one ~multi-GB vision model at a time. Evict UGround
    # if it's cached from a prior run before loading Florence on top.
    if v0_uground.release_uground():
        ctx.log("evicted resident UGround handle to make room for Florence")
    handle = v0_florence.load_florence(model.model_name or v0_florence.FLORENCE_BASE)
    ctx.log(f"florence loaded on device={handle.device}")

    for i, capture in enumerate(eval_captures, start=1):
        if ctx.is_cancelled():
            ctx.log(f"cancel requested at capture {i}/{len(eval_captures)} — stopping")
            return
        if ctx.already_done(capture):
            continue
        ctx.update_progress(current_capture=capture.artifact_filename, current_step=f"florence inference ({i}/{len(eval_captures)})")
        screenshot = _resolve_screenshot(capture, ctx.artifacts_root)
        if screenshot is None:
            ctx.log(f"[skip] {capture.artifact_filename} — screenshot missing")
            continue
        original = capture.element_query or ""
        if descriptive:
            sent = normalize_descriptive(original, capture.action_type_hint)
        elif normalize_query:
            sent = normalize_element_query(original)
        else:
            sent = original
        result = v0_florence.predict_bbox(handle=handle, screenshot_path=str(screenshot), element_query=sent)
        _record_bbox_prediction(ctx, capture, screenshot, original, sent, result)
        ctx.update_progress(current_capture=capture.artifact_filename, current_step=f"persisted ({i}/{len(eval_captures)})")


def _select_eval_captures(ctx: EvalRunContext) -> list[TrainingCapture]:
    captures = _eligible_captures(ctx.db)
    eval_captures = [c for c in captures if _stable_split(c.artifact_filename) == "eval"]
    ctx.total = len(eval_captures)
    ctx.log(f"eligible={len(captures)} eval_split={len(eval_captures)}")
    return eval_captures


def _record_bbox_prediction(
    ctx: EvalRunContext,
    capture: TrainingCapture,
    screenshot: Path,
    original_query: str,
    sent_query: str,
    result: dict[str, Any],
) -> None:
    predicted = result.get("bbox")
    target = capture.approved_bbox
    iou = _bbox_iou(predicted, target) if predicted else 0.0
    center_in = False
    if predicted and target:
        cx, cy = _bbox_center(predicted)
        center_in = _point_in_bbox(cx, cy, target)
    vp = _viewport_ref(capture)
    ctx.write_prediction({
        "artifact_filename": capture.artifact_filename,
        "screenshot_filename": screenshot.name,
        "screenshot_width": vp.get("width"),
        "screenshot_height": vp.get("height"),
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
        "latency_ms": result.get("latency_ms", 0),
        "raw_response": result.get("raw_response"),
    })


def run_eval_florence_zero_shot(*, ctx: EvalRunContext, model: ModelRegistry) -> None:
    _florence_loop(ctx=ctx, model=model, normalize_query=False, descriptive=False)


def run_eval_florence_zero_shot_normalized(*, ctx: EvalRunContext, model: ModelRegistry) -> None:
    _florence_loop(ctx=ctx, model=model, normalize_query=True, descriptive=False)


def _run_florence_descriptive(*, ctx: EvalRunContext, model: ModelRegistry) -> None:
    _florence_loop(ctx=ctx, model=model, normalize_query=False, descriptive=True)


def _fetch_omniparser_proposals(screenshot_filename: str) -> list[dict[str, Any]]:
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


def _run_omniparser_then_florence(*, ctx: EvalRunContext, model: ModelRegistry) -> None:
    eval_captures = _select_eval_captures(ctx)
    if v0_uground.release_uground():
        ctx.log("evicted resident UGround handle to make room for Florence")
    handle = v0_florence.load_florence(model.model_name or v0_florence.FLORENCE_BASE)
    ctx.log(f"florence loaded on device={handle.device}")
    for i, capture in enumerate(eval_captures, start=1):
        if ctx.is_cancelled():
            ctx.log(f"cancel requested at capture {i}/{len(eval_captures)} — stopping")
            return
        if ctx.already_done(capture):
            continue
        ctx.update_progress(current_capture=capture.artifact_filename, current_step=f"omniparser proposals ({i}/{len(eval_captures)})")
        screenshot = _resolve_screenshot(capture, ctx.artifacts_root)
        if screenshot is None:
            ctx.log(f"[skip] {capture.artifact_filename} — screenshot missing")
            continue
        proposals = _fetch_omniparser_proposals(screenshot.name)
        ctx.update_progress(current_capture=capture.artifact_filename, current_step=f"florence inference ({i}/{len(eval_captures)})")
        original = capture.element_query or ""
        sent = normalize_element_query(original)
        result = v0_florence.predict_bbox(handle=handle, screenshot_path=str(screenshot), element_query=sent)
        florence_bbox = result.get("bbox")

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
        vp = _viewport_ref(capture)
        ctx.write_prediction({
            "artifact_filename": capture.artifact_filename,
            "screenshot_filename": screenshot.name,
            "screenshot_width": vp.get("width"),
            "screenshot_height": vp.get("height"),
            "scenario_id": capture.scenario_id,
            "domain_id": capture.domain_id,
            "goal_id": capture.goal_id,
            "observed_page_state": capture.observed_page_state,
            "element_query": original,
            "sent_query": sent,
            "predicted_bbox": chosen_bbox,
            "florence_bbox": florence_bbox,
            "omniparser_proposal_count": len(proposals),
            "snap_strategy": snap_strategy,
            "predicted_confidence": chosen_confidence,
            "approved_bbox": target,
            "bbox_iou": round(iou, 4),
            "center_in_target": center_in,
            "latency_ms": result.get("latency_ms", 0),
            "raw_response": result.get("raw_response"),
        })
        ctx.update_progress(current_capture=capture.artifact_filename, current_step=f"persisted ({i}/{len(eval_captures)})")


def _run_uground_zero_shot(*, ctx: EvalRunContext, model: ModelRegistry) -> None:
    eval_captures = _select_eval_captures(ctx)
    # Evict Florence (and any other heavy handle) to free MPS before loading
    # UGround's ~4 GB of bf16 weights — both models can't coexist on a 9 GB MPS.
    if v0_florence.release_florence():
        ctx.log("evicted resident Florence handle to make room for UGround")
    ctx.update_progress(current_capture=None, current_step="loading uground (~4GB bf16 on MPS)")
    handle = v0_uground.load_uground(model.model_name or v0_uground.UGROUND_MODEL)
    ctx.log(f"uground loaded on device={handle.device}")
    for i, capture in enumerate(eval_captures, start=1):
        if ctx.is_cancelled():
            ctx.log(f"cancel requested at capture {i}/{len(eval_captures)} — stopping")
            return
        if ctx.already_done(capture):
            continue
        ctx.update_progress(current_capture=capture.artifact_filename, current_step=f"uground inference ({i}/{len(eval_captures)})")
        screenshot = _resolve_screenshot(capture, ctx.artifacts_root)
        if screenshot is None:
            ctx.log(f"[skip] {capture.artifact_filename} — screenshot missing")
            continue
        original = capture.element_query or ""
        t0 = time.perf_counter()
        result = v0_uground.predict_point(handle=handle, screenshot_path=str(screenshot), element_query=original)
        elapsed = int((time.perf_counter() - t0) * 1000)
        ctx.log(f"capture {i}/{len(eval_captures)}: {capture.artifact_filename} → {elapsed} ms")

        predicted_bbox = result.get("bbox")
        predicted_point = result.get("point")
        target = capture.approved_bbox
        iou = _bbox_iou(predicted_bbox, target) if predicted_bbox else 0.0
        center_in = False
        if predicted_point and target:
            center_in = _point_in_bbox(predicted_point["x"], predicted_point["y"], target)
        vp = _viewport_ref(capture)
        ctx.write_prediction({
            "artifact_filename": capture.artifact_filename,
            "screenshot_filename": screenshot.name,
            "screenshot_width": vp.get("width"),
            "screenshot_height": vp.get("height"),
            "scenario_id": capture.scenario_id,
            "domain_id": capture.domain_id,
            "goal_id": capture.goal_id,
            "observed_page_state": capture.observed_page_state,
            "element_query": original,
            "sent_query": original,
            "predicted_bbox": predicted_bbox,
            "predicted_point": predicted_point,
            "norm_point": result.get("norm_point"),
            "approved_bbox": target,
            "bbox_iou": round(iou, 4),
            "center_in_target": center_in,
            "latency_ms": result.get("latency_ms", elapsed),
            "raw_response": result.get("raw_response"),
        })
        ctx.update_progress(current_capture=capture.artifact_filename, current_step=f"persisted ({i}/{len(eval_captures)})")


IMPLEMENTATIONS: dict[str, Callable[..., None]] = {
    "v0_zero_shot_florence2_base": run_eval_florence_zero_shot,
    "v0_zero_shot_florence2_base_short_query": run_eval_florence_zero_shot_normalized,
    "v0_zero_shot_florence2_base_descriptive_query": _run_florence_descriptive,
    "v0_two_stage_omniparser_then_florence": _run_omniparser_then_florence,
    "v0_zero_shot_uground_v1_2b": _run_uground_zero_shot,
}


# ---------------------------------------------------------------------------
# Orchestration: schedule, resume, cancel.
# ---------------------------------------------------------------------------

def create_eval_run(
    *,
    db: Session,
    model_id: str,
    resumed_from: Optional[str] = None,
) -> ModelEvalRun:
    """Insert a fresh `pending` run row. Doesn't start the work — that's the
    background thread's job. Returns the row so the caller can hand its id
    back to the UI immediately."""
    model = db.get(ModelRegistry, model_id)
    if model is None:
        raise ValueError(f"model not registered: {model_id}")
    if model.implementation not in IMPLEMENTATIONS:
        raise ValueError(f"no implementation registered for {model.implementation}")
    run = ModelEvalRun(
        id=uuid.uuid4().hex,
        model_id=model.id,
        status="pending",
        started_at=datetime.now(timezone.utc),
        record_count=0,
        cancel_requested=False,
        resumed_from=resumed_from,
        progress={"completed": 0, "total": None, "current_step": "pending", "started_at": _iso_now()},
    )
    db.add(run)
    db.commit()
    return run


def execute_eval_run(*, run_id: str, artifacts_root: Path) -> None:
    """Run the eval to completion (or cancellation, or crash). Designed to be
    invoked from a background thread — uses its own DB session.

    Resumability: if the run row has `resumed_from`, we copy the prior run's
    predictions.jsonl into this run's directory before starting, so completed
    captures are skipped.
    """
    db = SessionLocal()
    try:
        run = db.get(ModelEvalRun, run_id)
        if run is None:
            logger.error("execute_eval_run: row not found id=%s", run_id)
            return
        model = db.get(ModelRegistry, run.model_id)
        runner = IMPLEMENTATIONS.get(model.implementation) if model else None
        if runner is None:
            run.status = "failed"
            run.error = "no implementation"
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            return

        run.status = "running"
        db.commit()

        out_dir = _eval_dir(artifacts_root, model.id, run.started_at.isoformat())
        existing_predictions: list[dict[str, Any]] = []
        # Resume: copy predictions from the previous run's directory.
        if run.resumed_from:
            prev = db.get(ModelEvalRun, run.resumed_from)
            if prev and prev.artifact_dir:
                src = Path(prev.artifact_dir) / "predictions.jsonl"
                if src.exists():
                    for line in src.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            existing_predictions.append(json.loads(line))
                        except Exception:
                            continue
                    # Pre-populate this run's predictions.jsonl with the resumed lines.
                    dst = out_dir / "predictions.jsonl"
                    if not dst.exists():
                        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

        ctx = EvalRunContext(
            db=db,
            run_id=run_id,
            model_id=model.id,
            artifacts_root=artifacts_root,
            out_dir=out_dir,
            existing_predictions=existing_predictions,
        )
        ctx.log(f"eval start model_id={model.id} implementation={model.implementation} resumed_from={run.resumed_from}")
        try:
            runner(ctx=ctx, model=model)
        except Exception as exc:
            ctx.log(f"runner crashed: {type(exc).__name__}: {exc}")
            logger.exception("eval failed for run=%s", run_id)
            ctx.finalize(status="failed", error=f"{type(exc).__name__}: {exc}")
            return

        # Distinguish cancel from normal completion.
        db.refresh(run)
        if run.cancel_requested:
            ctx.finalize(status="cancelled")
        else:
            ctx.finalize(status="success")
    finally:
        db.close()


def request_cancel(*, db: Session, run_id: str) -> Optional[ModelEvalRun]:
    run = db.get(ModelEvalRun, run_id)
    if run is None:
        return None
    run.cancel_requested = True
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
