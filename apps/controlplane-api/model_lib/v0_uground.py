"""UGround-V1-2B zero-shot UI grounder.

UGround is purpose-built for GUI grounding: trained on millions of (screenshot,
element description, click point) examples scraped from public web/desktop
captures. It's a Qwen2-VL fine-tune that takes a screenshot + element query
and returns a single (x, y) point — the "click here" coordinate — in 0-1000
normalized space.

Compared to Florence-2-base (general phrase grounding on natural images),
UGround should crush UI element localization without any fine-tuning on our
data. That's exactly the experiment.

OUTPUT NOTE: UGround returns a POINT, not a bbox. For IoU compatibility with
the rest of the eval contract, we wrap the point as a tiny synthetic bbox
(POINT_BBOX_PX wide/tall, centered on the point). The right metric for this
model is center_in_target — "did the predicted click land inside the
human-labeled element?" — and that one is honest about what UGround predicts.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

if os.environ.get("AGENT_MODEL_DOWNLOAD") != "1":
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

logger = logging.getLogger("controlplane.model_lib.v0_uground")

UGROUND_MODEL = "osunlp/UGround-V1-2B"

# System prompt from the model card — primes UGround to output a single (x, y).
UGROUND_SYSTEM_PROMPT = (
    "Your task is to help the user identify the precise coordinates (x, y) of a "
    "specific area/element/object on the screen based on a description.\n\n"
    "- Your response should aim to point to the center or a representative point "
    "within the described area/element/object as accurately as possible.\n"
    "- If the description is unclear or ambiguous, infer the most relevant area or "
    "element based on its likely context or purpose.\n"
    "- Your answer should be a single string (x, y) corresponding to the point of "
    "interest."
)

# Synthetic bbox size around the predicted point (in screenshot pixel coords).
# Small enough that IoU with a giant approved bbox (a wide input field) stays
# honest; the center_in_target metric is the one that actually matters for
# point-grounding models.
POINT_BBOX_PX = 40

MAX_NEW_TOKENS = 64  # we're only generating "(x, y)" — short.

# Qwen2-VL's vision encoder produces a vision-token grid at the input image's
# native resolution (one token per ~14x14 patch). On a 2880x1620 screenshot
# that's ~24k visual tokens, which explodes attention memory on MPS
# ("Invalid buffer size" at ~5GB). Resize to a more manageable max-pixel
# budget before sending. The model's output is in 0-1000 normalized coords,
# so coordinate fidelity is preserved when we scale back to the ORIGINAL
# screenshot dimensions afterwards.
#
# Aggressive budget for MPS: 2B-param Qwen2-VL weights already eat ~4 GB in bf16,
# leaving ~5 GB for vision-encoder activations + KV cache before MPS chokes.
# 768x576 (~440k pixels) keeps the whole pipeline under the limit. UGround's
# training data was largely 1080p+; expect SOME accuracy degradation at this
# size, but a non-zero center_in_target result is informative either way.
MAX_VISION_PIXELS = 768 * 576


@dataclass
class UGroundHandle:
    model: Any
    processor: Any
    device: str
    model_name: str = UGROUND_MODEL


_handle_lock = threading.Lock()
_handle: Optional[UGroundHandle] = None


def release_uground() -> bool:
    """Tear down the cached UGround handle and free its MPS / CUDA memory.

    Returns True if a handle was released. The Florence eval runner calls this
    before loading its own weights, since both models can't coexist on a 9 GB
    MPS budget.
    """
    global _handle
    if _handle is None:
        return False
    with _handle_lock:
        if _handle is None:
            return False
        try:
            import gc
            import torch
            device = _handle.device
            _handle.model = None
            _handle.processor = None
            _handle = None
            gc.collect()
            if device == "mps":
                try:
                    torch.mps.empty_cache()
                except Exception:
                    pass
            elif device == "cuda":
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass
            logger.info("released uground handle on %s", device)
        except Exception:
            _handle = None
    return True


def _resolve_device() -> str:
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_uground(model_name: str = UGROUND_MODEL) -> UGroundHandle:
    """Module-cached singleton. First call pays ~4GB weight load. fp32 on MPS."""
    global _handle
    if _handle is not None and _handle.model_name == model_name:
        return _handle
    with _handle_lock:
        if _handle is not None and _handle.model_name == model_name:
            return _handle

        import torch
        from transformers import AutoProcessor, Qwen2VLForConditionalGeneration

        device = _resolve_device()
        processor = AutoProcessor.from_pretrained(model_name, trust_remote_code=True)
        # UGround-V1-2B is ~8 GB at fp32, which blows MPS's allocation limit
        # before inference even starts. The model was trained and shipped in
        # bf16; bf16 inference is well-supported on Apple Silicon in recent
        # PyTorch and halves weight + activation memory. CPU stays at fp32
        # (no bf16 path) as a fallback.
        if device == "cpu":
            dtype = torch.float32
        else:
            dtype = torch.bfloat16
        model = Qwen2VLForConditionalGeneration.from_pretrained(
            model_name,
            torch_dtype=dtype,
            trust_remote_code=True,
        ).to(device).eval()

        _handle = UGroundHandle(model=model, processor=processor, device=device, model_name=model_name)
        logger.info("loaded uground: %s on %s (%s)", model_name, device, dtype)
    return _handle


_POINT_RE = re.compile(r"\(\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*\)")


def _parse_point(text: str) -> Optional[tuple[float, float]]:
    """Extract the first (x, y) tuple from UGround's text output.

    UGround's output is typically the bare string "(123, 456)" but we're
    permissive in case it gets chatty.
    """
    match = _POINT_RE.search(text or "")
    if not match:
        return None
    try:
        return float(match.group(1)), float(match.group(2))
    except ValueError:
        return None


def predict_point(
    *,
    handle: UGroundHandle,
    screenshot_path: str,
    element_query: str,
) -> dict[str, Any]:
    """Run UGround on one screenshot for one element query.

    Returns:
        {
          "point": {"x": float, "y": float} | None,   # original-image pixel coords
          "bbox":  {x, y, width, height} | None,      # synthetic POINT_BBOX_PX square around point
          "raw_response": str,                         # UGround's text decode
          "norm_point": (x_0_1000, y_0_1000) | None,   # the model's raw output before scaling
          "confidence": None,
          "latency_ms": int,
        }
    """
    import torch
    from PIL import Image

    started = time.perf_counter()
    path = Path(screenshot_path)
    if not path.exists():
        return {
            "point": None,
            "bbox": None,
            "raw_response": "",
            "norm_point": None,
            "confidence": None,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "error": f"screenshot not found: {screenshot_path}",
        }

    original_image = Image.open(path).convert("RGB")
    orig_w, orig_h = original_image.size

    # Downscale (preserving aspect ratio) so the Qwen2-VL vision encoder doesn't
    # blow MPS memory. We always scale UGround's 0-1000 normalized output back
    # against ORIGINAL (orig_w, orig_h) — not the resized dims — because the
    # approved bbox lives in original-image pixel space.
    if orig_w * orig_h > MAX_VISION_PIXELS:
        scale = (MAX_VISION_PIXELS / float(orig_w * orig_h)) ** 0.5
        new_w = max(64, int(orig_w * scale))
        new_h = max(64, int(orig_h * scale))
        image = original_image.resize((new_w, new_h), Image.LANCZOS)
    else:
        image = original_image
    img_w, img_h = orig_w, orig_h  # scaling target for the model's output points

    # Build the OpenAI-style chat message with image + text. The processor's
    # apply_chat_template will turn this into the right token sequence.
    messages = [
        {"role": "system", "content": UGROUND_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "image"},
                {"type": "text", "text": f"Description: {element_query.strip()}"},
            ],
        },
    ]
    prompt_text = handle.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

    inputs = handle.processor(
        text=[prompt_text],
        images=[image],
        return_tensors="pt",
        padding=True,
    ).to(handle.device)

    with torch.inference_mode():
        generated_ids = handle.model.generate(
            **inputs,
            max_new_tokens=MAX_NEW_TOKENS,
            do_sample=False,
        )

    # Release MPS activation memory before the next capture. Without this,
    # the cache holds onto vision-encoder workspace that grows with each call
    # and eventually overflows the 9 GB MPS limit during a batch eval.
    if handle.device == "mps":
        try:
            torch.mps.empty_cache()
        except Exception:
            pass

    # Strip the prompt prefix off the generation so we decode only the answer.
    prompt_len = inputs["input_ids"].shape[1]
    new_tokens = generated_ids[:, prompt_len:]
    raw = handle.processor.batch_decode(new_tokens, skip_special_tokens=True)[0]

    norm_point = _parse_point(raw)
    point: Optional[dict[str, float]] = None
    bbox: Optional[dict[str, float]] = None
    if norm_point is not None:
        nx, ny = norm_point
        # UGround outputs in [0, 1000); scale to pixel coords.
        px = nx / 1000.0 * img_w
        py = ny / 1000.0 * img_h
        point = {"x": px, "y": py}
        half = POINT_BBOX_PX / 2.0
        bbox = {
            "x": max(0.0, px - half),
            "y": max(0.0, py - half),
            "width": float(POINT_BBOX_PX),
            "height": float(POINT_BBOX_PX),
        }

    return {
        "point": point,
        "bbox": bbox,
        "raw_response": raw,
        "norm_point": list(norm_point) if norm_point is not None else None,
        "confidence": None,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }
