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
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Offline-by-default: use cached model weights only and never phone home to HuggingFace
# (which otherwise makes a small network call per model load to re-validate the cache).
# The weights are already downloaded; this just stops the recurring chatter — important
# on metered/hotspot connections. On a FRESH machine that needs to fetch weights once,
# run with AGENT_MODEL_DOWNLOAD=1 to allow the download. Must be set before any HF import.
if os.environ.get("AGENT_MODEL_DOWNLOAD") != "1":
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

logger = logging.getLogger("mcp-mock.vision_proposer")


OMNI_REPO = "microsoft/OmniParser-v2.0"
OMNI_DETECT_FILE = "icon_detect/model.pt"
OMNI_CAPTION_SUBFOLDER = "icon_caption"
# Florence-2 base — provides processor + modeling code that OmniParser's caption weights ride on.
FLORENCE_BASE = "microsoft/Florence-2-base"
MODEL_VERSION = "omniparser-v2.0/icon_detect+icon_caption"

# Detector tuning
INFERENCE_IMGSZ = 1280
# Confidence floor for keeping a detection. OmniParser's README uses 0.05 for
# maximum recall, but that floods a labeling tool with noise (49 boxes on a page
# with ~10 real elements) and balloons captioning cost. 0.30 keeps salient
# interactive elements (buttons, fields, icons) while dropping faint edges/padding.
# Empirically on a Google homepage: 0.05->49, 0.30->18, 0.40->11.
DEFAULT_CONF_THRESHOLD = 0.30

# Hard cap on candidates per screenshot, applied after sorting by confidence.
# Safety valve for dense pages (e.g. a Marketplace listing feed) so captioning
# stays bounded and the labeler doesn't get an unusable wall of boxes. The
# annotator labels one element per capture, so the top-N by confidence is plenty.
MAX_CANDIDATES = 30

# Captioner tuning — captioning (Florence-2) is the dominant cost, and on MPS it's
# highly variable (a 30-crop page can take minutes under memory pressure). Captions are
# only annotator scaffolding (they never train the grounding model), so we trade caption
# quality for speed aggressively:
CAPTION_PROMPT = "<CAPTION>"
CAPTION_MAX_NEW_TOKENS = 16
CAPTION_NUM_BEAMS = 1          # greedy decode — ~3x faster than beam search=3
CAPTION_BATCH_SIZE = 8
# Only caption the top-N highest-confidence detections. Boxes for ALL kept candidates
# still appear (up to MAX_CANDIDATES); lower-confidence ones just show their id instead
# of a caption. This bounds the slow step regardless of how many buttons are on screen.
CAPTION_TOP_N = 12


@dataclass
class ProposerHandle:
    """Cached models + device for the proposer pipeline.

    The captioner (Florence-2, ~1.5 GB) is loaded LAZILY — only the detector is
    loaded up front. Detect-only is the default path (instant boxes, no caption
    cost/RAM); captions are opt-in and load Florence-2 on first request.
    """
    detector: Any                       # ultralytics YOLO
    device: str
    caption_processor: Any = None       # transformers AutoProcessor (Florence-2) — lazy
    caption_model: Any = None           # transformers AutoModelForCausalLM — lazy
    model_name: str = OMNI_REPO


_handle_lock = threading.Lock()
_handle: Optional[ProposerHandle] = None
_caption_lock = threading.Lock()


def _resolve_device() -> str:
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_proposer() -> ProposerHandle:
    """Module-cached singleton — loads ONLY the detector (fast, small).

    The captioner is loaded lazily by `_ensure_captioner` on the first caption
    request, so the common detect-only path never pays Florence-2's load time or
    ~1.5 GB of RAM. Concurrent first callers block until the detector is built.
    """
    global _handle
    if _handle is not None:
        return _handle
    with _handle_lock:
        if _handle is not None:
            return _handle

        # Imports deferred so importing this module is cheap when the proposer
        # isn't actually invoked (e.g. during unit tests of other observer pieces).
        from huggingface_hub import hf_hub_download
        from ultralytics import YOLO

        device = _resolve_device()

        # Detector: YOLO icon_detect
        detect_weights = hf_hub_download(repo_id=OMNI_REPO, filename=OMNI_DETECT_FILE)
        detector = YOLO(detect_weights)

        _handle = ProposerHandle(detector=detector, device=device)
    return _handle


def _ensure_captioner(handle: ProposerHandle) -> None:
    """Lazily load the Florence-2 captioner into an existing handle. Only paid the
    first time captions are actually requested (the slow/heavy step)."""
    if handle.caption_model is not None:
        return
    with _caption_lock:
        if handle.caption_model is not None:
            return
        import torch
        from transformers import AutoProcessor, AutoModelForCausalLM

        # Processor comes from base Florence (tokenizer + image pipeline); weights
        # come from OmniParser's icon_caption subfolder.
        processor = AutoProcessor.from_pretrained(FLORENCE_BASE, trust_remote_code=True)
        caption_model = AutoModelForCausalLM.from_pretrained(
            OMNI_REPO,
            subfolder=OMNI_CAPTION_SUBFOLDER,
            trust_remote_code=True,
            torch_dtype=torch.float32,  # fp32 keeps MPS stable; bump to fp16 on CUDA if needed
        ).to(handle.device).eval()
        handle.caption_processor = processor
        handle.caption_model = caption_model


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
    include_captions: bool = False,
    stats: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Run the proposer (detection + captioning) on one screenshot.

    Returns an empty list if the screenshot doesn't exist or nothing is detected.
    Set `include_captions=False` to skip the slower Florence-2 step.

    Pass a `stats` dict to receive a per-phase timing/observability breakdown
    (load/detect/caption ms, counts, device) — the caller can persist it so the
    annotator can SEE what each capture's proposal cost and where time went.
    """
    path = Path(screenshot_path)
    if not path.exists():
        return []

    overall_start = time.perf_counter()

    # Time the (possibly cold) model load separately — first call pays download/load.
    load_start = time.perf_counter()
    if handle is None:
        handle = load_proposer()
    load_ms = int((time.perf_counter() - load_start) * 1000)

    # Phase 1: detection
    detect_start = time.perf_counter()
    results = handle.detector.predict(
        str(path),
        conf=conf,
        imgsz=INFERENCE_IMGSZ,
        device=handle.device,
        verbose=False,
    )
    detect_ms = int((time.perf_counter() - detect_start) * 1000)

    boxes = results[0].boxes if results else None
    raw_count = len(boxes) if boxes is not None else 0
    if not raw_count:
        if stats is not None:
            stats.update({"device": handle.device, "load_ms": load_ms, "detect_ms": detect_ms,
                          "raw_detections": 0, "kept": 0, "captioned": 0, "caption_ms": 0,
                          "total_ms": int((time.perf_counter() - overall_start) * 1000)})
        return []

    xyxy = boxes.xyxy.cpu().numpy().tolist()
    confs_all = boxes.conf.cpu().numpy().tolist()

    # Sort by confidence and cap at MAX_CANDIDATES before any crop/caption work.
    ranked = sorted(zip(xyxy, confs_all), key=lambda pair: pair[1], reverse=True)[:MAX_CANDIDATES]

    from PIL import Image
    img = Image.open(path).convert("RGB")

    crops: list[Any] = []
    bbox_dicts: list[dict[str, float]] = []
    confs: list[float] = []
    for (x1, y1, x2, y2), confidence in ranked:
        bbox_dicts.append({
            "x": float(x1), "y": float(y1),
            "width": float(x2 - x1), "height": float(y2 - y1),
        })
        confs.append(confidence)
        crops.append(img.crop((int(x1), int(y1), int(x2), int(y2))))

    # Phase 2: captioning — only the top CAPTION_TOP_N crops (the slow step). The rest
    # keep their boxes but get no caption, bounding cost regardless of page density.
    caption_start = time.perf_counter()
    captioned_n = 0
    if include_captions and crops:
        _ensure_captioner(handle)  # lazy-load Florence-2 only now, on actual demand
        top_caps = _generate_captions_batched(handle, crops[:CAPTION_TOP_N])
        captions = top_caps + [""] * (len(crops) - len(top_caps))
        captioned_n = len(top_caps)
    else:
        captions = [""] * len(crops)
    caption_ms = int((time.perf_counter() - caption_start) * 1000)
    total_ms = int((time.perf_counter() - overall_start) * 1000)

    if stats is not None:
        stats.update({
            "device": handle.device, "load_ms": load_ms, "detect_ms": detect_ms,
            "caption_ms": caption_ms, "total_ms": total_ms,
            "raw_detections": raw_count, "kept": len(crops), "captioned": captioned_n,
        })
    logger.info(
        "proposer timing: device=%s load=%dms detect=%dms caption=%dms total=%dms "
        "(raw=%d kept=%d captioned=%d)",
        handle.device, load_ms, detect_ms, caption_ms, total_ms,
        raw_count, len(crops), captioned_n,
    )

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
