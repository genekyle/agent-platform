"""Florence-2-base zero-shot grounding wrapper.

Lazy-loaded singleton, mirroring apps/mcp-mock/app/observer/vision_proposer.py.
fp32 on MPS (matches the proposer's captioner choice for stability).

Inference uses the `<CAPTION_TO_PHRASE_GROUNDING>` task: the element_query is
fed in as the "caption" to ground, and the model returns bboxes for the noun
phrase(s) it found in the image. For v0 we take the first returned bbox.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Stay offline by default — weights are cached, no need to re-validate with HF on
# every load. Set AGENT_MODEL_DOWNLOAD=1 to allow a fresh download on first run.
if os.environ.get("AGENT_MODEL_DOWNLOAD") != "1":
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

logger = logging.getLogger("controlplane.model_lib.v0_florence")

FLORENCE_BASE = "microsoft/Florence-2-base"
TASK_PROMPT = "<CAPTION_TO_PHRASE_GROUNDING>"
MAX_NEW_TOKENS = 1024
NUM_BEAMS = 3


@dataclass
class FlorenceHandle:
    model: Any
    processor: Any
    device: str
    model_name: str = FLORENCE_BASE


_handle_lock = threading.Lock()
_handle: Optional[FlorenceHandle] = None


def _resolve_device() -> str:
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_florence(model_name: str = FLORENCE_BASE) -> FlorenceHandle:
    """Module-cached singleton. First call pays weight-load (~460 MB). fp32 on MPS."""
    global _handle
    if _handle is not None and _handle.model_name == model_name:
        return _handle
    with _handle_lock:
        if _handle is not None and _handle.model_name == model_name:
            return _handle

        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor

        device = _resolve_device()
        processor = AutoProcessor.from_pretrained(model_name, trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            trust_remote_code=True,
            torch_dtype=torch.float32,
        ).to(device).eval()

        _handle = FlorenceHandle(model=model, processor=processor, device=device, model_name=model_name)
        logger.info("loaded florence-2: %s on %s", model_name, device)
    return _handle


def predict_bbox(
    *,
    handle: FlorenceHandle,
    screenshot_path: str,
    element_query: str,
    region_bbox: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """Run Florence's caption-to-phrase-grounding on a screenshot.

    If `region_bbox` is provided (in original-image pixel coords), the image is
    cropped to that region BEFORE being sent to Florence, and returned boxes
    are remapped back to the original image's coordinate space. This is the
    "screen region mode" — useful when you've already detected likely regions
    (e.g. via OmniParser) and want Florence to refine the box inside one of them.

    Returns:
        {
          "bbox": {x, y, width, height} | None,    original-image pixel coords
          "all_bboxes": [...],                      every box Florence returned, remapped
          "raw_response": str,
          "confidence": None,
          "latency_ms": int,
          "region_bbox": dict | None,               echoed for traceability
        }
    """
    import torch
    from PIL import Image

    started = time.perf_counter()
    path = Path(screenshot_path)
    if not path.exists():
        return {
            "bbox": None,
            "all_bboxes": [],
            "raw_response": "",
            "confidence": None,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "error": f"screenshot not found: {screenshot_path}",
            "region_bbox": region_bbox,
        }

    image = Image.open(path).convert("RGB")
    offset_x = 0.0
    offset_y = 0.0
    if region_bbox is not None:
        # Crop to the region, clamped to image bounds.
        rx = max(0.0, float(region_bbox.get("x", 0)))
        ry = max(0.0, float(region_bbox.get("y", 0)))
        rw = max(1.0, float(region_bbox.get("width", 1)))
        rh = max(1.0, float(region_bbox.get("height", 1)))
        rx2 = min(float(image.width), rx + rw)
        ry2 = min(float(image.height), ry + rh)
        # Round to int for PIL.crop.
        crop_box = (int(rx), int(ry), int(rx2), int(ry2))
        if crop_box[2] - crop_box[0] >= 2 and crop_box[3] - crop_box[1] >= 2:
            image = image.crop(crop_box)
            offset_x = float(crop_box[0])
            offset_y = float(crop_box[1])

    prompt = f"{TASK_PROMPT} {element_query.strip()}"

    with torch.inference_mode():
        inputs = handle.processor(text=prompt, images=image, return_tensors="pt").to(handle.device)
        generated_ids = handle.model.generate(
            input_ids=inputs["input_ids"],
            pixel_values=inputs["pixel_values"],
            max_new_tokens=MAX_NEW_TOKENS,
            do_sample=False,
            num_beams=NUM_BEAMS,
        )
    raw = handle.processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
    parsed = handle.processor.post_process_generation(
        raw, task=TASK_PROMPT, image_size=(image.width, image.height),
    )
    grounded = parsed.get(TASK_PROMPT) or {}
    raw_boxes = grounded.get("bboxes") or []

    all_bboxes: list[dict[str, float]] = []
    for box in raw_boxes:
        # Florence returns [x1, y1, x2, y2] in pixel coords of the IMAGE IT SAW
        # (which may be a crop). Remap back to original-image coords.
        try:
            x1, y1, x2, y2 = (float(v) for v in box)
        except Exception:
            continue
        all_bboxes.append({
            "x": x1 + offset_x,
            "y": y1 + offset_y,
            "width": max(0.0, x2 - x1),
            "height": max(0.0, y2 - y1),
        })

    # v0: take the first returned bbox. Selection refinement is a v1 concern.
    bbox = all_bboxes[0] if all_bboxes else None
    return {
        "bbox": bbox,
        "all_bboxes": all_bboxes,
        "raw_response": raw,
        "confidence": None,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "region_bbox": region_bbox,
    }
