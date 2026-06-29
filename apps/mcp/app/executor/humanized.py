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
import random
from typing import Optional

from .driver import ActionRequest, DirectDriver
from .minimum_jerk import min_jerk_points


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

    def __init__(self, seed: Optional[int] = None) -> None:
        self._rng = random.Random(seed)

    async def _path_to(self, x: float, y: float, start: Optional[tuple[float, float]]):
        if not start:  # no known cursor origin → start from a random nearby offset, not a teleport
            start = (x + self._rng.uniform(-140, 140), y + self._rng.uniform(-140, 140))
        return humanized_path(start, (x, y), self._rng)

    async def _click_sequence(self, cdp, x: float, y: float, path=None) -> None:
        jx, jy = x + self._rng.uniform(-2.5, 2.5), y + self._rng.uniform(-2.5, 2.5)  # click jitter
        for px, py in (path or []):
            await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": px, "y": py})
            await asyncio.sleep(self._rng.uniform(0.006, 0.022))
        await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": jx, "y": jy})
        await asyncio.sleep(self._rng.uniform(0.04, 0.13))  # settle before pressing
        await cdp.send("Input.dispatchMouseEvent",
                       {"type": "mousePressed", "x": jx, "y": jy, "button": "left", "clickCount": 1})
        await asyncio.sleep(self._rng.uniform(0.03, 0.09))  # press dwell
        await cdp.send("Input.dispatchMouseEvent",
                       {"type": "mouseReleased", "x": jx, "y": jy, "button": "left", "clickCount": 1})

    async def _apply_value(self, cdp, request: ActionRequest) -> None:
        if request.action_id in ("type", "select") and request.value:
            await self._clear_focused(cdp)          # don't append onto residue
            await self._human_type(cdp, request.value)
        else:
            await super()._apply_value(cdp, request)

    # React (and other framework) inputs manage their own value + caret, so synthetic
    # select-all/Backspace fight the re-render and leave residue / interleave. The reliable clear is
    # the React "trick": call the NATIVE value setter to '' then dispatch a bubbling input event, so
    # the framework syncs its state to the empty field. Operates on document.activeElement (the field
    # we just clicked to focus), so no node id is needed on the coordinate path.
    _CLEAR_FOCUSED_JS = (
        "(()=>{const el=document.activeElement; if(!el) return false;"
        " const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;"
        " const set=Object.getOwnPropertyDescriptor(proto,'value'); if(set&&set.set){set.set.call(el,'');}"
        " else {el.value='';}"
        " el.dispatchEvent(new Event('input',{bubbles:true}));"
        " el.dispatchEvent(new Event('change',{bubbles:true})); return true;})()"
    )

    async def _clear_focused(self, cdp) -> None:
        await cdp.send("Runtime.evaluate", {"expression": self._CLEAR_FOCUSED_JS, "returnByValue": True})
        await asyncio.sleep(self._rng.uniform(0.05, 0.12))

    async def _human_type(self, cdp, text: str) -> None:
        """Per-character entry with jittered pauses for a realistic TIMING signal, then an
        authoritative value set for CORRECTNESS. Per-char synthetic 'char' events race a
        framework-controlled input's value/caret (interleaving), so the keystrokes provide the human
        cadence and the final native-setter write guarantees the field holds exactly `text`. (Typing
        speed isn't the anti-detection signal — the mouse motion is — so this trade is sound.)"""
        for ch in text:
            await cdp.send("Input.dispatchKeyEvent", {"type": "char", "text": ch})
            delay = self._rng.uniform(0.04, 0.16)
            if ch == " " or ch in ",.;:":
                delay += self._rng.uniform(0.05, 0.15)
            if self._rng.random() < 0.06:  # occasional 'thinking' pause
                delay += self._rng.uniform(0.2, 0.6)
            await asyncio.sleep(delay)
        await self._set_value_react_safe(cdp, text)

    async def _set_value_react_safe(self, cdp, text: str) -> None:
        """Authoritatively set document.activeElement's value via the native setter + input/change
        events (the React-safe write), so the field ends EXACTLY `text` regardless of the per-char race."""
        import json as _json
        v = _json.dumps(text)
        expr = (
            "(()=>{const el=document.activeElement; if(!el) return false;"
            " const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;"
            " const set=Object.getOwnPropertyDescriptor(proto,'value');"
            f" if(set&&set.set){{set.set.call(el,{v});}} else {{el.value={v};}}"
            " el.dispatchEvent(new Event('input',{bubbles:true}));"
            " el.dispatchEvent(new Event('change',{bubbles:true})); return el.value;})()"
        )
        await cdp.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
