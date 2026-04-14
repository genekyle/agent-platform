from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from app.observer.region_scorer import RegionScorerInput, RegionScorerOutput


_OUTPUT_ROOT = Path(__file__).resolve().parent.parent.parent / "output"
_TRAINING_DIR = _OUTPUT_ROOT / "training"
_TRAINING_FILE = _TRAINING_DIR / "region_scorer_v1.jsonl"
_DERIVED_ROOT = _OUTPUT_ROOT / "derived"


def _to_dict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_dict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_to_dict(i) for i in obj]
    return obj


def log_training_record(
    scorer_input: RegionScorerInput,
    scorer_output: RegionScorerOutput,
) -> None:
    """Append one input+output record to the training JSONL file."""
    _TRAINING_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "input": _to_dict(scorer_input),
        "output": _to_dict(scorer_output),
    }
    with _TRAINING_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def write_region_scores(artifact_stem: str, scores: list[RegionScorerOutput]) -> None:
    """Write all region scores for one observation to derived/{stem}/region_scores.json."""
    out_dir = _DERIVED_ROOT / artifact_stem
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "region_scores.json"
    out_path.write_text(
        json.dumps([_to_dict(s) for s in scores], indent=2),
        encoding="utf-8",
    )
