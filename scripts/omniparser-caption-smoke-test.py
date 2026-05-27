"""OmniParser caption-head smoke test — does the Florence-2 captioner run?

Usage:
    python scripts/omniparser-caption-smoke-test.py
    python scripts/omniparser-caption-smoke-test.py --image path/to/screenshot.png

Pipeline: pick a screenshot → run YOLO detector to get bboxes → crop the
top-confidence bbox → caption it with the OmniParser icon_caption Florence-2.

Pass condition: prints a non-empty caption for the cropped region. The caption
does not need to be correct — this script surfaces dependency / weight-download
problems before integrating the captioner into the live proposer.

Throwaway: does NOT import from controlplane-api or mcp-mock packages.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCREENSHOT_DIR = REPO_ROOT / "apps" / "mcp-mock" / "output" / "observer-screenshots"

OMNI_REPO = "microsoft/OmniParser-v2.0"
OMNI_DETECT_WEIGHTS = "icon_detect/model.pt"
# Florence-2 base provides the processor + model code; OmniParser's icon_caption
# subfolder provides the fine-tuned weights for UI element captioning.
FLORENCE_BASE = "microsoft/Florence-2-base"
ICON_CAPTION_SUBFOLDER = "icon_caption"


def pick_image(args_image: str | None) -> Path:
    if args_image:
        path = Path(args_image)
        if not path.exists():
            sys.exit(f"Image not found: {path}")
        return path
    if DEFAULT_SCREENSHOT_DIR.exists():
        candidates = sorted(
            DEFAULT_SCREENSHOT_DIR.glob("*.png"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return candidates[0]
    sys.exit("No screenshot to test against. Pass --image PATH or capture a screenshot first.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", help="Path to a screenshot.")
    parser.add_argument("--top-k", type=int, default=3, help="How many top-confidence bboxes to caption.")
    parser.add_argument("--device", default=None, help="'mps' | 'cuda' | 'cpu'.")
    args = parser.parse_args()

    try:
        import torch
        from PIL import Image
        from ultralytics import YOLO
        from huggingface_hub import hf_hub_download
        from transformers import AutoProcessor, AutoModelForCausalLM
    except ImportError as exc:
        sys.exit(
            f"Missing dep: {exc.name}. Run:\n"
            "  ~/Projects/agent-platform/.venv/bin/pip install ultralytics transformers einops timm",
        )

    device = args.device
    if device is None:
        if torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
        else:
            device = "cpu"

    image_path = pick_image(args.image)
    print(f"[setup] image: {image_path.name}")
    print(f"[setup] device: {device}")

    # Step 1: YOLO detection (reuse what the proposer already does)
    print("[step 1] running YOLO detection…")
    t0 = time.perf_counter()
    weights_path = hf_hub_download(repo_id=OMNI_REPO, filename=OMNI_DETECT_WEIGHTS)
    yolo = YOLO(weights_path)
    results = yolo.predict(str(image_path), conf=0.05, imgsz=1280, device=device, verbose=False)
    if not results or results[0].boxes is None or len(results[0].boxes) == 0:
        sys.exit("[FAIL] YOLO found 0 detections — try a different image")
    boxes = results[0].boxes
    xyxy = boxes.xyxy.cpu().numpy().tolist()
    confs = boxes.conf.cpu().numpy().tolist()
    # Top-K by confidence
    ranked = sorted(zip(xyxy, confs), key=lambda pair: -pair[1])[: args.top_k]
    print(f"[step 1] {len(xyxy)} detections in {(time.perf_counter() - t0) * 1000:.0f}ms; using top {len(ranked)}")

    # Step 2: load Florence-2 + OmniParser captioner weights
    print("[step 2] loading caption model (Florence-2 processor + OmniParser icon_caption weights)…")
    t0 = time.perf_counter()
    # Processor from base Florence-2 (provides the prompt/tokenizer/image-processing pipeline)
    processor = AutoProcessor.from_pretrained(FLORENCE_BASE, trust_remote_code=True)
    # Model with OmniParser's fine-tuned weights
    model = AutoModelForCausalLM.from_pretrained(
        OMNI_REPO,
        subfolder=ICON_CAPTION_SUBFOLDER,
        trust_remote_code=True,
        torch_dtype=torch.float32,  # MPS doesn't love fp16 for Florence; keep fp32 in smoke test
    ).to(device).eval()
    print(f"[step 2] caption model loaded in {time.perf_counter() - t0:.1f}s")

    # Step 3: caption each top-K crop
    img = Image.open(image_path).convert("RGB")
    print(f"[step 3] captioning {len(ranked)} crops…")
    for i, ((x1, y1, x2, y2), conf) in enumerate(ranked):
        crop = img.crop((int(x1), int(y1), int(x2), int(y2)))
        prompt = "<CAPTION>"
        t0 = time.perf_counter()
        with torch.inference_mode():
            inputs = processor(text=prompt, images=crop, return_tensors="pt").to(device)
            generated_ids = model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=20,
                do_sample=False,
                num_beams=3,
            )
        generated_text = processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
        parsed = processor.post_process_generation(
            generated_text, task="<CAPTION>", image_size=(crop.width, crop.height),
        )
        caption = parsed.get("<CAPTION>") or generated_text
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"  #{i + 1} bbox=({int(x1)},{int(y1)}) {int(x2 - x1)}x{int(y2 - y1)} conf={conf:.2f}  →  {caption!r}  ({elapsed:.0f}ms)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
