from __future__ import annotations

from copy import deepcopy
from typing import Any, Optional


DEFAULT_OBSERVER_CONFIG: dict[str, Any] = {
    "stages": {
        "screenshot_capture": {"adapter_id": "heuristic"},
        "scene_interpreter": {"adapter_id": "heuristic"},
        "region_proposal": {"adapter_id": "heuristic"},
        "region_scorer": {"adapter_id": "heuristic"},
        "visual_element_proposal": {"adapter_id": "heuristic"},
        "grounding": {"adapter_id": "heuristic"},
        "fusion": {"adapter_id": "heuristic"},
    }
}


def get_observer_config(overrides: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    config = deepcopy(DEFAULT_OBSERVER_CONFIG)
    if not overrides:
        return config

    # Merge stage-level overrides
    override_stages = overrides.get("stages", {})
    for stage_name, stage_config in override_stages.items():
        config["stages"].setdefault(stage_name, {})
        config["stages"][stage_name].update(stage_config)

    # Passthrough top-level non-stage keys (e.g. task_context, _artifact_stem)
    for key, value in overrides.items():
        if key != "stages":
            config[key] = value

    return config
