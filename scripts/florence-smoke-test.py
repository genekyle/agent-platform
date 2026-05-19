"""Florence-2-base smoke test — does it run at all on this machine?

Usage:
    python scripts/florence-smoke-test.py
    python scripts/florence-smoke-test.py --image path/to/screenshot.png --query "the Apply Now button"

Pass condition: prints a bounding box and a latency. The bbox does not need to
be correct — this script is here to surface dependency, hardware, or weight-
download problems before they get entangled with the platform backend.

This is a throwaway. It does NOT import from the controlplane-api package.
"""

from __future__ import annotations

import argparse
import io
import sys
import time
import urllib.request
from pathlib import Path


STOCK_IMAGE_URL = (
    "https://huggingface.co/datasets/huggingface/documentation-images/"
    "resolve/main/transformers/tasks/car.jpg"
)
STOCK_QUERY = "a green car"


def pick_device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_image(image_arg: str | None):
    from PIL import Image

    if image_arg is None:
        print(f"[load] downloading stock image: {STOCK_IMAGE_URL}")
        with urllib.request.urlopen(STOCK_IMAGE_URL) as response:
            image_bytes = response.read()
        return Image.open(io.BytesIO(image_bytes)).convert("RGB")

    path = Path(image_arg).expanduser()
    if not path.exists():
        sys.exit(f"image not found: {path}")
    print(f"[load] reading local image: {path}")
    return Image.open(path).convert("RGB")


def main() -> int:
    parser = argparse.ArgumentParser(description="Florence-2-base smoke test.")
    parser.add_argument(
        "--image",
        default=None,
        help="Path to a local image. If omitted, downloads a stock image from HuggingFace.",
    )
    parser.add_argument(
        "--query",
        default=STOCK_QUERY,
        help=f"Phrase to ground in the image. Default: {STOCK_QUERY!r}",
    )
    parser.add_argument(
        "--model",
        default="microsoft/Florence-2-base",
        help="HuggingFace model id. Default: microsoft/Florence-2-base.",
    )
    args = parser.parse_args()

    print("[deps] importing torch / transformers / PIL ...")
    try:
        import torch  # noqa: F401
        from PIL import Image  # noqa: F401
        from transformers import AutoModelForCausalLM, AutoProcessor
    except ImportError as exc:
        sys.exit(
            f"missing dependency: {exc}\n"
            f"run: pip install -r apps/controlplane-api/requirements.txt"
        )

    device = pick_device()
    print(f"[device] using {device}")

    image = load_image(args.image)
    print(f"[image] size = {image.size}")

    print(f"[model] loading {args.model} (first run downloads ~460MB) ...")
    load_start = time.perf_counter()
    processor = AutoProcessor.from_pretrained(args.model, trust_remote_code=True)
    # attn_implementation="eager" bypasses the SDPA dispatch check that newer
    # transformers performs but Florence-2's custom modeling code predates.
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        trust_remote_code=True,
        attn_implementation="eager",
    ).to(device)
    model.eval()
    load_ms = int((time.perf_counter() - load_start) * 1000)
    print(f"[model] loaded in {load_ms} ms")

    task_prompt = "<CAPTION_TO_PHRASE_GROUNDING>"
    prompt = f"{task_prompt} {args.query}"
    print(f"[infer] query = {args.query!r}")

    infer_start = time.perf_counter()
    inputs = processor(text=prompt, images=image, return_tensors="pt").to(device)
    with __import__("torch").inference_mode():
        generated_ids = model.generate(
            input_ids=inputs["input_ids"],
            pixel_values=inputs["pixel_values"],
            max_new_tokens=1024,
            num_beams=3,
            do_sample=False,
        )
    generated_text = processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
    parsed = processor.post_process_generation(
        generated_text, task=task_prompt, image_size=image.size
    )
    infer_ms = int((time.perf_counter() - infer_start) * 1000)

    print(f"[infer] latency = {infer_ms} ms")
    print(f"[infer] raw response = {generated_text}")
    print(f"[infer] parsed = {parsed}")

    grounding = parsed.get(task_prompt, {}) if isinstance(parsed, dict) else {}
    bboxes = grounding.get("bboxes", []) if isinstance(grounding, dict) else []
    if not bboxes:
        print("[result] FAIL — no bboxes returned")
        return 2

    first = bboxes[0]
    print(f"[result] PASS — first bbox = {first}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
