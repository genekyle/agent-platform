"""Vision-based candidate proposer using OmniParser-v2.0.

Two stages, both lazy-loaded and cached at module scope:

1. **Detector** (YOLO, icon_detect/model.pt) — finds bboxes of interactive regions
2. **Captioner** (Florence-2 fine-tuned, icon_caption/) — describes each bbox in natural language

Detector + captioner together cover the "where" and "what" layers for vision
candidates. Annotators see human-readable captions in the labeler instead of
opaque candidate ids.

Lifecycle:
    - First call: weight downloads (~2 GB for both models) + model load.
        Subsequent process starts skip the download.
    - Per-screenshot warm cost: ~150 ms detection + ~3-10 s captioning (batched)
        on MPS. CPU is several times slower.

Output shape — drops straight into the labeler alongside observer and manual:

    {
        "candidate_id": "vision-<hash>",
        "bbox": {"x", "y", "width", "height"},
        "confidence": float,                  # YOLO objectness
        "caption": str,                       # Florence-2 caption (may be empty on failure)
        "source": "omniparser",
        "model_version": "omniparser-v2.0/icon_detect+icon_caption",
        "created_at": iso8601,
    }
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


OMNI_REPO = "microsoft/OmniParser-v2.0"
OMNI_DETECT_FILE = "icon_detect/model.pt"
OMNI_CAPTION_SUBFOLDER = "icon_caption"
# Florence-2 base — provides processor + modeling code that OmniParser's caption weights ride on.
FLORENCE_BASE = "microsoft/Florence-2-base"
MODEL_VERSION = "omniparser-v2.0/icon_detect+icon_caption"

# Detector tuning
INFERENCE_IMGSZ = 1280
DEFAULT_CONF_THRESHOLD = 0.05

# Captioner tuning
CAPTION_PROMPT = "<CAPTION>"
CAPTION_MAX_NEW_TOKENS = 20
CAPTION_NUM_BEAMS = 3
# Batch size for caption generation. Tuned for MPS / 16-32 GB unified memory;
# adjust down if OOM on smaller machines, up if you have lots of headroom.
CAPTION_BATCH_SIZE = 8


@dataclass
class ProposerHandle:
    """Cached models + device for the proposer pipeline."""
    detector: Any                # ultralytics YOLO
    caption_processor: Any       # transformers AutoProcessor (Florence-2)
    caption_model: Any           # transformers AutoModelForCausalLM (OmniParser caption weights)
    device: str
    model_name: str = OMNI_REPO


_handle_lock = threading.Lock()
_handle: Optional[ProposerHandle] = None


def _resolve_device() -> str:
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_proposer() -> ProposerHandle:
    """Module-cached singleton. Concurrent first callers block until built."""
    global _handle
    if _handle is not None:
        return _handle
    with _handle_lock:
        if _handle is not None:
            return _handle

        # Imports deferred so importing this module is cheap when the proposer
        # isn't actually invoked (e.g. during unit tests of other observer pieces).
        import torch
        from huggingface_hub import hf_hub_download
        from ultralytics import YOLO
        from transformers import AutoProcessor, AutoModelForCausalLM

        device = _resolve_device()

        # Detector: YOLO icon_detect
        detect_weights = hf_hub_download(repo_id=OMNI_REPO, filename=OMNI_DETECT_FILE)
        detector = YOLO(detect_weights)

        # Captioner: Florence-2 processor + OmniParser fine-tuned weights.
        # Processor comes from base Florence (provides tokenizer + image pipeline);
        # weights come from OmniParser's icon_caption subfolder.
        processor = AutoProcessor.from_pretrained(FLORENCE_BASE, trust_remote_code=True)
        caption_model = AutoModelForCausalLM.from_pretrained(
            OMNI_REPO,
            subfolder=OMNI_CAPTION_SUBFOLDER,
            trust_remote_code=True,
            torch_dtype=torch.float32,  # fp32 keeps MPS stable; bump to fp16 on CUDA if needed
        ).to(device).eval()

        _handle = ProposerHandle(
            detector=detector,
            caption_processor=processor,
            caption_model=caption_model,
            device=device,
        )
    return _handle


def _candidate_id(bbox: dict, confidence: float, screenshot_path: str) -> str:
    """Stable id from (screenshot, bbox, conf) so re-runs produce the same ids
    and annotator approvals stick to the right boxes."""
    payload = (
        f"{Path(screenshot_path).name}|"
        f"{bbox['x']:.1f},{bbox['y']:.1f},{bbox['width']:.1f},{bbox['height']:.1f}|"
        f"{confidence:.3f}"
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]
    return f"vision-{digest}"


def _generate_captions_batched(
    handle: ProposerHandle,
    crops: list[Any],  # list of PIL.Image
) -> list[str]:
    """Run the Florence-2 caption model over a list of crops in batches.

    Returns one caption per input crop, in the same order. On any per-batch
    failure, returns empty strings for that batch's slots — the proposer
    proceeds without captions rather than failing the whole capture.
    """
    if not crops:
        return []

    import torch
    captions: list[str] = []

    for start in range(0, len(crops), CAPTION_BATCH_SIZE):
        batch = crops[start : start + CAPTION_BATCH_SIZE]
        try:
            with torch.inference_mode():
                inputs = handle.caption_processor(
                    text=[CAPTION_PROMPT] * len(batch),
                    images=batch,
                    return_tensors="pt",
                    padding=True,
                ).to(handle.device)
                generated_ids = handle.caption_model.generate(
                    input_ids=inputs["input_ids"],
                    pixel_values=inputs["pixel_values"],
                    max_new_tokens=CAPTION_MAX_NEW_TOKENS,
                    do_sample=False,
                    num_beams=CAPTION_NUM_BEAMS,
                )
            decoded_batch = handle.caption_processor.batch_decode(
                generated_ids, skip_special_tokens=False,
            )
            for crop, raw in zip(batch, decoded_batch):
                parsed = handle.caption_processor.post_process_generation(
                    raw, task=CAPTION_PROMPT, image_size=(crop.width, crop.height),
                )
                caption_text = parsed.get(CAPTION_PROMPT) or ""
                # Batched decoding with padding=True leaves "<pad>" tokens trailing
                # in the parsed output. Strip them along with whitespace + trailing
                # punctuation noise Florence likes to emit.
                caption_text = str(caption_text).replace("<pad>", "").strip()
                while caption_text.endswith("."):
                    caption_text = caption_text[:-1].rstrip()
                captions.append(caption_text)
        except Exception:
            # Don't let a single bad batch kill the whole sidecar — log via empty
            # captions; the entries still have bbox + confidence + id.
            captions.extend([""] * len(batch))

    return captions


def propose_candidates(
    screenshot_path: str | Path,
    *,
    conf: float = DEFAULT_CONF_THRESHOLD,
    handle: Optional[ProposerHandle] = None,
    include_captions: bool = True,
) -> list[dict[str, Any]]:
    """Run the proposer (detection + captioning) on one screenshot.

    Returns an empty list if the screenshot doesn't exist or nothing is detected.
    Set `include_captions=False` to skip the slower Florence-2 step — useful when
    you only need bboxes (debugging, perf testing).
    """
    path = Path(screenshot_path)
    if not path.exists():
        return []

    if handle is None:
        handle = load_proposer()

    # Step 1: detection
    results = handle.detector.predict(
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

    # Build crops + bbox dicts in one pass (same order — used to align captions back)
    from PIL import Image
    img = Image.open(path).convert("RGB")

    crops: list[Any] = []
    bbox_dicts: list[dict[str, float]] = []
    for (x1, y1, x2, y2), confidence in zip(xyxy, confs):
        bbox_dicts.append({
            "x": float(x1),
            "y": float(y1),
            "width": float(x2 - x1),
            "height": float(y2 - y1),
        })
        crops.append(img.crop((int(x1), int(y1), int(x2), int(y2))))

    # Step 2: captioning (batched)
    captions = _generate_captions_batched(handle, crops) if include_captions else [""] * len(crops)

    now = datetime.now(timezone.utc).isoformat()
    out: list[dict[str, Any]] = []
    for bbox, confidence, caption in zip(bbox_dicts, confs, captions):
        out.append({
            "candidate_id": _candidate_id(bbox, float(confidence), str(path)),
            "bbox": bbox,
            "confidence": float(confidence),
            "caption": caption,
            "source": "omniparser",
            "model_version": MODEL_VERSION,
            "created_at": now,
        })
    return out
