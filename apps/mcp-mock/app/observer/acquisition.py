from __future__ import annotations

from typing import Any, Optional

from app.utils.json_helpers import extract_json_from_mcp_text


def build_acquisition_input(
    *,
    js_capture: Optional[dict[str, Any]],
    accessibility_snapshot: Any,
    console_entries: list[dict[str, Any]],
    network_entries: list[dict[str, Any]],
    screenshot_refs: list[dict[str, Any]],
    capture_status: dict[str, Any],
    task_context: Optional[dict[str, Any]] = None,
    training_metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    js_capture = js_capture or {}
    page_identity = js_capture.get("page_identity", {})
    frame_state = js_capture.get("frame_state", {})
    viewport_state = js_capture.get("viewport_state", {})
    merged_training_metadata = {
        "url": page_identity.get("url"),
        "title": page_identity.get("title"),
        "viewport_width": viewport_state.get("viewport_width"),
        "viewport_height": viewport_state.get("viewport_height"),
        "device_scale_factor": viewport_state.get("device_scale_factor"),
        "scroll_x": viewport_state.get("scroll_x"),
        "scroll_y": viewport_state.get("scroll_y"),
    }
    merged_training_metadata.update(training_metadata or {})

    acquisition = {
        "page_identity": page_identity,
        "frame_state": frame_state,
        "accessibility_snapshot": _ensure_list(accessibility_snapshot),
        "actionable_elements": js_capture.get("actionable_elements", []),
        "regions": js_capture.get("regions", []),
        "dom_context": js_capture.get("dom_context", {}),
        "js_state": js_capture.get("js_state", {}),
        "console": filter_console_entries(console_entries),
        "network": limit_network_entries(network_entries, limit=50),
        "screenshots": screenshot_refs,
        "capture_status": capture_status,
    }
    if task_context:
        acquisition["task_context"] = task_context
    if viewport_state:
        acquisition["viewport_state"] = viewport_state
    if any(value is not None for value in merged_training_metadata.values()) or training_metadata:
        acquisition["training_metadata"] = merged_training_metadata
    return acquisition


def filter_console_entries(entries: Any) -> list[dict[str, Any]]:
    normalized = _ensure_list(entries)
    filtered: list[dict[str, Any]] = []
    for entry in normalized:
        if not isinstance(entry, dict):
            continue
        level = str(entry.get("level", "")).lower()
        if level in {"warning", "warn", "error"}:
            filtered.append(entry)
    return filtered


def limit_network_entries(entries: Any, *, limit: int) -> list[dict[str, Any]]:
    normalized = _ensure_list(entries)
    filtered = [entry for entry in normalized if isinstance(entry, dict)]
    return filtered[-limit:]


def build_empty_capture_status() -> dict[str, Any]:
    return {
        "js_state": build_capture_status(status="unavailable", details="not_collected"),
        "accessibility_snapshot": build_capture_status(status="unavailable", details="not_collected"),
        "console": build_capture_status(status="unavailable", details="not_collected"),
        "network": build_capture_status(status="unavailable", details="not_collected"),
        "screenshot": build_capture_status(status="unavailable", details="not_collected"),
    }


def normalize_capture_tool_payload(result: Any) -> Any:
    content = getattr(result, "content", []) or []
    text_parts: list[str] = []
    structured_parts: list[Any] = []

    for item in content:
        if hasattr(item, "data"):
            structured_parts.append(item.data)
        elif hasattr(item, "text"):
            text_parts.append(item.text)

    if structured_parts:
        if len(structured_parts) == 1:
            return structured_parts[0]
        return structured_parts

    if text_parts:
        raw_text = "\n".join(text_parts)
        try:
            return extract_json_from_mcp_text(raw_text)
        except Exception:
            return {"raw_text": raw_text}

    return {}


def build_capture_status(
    *,
    status: str,
    tool: Optional[str] = None,
    details: Optional[str] = None,
) -> dict[str, Any]:
    payload = {"status": status}
    if tool:
        payload["tool"] = tool
    if details:
        payload["details"] = details
    return payload


def _ensure_list(payload: Any) -> list[Any]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("entries", "messages", "requests", "items", "nodes"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return [payload]
    return []
