"""Execution style — HOW an action is performed, as distinct from WHAT it is.

The split this module exists to enforce (docs/PLAN_interaction_api.md; the WHAT-vs-HOW note):
the **Intent** is canonical and frozen — `click the distance pill`, `type the query` — and it is
what trains the models. The **style** is how that intent is carried out in time: how long we
settle after an action, how long we wait between two of them. Style varies run to run; intent
never does. Keeping them apart is what lets pacing change without churning the training corpus.

Built after the operator watched a step finish in about half a second: `set_distance` fired with
no pause at all, and the query drive used three hard-coded sleeps. Both are "how", both were
scattered at their call sites, and neither varied.

**Style is not a safety mechanism, and must never be mistaken for one.** Varying our cadence does
not defeat bot detection — a captcha does not care how fast we typed (the standing note on this:
"typing speed doesn't bypass captcha"). The bot-safety FLOOR lives in `search_cadence.BOUNDS` and
applies to every style including `fast`; style only decides how far ABOVE that floor a given run
sits. A style that could dip under the floor would be a safety regression wearing a feature's
clothes, so `pause_for` clamps and there is no way to opt out.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

#: What a pause is FOR. The distinction that matters is navigation vs everything else: a pause
#: after something that loads a page is bounded by bot-safety, an in-page pause is not.
SETTLE = "settle"          # after an action, before reading the result back
BETWEEN = "between"        # between two actions in one sequence
NAVIGATION = "navigation"  # after something that loads a page (a search, a pagination click)


@dataclass(frozen=True)
class ExecutionStyle:
    """One way of spending time around actions. Ranges are (low, high) seconds, sampled uniformly."""

    name: str
    settle: tuple[float, float]
    between: tuple[float, float]
    navigation: tuple[float, float]
    why: str

    def range_for(self, kind: str) -> tuple[float, float]:
        return {SETTLE: self.settle, BETWEEN: self.between,
                NAVIGATION: self.navigation}.get(kind, self.settle)


#: `fast` is the behaviour as it was — kept, named, and no longer the only option. It is honest
#: about what it is: the machine's pace, useful when the operator is watching a step and wants the
#: answer now. It still cannot undercut the navigation floor.
FAST = ExecutionStyle(
    name="fast",
    settle=(0.2, 0.4),
    between=(0.3, 0.6),
    navigation=(2.0, 3.0),
    why="machine pace — for supervised stepping where the operator wants the result immediately",
)

#: `human` is the default: roughly 1.5s around each action, which is what an unhurried person
#: actually does — read the control, move, act, glance at the result.
HUMAN = ExecutionStyle(
    name="human",
    settle=(1.0, 2.0),
    between=(1.2, 2.2),
    # Kept close to the floor rather than well above it. A navigation pause is mostly "wait for
    # the page to render", and the operator asked for ~1.5s around an action, not a long stall —
    # so this sits at the floor the bot-safety bound already sets and does not inflate past it.
    navigation=(3.0, 4.0),
    why="an unhurried person: read the control, move, act, glance at what happened",
)

#: `unhurried` is the long tail. A cadence that is always ~1.5s is its own kind of signature —
#: real sessions contain pauses where someone read something, or looked away.
UNHURRIED = ExecutionStyle(
    name="unhurried",
    settle=(2.0, 3.5),
    between=(2.5, 4.0),
    navigation=(3.5, 6.0),
    why="someone reading the page, or briefly distracted — the long tail real sessions have",
)

STYLES = {s.name: s for s in (FAST, HUMAN, UNHURRIED)}

#: How often each style is chosen when nobody asks for one. Weighted rather than uniform because
#: the point is a believable DISTRIBUTION, not an even split: mostly ordinary pace, sometimes
#: slower, rarely brisk.
_WEIGHTS = {"human": 0.70, "unhurried": 0.25, "fast": 0.05}


def pick_style(name: Optional[str] = None,
               rng: Optional[random.Random] = None) -> ExecutionStyle:
    """The style for one action sequence.

    Chosen ONCE per sequence rather than per pause, so a single drive is internally coherent —
    a person is not brisk and dawdling in the same five seconds. Variation lives ACROSS sequences.
    An explicit `name` always wins, so the operator (and tests) can pin it.
    """
    if name:
        style = STYLES.get(name)
        if style is None:
            raise ValueError(f"unknown execution style {name!r}; have {sorted(STYLES)}")
        return style
    r = rng or random
    return STYLES[r.choices(list(_WEIGHTS), weights=list(_WEIGHTS.values()), k=1)[0]]


def pause_for(style: ExecutionStyle, kind: str = SETTLE, *,
              rng: Optional[random.Random] = None) -> float:
    """How many seconds to wait, sampled from the style.

    Navigation pauses are clamped UP to the bot-safety floor for every style, `fast` included:
    the floor is a property of what is safe to do to a real site, not of how eager this run is.
    """
    r = rng or random
    low, high = style.range_for(kind)
    seconds = r.uniform(low, high)
    if kind == NAVIGATION:
        import search_cadence
        seconds = max(seconds, float(search_cadence.BOUNDS["min_seconds_between_navigations"]))
    return seconds


def describe(style: ExecutionStyle) -> dict:
    """For the panel and the journal — the operator should be able to see the pace a step ran at,
    since 'it felt too quick' is otherwise unfalsifiable."""
    return {"style": style.name, "why": style.why,
            "settle_s": list(style.settle), "between_s": list(style.between),
            "navigation_s": list(style.navigation)}
