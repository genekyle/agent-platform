"""Walking a virtualised job list — the wheel, the pointer, and what counts as proof.

The bug these are written against was silent: LinkedIn's results list scrolled by assigning
`pane.scrollTop` on a class-named element, the element was null on the live page, so every scroll
went to the window instead — which moved the page frame and not the list. Nothing errored, and the
extractor kept reporting the seven cards that happened to be rendered.

So there are two things to hold: the scroll is a WHEEL AT A POINT INSIDE THE LIST (a wheel scrolls
whatever is under the pointer, and on a two-column app that decides which column moves), and a
scroll that neither moved the scroller nor rendered a new card has to say so rather than return the
`ok` that means "CDP did not throw".

Run from apps/mcp:  ../../.venv/bin/python -m pytest tests/test_job_list_traversal.py -q
"""

import asyncio

from app.executor.driver import DirectDriver
from app.executor.humanized import HumanizedDriver


class FakeCDP:
    """Records every CDP call and answers Runtime.evaluate from a queue of probe values."""

    def __init__(self, probes=None):
        self.sent = []
        self.probes = list(probes or [])

    async def send(self, method, params=None):
        self.sent.append((method, params or {}))
        if method == "Runtime.evaluate":
            value = self.probes.pop(0) if self.probes else {}
            return {"result": {"value": value}}
        return {"result": {"value": {}}}

    def mouse(self, kind):
        return [p for m, p in self.sent if m == "Input.dispatchMouseEvent" and p.get("type") == kind]


def _probe(at, cards, ids=None, kind="pane", at_end=False, ok=True, key="SearchResultsMainContent"):
    return {"ok": ok, "kind": kind, "cards": cards, "ids": ids or [f"id{i}" for i in range(cards)],
            "hover": {"x": 320, "y": 400}, "at": at, "height": 5000, "client": 800,
            "at_end": at_end,
            "container": {"testid": "lazy-column", "component": "LazyColumn", "key": key}}


# --- the driver seams -------------------------------------------------------------------
def test_scroll_at_wheels_where_it_was_told_and_moves_the_cursor_there_first():
    """The pointer position IS the argument. A wheel dispatched at mid-viewport on a two-column app
    lands on whichever column happens to be there — so the caller passes a point it measured inside
    the list, and the driver hovers it before turning the wheel (what a hand does, and what a list
    binding on mouseenter needs)."""
    cdp = FakeCDP()
    asyncio.run(HumanizedDriver(seed=7).scroll_at(cdp, 320.0, 400.0, 700.0))
    wheels = cdp.mouse("mouseWheel")
    assert wheels, "no wheel event was dispatched"
    assert all(w["x"] == 320.0 and w["y"] == 400.0 for w in wheels)
    assert sum(w["deltaY"] for w in wheels) == 700.0     # the plan lands on the asked-for total
    assert len(wheels) > 1, "one instant notch is the robotic signature, not a human scroll"
    # the cursor arrived before the wheel turned
    first_wheel = next(i for i, (m, p) in enumerate(cdp.sent)
                       if m == "Input.dispatchMouseEvent" and p.get("type") == "mouseWheel")
    assert any(p.get("type") == "mouseMoved" for _m, p in cdp.sent[:first_wheel])


def test_a_negative_total_walks_back_up_the_list():
    cdp = FakeCDP()
    asyncio.run(HumanizedDriver(seed=3).scroll_at(cdp, 100.0, 200.0, -500.0))
    assert sum(w["deltaY"] for w in cdp.mouse("mouseWheel")) == -500.0


def test_direct_driver_still_teleports_and_wheels_once():
    """The robotic baseline is unchanged — tests and record-only runs depend on it not moving."""
    cdp = FakeCDP()
    asyncio.run(DirectDriver().scroll_at(cdp, 10.0, 20.0, 600.0))
    assert len(cdp.mouse("mouseWheel")) == 1
    assert not cdp.mouse("mouseMoved")


def test_click_at_presses_with_an_approach_and_a_dwell():
    """The card click was three bare mouse events at the centre: trusted, but a teleport, while
    every other click in the system approaches and jitters. `click_at` is that same hand."""
    cdp = FakeCDP()
    asyncio.run(HumanizedDriver(seed=11).click_at(cdp, 500.0, 300.0))
    assert len(cdp.mouse("mouseMoved")) > 3, "no approach path — that is a teleport"
    press, release = cdp.mouse("mousePressed"), cdp.mouse("mouseReleased")
    assert len(press) == 1 and len(release) == 1
    assert abs(press[0]["x"] - 500.0) <= 3 and abs(press[0]["y"] - 300.0) <= 3  # jitter, not exact


# --- the evidence ------------------------------------------------------------------------
def test_a_scroll_that_moved_the_list_landed():
    from app.main_server import _scroll_job_list
    cdp = FakeCDP(probes=[_probe(at=0, cards=7), _probe(at=800, cards=14)])
    out = asyncio.run(_scroll_job_list(cdp, driver=DirectDriver(), settle_seconds=0.0))
    assert out["landed"] is True
    assert out["moved"] == 800
    assert out["cards_before"] == 7 and out["cards_after"] == 14
    assert len(out["new_ids"]) == 7
    assert out["detail"] == ""


def test_a_scroll_that_only_rendered_new_cards_still_landed():
    """A list that renders in place grows on almost no movement, so movement alone cannot be the
    test — that is how a working scroll gets reported as exhausted."""
    from app.main_server import _scroll_job_list
    cdp = FakeCDP(probes=[_probe(at=400, cards=7, ids=["a", "b"]),
                          _probe(at=400, cards=9, ids=["a", "b", "c"])])
    out = asyncio.run(_scroll_job_list(cdp, driver=DirectDriver(), settle_seconds=0.0))
    assert out["moved"] == 0 and out["new_ids"] == ["c"]
    assert out["landed"] is True


def test_a_wheel_that_changed_nothing_says_so_instead_of_ok():
    """THE ACTUAL BUG. Every call in the old chain returned ok while the list sat still. `landed`
    is False here and the detail names both suspects — a pointer over a column that does not
    scroll, or the end of the list."""
    from app.main_server import _scroll_job_list
    same = _probe(at=1200, cards=7, ids=["a"])
    cdp = FakeCDP(probes=[same, dict(same)])
    out = asyncio.run(_scroll_job_list(cdp, driver=DirectDriver(), settle_seconds=0.0))
    assert out["ok"] is True          # the mechanism ran…
    assert out["landed"] is False     # …and it accomplished nothing, which is the answer that matters
    assert "does not scroll" in out["detail"] and "at_end" in out["detail"]


def test_no_cards_is_not_a_scroll_failure_but_it_is_reported():
    """A page with no job anchors has no list to scroll. Saying "no results list found" keeps a
    caller from reading a stopped scroll as a finished one."""
    from app.main_server import _scroll_job_list
    cdp = FakeCDP(probes=[{"ok": False, "cards": 0, "reason": "no visible /jobs/view/ anchors"}])
    out = asyncio.run(_scroll_job_list(cdp, driver=DirectDriver(), settle_seconds=0.0))
    assert out["ok"] is False and out["landed"] is False
    assert "anchors" in out["detail"]
    assert not cdp.mouse("mouseWheel"), "wheeled a page with no list"


# --- bringing a card into view -----------------------------------------------------------
def test_a_rendered_but_offscreen_card_is_wheeled_by_the_measured_distance():
    from app.main_server import _bring_card_into_view

    measures = [{"found": True, "in_view": False, "x": 300, "y": 1400, "delta_y": 1000},
                {"found": True, "in_view": True, "x": 300, "y": 420, "delta_y": 20}]

    async def measure():
        return measures.pop(0)

    cdp = FakeCDP(probes=[_probe(at=0, cards=7), _probe(at=1000, cards=12)])
    box, steps = asyncio.run(_bring_card_into_view(cdp, DirectDriver(), measure))
    assert box["in_view"] is True and len(steps) == 1
    assert cdp.mouse("mouseWheel")[0]["deltaY"] == 1000.0   # the distance it measured, not a guess


def test_an_unrendered_card_is_hunted_downward_and_gives_up_honestly():
    """The virtualised list has not reached the row, so there is no distance to use — we walk down a
    batch at a time like a person scanning. When the list stops moving we stop, and the caller gets
    the batches as evidence instead of a bare "not found"."""
    from app.main_server import _bring_card_into_view

    async def measure():
        return {"found": False, "reason": "no node for this id (not rendered yet?)"}

    stuck = _probe(at=3000, cards=7, ids=["a"], at_end=True)
    cdp = FakeCDP(probes=[stuck, dict(stuck)])
    box, steps = asyncio.run(_bring_card_into_view(cdp, DirectDriver(), measure, max_batches=5))
    assert box["found"] is False
    assert len(steps) == 1 and steps[0]["landed"] is False   # stopped at the end, not after 5 tries


def test_a_move_measured_on_two_different_containers_is_not_a_move():
    """THE ONE THAT COST A LIVE DRIVE. When the card selector was wrong, the walk started from the
    DETAIL PANE's title anchor, called the pane "the list", wheeled it 561px and reported
    `landed: true` while the results list sat at scrollTop 0. `moved` is a difference of two scroll
    positions, and subtracting one element's position from another's is a number with no meaning —
    so if the container is not the same element before and after, nothing is claimed."""
    from app.main_server import _scroll_job_list
    cdp = FakeCDP(probes=[_probe(at=0, cards=7, ids=["a"], key="SearchResultsMainContent"),
                          _probe(at=561, cards=7, ids=["a"], key="JobDetailsPane")])
    out = asyncio.run(_scroll_job_list(cdp, driver=DirectDriver(), settle_seconds=0.0))
    assert out["moved"] == 0, "561px of the WRONG column was counted as progress"
    assert out["landed"] is False
    assert "changed identity" in out["detail"]


def test_the_same_container_moving_is_still_a_move():
    """The guard must not swallow the real case it sits next to."""
    from app.main_server import _scroll_job_list
    cdp = FakeCDP(probes=[_probe(at=0, cards=25, ids=["a"]), _probe(at=700, cards=25, ids=["a"])])
    out = asyncio.run(_scroll_job_list(cdp, driver=DirectDriver(), settle_seconds=0.0))
    assert out["moved"] == 700 and out["landed"] is True
    assert out["container"]["testid"] == "lazy-column"
