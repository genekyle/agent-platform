"""TrajectoryDriver interface + DirectDriver + driver factory.

A driver receives an ActionRequest (intent + target, in SCREENSHOT pixels) and
executes it against the live page over raw CDP. The screenshot→CSS conversion is
the one correctness-critical bit: AX bboxes are device pixels (CSS × DPR), but
CDP `Input.dispatchMouseEvent` wants CSS pixels — so we divide the target center
by the device_scale_factor before dispatching.

Reuses the CDP plumbing from the AX proposer (same endpoint, same session shape).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("mcp.executor")


@dataclass
class ActionRequest:
    """Selector → executor handoff. bbox is in SCREENSHOT pixels (as produced by
    the AX proposer); device_scale_factor converts it to CSS px for CDP."""
    action_id: str                      # click | type | select | scroll | submit | clear | upload
    target_bbox: dict[str, float]       # {x, y, width, height} screenshot px
    backend_node_id: Optional[int] = None
    value: Optional[str] = None
    files: Optional[list[str]] = None   # absolute local paths for an `upload` action (file input)
    device_scale_factor: float = 1.0


@dataclass
class ExecResult:
    ok: bool
    driver: str
    action_id: str
    css_point: Optional[tuple[float, float]] = None
    path_points: int = 0
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def target_css_point(bbox: dict[str, float], device_scale_factor: float = 1.0) -> tuple[float, float]:
    """Center of the target bbox in CSS pixels (CDP input space). Pure/testable."""
    dpr = device_scale_factor or 1.0
    cx = (float(bbox.get("x", 0)) + float(bbox.get("width", 0)) / 2) / dpr
    cy = (float(bbox.get("y", 0)) + float(bbox.get("height", 0)) / 2) / dpr
    return (round(cx, 2), round(cy, 2))


def parse_scroll_value(value: Optional[str], default: float = 600.0) -> float:
    """Parse a scroll spec into signed CSS px (DOWN positive, matching CDP wheel deltaY). Accepts
    'down'/'up' (± default), a bare number of px, or 'down 1000' / 'up 300'. Pure/testable."""
    if value is None:
        return default
    s = str(value).strip().lower()
    sign = -1.0 if s.startswith("up") else 1.0
    s = s.replace("up", "").replace("down", "").strip()
    try:
        n = abs(float(s)) if s else default
    except ValueError:
        n = default
    return sign * n


class TrajectoryDriver(ABC):
    name = "base"

    @abstractmethod
    async def move_and_act(
        self, *, browser_url: str, request: ActionRequest,
        tab_id: Optional[str] = None, tab_url: Optional[str] = None,
        start: Optional[tuple[float, float]] = None,
    ) -> ExecResult: ...

    async def _click_sequence(self, cdp, x: float, y: float, path: Optional[list[tuple[float, float]]] = None) -> None:
        """Optional pre-move along `path` (CSS px), then a left click at (x,y)."""
        for px, py in (path or []):
            await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": px, "y": py})
        await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        await cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        await cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})

    async def _path_to(self, x: float, y: float, start: Optional[tuple[float, float]] = None):
        """Cursor path (CSS px) to (x,y). Base = teleport (no path); the motion drivers
        (MinimumJerk / Humanized) override to synthesize a smooth / human-shaped curve."""
        return None

    async def _approach(self, cdp, x: float, y: float, start: Optional[tuple[float, float]] = None) -> None:
        """Move the cursor to (x,y) along this driver's path BEFORE a node-based action — the
        anti-bot motion signal for the robust `backend_node_id` path (which otherwise never moves the
        mouse). No-op for DirectDriver (no path → teleport, preserving the record-only/test baseline);
        Humanized/MinJerk wiggle to the point. The node action itself stays native (robust)."""
        path = await self._path_to(x, y, start)
        for px, py in (path or []):
            await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": px, "y": py})
        if path:
            await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})

    async def _apply_value(self, cdp, request: ActionRequest) -> None:
        """For type/select/clear actions, apply the value after focusing via click."""
        if request.action_id in ("type", "select") and request.value:
            await cdp.send("Input.insertText", {"text": request.value})
        elif request.action_id == "clear":
            # select-all + delete (focused field assumed)
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "modifiers": 2, "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65})
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "modifiers": 2, "key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65})
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Delete", "code": "Delete", "windowsVirtualKeyCode": 46})
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Delete", "code": "Delete", "windowsVirtualKeyCode": 46})

    async def _element_act(self, cdp, request: ActionRequest, start: Optional[tuple[float, float]] = None) -> str:
        """ELEMENT-BASED action via backend_node_id — robust to spacing/scroll/layout where
        coordinate-clicking fails (closely-spaced radios, off-screen fields). We resolve the node,
        scroll it into view, measure its FRESH centre, APPROACH it with the driver's human-shaped
        mouse path (the anti-bot motion signal — no-op for DirectDriver, a wiggle for Humanized), then
        drive it natively: .click() for click/select, focus + driver-specific typing for type. The
        native action keeps the 'clicked the No 25px below Yes' robustness; the approach + cadence add
        the human trajectory that node-driving used to skip entirely."""
        await cdp.send("DOM.enable")
        await cdp.send("Runtime.enable")
        resolved = await cdp.send("DOM.resolveNode", {"backendNodeId": request.backend_node_id})
        object_id = resolved["object"]["objectId"]

        async def call(fn: str) -> None:
            await cdp.send("Runtime.callFunctionOn",
                           {"objectId": object_id, "functionDeclaration": fn, "awaitPromise": False})

        # UPLOAD: set files directly on a <input type=file> node — no click (the OS file dialog a
        # click opens can't be driven over CDP). Paths must be ABSOLUTE and local to this machine.
        if request.action_id == "upload":
            await cdp.send("DOM.setFileInputFiles",
                           {"backendNodeId": request.backend_node_id, "files": list(request.files or [])})
            return "upload"

        # Scroll into view + measure the node's own centre (CSS px). A fresh per-node measurement is
        # more accurate than any pre-recorded bbox, so the human motion lands on the real element.
        center = await cdp.send("Runtime.callFunctionOn", {
            "objectId": object_id, "returnByValue": True,
            "functionDeclaration": ("function(){ this.scrollIntoView({block:'center',inline:'center'});"
                                    " const r=this.getBoundingClientRect();"
                                    " return {x:r.x+r.width/2, y:r.y+r.height/2}; }")})
        pt = (center.get("result") or {}).get("value") or {}
        if "x" in pt and "y" in pt:
            await self._approach(cdp, float(pt["x"]), float(pt["y"]), start)

        if request.action_id == "select" and request.value:
            await self._select_option(cdp, object_id, request.value)
        elif request.action_id == "type" and request.value:
            await call("function(){ this.focus(); }")
            await self._apply_value(cdp, request)   # driver-specific: humanized cadence, or single insertText
        elif request.action_id == "clear":
            await call("function(){ this.focus(); if(this.select)this.select(); }")
            await self._apply_value(cdp, request)
        else:  # click / submit / default
            await call("function(){ this.click(); }")
        return "element"

    def _scroll_plan(self, total: float) -> list[tuple[float, float]]:
        """Break a vertical scroll of `total` CSS px into (deltaY, pause_seconds) steps. Base = one
        instant wheel notch (robotic baseline). HumanizedDriver overrides to several eased, jittered
        notches with short read-pauses — a human trackpad/wheel signal, not a teleport."""
        return [(total, 0.0)]

    async def _do_scroll(self, cdp, request: ActionRequest) -> str:
        """Vertical wheel scroll over a viewport point. `request.value` = 'down'/'up' (default ~600px),
        a number of px, or 'down 1000'. Positive deltaY scrolls down. Dispatches this driver's plan."""
        import asyncio
        total = parse_scroll_value(request.value)
        x, y = target_css_point(request.target_bbox, request.device_scale_factor)
        if not x and not y:  # no anchor bbox → scroll over mid-viewport
            x, y = 400.0, 400.0
        for delta, pause in self._scroll_plan(total):
            await cdp.send("Input.dispatchMouseEvent",
                           {"type": "mouseWheel", "x": x, "y": y, "deltaX": 0, "deltaY": delta})
            if pause:
                await asyncio.sleep(pause)
        return "scroll"

    async def _select_option(self, cdp, object_id: str, value: str) -> None:
        """Choose `value` in a dropdown — handles BOTH a native <select> (set value +
        dispatch change) and a custom ARIA combobox (click to open, then click the option
        whose text matches). Custom-combobox options often render in a portal at the
        document root, so phase 2 searches the whole document, not just the node."""
        import asyncio

        phase = await cdp.send("Runtime.callFunctionOn", {
            "objectId": object_id, "returnByValue": True,
            "arguments": [{"value": value}],
            "functionDeclaration": (
                "function(v){"
                " v=(v||'').toLowerCase();"
                " if(this.tagName==='SELECT'){"
                "   const o=[...this.options].find(x=>x.text.trim().toLowerCase().includes(v));"
                "   if(o){this.value=o.value;this.dispatchEvent(new Event('change',{bubbles:true}));return 'native';}"
                "   return 'native_notfound';}"
                " this.scrollIntoView({block:'center'}); this.click(); return 'opened';"
                "}"),
        })
        if (phase.get("result") or {}).get("value") != "opened":
            return  # native <select> already handled (or not found)

        await asyncio.sleep(0.4)  # let the listbox/portal render
        import json as _json
        v = _json.dumps(value)
        await cdp.send("Runtime.evaluate", {"returnByValue": True, "expression": (
            "(()=>{const v=" + v + ".toLowerCase();"
            "const opts=[...document.querySelectorAll('[role=option],li[role=option],"
            "[role=listbox] li,[role=menuitem],ul[role=listbox] [role=option]')];"
            "const o=opts.find(x=>(x.innerText||'').trim().toLowerCase().includes(v));"
            "if(o){o.scrollIntoView({block:'center'});o.click();return 'picked';}return 'notfound';})()"
        )})


class DirectDriver(TrajectoryDriver):
    """Center-click, no synthesized path — the robotic baseline. Use for tests /
    when human-like motion isn't required. The min-jerk + diffusion drivers
    subclass the same interface and only add a path before the click."""
    name = "direct"

    async def move_and_act(self, *, browser_url, request, tab_id=None, tab_url=None, start=None) -> ExecResult:
        import websockets
        from app.observer.ax_proposer import _CDPSession, _discover_target

        x, y = target_css_point(request.target_bbox, request.device_scale_factor)
        mode = "coordinate"
        try:
            target = await _discover_target(browser_url, tab_id=tab_id, tab_url=tab_url)
            async with websockets.connect(target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024) as ws:
                cdp = _CDPSession(ws)
                # Scroll is viewport-level (no target node); dispatch this driver's wheel plan.
                if request.action_id == "scroll":
                    mode = await self._do_scroll(cdp, request)
                # Prefer ELEMENT-based action when we have the node id (robust); fall back
                # to coordinate-clicking only when no backend_node_id was provided.
                elif request.backend_node_id is not None:
                    mode = await self._element_act(cdp, request, start=start)
                else:
                    path = await self._path_to(x, y, start)
                    await self._click_sequence(cdp, x, y, path)
                    await self._apply_value(cdp, request)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s execute failed: %s", self.name, exc)
            return ExecResult(ok=False, driver=self.name, action_id=request.action_id, detail=str(exc))
        logger.info("%s executed %s (%s) at css(%.1f,%.1f)", self.name, request.action_id, mode, x, y)
        return ExecResult(ok=True, driver=self.name, action_id=request.action_id,
                          css_point=(x, y), path_points=0, extra={"mode": mode})


def get_driver(name: Optional[str] = None) -> TrajectoryDriver:
    """Factory. Default 'direct'; EXECUTOR_DRIVER env overrides. Min-jerk is Phase 6."""
    import os
    choice = (name or os.environ.get("EXECUTOR_DRIVER", "direct")).lower()
    if choice == "record_only":
        from .record_only import RecordOnlyDriver
        return RecordOnlyDriver()
    if choice == "minimum_jerk":
        try:
            from .minimum_jerk import MinimumJerkDriver
            return MinimumJerkDriver()
        except Exception:
            logger.warning("minimum_jerk driver unavailable — falling back to direct")
            return DirectDriver()
    if choice == "humanized":
        try:
            from .humanized import HumanizedDriver
            return HumanizedDriver()
        except Exception:
            logger.warning("humanized driver unavailable — falling back to direct")
            return DirectDriver()
    return DirectDriver()
