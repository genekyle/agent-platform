"""Vision-based candidate proposer using OmniParser-v2.0's YOLO icon detector.

Lives alongside the heuristic DOM-derived proposer in this same package.
Both feed candidates into the labeler; the vision proposer covers UI elements
the DOM-only path misses (images, SVGs, custom widgets, canvas-rendered
content, anything inside iframes we don't walk).

Lifecycle:
    - Model is lazy-loaded once per process and cached at module scope.
    - First call: weight download (if uncached) + model load — can be slow.
    - Subsequent calls: ~1-2s per screenshot on MPS / CUDA, several seconds on CPU.

Output shape — chosen to drop straight into the labeler alongside observer
and manual candidates:

    {
        "candidate_id": "vision-<hash>",     # never collides with observer or manual
        "bbox": {"x", "y", "width", "height"},
        "confidence": float,                  # raw YOLO objectness
        "source": "omniparser",
        "model_version": "omniparser-v2.0/icon_detect",
        "created_at": iso8601,
    }

This module does NOT depend on the controlplane-api package — keeps the
observer pipeline self-contained.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


OMNI_REPO = "microsoft/OmniParser-v2.0"
OMNI_WEIGHTS_FILE = "icon_detect/model.pt"
MODEL_VERSION = "omniparser-v2.0/icon_detect"

# Inference sizing — matches the OmniParser README's recommended imgsz for UI screenshots.
INFERENCE_IMGSZ = 1280
# YOLO objectness cutoff. 0.05 matches OmniParser README and is intentionally low —
# downstream rankers / annotators trim the long tail; here we cast wide.
DEFAULT_CONF_THRESHOLD = 0.05


@dataclass
class ProposerHandle:
    """Lazily-loaded handle to the YOLO icon detector. Cached at module level."""
    model: Any
    device: str
    model_name: str = OMNI_REPO


_handle_lock = threading.Lock()
_handle: Optional[ProposerHandle] = None


def _resolve_device() -> str:
    """Pick the best inference device available without forcing the import order."""
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_proposer() -> ProposerHandle:
    """Return the module-cached proposer handle, building it on first call.

    Thread-safe: concurrent first calls block until the singleton is ready.
    """
    global _handle
    if _handle is not None:
        return _handle
    with _handle_lock:
        if _handle is not None:
            return _handle
        # Imports deferred so this module is cheap to import when the proposer
        # is never invoked (e.g. during unit tests of other observer pieces).
        from huggingface_hub import hf_hub_download
        from ultralytics import YOLO

        weights_path = hf_hub_download(repo_id=OMNI_REPO, filename=OMNI_WEIGHTS_FILE)
        model = YOLO(weights_path)
        device = _resolve_device()
        _handle = ProposerHandle(model=model, device=device)
    return _handle


def _candidate_id(bbox: dict, confidence: float, screenshot_path: str) -> str:
    """Stable id for a single proposed candidate.

    Hashes (screenshot_path, bbox, conf) so re-running the proposer on the same
    screenshot produces the same ids — important so annotator labels stick to
    the right boxes across re-runs.
    """
    payload = f"{Path(screenshot_path).name}|{bbox['x']:.1f},{bbox['y']:.1f},{bbox['width']:.1f},{bbox['height']:.1f}|{confidence:.3f}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]
    return f"vision-{digest}"


def propose_candidates(
    screenshot_path: str | Path,
    *,
    conf: float = DEFAULT_CONF_THRESHOLD,
    handle: Optional[ProposerHandle] = None,
) -> list[dict[str, Any]]:
    """Run the proposer on one screenshot and return candidate dicts.

    Returns an empty list — never raises — if the screenshot path doesn't exist
    or the model finds nothing above `conf`. Callers should treat absent output
    as "no proposals yet" rather than an error.
    """
    path = Path(screenshot_path)
    if not path.exists():
        return []

    if handle is None:
        handle = load_proposer()

    results = handle.model.predict(
        str(path),
        conf=conf,
        imgsz=INFERENCE_IMGSZ,
        device=handle.device,
        verbose=False,
    )
    if not results:
        return []

    boxes = results[0].boxes
    if boxes is None or len(boxes) == 0:
        return []

    xyxy = boxes.xyxy.cpu().numpy().tolist()
    confs = boxes.conf.cpu().numpy().tolist()
    now = datetime.now(timezone.utc).isoformat()
    out: list[dict[str, Any]] = []
    for (x1, y1, x2, y2), confidence in zip(xyxy, confs):
        bbox = {
            "x": float(x1),
            "y": float(y1),
            "width": float(x2 - x1),
            "height": float(y2 - y1),
        }
        out.append({
            "candidate_id": _candidate_id(bbox, float(confidence), str(path)),
            "bbox": bbox,
            "confidence": float(confidence),
            "source": "omniparser",
            "model_version": MODEL_VERSION,
            "created_at": now,
        })
    return out
