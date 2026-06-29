"""OmniParser smoke test — does it run at all on this machine?

Usage:
    python scripts/omniparser-smoke-test.py
    python scripts/omniparser-smoke-test.py --image path/to/screenshot.png

Pass condition: prints detected bbox count and latency. The detections do not
need to be correct — this script is here to surface dependency, hardware, or
weight-download problems before they get entangled with the platform backend.

Mirrors scripts/florence-smoke-test.py in shape. This is a throwaway: it does
NOT import from the controlplane-api package.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCREENSHOT_DIR = REPO_ROOT / "apps" / "mcp" / "output" / "observer-screenshots"

# OmniParser v2 — YOLO icon detector. Ships as model.pt (ultralytics-loadable).
# v1 ships icon_detect/ as safetensors, less directly loadable. Use v2.
OMNI_REPO = "microsoft/OmniParser-v2.0"
OMNI_WEIGHTS_FILE = "icon_detect/model.pt"


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
    sys.exit(
        "No screenshot to test against. Pass --image PATH or capture a screenshot first.",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--image",
        help="Path to a screenshot. Defaults to the most recent observer screenshot.",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=0.05,
        help="YOLO confidence threshold (lower = more bboxes). OmniParser README suggests 0.05.",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Device override: 'mps' | 'cuda' | 'cpu'. Auto-detects if omitted.",
    )
    args = parser.parse_args()

    # Import inside main so a missing dep prints a helpful error, not a stack trace.
    try:
        import torch
        from ultralytics import YOLO
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        sys.exit(
            f"Missing dep: {exc.name}. Run:\n"
            "  ~/Projects/agent-platform/.venv/bin/pip install ultralytics",
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
    print(f"[setup] image: {image_path}")
    print(f"[setup] device: {device}")
    print(
        f"[setup] downloading weights ({OMNI_REPO} / {OMNI_WEIGHTS_FILE}) "
        "— may take a few minutes first time"
    )

    t0 = time.perf_counter()
    weights_path = hf_hub_download(repo_id=OMNI_REPO, filename=OMNI_WEIGHTS_FILE)
    t_dl = time.perf_counter() - t0
    print(f"[setup] weights: {weights_path} (download {t_dl:.1f}s)")

    t0 = time.perf_counter()
    model = YOLO(weights_path)
    t_load = time.perf_counter() - t0
    print(f"[setup] model loaded in {t_load:.1f}s")

    t0 = time.perf_counter()
    # imgsz=1280 matches OmniParser's recommended inference size for UI screenshots
    results = model.predict(
        str(image_path),
        conf=args.conf,
        imgsz=1280,
        device=device,
        verbose=False,
    )
    t_infer = time.perf_counter() - t0

    if not results:
        print("[FAIL] no results returned")
        return 1

    boxes = results[0].boxes
    if boxes is None or len(boxes) == 0:
        print(f"[WARN] 0 detections at conf={args.conf}. Try lowering --conf.")
        return 0

    xyxy = boxes.xyxy.cpu().numpy().tolist()
    confs = boxes.conf.cpu().numpy().tolist()
    print(f"[ok] {len(xyxy)} detections in {t_infer * 1000:.0f}ms")
    for i, (box, c) in enumerate(zip(xyxy[:5], confs[:5])):
        x1, y1, x2, y2 = box
        print(f"  #{i + 1}: ({x1:.0f}, {y1:.0f}) {x2 - x1:.0f}x{y2 - y1:.0f}  conf={c:.2f}")
    if len(xyxy) > 5:
        print(f"  ... +{len(xyxy) - 5} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
