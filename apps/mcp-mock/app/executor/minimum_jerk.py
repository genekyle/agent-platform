"""MinimumJerkDriver — DirectDriver + a smooth synthesized path (Phase 6).

Subclasses DirectDriver and only overrides the path generation: instead of
teleporting to the target, it dispatches a sequence of mouseMoved events along a
5th-order minimum-jerk curve (the same profile the Movement Playground previews
in JS) before the click. Deterministic + smooth, but identical every run — the
robotic baseline the diffusion input-model will eventually replace by learning
the user's real variability. OFF by default (EXECUTOR_DRIVER=direct); selected
via EXECUTOR_DRIVER=minimum_jerk.
"""

from __future__ import annotations

from typing import Optional

from .driver import DirectDriver


def min_jerk_points(p0: tuple[float, float], p1: tuple[float, float], steps: int = 24) -> list[tuple[float, float]]:
    """5th-order minimum-jerk path s(u) = 10u^3 - 15u^4 + 6u^5, in CSS px."""
    pts: list[tuple[float, float]] = []
    for i in range(steps + 1):
        u = i / steps
        s = 10 * u ** 3 - 15 * u ** 4 + 6 * u ** 5
        pts.append((p0[0] + (p1[0] - p0[0]) * s, p0[1] + (p1[1] - p0[1]) * s))
    return pts


class MinimumJerkDriver(DirectDriver):
    name = "minimum_jerk"

    def __init__(self, steps: int = 24) -> None:
        self.steps = steps

    async def _path_to(self, x: float, y: float, start: Optional[tuple[float, float]]):
        if not start:
            return None  # no known cursor origin -> behave like DirectDriver
        return min_jerk_points(start, (x, y), self.steps)
