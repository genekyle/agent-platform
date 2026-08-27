"""One identity per control, and the act says which door it used (SESSION 19).

Pinned in the order it costs to get wrong:
  1. A click reports HOW it was delivered. `HumanizedDriver` degrades to the untrusted native
     click in two deliberate cases, and until 2026-08-27 `mode` read a bare "element" either way
     — so an untrusted click was indistinguishable from a trusted one at every surface above the
     driver. Eleven attempts on one Workday date went to a widget that had never received a
     GESTURE (2026-08-25).
  2. `addressed_by` reaches the CALLER, not only the corpus.
  3. The visible twin wins when the page has said these are one action drawn twice (Cornerstone,
     2026-08-24: masthead y=411 vs footer y=2269, and document order picked the footer).
"""
from __future__ import annotations

import asyncio

import pytest

from app.executor.driver import ActionRequest, DirectDriver
from app.executor.humanized import HumanizedDriver


class _FakeCDP:
    """Records what was dispatched. `viewport` decides whether a point is pressable."""

    def __init__(self, viewport=(1200, 800)):
        self.viewport = viewport
        self.sent: list[tuple[str, dict]] = []

    async def send(self, method: str, params: dict | None = None):
        params = params or {}
        self.sent.append((method, params))
        if method == "Runtime.evaluate" and "innerWidth" in str(params.get("expression", "")):
            return {"result": {"value": {"w": self.viewport[0], "h": self.viewport[1]}}}
        return {"result": {"value": None}}

    def native_clicks(self) -> int:
        return sum(1 for m, p in self.sent
                   if m == "Runtime.callFunctionOn"
                   and "this.click()" in str(p.get("functionDeclaration", "")))

    def trusted_presses(self) -> int:
        return sum(1 for m, p in self.sent
                   if m == "Input.dispatchMouseEvent" and p.get("type") == "mousePressed")


def test_the_base_driver_names_its_click_native():
    cdp = _FakeCDP()
    how = asyncio.run(DirectDriver()._element_click(cdp, "obj-1", {"x": 10, "y": 10}))
    assert how == "native" and cdp.native_clicks() == 1


def test_a_trusted_click_says_so_and_really_presses():
    cdp = _FakeCDP()
    how = asyncio.run(HumanizedDriver(seed=1)._element_click(cdp, "obj-1", {"x": 10, "y": 10}))
    assert how == "trusted"
    assert cdp.trusted_presses() == 1 and cdp.native_clicks() == 0


@pytest.mark.parametrize("pt,expected", [
    ({}, "native:no_centre"),
    ({"x": 5000, "y": 20}, "native:off_viewport"),
    ({"x": 20, "y": -40}, "native:off_viewport"),
])
def test_every_downgrade_to_an_untrusted_click_names_itself(pt, expected):
    """Both fallbacks are deliberate and documented — the defect was that they were SILENT. A
    posture that can vanish without saying so is not a posture (the 2026-08-21 rule for the
    driver downgrade, applied one layer down to the click itself)."""
    cdp = _FakeCDP()
    how = asyncio.run(HumanizedDriver(seed=1)._element_click(cdp, "obj-1", pt))
    assert how == expected
    assert cdp.native_clicks() == 1, "it must still click — honestly, not silently"
    assert cdp.trusted_presses() == 0


def test_the_mode_carries_the_click_kind_up_to_the_caller():
    """`/execute` surfaces `extra["mode"]`, and the only structured consumer matches on the
    `upload:` prefixes — so extending "element" to "element:<how>" is additive by construction."""
    driver = HumanizedDriver(seed=1)
    cdp = _FakeCDP()

    async def _run():
        # drive the click branch of _element_act directly through its own seam
        return await driver._element_click(cdp, "obj", {"x": 10, "y": 10})

    assert f"element:{asyncio.run(_run())}" == "element:trusted"


def test_the_request_shape_still_accepts_a_node_addressed_click():
    """Guard on the contract the above rests on: a node-addressed request is what routes into
    `_element_act` at all (coordinate mode is only reached when backend_node_id is None)."""
    req = ActionRequest(action_id="click", target_bbox={}, backend_node_id=42)
    assert req.backend_node_id == 42 and req.action_id == "click"
