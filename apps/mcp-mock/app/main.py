import json
from datetime import datetime, timezone
from typing import Any, Optional

from mcp import ClientSession
from mcp.client.stdio import stdio_client

from app.artifacts import (
    build_observation_artifact,
    write_observation_artifact,
    write_screenshot_asset,
)
from app.mcp_client import build_server_params
from app.observer.acquisition import (
    build_acquisition_input,
    build_capture_status,
    normalize_capture_tool_payload,
)
from app.observer.pipeline import run_pipeline


JS_CAPTURE_STATE = """
() => {
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' &&
      style.visibility !== 'hidden' &&
      style.opacity !== '0' &&
      rect.width > 0 &&
      rect.height > 0;
  };

  const nearestRegion = (el) => {
    const region = el.closest('main, nav, aside, header, footer, section, form, dialog, [role="dialog"], [role="navigation"], [role="main"], [role="complementary"], [role="search"], [role="tablist"], [role="menu"], [data-pagelet]');
    if (!region) return null;
    const rect = region.getBoundingClientRect();
    return {
      uid: stableUid(region, -1),
      tag: region.tagName.toLowerCase(),
      role: region.getAttribute('role') || '',
      label: region.getAttribute('aria-label') || '',
      id: region.id || '',
      className: typeof region.className === 'string' ? region.className : '',
      rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
    };
  };

  const nearbyText = (el) => {
    const parent = el.parentElement;
    if (!parent) return '';
    return (parent.innerText || '').trim().slice(0, 180);
  };

  const stableUid = (el, index) => {
    const id = el.id ? `id:${el.id}` : '';
    const name = el.getAttribute('name') ? `name:${el.getAttribute('name')}` : '';
    const role = el.getAttribute('role') ? `role:${el.getAttribute('role')}` : '';
    const tag = el.tagName.toLowerCase();
    return [tag, id, name, role, `idx:${index}`].filter(Boolean).join('|');
  };

  const actionableSelector = [
    'a[href]',
    'button',
    'input',
    'textarea',
    'select',
    '[role="button"]',
    '[role="link"]',
    '[role="textbox"]',
    '[role="tab"]',
    '[role="menuitem"]',
    '[role="option"]',
    '[contenteditable="true"]'
  ].join(',');

  const actionableElements = [...document.querySelectorAll(actionableSelector)]
    .slice(0, 250)
    .map((el, index) => {
      const rect = el.getBoundingClientRect();
      return {
        uid: stableUid(el, index),
        tag: el.tagName.toLowerCase(),
        type: el.getAttribute('type') || '',
        role: el.getAttribute('role') || '',
        name: el.getAttribute('name') || '',
        label: el.getAttribute('aria-label') || el.getAttribute('title') || '',
        text: (el.innerText || el.value || '').trim().slice(0, 120),
        placeholder: el.getAttribute('placeholder') || '',
        href: el.href || '',
        disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
        hidden: el.type === 'hidden' || el.getAttribute('aria-hidden') === 'true',
        visible: isVisible(el),
        user_facing: isVisible(el) && el.type !== 'hidden',
        checked: typeof el.checked === 'boolean' ? el.checked : null,
        expanded: el.getAttribute('aria-expanded'),
        selected: el.getAttribute('aria-selected'),
        value: typeof el.value === 'string' ? el.value.slice(0, 120) : '',
        rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
        parent_tag: el.parentElement ? el.parentElement.tagName.toLowerCase() : null,
        parent_role: el.parentElement ? (el.parentElement.getAttribute('role') || '') : null,
        nearby_context: nearbyText(el),
        region: nearestRegion(el)
      };
    });

  const collectRegions = () => {
    const candidates = [...document.querySelectorAll('header, nav, main, aside, footer, form, dialog, section, [role="banner"], [role="navigation"], [role="main"], [role="complementary"], [role="dialog"], [role="search"], [role="tablist"], [role="menu"], [data-pagelet]')];
    return candidates.slice(0, 80).map((el, index) => {
      const rect = el.getBoundingClientRect();
      return {
        uid: stableUid(el, index),
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute('role') || '',
        label: el.getAttribute('aria-label') || '',
        id: el.id || '',
        className: typeof el.className === 'string' ? el.className : '',
        visible: isVisible(el),
        text: (el.innerText || '').trim().slice(0, 180),
        rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
      };
    });
  };

  return {
    page_identity: {
      title: document.title,
      url: location.href
    },
    viewport_state: {
      viewport_width: window.innerWidth,
      viewport_height: window.innerHeight,
      device_scale_factor: window.devicePixelRatio || 1,
      scroll_x: window.scrollX || 0,
      scroll_y: window.scrollY || 0
    },
    frame_state: {
      frame_count: window.frames.length,
      dialog_present: !!document.querySelector('dialog,[role="dialog"],[aria-modal="true"]'),
      active_element: document.activeElement ? {
        tag: document.activeElement.tagName.toLowerCase(),
        type: document.activeElement.getAttribute('type') || '',
        aria_label: document.activeElement.getAttribute('aria-label') || '',
        text: (document.activeElement.innerText || document.activeElement.value || '').trim().slice(0, 120)
      } : null
    },
    actionable_elements: actionableElements,
    regions: collectRegions(),
    dom_context: {
      headings: [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')]
        .map((el) => (el.innerText || '').trim())
        .filter(Boolean)
        .slice(0, 40),
      dialogs: [...document.querySelectorAll('dialog,[role="dialog"],[aria-modal="true"]')]
        .slice(0, 10)
        .map((el, index) => ({
          uid: stableUid(el, index),
          text: (el.innerText || '').trim().slice(0, 180),
          visible: isVisible(el)
        })),
      landmarks: [...document.querySelectorAll('header, nav, main, aside, footer, form')]
        .slice(0, 30)
        .map((el, index) => ({
          uid: stableUid(el, index),
          tag: el.tagName.toLowerCase(),
          role: el.getAttribute('role') || '',
          label: el.getAttribute('aria-label') || ''
        }))
    },
    js_state: {
      ready_state: document.readyState,
      location_href: location.href,
      title: document.title,
      forms_count: document.querySelectorAll('form').length,
      inputs_count: document.querySelectorAll('input, textarea, select').length,
      links_count: document.querySelectorAll('a[href]').length,
      buttons_count: document.querySelectorAll('button, input[type="button"], input[type="submit"]').length,
      selection_text: window.getSelection ? String(window.getSelection()).slice(0, 120) : '',
      body_text_preview: (document.body && document.body.innerText ? document.body.innerText.trim().slice(0, 500) : '')
    }
  };
}
"""


TOOL_CANDIDATES = {
    "accessibility_snapshot": [
        {"name": "get_accessibility_tree", "params": {}},
        {"name": "accessibility_snapshot", "params": {}},
        {"name": "take_snapshot", "params": {"format": "accessibility"}},
        {"name": "snapshot_accessibility_tree", "params": {}},
    ],
    "console": [
        {"name": "list_console_messages", "params": {}},
        {"name": "get_console_messages", "params": {}},
        {"name": "console_messages", "params": {}},
    ],
    "network": [
        {"name": "list_network_requests", "params": {}},
        {"name": "get_network_requests", "params": {}},
        {"name": "network_requests", "params": {}},
    ],
    "screenshot": [
        {"name": "take_screenshot", "params": {"format": "png"}},
        {"name": "capture_screenshot", "params": {"format": "png"}},
    ],
}


async def observe_live_capture(
    *,
    scenario: str = "live_capture",
    screenshot_output_dir=None,
    tab_id: Optional[str] = None,
    tab_url: Optional[str] = None,
    browser_url: str = "http://127.0.0.1:9222",
    task_context: Optional[dict[str, Any]] = None,
    training_metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    async with stdio_client(build_server_params(browser_url)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if tab_id is not None:
                await _select_tab(session, tab_id)
            # Verify we're on the intended tab, not the control panel
            await _verify_target_tab(session, expected_url=tab_url)
            return await capture_observation(
                session,
                scenario=scenario,
                screenshot_output_dir=screenshot_output_dir,
                task_context=task_context,
                training_metadata=training_metadata,
            )


async def _select_tab(session: ClientSession, tab_id: str) -> None:
    """Find the MCP page handle matching the CDP tab id, then select it."""
    try:
        pages_result = await session.call_tool("list_pages", {})
        pages_payload = normalize_capture_tool_payload(pages_result)
        pages = pages_payload if isinstance(pages_payload, list) else []
    except Exception:
        # list_pages may not be available; fall through to capture on default tab
        return

    if not pages:
        # MCP server hasn't enumerated pages yet — proceed on default tab
        return

    matched_page_id = None
    for page in pages:
        if str(page.get("targetId", "")) == tab_id:
            matched_page_id = page.get("pageId")
            break
        if str(page.get("pageId", "")) == tab_id:
            matched_page_id = page.get("pageId")
            break

    if matched_page_id is not None:
        await session.call_tool("select_page", {"pageId": matched_page_id})


async def _verify_target_tab(session: ClientSession, *, expected_url: Optional[str] = None) -> None:
    """Run a lightweight JS check to confirm we're on the intended page, not the control panel."""
    try:
        result = await session.call_tool("evaluate_script", {"function": "() => ({ url: location.href, title: document.title })"})
        payload = normalize_capture_tool_payload(result)
        current_url = payload.get("url", "") if isinstance(payload, dict) else ""

        # Reject if we accidentally landed on the control panel
        if "localhost:5173" in current_url or "localhost:3000" in current_url:
            raise RuntimeError(
                f"Capture is targeting the control panel UI ({current_url}), not the intended page."
                f"{' Expected: ' + expected_url if expected_url else ''}"
                " Select a different tab."
            )

        if expected_url and expected_url not in current_url:
            # Warn but don't block — URL may have redirected
            import logging
            logging.getLogger(__name__).warning(
                "Tab verification: expected URL containing %r but got %r", expected_url, current_url
            )
    except RuntimeError:
        raise
    except Exception:
        # Verification is best-effort; don't block capture if JS eval fails
        pass


async def run_observer():
    artifact = await observe_live_capture()
    write_observation_artifact(artifact)
    print(json.dumps(artifact, indent=2))


async def capture_observation(
    session: ClientSession,
    *,
    scenario: str,
    screenshot_output_dir=None,
    task_context: Optional[dict[str, Any]] = None,
    training_metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    capture_status: dict[str, Any] = {}

    js_capture = await _capture_js_state(session, capture_status)
    accessibility_snapshot = await _capture_generic(session, "accessibility_snapshot", capture_status)
    console_entries = await _capture_generic(session, "console", capture_status)
    network_entries = await _capture_generic(session, "network", capture_status)
    screenshot_payload = await _capture_screenshot(session, capture_status)

    screenshot_refs: list[dict[str, Any]] = []
    if screenshot_payload is not None:
        screenshot_ref = write_screenshot_asset(
            screenshot_payload,
            scenario=scenario,
            output_dir=screenshot_output_dir,
        )
        if screenshot_ref:
            screenshot_refs.append(screenshot_ref)
        else:
            capture_status["screenshot"] = build_capture_status(
                status="failed",
                details="Screenshot payload was returned but could not be persisted.",
            )

    acquisition = build_acquisition_input(
        js_capture=js_capture,
        accessibility_snapshot=accessibility_snapshot,
        console_entries=console_entries,
        network_entries=network_entries,
        screenshot_refs=screenshot_refs,
        capture_status=capture_status,
        task_context=task_context,
        training_metadata=training_metadata,
    )

    # Pre-compute a stable timestamp for both the artifact filename and region_scorer derived outputs
    capture_ts = datetime.now(timezone.utc).isoformat()
    artifact_stem = f"{capture_ts.replace(':', '-')}__live_mcp__{scenario}"

    pipeline_config: dict[str, Any] = {"_artifact_stem": artifact_stem}
    if task_context:
        pipeline_config["task_context"] = task_context

    pipeline_run = run_pipeline(acquisition, config=pipeline_config)

    return build_observation_artifact(
        source="live_mcp",
        scenario=scenario,
        acquisition=acquisition,
        pipeline_run=pipeline_run,
        timestamp=capture_ts,
    )


async def _capture_js_state(session: ClientSession, capture_status: dict[str, Any]) -> dict[str, Any]:
    try:
        result = await session.call_tool("evaluate_script", {"function": JS_CAPTURE_STATE})
        payload = normalize_capture_tool_payload(result)
        if not isinstance(payload, dict):
            payload = {}
        capture_status["js_state"] = build_capture_status(status="success", tool="evaluate_script")
        return payload
    except Exception as exc:
        capture_status["js_state"] = build_capture_status(
            status="failed",
            tool="evaluate_script",
            details=str(exc),
        )
        return {}


async def _capture_generic(session: ClientSession, capture_key: str, capture_status: dict[str, Any]) -> Any:
    errors: list[str] = []
    for candidate in TOOL_CANDIDATES[capture_key]:
        try:
            result = await session.call_tool(candidate["name"], candidate["params"])
            payload = normalize_capture_tool_payload(result)
            capture_status[capture_key] = build_capture_status(
                status="success",
                tool=candidate["name"],
            )
            return payload
        except Exception as exc:
            errors.append(f"{candidate['name']}: {exc}")

    capture_status[capture_key] = build_capture_status(
        status="unavailable",
        details=" | ".join(errors) if errors else "No supported tool candidates were available.",
    )
    return [] if capture_key in {"console", "network", "accessibility_snapshot"} else {}


async def _capture_screenshot(session: ClientSession, capture_status: dict[str, Any]) -> Optional[dict[str, Any]]:
    errors: list[str] = []
    for candidate in TOOL_CANDIDATES["screenshot"]:
        try:
            result = await session.call_tool(candidate["name"], candidate["params"])
            # Try MCP ImageContent extraction first (base64 data + mimeType attrs)
            screenshot_payload = _extract_image_content(result)
            if screenshot_payload is None:
                # Fall back to generic normalization
                payload = normalize_capture_tool_payload(result)
                screenshot_payload = _normalize_screenshot_payload(payload)
            if screenshot_payload is not None:
                capture_status["screenshot"] = build_capture_status(
                    status="success",
                    tool=candidate["name"],
                )
                return screenshot_payload
            errors.append(f"{candidate['name']}: unsupported screenshot payload")
        except Exception as exc:
            errors.append(f"{candidate['name']}: {exc}")

    capture_status["screenshot"] = build_capture_status(
        status="unavailable",
        details=" | ".join(errors) if errors else "No screenshot tool was available.",
    )
    return None


def _extract_image_content(result: Any) -> Optional[dict[str, Any]]:
    """Extract screenshot from MCP ImageContent blocks (type='image', .data, .mimeType)."""
    content = getattr(result, "content", []) or []
    for item in content:
        item_type = getattr(item, "type", None)
        if item_type == "image":
            data = getattr(item, "data", None)
            if data:
                mime_type = getattr(item, "mimeType", None) or getattr(item, "mime_type", None) or "image/png"
                return {
                    "data_base64": data,
                    "mime_type": mime_type,
                    "label": "page_screenshot",
                }
    return None


def _normalize_screenshot_payload(payload: Any) -> Optional[dict[str, Any]]:
    # Raw base64 string (from normalize_capture_tool_payload extracting .data)
    if isinstance(payload, str) and len(payload) > 100:
        return {
            "data_base64": payload,
            "mime_type": "image/png",
            "label": "page_screenshot",
        }
    if isinstance(payload, dict):
        if payload.get("data_base64"):
            return {
                "data_base64": payload["data_base64"],
                "mime_type": payload.get("mime_type", "image/png"),
                "width": payload.get("width"),
                "height": payload.get("height"),
                "label": payload.get("label", "page_screenshot"),
            }
        if payload.get("data"):
            return {
                "data_base64": payload["data"],
                "mime_type": payload.get("mime_type", payload.get("mimeType", "image/png")),
                "width": payload.get("width"),
                "height": payload.get("height"),
                "label": payload.get("label", "page_screenshot"),
            }
        if payload.get("data_url"):
            return {
                "data_url": payload["data_url"],
                "mime_type": payload.get("mime_type", "image/png"),
                "width": payload.get("width"),
                "height": payload.get("height"),
                "label": payload.get("label", "page_screenshot"),
            }
    return None
