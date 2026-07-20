"""The unexpected-state policy — "we are not where we assumed", decided in ONE place.

Why this module exists. The rule *"a stale-state outcome means re-observe before retrying — do
it ONCE, then escalate"* was written inline in `controller/loop.py` and nowhere else, so the two
other loops that need it reinvented it (or, in the login drive's case, simply didn't have it and
died opaquely on a stale tab — 2026-07-19). This is that rule as a pure function both can call.

It is a shared **policy**, not a shared loop: the caller still owns its own observe/act/verify
mechanics, and only asks this module the one question it must not answer differently in two
places — *given what came back, do I re-observe, escalate, or carry on?*

Two levels of "not where we assumed" answer to the same rule, which is the point:

  * **protocol level** — `STALE_STATE_OUTCOMES` (`not_found` / `not_opened` / `ambiguous`): the
    control we addressed isn't on the page we thought.
  * **tab level** — `STALE_TAB`: the CDP target we pinned is gone entirely (an SSO redirect
    spawned a new target, an OOPIF iframe form was replaced after submit, the operator reloaded
    the Sign-In screen). Deliberately NOT an `Outcome` member — it is one level above the
    protocol outcomes (the whole tab, not one control) — but it obeys the same policy.

`BLOCKED` must never reach here: a captcha/challenge/session block goes straight to the human and
is never retried (`loop.py` handles it before this call). Nothing in this module ever auto-retries
more than once — "try harder" is exactly the behaviour the stale taxonomy exists to prevent.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from interaction.contract import STALE_STATE_OUTCOMES

#: Protocol-level staleness, as Outcome *values* (the loops compare strings off the wire).
STALE_OUTCOMES = frozenset(o.value for o in STALE_STATE_OUTCOMES)

#: Tab-level staleness — the pinned CDP target is gone. See the module docstring.
STALE_TAB = "stale_tab"


class Response(str, Enum):
    """What the caller should do next. Three answers, no fourth."""

    CONTINUE = "continue"        # it verified — carry on, and reset the retry/escalation streaks
    RE_OBSERVE = "re_observe"    # not where we assumed — re-observe/re-resolve ONCE, then retry
    ESCALATE = "escalate"        # hand up the ladder; never guess, never loop


def is_stale(outcome: Optional[str]) -> bool:
    """True when `outcome` means "the page/tab is not in the state the caller assumed" — at
    either level. Anything else (including BLOCKED and ERROR) is not a re-observe case."""
    return bool(outcome) and (outcome in STALE_OUTCOMES or outcome == STALE_TAB)


def respond(outcome: Optional[str], *, verified: bool = False,
            already_retried: bool = False) -> Response:
    """The whole policy.

    `verified` wins: a verified action always continues. Otherwise a stale outcome earns exactly
    one re-observe (`already_retried` is the caller's one-shot latch), and everything else — a
    second consecutive stale, a non-stale failure — escalates. Callers reset their latch on
    CONTINUE, set it on RE_OBSERVE, and clear it when they escalate.
    """
    if verified:
        return Response.CONTINUE
    if is_stale(outcome) and not already_retried:
        return Response.RE_OBSERVE
    return Response.ESCALATE
