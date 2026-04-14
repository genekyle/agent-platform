from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.config import get_observer_config
from app.observer.adapters import DEFAULT_STAGE_REGISTRY


STAGE_ORDER = [
    "screenshot_capture",
    "scene_interpreter",
    "region_proposal",
    "region_scorer",
    "visual_element_proposal",
    "grounding",
    "fusion",
]


def run_pipeline(
    acquisition: dict[str, Any],
    *,
    config: Optional[dict[str, Any]] = None,
    registry: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, Any]:
    resolved_config = get_observer_config(config)
    stage_registry = registry or DEFAULT_STAGE_REGISTRY
    stage_outputs: dict[str, dict[str, Any]] = {}

    for stage_name in STAGE_ORDER:
        adapter_id = resolved_config["stages"][stage_name]["adapter_id"]
        stage_definition = stage_registry[stage_name][adapter_id]
        stage_result = stage_definition.handler(acquisition, {"stage_outputs": stage_outputs, "config": resolved_config})
        stage_outputs[stage_name] = stage_result

    return {
        "config": resolved_config,
        "stages": stage_outputs,
    }


def build_observer_artifact(
    *,
    source: str,
    scenario: Optional[str],
    acquisition: dict[str, Any],
    pipeline_run: dict[str, Any],
    timestamp: Optional[str] = None,
) -> dict[str, Any]:
    metadata = {
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "source": source,
        "observer_version": "vision-first-observer-v1",
    }
    if scenario:
        metadata["scenario"] = scenario

    stages = pipeline_run["stages"]
    return {
        "metadata": metadata,
        "acquisition": acquisition,
        "pipeline": {
            "stage_order": STAGE_ORDER,
            "config": pipeline_run["config"],
            "stages": stages,
        },
        "scene_interpretation": stages["scene_interpreter"]["output"],
        "region_proposals": stages["region_proposal"]["output"],
        "region_scores": stages["region_scorer"]["output"],
        "visual_element_proposals": stages["visual_element_proposal"]["output"],
        "grounded_candidates": stages["grounding"]["output"],
        "ranked_candidates": stages["fusion"]["output"],
    }
