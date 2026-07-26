"""Staleness — how old is what we are looking at, and is it still safe to act on?

**PROTOTYPE (2026-07-26). The thresholds in `THRESHOLDS` are GUESSES, not measurements.** They are
here to start producing labelled evidence, not to be trusted. See "Calibration" below before
changing behaviour on the strength of a level.

--------------------------------------------------------------------------------------
The question nothing answered
--------------------------------------------------------------------------------------
Three modules already sit next to this one and none of them answer it:

    perception   : WHERE are we                (which state is this)
    reach.py     : CAN we touch it             (do the controls exist)
    unexpected.py: is this WHERE WE EXPECTED   (did we land somewhere else)
    staleness    : is what we are looking at STILL TRUE, and what does that cost us

A drive can be perfectly oriented, fully reachable, exactly where it expected — on a view that
went stale twenty minutes ago. Every one of those modules answers "yes" and the action still
fails, because freshness is an axis of its own. This session alone: a session left open two days
whose auth rung read a tab from another site; a Chrome relaunched windowless holding a profile;
a submit that raced a navigation. None of those are perception faults.

--------------------------------------------------------------------------------------
The shape: a datapoint, not a gate
--------------------------------------------------------------------------------------
Staleness rides ALONG with every observation as a datapoint — `Bundle.staleness` — the way
`belief` does. It advises; it does not act. The caller decides, because the right response is
context-dependent and one of the responses is destructive:

    CONTINUE : the page is operable. Carry on.
    REFRESH  : reload in place — cheap, and it costs nothing but a page load.
    RENEW    : the view cannot be repaired by reloading (logged out, session gone). Get a fresh
               state: re-navigate from the front door, re-auth, or provision a new session.
    HANDOFF  : we cannot SEE the page well enough to judge. Never guess a remedy from a blind
               reading — that is how a refresh lands on a page nobody looked at.

**THE VERDICT COMES FROM THE LOUDEST SIGNAL, NOT FROM THE LEVEL.** Corrected on the first live
test (2026-07-26), which is the whole reason to test a prototype against a real stale session
rather than a fixture: a results page left 14.5 hours — still authenticated, still answering with
210 controls — scored RED on age and proposed RENEW. That would have destroyed a working session
to fix out-of-date search results. Two different kinds of bad were being collapsed into one:

    the CONTENT is old      -> REFRESH. A reload is exactly the cure.
    the SESSION is gone     -> RENEW.   A reload lands on a login wall.

So every signal carries its own `remedy` and the loudest one decides. Level says *how suspect*;
remedy says *what to do about it*. Age can reach RED and still only ever ask for a reload.

And the signal that outranks all the clock-based ones: `responsive` — did the page just answer.
Every other signal INFERS staleness from elapsed time; that one reads it directly. It cannot raise
the level (a responsive page can still be showing yesterday's results) but it is the evidence the
time signals are a proxy for, and it is recorded as such.

**A REFRESH IS DESTRUCTIVE WHEN THE PAGE HOLDS UNSAVED WORK.** A half-filled Workday application
is exactly the case: reloading it throws away typed answers that cost real effort, and the
staleness that prompted the reload was never worth that. So `holds_unsaved_work` downgrades
REFRESH to CONTINUE and RENEW to HANDOFF, always, at every level. Freshness is not worth more
than work.

--------------------------------------------------------------------------------------
Calibration — how this stops being a guess
--------------------------------------------------------------------------------------
`as_dict()` journals the RAW SIGNAL VALUES, not just the level. That is the point of the
prototype: every drive writes rows carrying `idle_s`, `page_age_s`, `cookie_ttl_s` alongside what
actually happened next, so the thresholds can be fitted from our own history instead of
re-instrumented later. The research question is narrow and answerable from those rows:

    for each signal, at what value does the NEXT action's failure rate rise?

Until that is measured, treat levels as ordering, not as truth: ORANGE means "more suspect than
YELLOW", not "stale". Two known unknowns, recorded so they are not re-derived:
  * cookie TTL is not yet read anywhere — `cookie_expires_at` is almost always None today, so
    that signal is inert until a CDP `Network.getCookies` lands. UNKNOWN, not fresh.
  * idle time is measured from OUR last action, which says nothing about what the SITE did in the
    meantime. A site-side session timeout can fire at any idle value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# --- levels, ordered ----------------------------------------------------------------
FRESH = "fresh"
YELLOW = "yellow"
ORANGE = "orange"
RED = "red"

#: Ordering is the only thing about the levels that is not provisional.
ORDER = (FRESH, YELLOW, ORANGE, RED)


def worst(levels) -> str:
    """The highest level present — staleness is a max, not an average. One dead signal makes the
    observation suspect no matter how fresh the others look."""
    out = FRESH
    for lv in levels:
        if ORDER.index(lv) > ORDER.index(out):
            out = lv
    return out


# --- verdicts -----------------------------------------------------------------------
CONTINUE = "continue"
REFRESH = "refresh"
RENEW = "renew"
HANDOFF = "handoff"

#: PROVISIONAL. Seconds. Each entry is (yellow_at, orange_at, red_at) — the value at which the
#: signal ENTERS that level. Nothing here is measured; see "Calibration" in the module docstring.
#: Keep every number in this one table so calibration is a diff to a table, never a hunt through
#: branches.
THRESHOLDS: dict[str, tuple[float, float, float]] = {
    # Time since WE last acted. A drive that has been idle is not necessarily stale, but every
    # stale drive we have hit was idle first.
    "idle_s": (5 * 60, 30 * 60, 2 * 60 * 60),
    # Time since the tab last navigated. Long-lived SPA views drift: tokens rotate, lists go
    # out of date, node ids go stale (the FB listing lesson — "node-ids go stale fast").
    "page_age_s": (15 * 60, 60 * 60, 4 * 60 * 60),
    # Time REMAINING on the soonest-expiring session cookie. Inverted: smaller is worse.
    "cookie_ttl_s": (30 * 60, 10 * 60, 2 * 60),
}

#: The version stamped onto every journaled row. BUMP IT when `THRESHOLDS` or the verdict rules
#: change, so rows written under different rules are never pooled in one calibration.
RULES_VERSION = "staleness/proto-1"


@dataclass(frozen=True)
class Signal:
    """One reading, what it means, and WHAT WOULD FIX IT. `value` is kept raw so calibration can
    re-derive `level` from journaled rows under different thresholds.

    `remedy` is the correction that came out of the first live test (2026-07-26). The verdict used
    to be read off the overall LEVEL alone, which conflated two different kinds of bad: a view
    whose CONTENT is old, and a session that is GONE. Age is precisely what a reload cures, so a
    14-hour-old results page — still authenticated, still answering with 210 controls — scored RED
    and proposed RENEW, which would have thrown away a working session to fix stale search results.
    Each signal now names its own cure and the loudest one decides.
    """

    name: str
    level: str
    value: Optional[float] = None
    why: str = ""
    remedy: str = ""          # the verdict THIS signal implies when it is the loudest

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "level": self.level, "value": self.value, "why": self.why,
                "remedy": self.remedy}


@dataclass(frozen=True)
class Evidence:
    """What the caller already knows. Every field is optional and None means NOT MEASURED — which
    is not the same as fresh, and never scores as fresh (today's recurring lesson one layer up:
    an unknown must not read as a regression, nor as an all-clear)."""

    now: float
    logged_in: Optional[bool] = None
    blind_reason: str = ""
    last_action_at: Optional[float] = None
    last_nav_at: Optional[float] = None
    cookie_expires_at: Optional[float] = None
    #: Did the page just ANSWER — probes returned, controls present? Direct evidence, where every
    #: other signal is a clock-based proxy for it. None when nobody checked.
    responsive: Optional[bool] = None
    #: Typed answers / staged widget input that a reload would throw away.
    holds_unsaved_work: bool = False


@dataclass(frozen=True)
class Staleness:
    level: str
    verdict: str
    why: str
    signals: tuple[Signal, ...] = ()
    rules_version: str = RULES_VERSION
    unmeasured: tuple[str, ...] = field(default_factory=tuple)

    @property
    def operable(self) -> bool:
        """Can the drive keep acting without doing anything about this first?"""
        return self.verdict == CONTINUE

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "verdict": self.verdict,
            "why": self.why,
            "rules_version": self.rules_version,
            "unmeasured": list(self.unmeasured),
            "signals": [s.as_dict() for s in self.signals],
        }


def _age_level(key: str, seconds: Optional[float]) -> str:
    """Bigger is worse (age)."""
    if seconds is None:
        return FRESH
    y, o, r = THRESHOLDS[key]
    if seconds >= r:
        return RED
    if seconds >= o:
        return ORANGE
    if seconds >= y:
        return YELLOW
    return FRESH


def _ttl_level(key: str, seconds: Optional[float]) -> str:
    """Smaller is worse (time remaining)."""
    if seconds is None:
        return FRESH
    y, o, r = THRESHOLDS[key]
    if seconds <= r:
        return RED
    if seconds <= o:
        return ORANGE
    if seconds <= y:
        return YELLOW
    return FRESH


def assess(ev: Evidence) -> Staleness:
    """The whole assessment. Pure — no clock, no I/O, no page hit: every input is material the
    caller already had, which is what keeps it from being skipped on the turns it matters.
    """
    signals: list[Signal] = []
    unmeasured: list[str] = []

    # --- BLIND WINS OUTRIGHT. If we cannot see the page, every other signal is a reading of
    # nothing. The remedy is a human, not a reload: refreshing a page nobody could observe is
    # guessing with a page load.
    if ev.blind_reason:
        sig = Signal("blind", RED, None, ev.blind_reason)
        return Staleness(RED, HANDOFF,
                         f"cannot observe the page — {ev.blind_reason}", (sig,),
                         unmeasured=("all — the observation itself failed",))

    # --- logged out: a reload lands on a login wall. Only a new session cures this.
    if ev.logged_in is False:
        signals.append(Signal("logged_in", RED, 0.0, "the session is signed out", remedy=RENEW))
    elif ev.logged_in is None:
        unmeasured.append("logged_in")

    # --- AGE SIGNALS. Their remedy is REFRESH at every level, because a reload is exactly what
    # cures old content. Level says how suspect, `remedy` says what to do about it — conflating
    # the two is what made a healthy 14-hour session propose RENEW on its first live test.
    idle = None if ev.last_action_at is None else max(0.0, ev.now - ev.last_action_at)
    if idle is None:
        unmeasured.append("idle_s")
    signals.append(Signal("idle_s", _age_level("idle_s", idle), idle,
                          "time since this drive last acted", remedy=REFRESH))

    age = None if ev.last_nav_at is None else max(0.0, ev.now - ev.last_nav_at)
    if age is None:
        unmeasured.append("page_age_s")
    signals.append(Signal("page_age_s", _age_level("page_age_s", age), age,
                          "time since the tab last navigated", remedy=REFRESH))

    # --- a dying cookie is NOT cured by reloading: the credential itself is expiring.
    ttl = None if ev.cookie_expires_at is None else max(0.0, ev.cookie_expires_at - ev.now)
    if ttl is None:
        unmeasured.append("cookie_ttl_s")
    signals.append(Signal("cookie_ttl_s", _ttl_level("cookie_ttl_s", ttl), ttl,
                          "time left on the soonest-expiring session cookie", remedy=RENEW))

    # --- LIVENESS IS EVIDENCE; AGE IS ONLY A PRIOR. Everything above infers staleness from the
    # clock. This one is a direct reading of the thing we actually care about — did the page just
    # answer, and are we still who we were. It cannot raise the level (a responsive page can still
    # be showing yesterday's results), but it is recorded because it is the strongest thing we
    # know and it is what the time signals are a proxy FOR.
    if ev.responsive is not None:
        signals.append(Signal("responsive", FRESH if ev.responsive else RED,
                              1.0 if ev.responsive else 0.0,
                              "the page answered our probes" if ev.responsive
                              else "the page did not answer",
                              remedy=CONTINUE if ev.responsive else HANDOFF))
    else:
        unmeasured.append("responsive")

    level = worst(s.level for s in signals)
    # The LOUDEST signal picks the remedy — not the overall level. Ties go to the earliest
    # signal listed, which puts session-death (logged_in) ahead of mere age by construction.
    loudest = max(signals, key=lambda s: ORDER.index(s.level))

    if level in (FRESH, YELLOW):
        verdict = CONTINUE
        why = f"{level}: the page is operable ({loudest.why})"
    else:
        verdict = loudest.remedy or REFRESH
        why = f"{level}: {loudest.why}"
        if verdict == REFRESH:
            why += " — a reload cures this"
        elif verdict == RENEW:
            why += " — reloading cannot repair it"

    # --- WORK OUTRANKS FRESHNESS. Both remedies throw away typed input, and the staleness that
    # prompted them is a suspicion, not a fault. Downgrade rather than destroy: keep going while
    # it is merely suspect, and hand a genuinely dead view to the operator, who can save the work
    # or abandon it knowingly. Never make that call for them.
    if ev.holds_unsaved_work and verdict in (REFRESH, RENEW):
        was = verdict
        verdict = CONTINUE if was == REFRESH else HANDOFF
        why = (f"{why} — but the page holds unsaved work, so {was} is withheld "
               f"({'continuing' if verdict == CONTINUE else 'handing to the operator'})")

    return Staleness(level, verdict, why, tuple(signals), unmeasured=tuple(unmeasured))
