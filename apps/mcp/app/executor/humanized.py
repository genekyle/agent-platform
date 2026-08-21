"""HumanizedDriver — the interim human-like 'body' (a formula, not yet learned).

Until the diffusion input-model trained on REAL clicking/typing sessions exists, this masks the
automation signature with randomized, human-shaped motion and typing — the "next best thing":

  * mouse: a minimum-jerk base curve perturbed with tapering lateral WIGGLE, a randomized
    OVERSHOOT-and-settle, randomized step count + per-move latency, and a few px of click-point
    JITTER. Randomized proportions every run (the opposite of DirectDriver's dead-straight,
    zero-latency teleport and MinimumJerkDriver's identical-every-run curve).
  * typing: per-character key events with jittered inter-key delays, longer pauses after
    space/punctuation, and occasional "thinking" pauses.

This is deliberately a stand-in. The whole point of driving with it is to TEACH the brain
(state manager / observer / planner) how to use the body — and to surface the failures that get
these agents to autonomy. Selected via EXECUTOR_DRIVER=humanized or driver="humanized".
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Optional

from .driver import ActionRequest, DirectDriver
from .minimum_jerk import min_jerk_points

logger = logging.getLogger("mcp.executor")


def humanized_path(start: tuple[float, float], end: tuple[float, float],
                   rng: Optional[random.Random] = None) -> list[tuple[float, float]]:
    """Min-jerk base + randomized overshoot + tapering perpendicular wiggle, settling on the
    true target. Pure/deterministic given `rng` — unit-testable. CSS px."""
    rng = rng or random.Random()
    dx, dy = end[0] - start[0], end[1] - start[1]
    dist = (dx * dx + dy * dy) ** 0.5
    steps = max(8, min(40, int(dist / rng.uniform(8.0, 16.0)) + 1))
    # aim slightly past the target, then settle back (human overshoot)
    over = rng.uniform(0.0, 0.16)
    aim = (end[0] + dx * over, end[1] + dy * over)
    base = min_jerk_points(start, aim, steps)
    # unit normal to the travel direction, for lateral wiggle that tapers to 0 at both ends
    L = max(1e-6, dist)
    nx, ny = -dy / L, dx / L
    amp = rng.uniform(0.5, 2.5) + dist * rng.uniform(0.008, 0.03)
    pts: list[tuple[float, float]] = []
    n = max(1, len(base) - 1)
    for i, (px, py) in enumerate(base):
        u = i / n
        taper = 4.0 * u * (1.0 - u)  # 0 at ends, ~1 mid
        w = amp * taper * rng.uniform(-1.0, 1.0)
        pts.append((px + nx * w, py + ny * w))
    pts += min_jerk_points(pts[-1], end, max(3, steps // 4))  # settle onto the real target
    return pts


class HumanizedDriver(DirectDriver):
    name = "humanized"

    #: How much of a long text gets the per-character cadence before the authoritative write
    #: takes over — see the bound in `_human_type`. Both are generous for every real search box
    #: and question field, and small enough that the correctness write can never be starved by
    #: the request timeout again.
    _TYPE_CADENCE_MAX_CHARS = 48
    _TYPE_CADENCE_MAX_SECONDS = 10.0

    def __init__(self, seed: Optional[int] = None) -> None:
        self._rng = random.Random(seed)

    async def _path_to(self, x: float, y: float, start: Optional[tuple[float, float]]):
        if not start:  # no known cursor origin → start from a random nearby offset, not a teleport
            start = (x + self._rng.uniform(-140, 140), y + self._rng.uniform(-140, 140))
        return humanized_path(start, (x, y), self._rng)

    async def _click_sequence(self, cdp, x: float, y: float, path=None) -> None:
        jx, jy = x + self._rng.uniform(-2.5, 2.5), y + self._rng.uniform(-2.5, 2.5)  # click jitter
        for px, py in (path or []):
            await self._dispatch_mouse(cdp, {"type": "mouseMoved", "x": px, "y": py})
            await asyncio.sleep(self._rng.uniform(0.006, 0.022))
        await self._dispatch_mouse(cdp, {"type": "mouseMoved", "x": jx, "y": jy})
        await asyncio.sleep(self._rng.uniform(0.04, 0.13))  # settle before pressing
        await self._dispatch_mouse(cdp, {"type": "mousePressed", "x": jx, "y": jy, "button": "left", "clickCount": 1})
        await asyncio.sleep(self._rng.uniform(0.03, 0.09))  # press dwell
        await self._dispatch_mouse(cdp, {"type": "mouseReleased", "x": jx, "y": jy, "button": "left", "clickCount": 1})

    async def _element_click(self, cdp, object_id: str, pt: dict) -> None:
        """Trusted click on the robust node path: a real CDP mouse press/release (isTrusted=true) at
        the node's freshly-measured centre, with a few px of jitter and a short press dwell — a human
        hand, not a synthetic `.click()`. The `_element_act` approach has already wiggled the cursor
        here; this presses. Falls back to the native click only if we somehow have no centre."""
        x, y = pt.get("x"), pt.get("y")
        if x is None or y is None:
            return await super()._element_click(cdp, object_id, pt)
        # A POINT OUTSIDE THE VIEWPORT CANNOT BE PRESSED. `_element_act` translates frame-local
        # coordinates into page space and scrolls each ancestor frame into view, but a frame taller
        # than the window (iCIMS's is 1654px) can still leave its target off-screen — and a trusted
        # press at a negative y silently hits nothing while reporting success, which is exactly the
        # failure this whole path exists to avoid. Fall back to the native click there: it is less
        # human, and it is honest, which matters more. Only reachable for framed content.
        vp = await cdp.send("Runtime.evaluate", {"returnByValue": True,
                                                 "expression": "({w: innerWidth, h: innerHeight})"})
        v = (vp.get("result") or {}).get("value") or {}
        if not (0 <= float(x) <= float(v.get("w") or 0) and 0 <= float(y) <= float(v.get("h") or 0)):
            return await super()._element_click(cdp, object_id, pt)
        jx, jy = float(x) + self._rng.uniform(-2.0, 2.0), float(y) + self._rng.uniform(-2.0, 2.0)
        await self._dispatch_mouse(cdp, {"type": "mouseMoved", "x": jx, "y": jy})
        await asyncio.sleep(self._rng.uniform(0.04, 0.12))   # settle before pressing
        await self._dispatch_mouse(cdp, {"type": "mousePressed", "x": jx, "y": jy, "button": "left", "clickCount": 1})
        await asyncio.sleep(self._rng.uniform(0.03, 0.09))   # press dwell
        await self._dispatch_mouse(cdp, {"type": "mouseReleased", "x": jx, "y": jy, "button": "left", "clickCount": 1})

    def _scroll_plan(self, total: float) -> list[tuple[float, float]]:
        """Human wheel scroll: several notches under an ease-in/out envelope with per-notch jitter and
        short pauses (plus the occasional longer 'reading' pause), so it never lands as one instant
        jump. Signed `total` (down positive) is preserved; the remainder is trued-up on a final notch."""
        rng = self._rng
        n = rng.randint(6, 11)
        weights = [((i + 1) / n) * (1 - (i + 1) / n) + 0.15 for i in range(n)]  # slow-fast-slow
        wsum = sum(weights) or 1.0
        steps: list[tuple[float, float]] = []
        for w in weights:
            delta = total * (w / wsum) * rng.uniform(0.8, 1.2)
            pause = rng.uniform(0.03, 0.13)
            if rng.random() < 0.12:  # occasional longer read-pause
                pause += rng.uniform(0.18, 0.55)
            steps.append((delta, pause))
        drift = total - sum(d for d, _ in steps)  # true up rounding/jitter so we land on ~total
        if abs(drift) > 1.0:
            steps.append((drift, rng.uniform(0.02, 0.08)))
        return steps

    async def _apply_value(self, cdp, request: ActionRequest,
                           object_id: Optional[str] = None) -> None:
        if request.action_id in ("type", "select") and request.value:
            await self._clear_focused(cdp)          # don't append onto residue
            await self._human_type(cdp, request.value, object_id=object_id)
        elif request.action_id == "clear":
            # `clear` used to fall through to the base driver's select-all + Delete, whose select-all
            # is Ctrl+A (`modifiers: 2`). ON macOS THAT IS NOT SELECT-ALL — it is the emacs
            # "move to line start" binding — so the Delete that follows removes one character, or on
            # an already-empty caret position none at all, and the field keeps its value while the
            # call reports ok. Every browser this project drives runs on darwin. `type` never hit
            # this because it clears through `_clear_focused` first; only a bare `clear` took the
            # keyboard route. Use the same authoritative write `type` already trusts.
            await self._clear_focused(cdp)
        else:
            await super()._apply_value(cdp, request, object_id=object_id)

    # THE ACTIVE ELEMENT IS PER-DOCUMENT, AND THE FIELD MAY NOT BE IN THIS ONE. When focus sits
    # inside an iframe, the TOP document's `activeElement` is the IFRAME ELEMENT — not the input.
    # Everything below writes to the focused field, so on a framed form both writes landed on the
    # frame element and did nothing, silently: the clear reported success, the keystrokes (which go
    # to the browser's REAL focus) appended onto the prefilled value, and the authoritative
    # value-set that exists to guarantee correctness no-oped. iCIMS's email field came out
    # "genomags@gmail.comgenomags@gmail.com" — a malformed address one click from creating a real
    # account with it (live, jobs-joslin.icims.com, 2026-07-27).
    #
    # So both writes start by walking DOWN the focus chain to the deepest document that owns focus.
    # A cross-origin frame throws on `contentDocument`; that is caught and we stop at the last
    # document we could read, rather than pretending we reached the field.
    _FOCUSED_EL_JS = (
        "let el=document.activeElement;"
        " for(let hops=0; hops<5; hops++){"
        "   let inner=null;"
        "   try { inner = el && el.contentDocument && el.contentDocument.activeElement; }"
        "   catch(e) { inner = null; }"           # cross-origin frame — cannot reach in
        "   if(!inner) break;"
        "   el = inner;"
        " }"
    )

    # React (and other framework) inputs manage their own value + caret, so synthetic
    # select-all/Backspace fight the re-render and leave residue / interleave. The reliable write is
    # the React "trick": call the NATIVE value setter, then dispatch a bubbling input event so the
    # framework syncs its state to the field. The setter must come from the element's OWN window —
    # an inner document is a different realm, and reaching for this frame's constructors to patch
    # that frame's node is the same "which document do you mean" mistake one level down.
    #
    # AND THE FOCUSED THING IS NOT ALWAYS AN <input>. A WEB COMPONENT is the focus target in its own
    # right: the browser focuses the HOST, and the real input (if there is one) lives one or more
    # shadow roots down, unreachable from here. `HTMLInputElement.prototype`'s setter applied to such
    # a host throws "Illegal invocation" — and this expression's result was never checked, so the
    # write vanished while `clear` reported success. Measured live 2026-08-14 on MAPFRE's
    # SuccessFactors form: `ui5-date-picker-xweb-calendar-widget` held a wrong date, `activeElement`
    # WAS that host, its shadow root contained no input, and `.select` was undefined — so the
    # keyboard fallback (below) had nothing selected to delete either. The host does expose a `value`
    # property; assigning it is the widget's own public API and the same "stage then commit" protocol
    # composite widgets already use.
    #
    # So pick the setter by what the element ACTUALLY IS, and let a non-input fall through to a plain
    # property assignment rather than borrowing a prototype it does not implement.
    @classmethod
    def _set_focused_value_js(cls, value_expr: str) -> str:
        return (
            "(()=>{" + cls._FOCUSED_EL_JS +
            " if(!el) return false;"
            " const win = (el.ownerDocument && el.ownerDocument.defaultView) || window;"
            " const proto = el instanceof win.HTMLTextAreaElement ? win.HTMLTextAreaElement.prototype"
            "              : el instanceof win.HTMLInputElement ? win.HTMLInputElement.prototype"
            "              : null;"
            " const set=proto&&Object.getOwnPropertyDescriptor(proto,'value');"
            f" if(set&&set.set){{set.set.call(el,{value_expr});}} else {{el.value={value_expr};}}"
            " el.dispatchEvent(new Event('input',{bubbles:true}));"
            " el.dispatchEvent(new Event('change',{bubbles:true})); return el.value;})()"
        )

    async def _clear_focused(self, cdp) -> None:
        await cdp.send("Runtime.evaluate",
                       {"expression": self._set_focused_value_js("''"), "returnByValue": True})
        await asyncio.sleep(self._rng.uniform(0.05, 0.12))

    async def _human_type(self, cdp, text: str, object_id: Optional[str] = None) -> None:
        """Per-character entry with jittered pauses for a realistic TIMING signal, then an
        authoritative value set for CORRECTNESS. Per-char synthetic key events race a
        framework-controlled input's value/caret (interleaving), so the keystrokes provide the human
        cadence and the final native-setter write guarantees the field holds exactly `text`. (Typing
        speed isn't the anti-detection signal — the mouse motion is — so this trade is sound.)

        EACH CHARACTER IS keyDown(text) + keyUp, not a bare `char`. A `char` event carries the
        character but fires no keydown/keypress/keyup, so a widget that filters or fetches ON
        KEYSTROKE never hears it — and neither does the `input` event from the authoritative write.
        iCIMS's searchable State dropdown sat unfiltered with "New Hampshire" sitting in its own
        search box (live 2026-07-27); /select_prompt already knew this about Workday's prompts and
        typed real keys, but the shared driver every other caller uses did not.
        """
        # THE CADENCE IS BOUNDED; THE CORRECTNESS WRITE IS NOT. Per-char at ~0.1s plus pauses, a
        # 640-char answer spends ~90s in this loop and the HTTP timeout kills the coroutine
        # BEFORE the authoritative write below — the one statement guaranteeing the field holds
        # exactly `text` was the one the timeout skipped, leaving partial text behind (live
        # 2026-08-19, the ~350-char practical ceiling; root-caused 2026-08-20). The docstring's
        # own trade already concedes typing speed is not the anti-detection signal, so the human
        # timing comes from a bounded prefix and the full value always lands in constant time.
        # Keystroke-listening widgets (the iCIMS case above) still hear real keys, and the final
        # native-setter write fires `input`, so a filter re-runs over the complete text.
        deadline = asyncio.get_event_loop().time() + self._TYPE_CADENCE_MAX_SECONDS
        for ch in text[: self._TYPE_CADENCE_MAX_CHARS]:
            await cdp.send("Input.dispatchKeyEvent",
                           {"type": "keyDown", "text": ch, "key": ch, "unmodifiedText": ch})
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch})
            delay = self._rng.uniform(0.04, 0.16)
            if ch == " " or ch in ",.;:":
                delay += self._rng.uniform(0.05, 0.15)
            if self._rng.random() < 0.06:  # occasional 'thinking' pause
                delay += self._rng.uniform(0.2, 0.6)
            await asyncio.sleep(delay)
            if asyncio.get_event_loop().time() >= deadline:
                break
        await self._set_value_react_safe(cdp, text, object_id=object_id)

    #: The authoritative write, targeted at a NODE WE ALREADY RESOLVED rather than at whatever is
    #: focused when it runs. Same native-setter + input/change body as the focused variant.
    _SET_NODE_VALUE_JS = (
        "function(v){"
        " const win = (this.ownerDocument && this.ownerDocument.defaultView) || window;"
        " const proto = this instanceof win.HTMLTextAreaElement ? win.HTMLTextAreaElement.prototype"
        "                                                       : win.HTMLInputElement.prototype;"
        " const set = Object.getOwnPropertyDescriptor(proto,'value');"
        " if(set&&set.set){set.set.call(this,v);} else {this.value=v;}"
        " this.dispatchEvent(new Event('input',{bubbles:true}));"
        " this.dispatchEvent(new Event('change',{bubbles:true}));"
        " return this.value;}"
    )

    async def _set_value_react_safe(self, cdp, text: str,
                                    object_id: Optional[str] = None) -> None:
        """Authoritatively set the field's value via the native setter + input/change events (the
        React-safe write), so it ends EXACTLY `text` regardless of the per-char race.

        WRITE TO THE NODE, NOT TO WHATEVER IS FOCUSED. When we already resolved the element we
        address it directly, because focus is not ours to rely on between the first keystroke and
        this write: a typeahead that opens on input takes focus with it, and then `activeElement`
        is the dropdown. Measured on LinkedIn's job-search combobox (session #22, 2026-07-28) —
        click reported focused, `type` reported ok, and the field was still EMPTY afterwards with
        focus gone. The keystrokes had gone to the browser's real focus and the authoritative write
        had landed on something else, so both halves "succeeded" and nothing was typed.

        The focused-element path stays for the coordinate/bbox route, which has no node to hold on
        to — and it keeps the iframe hop that route needs.
        """
        import json as _json
        if object_id:
            res = await cdp.send("Runtime.callFunctionOn",
                                 {"objectId": object_id,
                                  "functionDeclaration": self._SET_NODE_VALUE_JS,
                                  "arguments": [{"value": text}], "returnByValue": True})
        else:
            res = await cdp.send("Runtime.evaluate",
                                 {"expression": self._set_focused_value_js(_json.dumps(text)),
                                  "returnByValue": True})
        got = ((res or {}).get("result") or {}).get("value")
        if got != text:
            logger.warning("humanized type: field reads %r after writing %r — the write did not "
                           "reach the field (a frame we cannot see into?)", got, text)
