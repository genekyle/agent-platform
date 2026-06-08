from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.artifacts import ARTIFACTS_DIR, SCREENSHOTS_DIR, write_observation_artifact
from app.main import observe_live_capture
from app.observer.vision_proposer import MODEL_VERSION, propose_candidates


logger = logging.getLogger("mcp-mock.proposer")

app = FastAPI(title="MCP Mock Capture Server", version="0.0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _vision_sidecar_path(artifact_filename: str) -> Path:
    """Sidecar lives next to the artifact, named <artifact>.vision.json.

    Keeps the raw artifact immutable while still letting the controlplane-api
    surface vision candidates via the existing GET /api/observations/{filename}.
    """
    return ARTIFACTS_DIR / f"{artifact_filename}.vision.json"


def _screenshot_filename_from_artifact(artifact: dict) -> Optional[str]:
    """Pull the first screenshot filename out of an artifact dict.

    Returns None if the artifact has no screenshot — the proposer needs one
    to do anything useful.
    """
    shots = artifact.get("acquisition", {}).get("screenshots") or []
    if not shots:
        return None
    return shots[0].get("filename")


def _write_vision_sidecar(artifact_filename: str, screenshot_filename: str, proposals: list[dict],
                          timing: dict | None = None) -> Path:
    sidecar = {
        "version": MODEL_VERSION,
        "artifact_filename": artifact_filename,
        "screenshot_filename": screenshot_filename,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "proposal_count": len(proposals),
        "timing": timing or {},
        "proposals": proposals,
    }
    path = _vision_sidecar_path(artifact_filename)
    path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return path


def _backfill_vision_candidates(artifact_filename: str, screenshot_filename: str) -> None:
    """Background-task entry point: run the proposer and persist the sidecar.

    Exceptions are caught and logged, never raised — this runs after the
    capture HTTP response is sent, so there's no caller to handle errors.
    A missing sidecar simply means "no vision candidates yet" on the read path.
    """
    try:
        screenshot_path = SCREENSHOTS_DIR / screenshot_filename
        timing: dict = {}
        proposals = propose_candidates(screenshot_path, stats=timing)
        sidecar_path = _write_vision_sidecar(artifact_filename, screenshot_filename, proposals, timing)
        logger.info(
            "vision backfill ok: %s -> %d proposals in %dms (%s)",
            artifact_filename, len(proposals), timing.get("total_ms", 0), sidecar_path.name,
        )
    except Exception:
        logger.exception("vision backfill failed for %s", artifact_filename)


class CaptureRequest(BaseModel):
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    scenario: str = "live_capture"
    task_context: Optional[dict] = None
    training_metadata: Optional[dict] = None
    browser_url: str = "http://127.0.0.1:9222"


class ProposerPredictRequest(BaseModel):
    """In-process call: just run the proposer on a screenshot, don't persist anything."""
    screenshot_filename: str


@app.get("/health")
def health():
    return {"ok": True, "service": "mcp-mock-capture-server"}


@app.post("/capture")
async def trigger_capture(body: CaptureRequest, background_tasks: BackgroundTasks):
    artifact = await observe_live_capture(
        scenario=body.scenario,
        tab_id=body.tab_id,
        tab_url=body.tab_url,
        browser_url=body.browser_url,
        task_context=body.task_context,
        training_metadata=body.training_metadata,
    )
    path = write_observation_artifact(artifact)
    candidate_count = len(artifact.get("ranked_candidates", []))

    # NOTE: the vision proposer is NOT run here. It runs lazily when a capture is
    # opened in the labeler (see /proposer/backfill), so captures nobody reviews
    # never cost compute. This is the deliberate fail-safe gate.
    return {"filename": path.name, "candidate_count": candidate_count}


@app.post("/proposer/predict")
def proposer_predict(body: ProposerPredictRequest):
    """Run the proposer on demand against an existing screenshot.

    Used for ad-hoc debugging and for the bulk backfill below. Does not
    persist — caller decides what to do with the result.
    """
    screenshot_path = SCREENSHOTS_DIR / body.screenshot_filename
    if not screenshot_path.exists():
        raise HTTPException(status_code=404, detail=f"Screenshot not found: {body.screenshot_filename}")
    proposals = propose_candidates(screenshot_path)
    return {
        "screenshot_filename": body.screenshot_filename,
        "model_version": MODEL_VERSION,
        "proposal_count": len(proposals),
        "proposals": proposals,
    }


@app.post("/proposer/backfill/{artifact_filename}")
def proposer_backfill_one(artifact_filename: str, include_captions: bool = False):
    """Run the proposer for a single capture and write its sidecar.

    This is the lazy entry point: the labeler calls it when a capture is opened
    without candidates (detect-only, fast), and again with include_captions=true
    when the annotator explicitly asks for Florence-2 captions.
    """
    artifact_path = ARTIFACTS_DIR / artifact_filename
    if not artifact_path.exists():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_filename}")

    try:
        artifact = json.loads(artifact_path.read_text())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read artifact: {exc}")

    screenshot_filename = _screenshot_filename_from_artifact(artifact)
    if not screenshot_filename:
        raise HTTPException(status_code=400, detail="Artifact has no screenshot to propose against.")

    screenshot_path = SCREENSHOTS_DIR / screenshot_filename
    if not screenshot_path.exists():
        raise HTTPException(status_code=404, detail=f"Screenshot file missing: {screenshot_filename}")

    timing: dict = {}
    proposals = propose_candidates(screenshot_path, include_captions=include_captions, stats=timing)
    sidecar_path = _write_vision_sidecar(artifact_filename, screenshot_filename, proposals, timing)
    return {
        "artifact_filename": artifact_filename,
        "screenshot_filename": screenshot_filename,
        "sidecar_path": str(sidecar_path),
        "proposal_count": len(proposals),
        "captioned": include_captions,
        "timing": timing,
    }
