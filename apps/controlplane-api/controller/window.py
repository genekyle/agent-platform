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
# `linkedin.com/jobs` without the `/search` covers the jobs HOME too — the logged-out wall and the
# landing both live there, and the recorder showed them classifying as `unknown`, which is the one
# role hygiene will never touch. Indeed's entry is already prefix-shaped for the same reason.
_SEARCH_MARKERS = ("indeed.com/jobs", "indeed.com/q-", "linkedin.com/jobs")
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


#: Two tabs are the SAME APPLICATION when they are the apply flow of the same host. A given ATS
#: runs exactly one apply flow per session — Indeed opens one `smartapply.indeed.com` window per
#: Apply click, Workday one tenant flow — so a second apply tab on that host is not a second job,
#: it is an ORPHAN: an earlier Apply/re-entry that was left behind (observed live 2026-07-23, two
#: smartapply tabs for one Nichols application). This is the "something is very wrong" the operator
#: asked the tab manager to notice, not merely clutter to retire when over budget.
ANOMALY_EXACT_DUPLICATE = "exact_duplicate"
ANOMALY_DUPLICATE_APPLICATION = "duplicate_application"


def _host(url: str) -> str:
    return re.sub(r"^https?://", "", (url or "")).split("/")[0].split("?")[0].lower()


def _norm_url(url: str) -> str:
    """URL identity for exact-duplicate detection: no scheme case, no fragment, no trailing slash."""
    u = re.sub(r"^https?://", "", (url or ""), flags=re.I)
    return u.split("#")[0].rstrip("/").lower()


@dataclass(frozen=True)
class Anomaly:
    """Something wrong with the window, named — not a tab to close but a SITUATION to flag.

    `keeper` is the tab we would keep if we resolved it; "" means we cannot tell which is the real
    one and must not guess (closing the wrong apply tab discards real progress). `resolvable` is
    exactly `bool(keeper)`: the tab manager only closes a duplicate when it knows which one holds
    the work — otherwise it raises its hand and lets the operator choose.
    """
    kind: str
    why: str
    tab_ids: tuple[str, ...]
    keeper: str = ""

    @property
    def resolvable(self) -> bool:
        return bool(self.keeper)

    def redundant(self) -> tuple[str, ...]:
        return tuple(t for t in self.tab_ids if t != self.keeper)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "why": self.why, "tab_ids": list(self.tab_ids),
                "keeper": self.keeper, "resolvable": self.resolvable}


def detect_anomalies(tabs: tuple["TabInfo", ...], *, active_tab_id: str = "") -> tuple[Anomaly, ...]:
    """Health check over the window. Pure. Finds duplicates the role counts alone cannot see.

    Exact-URL duplicates are unambiguous — the copies are identical, so any one is the keeper and
    the rest are safe to close. Same-application duplicates (two apply tabs on one host) are the
    real hazard: they are the same in-progress application, and the keeper is the one we are
    DRIVING. If we are not driving either (a survey between drives), we flag and stop — picking
    the less-advanced tab to keep would throw away the more-advanced one's work.
    """
    anomalies: list[Anomaly] = []
    seen: set[str] = set()

    # 1 — exact duplicates (any role).
    by_url: dict[str, list[TabInfo]] = {}
    for t in tabs:
        by_url.setdefault(_norm_url(t.url), []).append(t)
    for url, group in by_url.items():
        if len(group) < 2 or not url:
            continue
        ids = tuple(t.tab_id for t in group)
        keeper = active_tab_id if active_tab_id in ids else ids[0]
        anomalies.append(Anomaly(
            kind=ANOMALY_EXACT_DUPLICATE, tab_ids=ids, keeper=keeper,
            why=f"{len(group)} tabs open on the exact same page ({group[0].short_url})"))
        seen.update(ids)

    # 2 — same-application duplicates: >1 apply tab on one host, not already an exact dup.
    by_host: dict[str, list[TabInfo]] = {}
    for t in tabs:
        if t.role == ROLE_APPLY and t.tab_id not in seen:
            by_host.setdefault(_host(t.url), []).append(t)
    for host, group in by_host.items():
        if len(group) < 2:
            continue
        ids = tuple(t.tab_id for t in group)
        # The keeper is the tab we are driving; if none, we do not know which holds the work.
        keeper = active_tab_id if active_tab_id in ids else ""
        anomalies.append(Anomaly(
            kind=ANOMALY_DUPLICATE_APPLICATION, tab_ids=ids, keeper=keeper,
            why=(f"{len(group)} application tabs open on {host} — one apply flow should be open at "
                 f"a time, so the extras are orphaned re-entries"
                 + ("" if keeper else "; not driving any of them, so which to keep is the "
                    "operator's call"))))

    return tuple(anomalies)


@dataclass(frozen=True)
class WindowState:
    """Everything the controller should know about the window it is operating in."""
    tabs: tuple[TabInfo, ...] = ()
    active_tab_id: str = ""
    closable: tuple[TabInfo, ...] = ()
    reasons: tuple[str, ...] = ()
    anomalies: tuple[Anomaly, ...] = ()
    budget: int = TAB_BUDGET

    @property
    def count(self) -> int:
        return len(self.tabs)

    @property
    def over_budget(self) -> bool:
        return self.count > self.budget

    @property
    def health(self) -> str:
        """`ok` | `warn`. A window with an anomaly or over budget is not healthy, and the reasoner,
        the cockpit and the tidy pass all read this one word before the details."""
        return "warn" if (self.anomalies or self.over_budget) else "ok"

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
            "health": self.health,
            "active_role": self.active.role if self.active else None,
            "active_tab_id": self.active_tab_id,
            "roles": {r: len(self.by_role(r)) for r in
                      (ROLE_SEARCH, ROLE_APPLY, ROLE_ERRAND, ROLE_TERMINAL, ROLE_BLANK,
                       ROLE_UNKNOWN) if self.by_role(r)},
            "anomalies": [a.as_dict() for a in self.anomalies],
            "closable": [{"tab_id": t.tab_id, "role": t.role, "url": t.short_url}
                         for t in self.closable],
            "reasons": list(self.reasons),
        }


def survey(tabs: Iterable[dict[str, Any]], *, active_tab_id: str = "",
           budget: int = TAB_BUDGET) -> WindowState:
    """A raw tab list -> the window, with hygiene and health already assessed.

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
    anomalies = detect_anomalies(ordered, active_tab_id=active_tab_id)
    closable, reasons = plan_hygiene(ordered, active_tab_id=active_tab_id, budget=budget,
                                     anomalies=anomalies)
    return WindowState(tabs=ordered, active_tab_id=active_tab_id, closable=closable,
                       reasons=reasons, anomalies=anomalies, budget=budget)


def plan_hygiene(tabs: tuple[TabInfo, ...], *, active_tab_id: str = "",
                 budget: int = TAB_BUDGET,
                 anomalies: tuple[Anomaly, ...] = ()) -> tuple[tuple[TabInfo, ...], tuple[str, ...]]:
    """What SHOULD be closed, and why. Pure; closes nothing.

    Four rails, and each one is a mistake we can point at rather than a precaution:

    * **Never the active tab.** Closing the page we are driving is the obvious catastrophe.
    * **Never the last tab.** `/close_tab` already refuses this; stating it here too means the
      plan is correct on its own rather than correct because a downstream endpoint saves it.
    * **Never an UNKNOWN role.** The operator shares this window. A tab we cannot classify might
      be theirs, and "I could not identify it" is the weakest possible reason to close something.
    * **Never the only SEARCH tab.** It is the drive's home base between applications
      (`BOUNDS.tab_hygiene`), and reopening it means a fresh page load, which costs real data.

    Beyond hygiene it also RESOLVES anomalies: a duplicate whose keeper is known (an exact copy,
    or an orphaned apply flow of the application we are driving) has its redundant tabs retired
    here, regardless of the budget — a duplicate is a fault, not clutter, so it does not wait for
    the window to get crowded. A duplicate whose keeper is unknown is left for the operator; it
    still surfaces as an anomaly, it just is not auto-closed.
    """
    if len(tabs) <= 1:
        return (), ()

    by_id = {t.tab_id: t for t in tabs}
    out: list[TabInfo] = []
    why: list[str] = []
    chosen: set[str] = set()

    def _add(t: TabInfo, reason: str) -> None:
        if t.tab_id in chosen or t.tab_id == active_tab_id:
            return
        chosen.add(t.tab_id)
        out.append(t)
        why.append(reason)

    # 0 — resolvable duplicates first, so the keeper is protected before anything else runs.
    for a in anomalies:
        if not a.resolvable:
            continue
        label = ("orphaned duplicate application — a second apply flow for the job we are already "
                 "driving elsewhere" if a.kind == ANOMALY_DUPLICATE_APPLICATION
                 else "an exact duplicate of another open tab")
        for tid in a.redundant():
            t = by_id.get(tid)
            if t is not None:
                _add(t, f"{a.kind}:{t.short_url} — {label}")

    searches = [t for t in tabs if t.role == ROLE_SEARCH]
    for t in tabs:
        if t.role in (ROLE_TERMINAL, ROLE_BLANK):
            if t.role == ROLE_SEARCH and len(searches) <= 1:
                continue
            _add(t, f"{t.role}:{t.short_url} — finished or empty, it holds no work")

    # Over budget: retire the OLDEST duplicate applications, never the newest (that is where the
    # work most likely is) and never the active one. Only kicks in past the budget, so a normal
    # two-tab drive never trips it.
    if len(tabs) - len(out) > budget:
        applies = [t for t in tabs
                   if t.role == ROLE_APPLY and t.tab_id != active_tab_id and t.tab_id not in chosen]
        for t in applies[:-1]:                       # keep the newest
            _add(t, f"apply:{t.short_url} — over the {budget}-tab budget and superseded")

    # The last-tab rail, applied to the PLAN rather than trusted downstream.
    if len(out) >= len(tabs):
        out = out[:len(tabs) - 1]

    return tuple(out), tuple(why)


def plan_fresh_start(tabs: tuple[TabInfo, ...]) -> tuple[tuple[TabInfo, ...], Optional[TabInfo],
                                                         tuple[str, ...]]:
    """What a session should close to START on a clean window. Pure; closes nothing.

    This is deliberately NOT `plan_hygiene`, and the difference is the situation rather than the
    strictness. Hygiene runs mid-drive on a window we share with a human, so it protects the
    active tab, the only search tab, and anything it cannot classify. **Provisioning is the
    opposite situation:** a persistent profile restores its previous window, so on a fresh session
    every tab is inherited from work that already ended. Applying the hygiene rails here would
    preserve exactly the junk we are trying to be rid of — on 2026-07-23 that was a half-finished
    `smartapply` form and a stale Manchester NH search, and hygiene would have kept the search
    because it was the "only" one.

    So: everything goes, except we keep exactly one tab to land on (Chrome and `/close_tab` both
    refuse to close the last one). Preference order for the survivor is a blank tab, then the
    least-committed role — never an apply flow, which is the tab most likely to hold real work.

    Returns (to_close, keeper, reasons). An empty plan means the window is already clean.
    """
    if not tabs:
        return (), None, ()

    # Rank candidates to SURVIVE: blank is ideal (nothing to lose), apply is worst (a form in
    # progress). The keeper gets navigated to the target site afterwards, so its content does not
    # matter — only that discarding it costs nothing.
    survivor_rank = {ROLE_BLANK: 0, ROLE_TERMINAL: 1, ROLE_UNKNOWN: 2,
                     ROLE_SEARCH: 3, ROLE_ERRAND: 4, ROLE_APPLY: 5}
    keeper = min(tabs, key=lambda t: (survivor_rank.get(t.role, 3), t.tab_id))

    to_close = tuple(t for t in tabs if t.tab_id != keeper.tab_id)
    reasons = tuple(f"{t.role}:{t.short_url} — inherited from a previous session" for t in to_close)
    return to_close, keeper, reasons


def inherited_work(tabs: tuple[TabInfo, ...]) -> tuple[TabInfo, ...]:
    """Inherited tabs that plausibly hold REAL work, so a fresh start is proposed and never
    silently performed. An abandoned apply form is someone's half-finished application; the
    operator decides whether it is worth keeping, not us."""
    return tuple(t for t in tabs if t.role in (ROLE_APPLY, ROLE_ERRAND))


# The `# WINDOW` prompt block lives in `interaction.decision.window_to_prompt`, beside
# `bundle_to_prompt` — the prompt is the frozen feature contract, so its every part belongs in the
# contract module, and `interaction` must not import from the control plane.
