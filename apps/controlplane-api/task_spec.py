"""TaskSpec — declares when a task is DONE, so the loop can terminate on success.

Without a terminal-state predicate the runtime loop can only stop on escalation or on
`max_steps` — a FINISHED flow then looks identical to a STALLED one (both just "ran out").
A TaskSpec gives each task a cheap, deterministic "are we there yet?" check matched against
the live URL / page text, so `run_loop` returns COMPLETED the moment the goal state is
reached, and a MAX_STEPS result becomes an explicit "did NOT reach the goal — review",
not an ambiguous stop. This is the difference between "task complete" and "dropped it".

Specs are seeded here from known terminal states and refine as live captures confirm the
real success page (same teacher→distill pattern as the recipes). A task is matched either
explicitly by `name` or by a `goal_aliases` substring in the free-text task_goal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class TaskSpec:
    name: str
    description: str = ""
    goal_aliases: tuple[str, ...] = ()            # task_goal substrings that select this spec
    terminal_url_patterns: tuple[str, ...] = ()   # regex (case-insensitive) over the URL
    terminal_text: tuple[str, ...] = ()           # case-insensitive substrings in page text

    def is_complete(self, url: str, page_text: str = "") -> bool:
        u = url or ""
        if any(re.search(p, u, re.I) for p in self.terminal_url_patterns):
            return True
        t = (page_text or "").lower()
        return any(s.lower() in t for s in self.terminal_text)


# Seeded terminal specs. URL patterns are the strong signal; text is the fallback for
# SPA states that don't change the URL. Refine against live captures of the success page.
TASK_SPECS: list[TaskSpec] = [
    TaskSpec(
        name="indeed_apply",
        description="Apply to an Indeed job (quick-apply spine ends at the post-apply page).",
        goal_aliases=("apply", "quick apply", "apply with indeed"),
        terminal_url_patterns=(r"/post-apply", r"application-submitted"),
        terminal_text=("your application has been submitted", "application submitted"),
    ),
    TaskSpec(
        name="facebook_login",
        description="Log in to Facebook — done once the authed home/feed is shown.",
        goal_aliases=("facebook login", "log in to facebook", "sign in to facebook"),
        # NOTE: facebook.com/ serves BOTH the logged-out login wall AND the logged-in feed, so
        # the URL cannot tell them apart (a live run proved a bare-domain URL pattern falsely
        # reports "done" on the login wall). The composer text is the only reliable authed signal;
        # /home.php is an authed-only URL, kept as a narrow secondary.
        terminal_url_patterns=(r"facebook\.com/home\.php", r"facebook\.com/home/"),
        terminal_text=("what's on your mind", "create a post", "create story"),
    ),
    TaskSpec(
        name="facebook_create_listing",
        description="Create a Facebook Marketplace listing — done once the item page/confirmation shows.",
        goal_aliases=("create a listing", "create listing", "marketplace listing", "list an item"),
        terminal_url_patterns=(r"facebook\.com/marketplace/item/", r"marketplace/you/selling"),
        terminal_text=("your listing is being published", "listing is now live",
                       "your item is now listed"),
    ),
]


def spec_for(*, task: Optional[str] = None, task_goal: str = "") -> Optional[TaskSpec]:
    """Resolve a TaskSpec by explicit name, else by a goal_aliases substring match."""
    if task:
        for s in TASK_SPECS:
            if s.name == task:
                return s
    g = (task_goal or "").lower()
    for s in TASK_SPECS:
        if any(a in g for a in s.goal_aliases):
            return s
    return None


def is_done_for(spec: Optional[TaskSpec]) -> Optional[Callable[[Any], bool]]:
    """Build an `is_done(observation)` predicate for run_loop from a spec (None → no
    terminal check, so the loop falls back to max_steps as before)."""
    if spec is None:
        return None

    def _done(observation: Any) -> bool:
        return spec.is_complete(getattr(observation, "url", "") or "",
                                getattr(observation, "page_text", "") or "")

    return _done
