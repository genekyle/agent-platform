"""The session window — the controller's tab manager, as a PURE function over a tab list.

--------------------------------------------------------------------------------------
Why this exists (operator-directed 2026-07-23)
--------------------------------------------------------------------------------------
The controller drove tabs it could not see. `Bundle` carried `url` and `state` — SINGULAR — so
`decide()` could not reason about the window it was operating in, the supervisor could not name a
tab fault, and the maturity registry could never learn one. Tab awareness did exist, but on the
OTHER stack: `apply_state_store.Blackboard.world` carries `tabs`/`active_tab_index`, and
`search_cadence.BOUNDS["tab_hygiene"]` states the rule in prose. Neither was wired to the
controller.

Three separate incidents on 2026-07-22 were all this one missing component:

1. `/capture` captured the frontmost tab — a STALE post-apply tab from an earlier session —
   instead of the addressed one, and four corpus rows were written with the driven page's label
   and a different page's content.
2. Clicking Apply opened the application in a NEW tab and the drive had no idea, so a successful
   teacher action read as no progress (`TakeoverResult.new_tab_id` closed that half).
3. A review-module tab left open ~50 minutes went stale, so pressing Submit bounced the flow back
   to resume-selection instead of submitting.

None of those are page-recognition failures. They are all "we do not know what else is open".

--------------------------------------------------------------------------------------
Pure, and reporting-first
--------------------------------------------------------------------------------------
Everything here is a pure function over a list of `{tab_id, url}`: no IO, no CDP, no HTTP, so the
whole policy is unit-testable without a browser — the same discipline as `interaction/authority.py`
and `supervision.classify`. The caller does the listing and any closing.

And the default posture is REPORT, not close. A janitor that silently closes tabs is indistinguishable
from a bug when it is wrong, and the operator's window is shared with a human who may have opened
something themselves. `plan_hygiene` returns what it would close AND why; acting on that is the
caller's decision, gated by its own flag.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

#: How many page tabs a session should hold before we consider it cluttered. Not a hard limit —
#: exceeding it is a CONDITION the reasoner and supervisor can see, not an error. Four is the
#: working shape of an apply drive: the search results, the application, one errand (a login-code
#: mail tab), and one spare.
TAB_BUDGET = 4

#: Roles a tab can play in a career-search session. The vocabulary is deliberately small: it exists
#: to answer "may I close this?" and "where is the work?", not to describe pages — that is the state
#: observer's job, and duplicating it here would create a second, drifting page classifier.
ROLE_SEARCH = "search"        # the results list we return to between applications
ROLE_APPLY = "apply"          # an application in progress (smartapply / an ATS)
ROLE_ERRAND = "errand"        # a cross-domain detour, e.g. mail for a login code
ROLE_TERMINAL = "terminal"    # finished and inert: a post-apply confirmation
ROLE_BLANK = "blank"          # about:blank / new tab / devtools — never work
ROLE_UNKNOWN = "unknown"      # anything we cannot place. NEVER closable (see below).

#: URL markers, most specific first. Terminal is checked before apply on purpose: a post-apply
#: confirmation lives under the same smartapply host as a live application, and calling it "apply"
#: is exactly the mistake that had us capture a finished tab as if it were the work.
_TERMINAL_MARKERS = ("/post-apply", "/application-submitted", "myworkdayjobs.com/thank-you")
_APPLY_MARKERS = ("smartapply.indeed.com", "myworkdayjobs.com", "greenhouse.io", "lever.co",
                  "icims.com", "appvault", "/apply", "applystart")
_SEARCH_MARKERS = ("indeed.com/jobs", "indeed.com/q-", "linkedin.com/jobs/search")
_ERRAND_MARKERS = ("mail.google.com", "gmail.com", "accounts.google.com", "outlook.")
_BLANK_MARKERS = ("about:blank", "chrome://", "devtools://", "chrome-extension://")


def classify_tab(url: str) -> str:
    """A tab's ROLE from its url. Unknown is a real answer and the safe one."""
    u = (url or "").strip().lower()
    if not u:
        return ROLE_BLANK
    if any(m in u for m in _BLANK_MARKERS):
        return ROLE_BLANK
    if any(m in u for m in _TERMINAL_MARKERS):
        return ROLE_TERMINAL
    if any(m in u for m in _SEARCH_MARKERS):
        return ROLE_SEARCH
    if any(m in u for m in _APPLY_MARKERS):
        return ROLE_APPLY
    if any(m in u for m in _ERRAND_MARKERS):
        return ROLE_ERRAND
    return ROLE_UNKNOWN


@dataclass(frozen=True)
class TabInfo:
    """One page tab, as the controller sees it."""
    tab_id: str
    url: str
    role: str
    is_active: bool = False

    @property
    def short_url(self) -> str:
        """A compact, PII-free rendering for prompts and the cockpit — host + last path segment."""
        u = re.sub(r"^https?://", "", self.url or "")
        u = u.split("?")[0].rstrip("/")
        bits = u.split("/")
        return bits[0] if len(bits) == 1 else f"{bits[0]}/…/{bits[-1]}"[:64]


@dataclass(frozen=True)
class WindowState:
    """Everything the controller should know about the window it is operating in."""
    tabs: tuple[TabInfo, ...] = ()
    active_tab_id: str = ""
    closable: tuple[TabInfo, ...] = ()
    reasons: tuple[str, ...] = ()
    budget: int = TAB_BUDGET

    @property
    def count(self) -> int:
        return len(self.tabs)

    @property
    def over_budget(self) -> bool:
        return self.count > self.budget

    @property
    def active(self) -> Optional[TabInfo]:
        return next((t for t in self.tabs if t.tab_id == self.active_tab_id), None)

    def by_role(self, role: str) -> tuple[TabInfo, ...]:
        return tuple(t for t in self.tabs if t.role == role)

    def as_dict(self) -> dict[str, Any]:
        """The compact projection carried on the Bundle. Small on purpose — this rides in a prompt
        and into every journaled row, so it holds counts and roles, never full urls per tab."""
        return {
            "count": self.count,
            "budget": self.budget,
            "over_budget": self.over_budget,
            "active_role": self.active.role if self.active else None,
            "active_tab_id": self.active_tab_id,
            "roles": {r: len(self.by_role(r)) for r in
                      (ROLE_SEARCH, ROLE_APPLY, ROLE_ERRAND, ROLE_TERMINAL, ROLE_BLANK,
                       ROLE_UNKNOWN) if self.by_role(r)},
            "closable": [{"tab_id": t.tab_id, "role": t.role, "url": t.short_url}
                         for t in self.closable],
            "reasons": list(self.reasons),
        }


def survey(tabs: Iterable[dict[str, Any]], *, active_tab_id: str = "",
           budget: int = TAB_BUDGET) -> WindowState:
    """A raw tab list -> the window, with hygiene already planned.

    `tabs` is whatever the lister returns: dicts carrying at least `tab_id` (or `id`) and `url`.
    """
    infos: list[TabInfo] = []
    for raw in tabs or ():
        if not isinstance(raw, dict):
            continue
        tid = str(raw.get("tab_id") or raw.get("id") or "")
        url = str(raw.get("url") or "")
        if not tid:
            continue
        infos.append(TabInfo(tab_id=tid, url=url, role=classify_tab(url),
                             is_active=bool(active_tab_id) and tid == active_tab_id))
    ordered = tuple(infos)
    closable, reasons = plan_hygiene(ordered, active_tab_id=active_tab_id, budget=budget)
    return WindowState(tabs=ordered, active_tab_id=active_tab_id, closable=closable,
                       reasons=reasons, budget=budget)


def plan_hygiene(tabs: tuple[TabInfo, ...], *, active_tab_id: str = "",
                 budget: int = TAB_BUDGET) -> tuple[tuple[TabInfo, ...], tuple[str, ...]]:
    """What SHOULD be closed, and why. Pure; closes nothing.

    Four rails, and each one is a mistake we can point at rather than a precaution:

    * **Never the active tab.** Closing the page we are driving is the obvious catastrophe.
    * **Never the last tab.** `/close_tab` already refuses this; stating it here too means the
      plan is correct on its own rather than correct because a downstream endpoint saves it.
    * **Never an UNKNOWN role.** The operator shares this window. A tab we cannot classify might
      be theirs, and "I could not identify it" is the weakest possible reason to close something.
    * **Never the only SEARCH tab.** It is the drive's home base between applications
      (`BOUNDS.tab_hygiene`), and reopening it means a fresh page load, which costs real data.
    """
    if len(tabs) <= 1:
        return (), ()

    out: list[TabInfo] = []
    why: list[str] = []
    searches = [t for t in tabs if t.role == ROLE_SEARCH]

    for t in tabs:
        if t.tab_id == active_tab_id:
            continue
        if t.role in (ROLE_TERMINAL, ROLE_BLANK):
            if t.role == ROLE_SEARCH and len(searches) <= 1:
                continue
            out.append(t)
            why.append(f"{t.role}:{t.short_url} — finished or empty, it holds no work")

    # Over budget: retire the OLDEST duplicate applications, never the newest (that is where the
    # work most likely is) and never the active one. Only kicks in past the budget, so a normal
    # two-tab drive never trips it.
    if len(tabs) - len(out) > budget:
        applies = [t for t in tabs
                   if t.role == ROLE_APPLY and t.tab_id != active_tab_id and t not in out]
        for t in applies[:-1]:                       # keep the newest
            out.append(t)
            why.append(f"apply:{t.short_url} — over the {budget}-tab budget and superseded")

    # The last-tab rail, applied to the PLAN rather than trusted downstream.
    if len(out) >= len(tabs):
        out = out[:len(tabs) - 1]

    return tuple(out), tuple(why)


# The `# WINDOW` prompt block lives in `interaction.decision.window_to_prompt`, beside
# `bundle_to_prompt` — the prompt is the frozen feature contract, so its every part belongs in the
# contract module, and `interaction` must not import from the control plane.
