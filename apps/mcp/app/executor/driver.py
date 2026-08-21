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


#: Did the upload land? Asked OF THE UPLOADER, in its own terms — `this` is the file input.
#: `at_node` is the plain-input witness (the FileList still holds it); `rendered` is the
#: ingesting-widget witness (the container now prints the filename). The container walk stops
#: before any ancestor holding a SECOND file input, so a neighbouring uploader's filename can
#: never confirm this one. `error` catches the uploader that rejected the file outright (size,
#: type) — a refusal is a real answer and must not be polled over ten times.
_UPLOAD_WITNESS_JS = r"""
function(names) {
  const txt = (n) => ((n && (n.innerText || n.textContent)) || '').replace(/\s+/g, ' ').trim();
  const files = this.files ? this.files.length : -1;
  let box = null;
  for (let n = this.parentElement, d = 0; n && n !== document.body && d < 8; d++, n = n.parentElement) {
    if (n.querySelectorAll('input[type=file]').length > 1) break;   // n already spans a neighbour
    box = n;
  }
  // No container of this input excludes the others: we cannot tell whose row a filename is, so
  // we do not claim one. `rendered` stays false and the caller keeps waiting or reports honestly.
  const scope = box ? txt(box) : '';
  const rendered = !!scope && (names || []).some(n => n && scope.includes(n));
  const err = /\b(too large|exceeds|not supported|invalid file|unsupported file|failed to upload)\b/i.exec(scope);
  return {files, at_node: files > 0, rendered, error: err ? err[0] : '',
          scope: scope.slice(0, 120)};
}
"""


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

    async def _dispatch_mouse(self, cdp, params: dict, timeout: float = 0.8) -> None:
        """Dispatch a mouse event, TOLERATING a dropped/late CDP ack. Chrome's ack for mouse events
        is unreliable in some builds — a click that opens a new tab steals the CDP context, a wheel
        during smooth scroll — but the EVENT still lands. So we don't block on the reply past a short
        bound (seen live 2026-07-18: a trusted press on a new-tab "Apply" button hung ~40s). The
        `_CDPSession.send` recv-timeout is the last-ditch net; this keeps normal driving snappy."""
        import asyncio
        try:
            await asyncio.wait_for(cdp.send("Input.dispatchMouseEvent", params), timeout=timeout)
        except asyncio.TimeoutError:
            # Tolerated, but COUNTED (2026-08-21): a press whose ack never came and a press that
            # never landed used to be the same fact. The count rides out in ExecResult.extra so a
            # no_progress diagnosis can see whether the input channel was dropping acks.
            self._dropped_acks = getattr(self, "_dropped_acks", 0) + 1

    async def _click_sequence(self, cdp, x: float, y: float, path: Optional[list[tuple[float, float]]] = None) -> None:
        """Optional pre-move along `path` (CSS px), then a left click at (x,y)."""
        for px, py in (path or []):
            await self._dispatch_mouse(cdp, {"type": "mouseMoved", "x": px, "y": py})
        await self._dispatch_mouse(cdp, {"type": "mouseMoved", "x": x, "y": y})
        await self._dispatch_mouse(cdp, {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        await self._dispatch_mouse(cdp, {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})

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
            await self._dispatch_mouse(cdp, {"type": "mouseMoved", "x": px, "y": py})
        if path:
            await self._dispatch_mouse(cdp, {"type": "mouseMoved", "x": x, "y": y})

    # --- the two PUBLIC seams for a caller that already holds a CDP session --------------------
    # A tier-2 recipe (LinkedIn's virtualised results list, say) needs to click and scroll several
    # times inside ONE session, interleaved with its own reads. Going back out through
    # `move_and_act` per act would open a fresh websocket and re-discover the target each time, and
    # reaching for `_click_sequence` / `_scroll_plan` from outside is how a second, drifting copy of
    # the humanized signature gets written. These two are the same code `move_and_act` runs — the
    # driver stays the ONE place that knows what a human hand looks like.

    async def click_at(self, cdp, x: float, y: float,
                       start: Optional[tuple[float, float]] = None) -> None:
        """This driver's trusted click at a VIEWPORT point (CSS px), on an open session.

        Humanized: approach along a wiggly path, jitter the press point, dwell. Direct: teleport
        and press. Identical to the coordinate branch of `move_and_act`."""
        path = await self._path_to(x, y, start)
        await self._click_sequence(cdp, x, y, path)

    async def scroll_at(self, cdp, x: float, y: float, total_px: float,
                        start: Optional[tuple[float, float]] = None) -> None:
        """This driver's wheel scroll of `total_px` (DOWN positive) WITH THE CURSOR AT (x, y).

        THE CURSOR POSITION IS THE ARGUMENT, not a detail. A wheel event scrolls whichever
        scroll container sits under the pointer, so on a two-column app (LinkedIn's results list
        beside its detail pane) the same wheel scrolls a different thing depending on where the
        pointer is — and a wheel over a column that cannot scroll simply does nothing while every
        `ok` in the chain still says success. Callers must pass a point they measured INSIDE the
        thing they mean to scroll.

        The cursor is MOVED there first (along this driver's path), so the page sees a hover before
        the wheel — which is both what a hand does and what a list binding on mouseenter needs.
        """
        import asyncio
        await self._approach(cdp, x, y, start)
        for delta, pause in self._scroll_plan(total_px):
            # tolerate a dropped wheel ack per notch (the wheel lands regardless) — see _dispatch_mouse
            await self._dispatch_mouse(
                cdp, {"type": "mouseWheel", "x": x, "y": y, "deltaX": 0, "deltaY": delta},
                timeout=0.6)
            if pause:
                await asyncio.sleep(pause)

    async def _apply_value(self, cdp, request: ActionRequest,
                           object_id: Optional[str] = None) -> None:
        """For type/select/clear actions, apply the value after focusing via click.

        `object_id` is the already-resolved node, when the caller has one. Subclasses use it to
        write to THAT element rather than to whatever is focused at the moment the write runs —
        focus is not reliable across a typeahead that opens on input."""
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
        #
        # CONFIRMED FROM THE PAGE, never from the dispatch — and `files.length` IS NOT THE PAGE.
        # Two shapes of file input exist and they disagree about what success looks like:
        #   * a PLAIN input keeps what you set, so `files.length > 0` is its truth;
        #   * an INGESTING uploader (Workday, and every widget that POSTs on change) hands the
        #     file to its own handler and RESETS the input — so `files.length` returns to 0 on
        #     success, and the widget renders a row instead ("GM_Resume.pdf … Successfully
        #     Uploaded!").
        # Measured live 2026-08-12 on SolutionHealth's Workday: three uploads had SUCCEEDED into
        # the Attachments box — the page showed all three — while every file input on the page read
        # `files: 0`. A files.length check calls that a failure, and last night's `not_staged`
        # verdict was exactly that false negative; the night before, the same counter read >0 in the
        # instant before the handler consumed it and called a wrong-target upload a success. It is
        # not a lie in one direction, it is the wrong witness.
        #
        # So: dispatch, then poll for EITHER witness, and say which one confirmed. The rendered-row
        # search is bounded to the uploader's own container — the walk stops before any ancestor
        # that contains a SECOND file input, or a filename in a neighbouring uploader would confirm
        # this one (which is precisely the confusion that put the resume in the wrong box).
        if request.action_id == "upload":
            import asyncio
            files = [str(f) for f in (request.files or [])]
            await cdp.send("DOM.setFileInputFiles",
                           {"backendNodeId": request.backend_node_id, "files": files})
            await cdp.send("Runtime.callFunctionOn", {
                "objectId": object_id, "returnByValue": True,
                "functionDeclaration": "function(){ if(this.files && this.files.length){"
                                       " this.dispatchEvent(new Event('input',{bubbles:true}));"
                                       " this.dispatchEvent(new Event('change',{bubbles:true})); }"
                                       " return this.files ? this.files.length : -1; }"})
            names = [f.rsplit("/", 1)[-1] for f in files]
            # An ingesting uploader has to round-trip to its own server; a plain one is instant.
            # Poll rather than sleep-once so the fast path stays fast.
            for attempt in range(10):
                got = await cdp.send("Runtime.callFunctionOn", {
                    "objectId": object_id, "returnByValue": True,
                    "functionDeclaration": _UPLOAD_WITNESS_JS,
                    "arguments": [{"value": names}]})
                w = (got.get("result") or {}).get("value") or {}
                if w.get("at_node"):
                    return "upload"                                  # plain input kept the file
                if w.get("rendered"):
                    return "upload"                                  # the widget shows it
                if w.get("error"):
                    return f"upload:rejected:{str(w.get('error'))[:60]}"
                await asyncio.sleep(0.8)
            return f"upload:not_staged:files={w.get('files')} rendered=no"

        # Scroll into view + measure the node's own centre (CSS px). A fresh per-node measurement is
        # more accurate than any pre-recorded bbox, so the human motion lands on the real element.
        #
        # IN PAGE COORDINATES, NOT FRAME COORDINATES. `getBoundingClientRect` is relative to the
        # node's OWN document, and a trusted `Input.dispatchMouseEvent` takes viewport coordinates
        # of the top document — so for anything inside an iframe the two disagree by the frame's
        # offset, silently. Measured live on iCIMS (2026-07-26): the apply link sat at (1079, 126)
        # in its frame, the frame itself at (110, -802) in the page, so the real target was
        # (1189, -676) — off-screen — and the untranslated coordinates landed on the IFRAME
        # ELEMENT. The click dispatched, hit the frame's chrome, changed nothing, and /execute
        # reported ok. Whole classes of ATS (iCIMS, branded Greenhouse/Workday wrappers) put their
        # entire apply flow in exactly such a frame.
        #
        # Walking `frameElement` up the chain also lets each ancestor scroll the frame itself into
        # view, which is what makes the final coordinates land inside the viewport at all.
        #
        # BUT THE WALK CANNOT MEASURE, ONLY SCROLL. It translates frame-local coordinates into page
        # space just as long as `frameElement` is reachable — and from inside a CROSS-ORIGIN frame
        # `window.frameElement` is NULL, not an error. So the loop never runs, `framed` reports
        # false, and the function hands back FRAME-LOCAL coordinates wearing page coordinates'
        # clothes: the same silent no-op described above, one origin further out, and invisible
        # because nothing throws. Measured live on iCIMS (2026-08-12): the VEVRAA self-ID radio sat
        # at (25.5, 574) inside `icims_formFrame` and at (45.5, 392) in the page; the trusted click
        # went to (25.5, 574), landed on nothing, and /execute returned ok with the radio still
        # unchecked. Trusted input DOES reach that frame — the coordinates were simply wrong.
        #
        # `DOM.getBoxModel` performs the frame translation in the browser, so it is right at every
        # frame depth and for both origins, and it is the same measurement the AX proposer's bboxes
        # come from — selector and executor now agree by construction. The in-page call keeps only
        # the job it can still do everywhere: scrolling.
        await cdp.send("Runtime.callFunctionOn", {
            "objectId": object_id, "returnByValue": True,
            "functionDeclaration": ("function(){ this.scrollIntoView({block:'center',inline:'center'});"
                                    " let win=this.ownerDocument.defaultView, hops=0;"
                                    " while(win && win.frameElement && hops++ < 5){"
                                    "   const fe=win.frameElement;"
                                    "   fe.scrollIntoView({block:'center',inline:'center'});"
                                    "   win=fe.ownerDocument.defaultView; }"
                                    " return {framesScrolled:hops}; }")})
        pt = await self._node_centre(cdp, request.backend_node_id)
        if "x" in pt and "y" in pt:
            await self._approach(cdp, float(pt["x"]), float(pt["y"]), start)

        if request.action_id == "select" and request.value:
            # The verdict rides out in the mode so the caller can see WHICH kind of select this
            # was and whether the option was actually found.
            return f"element:select:{await self._select_option(cdp, object_id, request.value)}"
        elif request.action_id == "type" and request.value:
            await call("function(){ this.focus(); }")
            # Hand the resolved node down: the authoritative write must target the element we
            # addressed, not `document.activeElement` — a typeahead steals focus mid-type.
            await self._apply_value(cdp, request, object_id=object_id)
        elif request.action_id == "clear":
            await call("function(){ this.focus(); if(this.select)this.select(); }")
            await self._apply_value(cdp, request)
        elif request.action_id == "submit":
            # SUBMIT IS A KEY, NOT A CLICK. The vocabulary already has `submit` and it fell through
            # to _element_click, which is right for a form with a button and wrong for every
            # control that commits on Enter. LinkedIn's job search has NO submit button at all —
            # measured from the operator's own recording: click, type, `keydown Enter`, then
            # `change` + `blur`. Dispatching a click there hits whatever is under the caret.
            #
            # Focus first, because the key goes to the browser's real focus and not to the node we
            # resolved: a submit sent at an unfocused field lands somewhere else entirely and
            # reports ok, which is the failure shape this codebase keeps meeting.
            await call("function(){ this.focus(); }")
            for typ in ("rawKeyDown", "char", "keyUp"):
                ev = {"type": typ, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13,
                      "nativeVirtualKeyCode": 13}
                if typ == "char":
                    ev = {"type": "char", "text": "\r", "key": "Enter"}
                await cdp.send("Input.dispatchKeyEvent", ev)
        else:  # click / default
            await self._element_click(cdp, object_id, pt)
        return "element"

    async def _node_centre(self, cdp, backend_node_id: Optional[int]) -> dict[str, float]:
        """The node's centre in TOP-LEVEL PAGE coordinates, measured by the browser.

        `DOM.getBoxModel` reports the content quad already translated out of whatever frame the node
        lives in — the one measurement an in-page `getBoundingClientRect` cannot make across a
        cross-origin boundary, where `frameElement` reads null and the arithmetic silently stops.

        Returns {} when the node has no box (display:none, detached, collapsed) so the caller falls
        back honestly to the native click instead of pressing a made-up point.
        """
        if backend_node_id is None:
            return {}
        try:
            box = await cdp.send("DOM.getBoxModel", {"backendNodeId": backend_node_id})
        except Exception as exc:  # noqa: BLE001 — a node with no box is a verdict, not a crash
            logger.debug("getBoxModel found no box for node %s: %s", backend_node_id, exc)
            return {}
        quad = ((box.get("model") or {}).get("content")) or []
        if len(quad) < 6:
            return {}
        return {"x": (float(quad[0]) + float(quad[4])) / 2.0,
                "y": (float(quad[1]) + float(quad[5])) / 2.0}

    async def _element_click(self, cdp, object_id: str, pt: dict) -> None:
        """The click portion of a node-based action. Base = the native synthetic `this.click()` —
        the robotic baseline (isTrusted=false, and it doesn't move/focus like a hand). HumanizedDriver
        overrides this to a TRUSTED CDP mouse press/release at the node's freshly-measured centre
        (`pt`), so the robust `backend_node_id` path clicks like a human instead of scripting a click.
        Trusted clicks are the system-wide standard (operator-directed 2026-07-18); this is the seam."""
        await cdp.send("Runtime.callFunctionOn",
                       {"objectId": object_id, "functionDeclaration": "function(){ this.click(); }",
                        "awaitPromise": False})

    def _scroll_plan(self, total: float) -> list[tuple[float, float]]:
        """Break a vertical scroll of `total` CSS px into (deltaY, pause_seconds) steps. Base = one
        instant wheel notch (robotic baseline). HumanizedDriver overrides to several eased, jittered
        notches with short read-pauses — a human trackpad/wheel signal, not a teleport."""
        return [(total, 0.0)]

    async def _do_scroll(self, cdp, request: ActionRequest) -> str:
        """Vertical wheel scroll over a viewport point. `request.value` = 'down'/'up' (default ~600px),
        a number of px, or 'down 1000'. Positive deltaY scrolls down.

        `target_bbox` is WHERE THE CURSOR GOES — pass the bbox of the pane you mean to scroll (see
        `scroll_at`). With no bbox we fall back to mid-viewport, which is a guess: on a two-column
        app it may well land on the column that does not move."""
        total = parse_scroll_value(request.value)
        x, y = target_css_point(request.target_bbox, request.device_scale_factor)
        if not x and not y:  # no anchor bbox → scroll over mid-viewport
            x, y = 400.0, 400.0
        await self.scroll_at(cdp, x, y, total)
        return "scroll"

    #: Where a custom combobox's options are searched. `this.ownerDocument` — NOT the top
    #: `document` — because a portal renders at the root of the document its widget lives in, and on
    #: a branded ATS wrapper that is the iframe, not the page. Searching the top document there
    #: finds nothing while the open listbox sits on screen: iCIMS's Country and State both reported
    #: a completed action and stayed unset (live, 2026-07-27).
    _OPTION_SELECTORS = ("[role=option],li[role=option],[role=listbox] li,[role=menuitem],"
                         "ul[role=listbox] [role=option]")

    async def _select_option(self, cdp, object_id: str, value: str) -> str:
        """Choose `value` in a dropdown — handles BOTH a native <select> (set value + dispatch
        change) and a custom ARIA combobox (click to open, then click the option whose text
        matches).

        EXACT MATCH WINS, substring is the fallback — the same order `_resolve_ax_node` uses for
        accessible names, and for the same reason. This was substring-only, which on SAP's country
        list meant "United States" was tested against both "United States" and "United States Minor
        Outlying Islands"; it picked the right one purely because the exact option sorts first
        alphabetically. Order is not a guarantee we have — reorder that list, or meet a site that
        sorts by ISO code, and the drive silently applies to a job in the wrong country with an
        `ok` on the way out. Verified live on career41.sapsf.com 2026-07-28, where both options are
        genuinely present.

        Returns WHAT HAPPENED — native / native_contains / native_notfound / picked /
        picked_contains / notfound — because the two failures are the interesting ones and this
        used to return None either way. A dropdown that could not find its option looked exactly
        like one that had been set, all the way up to a caller whose `ok` only ever meant "CDP did
        not throw". The `_contains` verdicts are not failures, but they say the value we were given
        is not what the option is called, which is worth seeing before it becomes a wrong answer."""
        import asyncio

        phase = await cdp.send("Runtime.callFunctionOn", {
            "objectId": object_id, "returnByValue": True,
            "arguments": [{"value": value}],
            "functionDeclaration": (
                "function(v){"
                " v=(v||'').toLowerCase();"
                " if(this.tagName==='SELECT'){"
                "   const os=[...this.options];"
                "   const t=x=>x.text.trim().toLowerCase();"
                "   const o=os.find(x=>t(x)===v)||os.find(x=>t(x).includes(v));"
                "   if(o){this.value=o.value;this.dispatchEvent(new Event('change',{bubbles:true}));"
                "     return t(o)===v?'native':'native_contains';}"
                "   return 'native_notfound';}"
                " this.scrollIntoView({block:'center'}); this.click(); return 'opened';"
                "}"),
        })
        verdict = (phase.get("result") or {}).get("value")
        if verdict != "opened":
            return str(verdict or "unknown")   # native <select> already handled (or not found)

        await asyncio.sleep(0.4)  # let the listbox/portal render
        import json as _json
        v = _json.dumps(value)
        picked = await cdp.send("Runtime.callFunctionOn", {
            "objectId": object_id, "returnByValue": True,
            "functionDeclaration": (
                "function(){const v=" + v + ".toLowerCase();"
                " const doc=this.ownerDocument||document;"
                " const opts=[...doc.querySelectorAll('" + self._OPTION_SELECTORS + "')];"
                " const t=x=>(x.innerText||'').trim().toLowerCase();"
                " const o=opts.find(x=>t(x)===v)||opts.find(x=>t(x).includes(v));"
                " if(o){o.scrollIntoView({block:'center'});o.click();"
                "   return t(o)===v?'picked':'picked_contains';}"
                " return 'notfound';}"),
        })
        return str(((picked.get("result") or {}).get("value")) or "notfound")


class DirectDriver(TrajectoryDriver):
    """Center-click, no synthesized path — the robotic baseline. Use for tests /
    when human-like motion isn't required. The min-jerk + diffusion drivers
    subclass the same interface and only add a path before the click."""
    name = "direct"

    async def move_and_act(self, *, browser_url, request, tab_id=None, tab_url=None, start=None) -> ExecResult:
        import websockets
        from app.observer.ax_proposer import _CDPSession, _discover_target

        self._dropped_acks = 0            # per-act; _dispatch_mouse increments on a swallowed ack
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
        # A select's verdict belongs in `detail`, where /execute actually surfaces it. `ok` still
        # means only "the mechanism completed" — but "notfound" now reaches the caller instead of
        # dying in a local variable.
        dropped = getattr(self, "_dropped_acks", 0)
        return ExecResult(ok=True, driver=self.name, action_id=request.action_id,
                          css_point=(x, y), path_points=0,
                          extra={"mode": mode, **({"dropped_acks": dropped} if dropped else {})},
                          detail=(mode if mode.startswith("element:select:") else ""))


def get_driver(name: Optional[str] = None) -> TrajectoryDriver:
    """Factory. **Default 'humanized' — the system-wide standard for real driving** (trusted clicks,
    human wiggle, human wheel-scroll, cadence typing). `EXECUTOR_DRIVER` or an explicit `name`
    overrides. `direct` is the robotic teleport baseline, kept for tests / record-only / internal
    atomic actions that must not move the mouse. An unknown name resolves to the humanized default,
    not an error (fail toward human-like, never toward the robotic signature). Operator-directed
    2026-07-18: humanized/trusted is the default everywhere the agent actually drives."""
    import os
    choice = (name or os.environ.get("EXECUTOR_DRIVER", "humanized")).lower()
    if choice == "record_only":
        from .record_only import RecordOnlyDriver
        return RecordOnlyDriver()
    if choice == "direct":
        return DirectDriver()
    if choice == "minimum_jerk":
        try:
            from .minimum_jerk import MinimumJerkDriver
            return MinimumJerkDriver()
        except Exception:
            logger.warning("minimum_jerk driver unavailable — falling back to direct")
            return DirectDriver()
    # default ("humanized") and any unknown name -> the human-motion standard
    try:
        from .humanized import HumanizedDriver
        return HumanizedDriver()
    except Exception:
        logger.warning("humanized driver unavailable — falling back to direct")
        return DirectDriver()
