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

logger = logging.getLogger("mcp-mock.executor")


@dataclass
class ActionRequest:
    """Selector → executor handoff. bbox is in SCREENSHOT pixels (as produced by
    the AX proposer); device_scale_factor converts it to CSS px for CDP."""
    action_id: str                      # click | type | select | scroll | submit | clear
    target_bbox: dict[str, float]       # {x, y, width, height} screenshot px
    backend_node_id: Optional[int] = None
    value: Optional[str] = None
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

    async def _element_act(self, cdp, request: ActionRequest) -> str:
        """ELEMENT-BASED action via backend_node_id — robust to spacing/scroll/layout
        where coordinate-clicking fails (closely-spaced radios, off-screen fields). We
        resolve the node, scroll it into view, then drive it natively: .click() for
        click/select, focus+insertText for type. Avoids the 'clicked the No 25px below
        Yes' class of bug coordinate-clicking hit on long application forms."""
        await cdp.send("DOM.enable")
        await cdp.send("Runtime.enable")
        resolved = await cdp.send("DOM.resolveNode", {"backendNodeId": request.backend_node_id})
        object_id = resolved["object"]["objectId"]

        async def call(fn: str) -> None:
            await cdp.send("Runtime.callFunctionOn",
                           {"objectId": object_id, "functionDeclaration": fn, "awaitPromise": False})

        if request.action_id == "select" and request.value:
            await self._select_option(cdp, object_id, request.value)
        elif request.action_id == "type" and request.value:
            await call("function(){ this.scrollIntoView({block:'center',inline:'center'}); this.focus(); }")
            await cdp.send("Input.insertText", {"text": request.value})
        elif request.action_id == "clear":
            await call("function(){ this.scrollIntoView({block:'center'}); this.focus();"
                       " if(this.select)this.select(); }")
            await self._apply_value(cdp, request)  # reuse the select-all+delete keystrokes
        else:  # click / submit / default
            await call("function(){ this.scrollIntoView({block:'center',inline:'center'}); this.click(); }")
        return "element"

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

    async def _path_to(self, x: float, y: float, start: Optional[tuple[float, float]]) -> Optional[list[tuple[float, float]]]:
        return None  # DirectDriver teleports; subclasses override to synthesize a path

    async def move_and_act(self, *, browser_url, request, tab_id=None, tab_url=None, start=None) -> ExecResult:
        import websockets
        from app.observer.ax_proposer import _CDPSession, _discover_target

        x, y = target_css_point(request.target_bbox, request.device_scale_factor)
        mode = "coordinate"
        try:
            target = await _discover_target(browser_url, tab_id=tab_id, tab_url=tab_url)
            async with websockets.connect(target["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024) as ws:
                cdp = _CDPSession(ws)
                # Prefer ELEMENT-based action when we have the node id (robust); fall back
                # to coordinate-clicking only when no backend_node_id was provided.
                if request.backend_node_id is not None:
                    mode = await self._element_act(cdp, request)
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
    return DirectDriver()
