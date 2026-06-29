import base64
import imghdr
import json
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.observer.pipeline import build_observer_artifact


ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "output" / "observer-traces"
SCREENSHOTS_DIR = Path(__file__).resolve().parent.parent / "output" / "observer-screenshots"


def build_observation_artifact(
    *,
    source: str,
    scenario: Optional[str],
    acquisition: dict[str, Any],
    pipeline_run: dict[str, Any],
    timestamp: Optional[str] = None,
) -> dict[str, Any]:
    return build_observer_artifact(
        source=source,
        scenario=scenario,
        acquisition=acquisition,
        pipeline_run=pipeline_run,
        timestamp=timestamp,
    )


def write_observation_artifact(
    artifact: dict[str, Any],
    *,
    output_dir: Optional[Path] = None,
) -> Path:
    target_dir = output_dir or ARTIFACTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = artifact["metadata"]["timestamp"].replace(":", "-")
    scenario = artifact["metadata"].get("scenario", "capture")
    source = artifact["metadata"].get("source", "unknown")
    filename = f"{timestamp}__{source}__{scenario}.json"
    path = target_dir / filename
    path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return path


def write_screenshot_asset(
    screenshot_payload: dict[str, Any],
    *,
    scenario: str,
    output_dir: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    image_bytes = _extract_screenshot_bytes(screenshot_payload)
    if image_bytes is None:
        return None

    target_dir = output_dir or SCREENSHOTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).isoformat().replace(":", "-")
    extension = _screenshot_extension(screenshot_payload.get("mime_type"))
    filename = f"{timestamp}__{scenario}.{extension}"
    path = target_dir / filename
    path.write_bytes(image_bytes)
    width, height = _image_dimensions(
        image_bytes,
        mime_type=screenshot_payload.get("mime_type"),
    )

    return {
        "filename": filename,
        "path": str(path),
        "image_path": str(path),
        "mime_type": screenshot_payload.get("mime_type", "image/png"),
        "label": screenshot_payload.get("label", "page_screenshot"),
        "width": screenshot_payload.get("width") or width,
        "height": screenshot_payload.get("height") or height,
        "shot_type": screenshot_payload.get("shot_type", "viewport"),
    }


def _extract_screenshot_bytes(screenshot_payload: dict[str, Any]) -> Optional[bytes]:
    if screenshot_payload.get("bytes") is not None:
        return screenshot_payload["bytes"]

    if screenshot_payload.get("data_base64"):
        return base64.b64decode(screenshot_payload["data_base64"])

    data_url = screenshot_payload.get("data_url")
    if data_url and "," in data_url:
        _, encoded = data_url.split(",", 1)
        return base64.b64decode(encoded)

    return None


def _screenshot_extension(mime_type: Optional[str]) -> str:
    if mime_type == "image/jpeg":
        return "jpg"
    return "png"


def _image_dimensions(image_bytes: bytes, *, mime_type: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    detected_type = mime_type or ""
    if detected_type == "image/png" or imghdr.what(None, image_bytes) == "png":
        return _png_dimensions(image_bytes)
    if detected_type == "image/jpeg" or imghdr.what(None, image_bytes) == "jpeg":
        return _jpeg_dimensions(image_bytes)
    return None, None


def _png_dimensions(image_bytes: bytes) -> tuple[Optional[int], Optional[int]]:
    if len(image_bytes) < 24 or image_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        return None, None
    width, height = struct.unpack(">II", image_bytes[16:24])
    return int(width), int(height)


def _jpeg_dimensions(image_bytes: bytes) -> tuple[Optional[int], Optional[int]]:
    if len(image_bytes) < 4 or image_bytes[:2] != b"\xff\xd8":
        return None, None

    offset = 2
    while offset + 9 < len(image_bytes):
        if image_bytes[offset] != 0xFF:
            offset += 1
            continue

        marker = image_bytes[offset + 1]
        offset += 2

        if marker in {0xD8, 0xD9}:
            continue

        if offset + 2 > len(image_bytes):
            break

        segment_length = struct.unpack(">H", image_bytes[offset:offset + 2])[0]
        if segment_length < 2 or offset + segment_length > len(image_bytes):
            break

        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if offset + 7 > len(image_bytes):
                break
            height, width = struct.unpack(">HH", image_bytes[offset + 3:offset + 7])
            return int(width), int(height)

        offset += segment_length

    return None, None
