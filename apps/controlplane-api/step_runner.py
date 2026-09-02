"""The StepRunner — observe before, act, observe after, verify. No rung marks itself complete.

PLAN_step_runner.md, operator-authored 2026-08-03. The shape it replaces: every rung recorded its
own outcome and the ladder advanced on that claim — "every action counted as a positive flag" —
which is how open_pane failed three times over a working pane and an account card was offered over
a finished review page. The rung's outcome is now a CLAIM; the world settles it.

This module is MANDATORY for every execution path in the domain, not just the apply ladder:
the checkpoint ladder (`/step`), the observer's offered actions (`/orient_action`), the login and
SSO drives, the account and form fills, and the controller loop all pass through `run_step` (or
assemble the same row at their own seam). One wrapper, one corpus — a path that acts without
leaving a transition row is training nothing, which is the original sin this plan corrects.

Credential flows observe with `collect=False`: state identity only (URL, tab list, role+name),
no /capture artifact and no screenshot — PRINCIPLES §4. The belief still rides (the DOM witness
needs only roles and names); what never lands anywhere is a secret value.

Three layers, cheapest first (the observation stack):

  1. URL + tabs + AX tree           — `observe()`, all local CDP sockets, always taken
  2. deterministic diff             — `diff()`, pure, what changed between two observations
  3. the perception witnesses      — `perception_live.sense()` riding on the same observation,
                                      Apple Vision included when a screenshot exists (SHADOW:
                                      recorded in the transition row, gating nothing yet)

Everything here is BEST-EFFORT BY CONSTRUCTION, same rule as perception/live.py: an observation
that cannot be taken yields verdict "unobserved" and the ladder behaves exactly as it did before
this module existed. The verifier only ever *demotes* a claimed success into a mismatch — it never
promotes a failure, and it never blocks a rung it could not observe. Hard-stops are reserved for
the irreversible rungs, per the plan.

The transition row is the corpus this exists to grow: (before, evidence, action, expected, after,
actual changes, verification, teacher correction) — the training row for the state classifier, the
action-result verifier, the target ranker and, eventually, the next-action policy. Written as
JSONL beside the capture artifacts so `state_transition.py` and its siblings can read it without a
database in the loop.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import ats_registry
from settings import settings

#: Rungs whose action cannot be taken back. A verification failure anywhere else recovers or
#: retries; AT one of these, an unresolved mismatch on the way in refuses to act at all.
IRREVERSIBLE_RUNGS = frozenset({"submit"})

#: Verdicts. `unobserved` is load-bearing: it is the honest name for "the eyes were not available",
#: and it must behave exactly like the pre-StepRunner world (advance on the rung's claim) — a
#: blind verifier that blocks is worse than no verifier.
CONFIRMED = "confirmed"          # the world moved the way the rung predicted
MISMATCH = "mismatch"            # the rung claimed ok; the world disagrees
UNOBSERVED = "unobserved"        # could not see; the claim stands unchallenged
READ_ONLY = "read_only"          # the rung predicts no change; pair kept for the corpus

#: TWO DIFFERENT FACTS HAVE BEEN CALLED `mismatch` SINCE BOTH WERE WRITTEN (flagged 2026-08-22,
#: split now). `verify()` returns MISMATCH for *the world did not move / the expected URL never
#: appeared*; `live_actuator`'s recorder returns MISMATCH for *the supervisor judged the turn
#: non-nominal*. Different evidence, different meaning, one word — and the teacher's label queue
#: ranks `mismatch` first as a single class, so a queue head that looks like one problem is two.
#: The kind rides BESIDE the verdict rather than replacing it: every existing reader of
#: `verdict == "mismatch"` is unchanged, and a reader that wants the distinction can now have it.
MISMATCH_WORLD = "world_did_not_move"     # verify(): the postcondition did not hold
MISMATCH_JUDGED = "supervisor_judged"     # the supervisor called the turn non-nominal
MISMATCH_KINDS = (MISMATCH_WORLD, MISMATCH_JUDGED)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bbox_ints(bbox: Any) -> Optional[list[int]]:
    """A candidate's bbox as `[x, y, width, height]` ints, or None. Ints because sub-pixel
    precision is noise for training and floats double the row bytes."""
    if not isinstance(bbox, dict):
        return None
    try:
        return [int(bbox["x"]), int(bbox["y"]), int(bbox["width"]), int(bbox["height"])]
    except (KeyError, TypeError, ValueError):
        return None


# --------------------------------------------------------------------------------------------
# Observation — layer 1, the cheap look
# --------------------------------------------------------------------------------------------

@dataclass
class Observation:
    """One look at the window: which tabs exist, and what the acted tab shows."""
    ts: str = ""
    ok: bool = False
    url: str = ""
    title: str = ""
    tabs: list[dict[str, str]] = field(default_factory=list)      # [{tab_id, url}]
    candidates: list[dict[str, Any]] = field(default_factory=list)  # [{role, name}]
    ax_count: int = 0
    #: The belief the witnesses formed over this observation (perception_live.sense), or None.
    #: Carries state, uncertainty, novelty and the per-witness views — the Apple Vision fields
    #: ride in here when a screenshot existed. SHADOW: recorded, never gating.
    belief: Optional[dict[str, Any]] = None
    #: The /capture artifact this look wrote (filename), when collection succeeded.
    artifact: Optional[str] = None
    screenshot: Optional[str] = None
    #: THE WHOLE WINDOW, not just the tab we drove — `controller.window.survey().as_dict()`:
    #: each tab's role, duplicate anomalies, health, over-budget. Costs NOTHING: the tab list is
    #: already fetched for `tabs`, and the survey is a pure function over it. That module's own
    #: docstring says this projection is "small on purpose — this rides in a prompt and into every
    #: journaled row"; until now nothing put it in one.
    window: Optional[dict[str, Any]] = None

    def as_row(self) -> dict[str, Any]:
        row = {"ts": self.ts, "ok": self.ok, "url": self.url, "title": self.title,
               "tabs": self.tabs, "ax_count": self.ax_count,
               "candidates": [(c.get("role"), c.get("name")) for c in self.candidates],
               "belief": self.belief, "artifact": self.artifact,
               "screenshot": self.screenshot, "window": self.window}
        # GEOMETRY, un-deferred (2026-09-02, PLAN_inhouse_reasoner_v1 §4): `/ax_scan` has
        # always returned each candidate's bbox and this projection stripped it — the
        # element-level faucet was one dropped key, not a missing capture stage. A parallel
        # list (same order as `candidates`, ints, None where the scan had none) so every
        # existing reader of the 2-tuples is untouched; absent entirely on a look whose scan
        # carried no boxes, which is honest — historical rows never recorded any.
        geometry = [_bbox_ints(c.get("bbox")) for c in self.candidates]
        if any(g is not None for g in geometry):
            row["geometry"] = geometry
        return row


def window_census(tabs: list[dict[str, str]], *, active_tab_id: str = "") -> Optional[dict]:
    """The whole window's shape, from the tab list we already hold. FREE — no I/O.

    This is the answer to "when should the tab manager involve itself": ALWAYS, because taking
    the census costs nothing (`controller.window.survey` is a pure function over a list we
    fetched anyway). What is expensive is INVESTIGATING a tab — scanning its AX tree, capturing
    it — and that stays gated behind an alert. Census always, investigation on cause.
    """
    try:
        from controller import window as window_mod
        return window_mod.survey(tabs, active_tab_id=active_tab_id).as_dict()
    except Exception:  # noqa: BLE001 — the census is an aid, never a dependency
        return None


#: Tab roles whose ARRIVAL mid-step is news: an errand tab (a mail/login detour), a terminal
#: page (a confirmation we did not expect yet), or something unplaceable. A new APPLY tab during
#: `enter_apply` is the thing working as designed, so roles are judged against what the step
#: predicted rather than flagged blanket-fashion.
NOTEWORTHY_ARRIVALS = frozenset({"errand", "terminal", "unknown"})


def window_alert(before: Optional[dict], after: Optional[dict], *,
                 expected_new_tab: bool = False) -> Optional[dict[str, Any]]:
    """Did something happen in the WINDOW that the step itself did not account for?

    Pure, and deliberately conservative — this raises a hand, it never acts. The tab manager
    closing something on its own is how you discard a half-finished application; the operator's
    own rule. So the output is a flag with a reason, carried on the row and rendered on the
    response, and the deciding stays where it was.

    Three things count as news:
      * the window's HEALTH degraded (a duplicate application appeared, or it went over budget);
      * a tab arrived whose ROLE the step did not predict (an errand, a terminal page, an
        unplaceable one) — the popup/interstitial/second-window case;
      * the window emptied of the role we were working in.
    """
    if not before or not after:
        return None
    reasons: list[str] = []
    if before.get("health") == "ok" and after.get("health") == "warn":
        kinds = [a.get("kind") for a in (after.get("anomalies") or [])]
        reasons.append("the window became unhealthy"
                       + (f" ({', '.join(k for k in kinds if k)})" if kinds else
                          " (over the tab budget)"))
    b_roles, a_roles = before.get("roles") or {}, after.get("roles") or {}
    for role in sorted(NOTEWORTHY_ARRIVALS):
        if a_roles.get(role, 0) > b_roles.get(role, 0):
            reasons.append(f"a {role} tab appeared")
    if not expected_new_tab and (after.get("count") or 0) > (before.get("count") or 0) \
            and not reasons:
        reasons.append("a tab opened that this step did not predict")
    for role in ("apply", "search"):
        if b_roles.get(role, 0) and not a_roles.get(role, 0):
            reasons.append(f"the {role} tab is gone")
    if not reasons:
        return None
    return {"why": "; ".join(reasons), "health": after.get("health"),
            "anomalies": after.get("anomalies") or [], "roles": a_roles}


async def observe(capture_post: Callable[..., Awaitable[dict]], *, browser_url: str,
                  tab_id: Optional[str] = None, tab_url: Optional[str] = None,
                  collect: bool = True, session_id: Optional[int] = None) -> Observation:
    """Layer 1: tabs + AX + title over local CDP sockets, plus (best-effort) a /capture artifact
    and the witnesses' belief over exactly what was captured.

    Never raises. A browser that will not answer returns `ok=False`, and everything downstream
    treats that as "the eyes were unavailable", never as evidence of anything about the page.
    """
    obs = Observation(ts=_utc())
    try:
        tabs_res = await capture_post("/list_tabs", {"browser_url": browser_url}, timeout=8.0)
        obs.tabs = [{"tab_id": t.get("tab_id", ""), "url": t.get("url", "")}
                    for t in (tabs_res.get("tabs") or [])]
        scan = await capture_post("/ax_scan", {"browser_url": browser_url, "tab_id": tab_id,
                                               "tab_url": tab_url}, timeout=25.0)
        # bbox rides along (2026-09-02): the scan always had it; this projection was the strip.
        obs.candidates = [{"role": c.get("role"), "name": c.get("name"), "bbox": c.get("bbox")}
                          for c in (scan.get("candidates") or []) if c.get("name")]
        obs.ax_count = int(scan.get("count") or len(obs.candidates))
        obs.url = str(scan.get("target_url") or "")
        obs.ok = bool(tabs_res.get("ok", True)) and bool(scan.get("ok", True))
        obs.window = window_census(obs.tabs, active_tab_id=tab_id or "")
    except Exception:  # noqa: BLE001 — an observation must never sink the step it observes
        return obs

    if collect and obs.ok:
        # The corpus grows on every look (the always-be-collecting directive, 2026-07-22) — and
        # the screenshot this writes is what lets the visual witness testify in shadow.
        # `collect=False` is the credential-flow posture (§4): no artifact, no screenshot,
        # state identity only.
        try:
            cap = await capture_post("/capture", {
                "browser_url": browser_url, "tab_id": tab_id, "tab_url": tab_url,
                "scenario": "step_runner",
                "task_context": {"session_id": session_id} if session_id else None}, timeout=30.0)
            obs.artifact = cap.get("filename")
            obs.screenshot = cap.get("screenshot") or cap.get("screenshot_path")
        except Exception:  # noqa: BLE001
            pass
    if obs.ok:
        # The belief rides on EVERY successful look, collected or not: the DOM witness reads only
        # roles, names and the URL, so it is credential-safe, and a login screen's state identity
        # is exactly what §4 says we may keep. The visual witness joins in only when a screenshot
        # was collected.
        try:
            from perception import live as perception_live
            obs.belief = perception_live.sense(
                url=obs.url, title=obs.title,
                ax_candidates=obs.candidates,
                screenshot_path=Path(obs.screenshot) if obs.screenshot else None)
        except Exception:  # noqa: BLE001 — perception is an aid, never a dependency
            obs.belief = None
    return obs


# --------------------------------------------------------------------------------------------
# Diff — layer 2, pure and deterministic
# --------------------------------------------------------------------------------------------

def diff(before: Observation, after: Observation, *,
         expect_new_tab: bool = False) -> Optional[dict[str, Any]]:
    """What changed between two looks. None when either side was blind — a diff against a failed
    observation is not evidence, and pretending it is would let a dead tab veto a good rung.

    `expect_new_tab` says the STEP predicted a tab would open (clicking Apply), so a new tab is
    the plan working rather than window news worth flagging.
    """
    if not (before.ok and after.ok):
        return None
    b_tabs = {t["tab_id"]: t["url"] for t in before.tabs}
    a_tabs = {t["tab_id"]: t["url"] for t in after.tabs}
    b_names = {(c.get("role"), c.get("name")) for c in before.candidates}
    a_names = {(c.get("role"), c.get("name")) for c in after.candidates}
    return {
        "url_changed": before.url != after.url,
        "url_before": before.url, "url_after": after.url,
        "tabs_opened": [{"tab_id": tid, "url": u} for tid, u in a_tabs.items() if tid not in b_tabs],
        "tabs_closed": [{"tab_id": tid, "url": u} for tid, u in b_tabs.items() if tid not in a_tabs],
        "tabs_navigated": [{"tab_id": tid, "from": b_tabs[tid], "to": u}
                           for tid, u in a_tabs.items()
                           if tid in b_tabs and b_tabs[tid] != u],
        "elements_added": sorted(str(x) for x in (a_names - b_names))[:40],
        "elements_removed": sorted(str(x) for x in (b_names - a_names))[:40],
        "ax_count_before": before.ax_count, "ax_count_after": after.ax_count,
        # Before/after visual agreement, when both looks had eyes — SHADOW, recorded not gating.
        "visual_agreement": _visual_agreement(before.belief, after.belief),
        # THE REST OF THE WINDOW. Every step happens inside a window that holds other tabs, and
        # until now a step could only see the one it drove. `window_alert` is None on the normal
        # case and costs nothing to compute — the census it reads was already taken.
        "window_alert": window_alert(before.window, after.window,
                                     expected_new_tab=(expect_new_tab)),
        "window_before": (before.window or {}).get("roles"),
        "window_after": (after.window or {}).get("roles"),
        # THE PAGE'S OWN EXPLANATION (2026-08-20). A mismatch row used to record that the world
        # disagreed and throw away the page's stated reason — the one fact that turns "the recipe
        # is wrong about this page" into "the page is refusing, and it says why". Pure over the
        # AX candidates both looks already hold: fresh alert-role text the AFTER shows and the
        # BEFORE did not. Costs nothing; rides into every corpus row.
        "page_says": _page_says(before, after),
    }


_ERROR_ROLES = frozenset({"alert", "alertdialog"})


def _page_says(before: Observation, after: Observation) -> list[str]:
    """Fresh error/alert text the AFTER look shows that the BEFORE did not — capped, deduped."""
    b = {(c.get("role"), c.get("name")) for c in before.candidates}
    out: list[str] = []
    for c in after.candidates:
        name = str(c.get("name") or "").strip()
        if (c.get("role") in _ERROR_ROLES and name
                and (c.get("role"), c.get("name")) not in b and name not in out):
            out.append(name)
    return out[:6]


def _visual_agreement(b: Optional[dict], a: Optional[dict]) -> Optional[bool]:
    try:
        bs, as_ = (b or {}).get("state"), (a or {}).get("state")
        return (bs == as_) if bs and as_ else None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------------------------
# Expectations — what each rung PREDICTS, declared before it acts
# --------------------------------------------------------------------------------------------

@dataclass
class Expectation:
    """A rung's declared postcondition. `kind` picks the check; the fields feed it.

    Kinds, and what each honestly claims to know:
      url_param       — some URL in the window carries `param=value` (measured, e.g. vjk=<id>)
      url_value       — some URL in the window carries `value` (encoding-tolerant; a query landing)
      new_tab_or_nav  — a tab opens or navigates to one of `hosts_hint`
      content_changed — the step predicts the page moves AT ALL (URL, tabs, or AX content —
                        the honest floor for a click whose exact landing we have not measured;
                        SPA screens that swap content in place count via elements added/removed)
      read_only       — the step predicts NO change; the pair is kept for the corpus unjudged
      unmodeled       — the step DOES change the world but no postcondition has been measured
                        yet; verdict is `unobserved` by construction, never a guess. The corpus
                        row is the point — when enough rows exist, the expectation gets written
                        from measurements, not invented here.
    """
    kind: str                              # url_param | url_value | new_tab_or_nav |
                                           # content_changed | read_only | unmodeled
    param: str = ""                        # url_param: the query key…
    value: str = ""                        # …and the value it must hold (url_value: the value)
    hosts_hint: tuple[str, ...] = ()       # new_tab_or_nav: hosts that count as "the application"

    def as_row(self) -> dict[str, Any]:
        return {"kind": self.kind, "param": self.param, "value": self.value,
                "hosts_hint": list(self.hosts_hint)}


#: The SERP's own name for "which posting is open". Indeed's default; every other engine that
#: differs declares its own `SERP_JOB_PARAM` and is delegated to, so this never becomes a second
#: table drifting away from the recipes.
_DEFAULT_SERP_JOB_PARAM = "vjk"
_SERP_JOB_PARAM_RECIPES = {"linkedin": "linkedin_recipe"}


def serp_job_param(platform: str = "") -> str:
    """The URL param this engine's results page uses to say which job is open.

    A PREDICTION IS ENGINE-SPECIFIC OR IT IS NOT A PREDICTION. This was hardcoded to Indeed's
    `vjk`, under a comment asserting LinkedIn was "carried the same way by the pane check itself"
    — which was true of the pane check and false of this expectation. So on LinkedIn `open_pane`
    predicted a param that cannot occur, every correctly-opened pane verified as MISMATCH, and the
    run loop stopped on no-progress with the right job open on screen (live 2026-08-14).
    """
    module_name = _SERP_JOB_PARAM_RECIPES.get((platform or "").strip().lower())
    if not module_name:
        return _DEFAULT_SERP_JOB_PARAM
    from importlib import import_module
    return getattr(import_module(module_name), "SERP_JOB_PARAM", _DEFAULT_SERP_JOB_PARAM)


def expectation_for(rung_id: str, *, external_id: str = "", platform: str = "") -> Expectation:
    """The apply ladder's predictions, one per rung. Declared HERE, before the act, because an
    expectation invented after looking at the outcome is a rationalisation, not a prediction
    (PRINCIPLES §13)."""
    if rung_id == "open_pane":
        # Opening a card puts the job's id in the SERP's own URL — `?vjk=` on Indeed,
        # `?currentJobId=` on LinkedIn. Asked per engine rather than assumed.
        return Expectation(kind="url_param", param=serp_job_param(platform), value=external_id)
    if rung_id == "enter_apply":
        # Clicking Apply either opens the application's own tab or navigates this one to it.
        #
        # WHICH HOSTS COUNT AS "THE APPLICATION" IS THE REGISTRY'S ANSWER, NOT A HAND-KEPT SIX.
        # This tuple was typed out and went stale, and a stale hosts_hint does not merely fail to
        # praise — it CONTRADICTS: an enter_apply that clicked the right button and opened the
        # right tab was demoted from `ok` to `mismatch` ("something moved, but not to an
        # application host: https://bc.csod.com/…"), which pinned the ladder on a rung it had
        # already completed and, because a mismatch hard-stops the irreversible rungs, would have
        # blocked Submit at the end of it (measured live 2026-08-11, Boston College / Cornerstone).
        # A prediction is only honest if the thing it predicts against is kept current.
        return Expectation(kind="new_tab_or_nav",
                           hosts_hint=("smartapply.indeed.com", "sapsf.com")
                                      + ats_registry.off_engine_apply_hosts())
    if rung_id in ("verify_identity", "classify", "account", "orient"):
        # These read the world; they change nothing.
        return Expectation(kind="read_only")
    if rung_id == "submit":
        # Sending an application always moves the screen — to a confirmation, a post-submit
        # branch, or an error. What it must never do is nothing.
        return Expectation(kind="content_changed")
    # A TAIL RUNG, whose id is the state it advances FROM. It clicks the screen's own Continue, so
    # a screen that did not change means the click did not take — which is the whole reason to
    # check. Measured live 2026-08-06: `indeed_apply_resume_selection` + Continue moved the tab
    # from `/resume-selection-module/resume-selection` to `/resume-module/structured-data-intro`.
    #
    # Before this, a state-named rung fell through to the `read_only` default and every advance was
    # journaled "nothing to verify" — the verifier was switched off for the entire half of the
    # ladder that actually drives.
    return Expectation(kind="content_changed")


def expectation_for_checkpoint(action: str, *, query: str = "") -> Expectation:
    """The checkpoint ladder's predictions (`/step`), one per executor action.

    Same discipline as `expectation_for`: a prediction is declared only where a live measurement
    backs it. `run_query` is the one rung with a clean, engine-agnostic postcondition — the
    results URL carries the query (q= on Indeed, keywords= on LinkedIn, both measured live).
    `auth_probe` may navigate, survey or drive a login depending on what it finds, and
    `set_distance`'s effect on the URL differs per engine and has not been pinned down — both are
    `unmodeled` (the row still lands; the expectation gets written when the rows say what it is).
    """
    if action == "run_query":
        return Expectation(kind="url_value", value=query)
    if action in ("probe_browser", "review_page"):
        return Expectation(kind="read_only")
    return Expectation(kind="unmodeled")


def verify(expect: Expectation, d: Optional[dict[str, Any]],
           after: Observation) -> tuple[str, str]:
    """(verdict, evidence). Only ever demotes a claimed success; never promotes a failure."""
    if expect.kind == "read_only":
        return READ_ONLY, "read-only rung — pair kept for the corpus, nothing to verify"
    if expect.kind == "unmodeled":
        # DECLARED BLINDNESS, not a judgement. The step changes the world and nobody has measured
        # how yet — inventing a check here would be the rationalisation §13 forbids. The pair and
        # diff still land in the corpus, which is how the expectation eventually gets written.
        return UNOBSERVED, "no measured postcondition for this step yet — pair kept for the corpus"
    if d is None:
        return UNOBSERVED, "could not observe both sides — the claim stands unchallenged"
    if expect.kind == "content_changed":
        moved = (d.get("url_changed") or d.get("tabs_opened") or d.get("tabs_closed")
                 or d.get("tabs_navigated") or d.get("elements_added")
                 or d.get("elements_removed"))
        if moved:
            what = ("url changed" if d.get("url_changed") else
                    "tabs moved" if (d.get("tabs_opened") or d.get("tabs_closed")
                                     or d.get("tabs_navigated")) else
                    f"content moved (+{len(d.get('elements_added') or [])}/"
                    f"-{len(d.get('elements_removed') or [])} elements)")
            return CONFIRMED, f"the page moved — {what}"
        return MISMATCH, ("nothing observable changed — no URL, tab or AX-content movement "
                          "between the two looks")
    if expect.kind == "url_value":
        if not expect.value:
            return UNOBSERVED, "no value declared to look for — the claim stands"
        urls = [u for u in [after.url] + [t["url"] for t in after.tabs] if u]
        if not urls:
            return UNOBSERVED, "no URLs visible to compare against — the claim stands"
        # Encoding-tolerant on purpose: 'data warehouse' rides in a URL as data+warehouse or
        # data%20warehouse depending on the engine, and a verifier that demotes a landed query
        # over percent-encoding would reopen the one CONSUMING rung this ladder protects.
        from urllib.parse import quote, quote_plus, unquote_plus
        want = " ".join(expect.value.split()).lower()
        variants = {want, quote(want), quote_plus(want)}
        for u in urls:
            low = u.lower()
            if any(v in low for v in variants) or want in unquote_plus(low):
                return CONFIRMED, f"a window URL carries {expect.value!r}"
        return MISMATCH, (f"expected {expect.value!r} in some window URL and found it nowhere "
                          f"(url: {after.url[:80]})")
    if expect.kind == "url_param":
        needle = f"{expect.param}={expect.value}"
        urls = [u for u in [after.url] + [t["url"] for t in after.tabs] if u]
        if not urls:
            # BLIND IS NOT WRONG. An observation that saw no URLs at all (a faked capture server,
            # a scan that answered without a target) cannot testify either way — demoting on it
            # would let a dead probe veto a good rung, the exact inversion of the bug this fixes.
            return UNOBSERVED, "no URLs visible to compare against — the claim stands"
        if expect.value and any(needle in u for u in urls):
            return CONFIRMED, f"the window carries {needle}"
        return MISMATCH, (f"expected {needle} somewhere in the window and found it nowhere "
                          f"(url: {after.url[:80]})")
    if expect.kind == "new_tab_or_nav":
        if not (d.get("tabs_opened") or d.get("tabs_navigated") or d.get("tabs_closed")) \
                and not after.tabs:
            return UNOBSERVED, "no tabs visible on either side — the claim stands"
        moved = (d.get("tabs_opened") or []) + (d.get("tabs_navigated") or [])
        hits = [m for m in moved
                if any(h in (m.get("url") or m.get("to") or "") for h in expect.hosts_hint)]
        if hits:
            where = hits[0].get("url") or hits[0].get("to") or ""
            return CONFIRMED, f"the window gained/navigated to {where[:80]}"
        # AN EMPLOYER'S OWN CAREERS DOMAIN CONFIRMS TOO. The hint list can only ever name the
        # REGISTRY's hosts, and a company-site apply lands by definition on a host no registry
        # will ever list (careers.solutionhealth.org, measured live 2026-08-11 — a correct
        # Apply click demoted to mismatch minutes after the registry-derived hint shipped).
        # Leaving the ENGINE is the prediction's real content: an apply that moved the window
        # to any non-engine host entered *something*, and naming which something is `classify`'s
        # job, not this verifier's. Staying ON the engine (a card click, a serp reload) remains
        # the real failure.
        def _host(u: str) -> str:
            from urllib.parse import urlparse
            return (urlparse(u or "").hostname or "").lower()
        engine_hosts = ("indeed.com", "linkedin.com")
        external = [m for m in moved
                    if _host(m.get("url") or m.get("to") or "")
                    and not any(_host(m.get("url") or m.get("to") or "").endswith(e)
                                for e in engine_hosts)]
        if external:
            where = external[0].get("url") or external[0].get("to") or ""
            return CONFIRMED, (f"the window left the engine for {where[:80]} — an unlisted "
                               f"host; classify names it")
        if moved:
            return MISMATCH, ("something moved, but only on the engine's own pages: "
                              + "; ".join((m.get("url") or m.get("to") or "")[:60] for m in moved[:3]))
        return MISMATCH, "no tab opened and none navigated — the click left the window unchanged"
    if expect.kind == "expected_next":
        # THE KIND THAT FELL THROUGH TO "unknown" (flagged 2026-08-22, built now). `live_actuator`
        # emits `expected_next` — the states a decision said it should land on — and `verify` had
        # no branch for it, so a row that reached here was filed UNOBSERVED, i.e. "we could not
        # judge", when in fact the strongest possible judgement was available: the decision named
        # the acceptable destinations in advance. Those rows got their verdict from the supervisor
        # path instead and never touched this function, which is why the hole stayed invisible.
        #
        # Matching is on the LANDED state, and containment is deliberate: a declared
        # `workday_my_information` should be satisfied by a landed
        # `workday_my_information_edit` — the recipe names families, not renders.
        landed = str((d.get("landed_state") if d else "") or "")
        wanted = [str(v) for v in (expect.value or ()) if v] if isinstance(
            expect.value, (list, tuple)) else ([str(expect.value)] if expect.value else [])
        if not wanted:
            return UNOBSERVED, "expected_next declared no states — the claim stands unchallenged"
        if not landed:
            return UNOBSERVED, (f"expected one of {wanted} and the landing was never named "
                                f"— nothing to compare, so the claim stands")
        if any(landed == w or landed.startswith(w) or w.startswith(landed) for w in wanted):
            return CONFIRMED, f"landed {landed!r}, which the decision named in advance"
        return MISMATCH, (f"expected one of {wanted} and landed {landed!r} — the decision named "
                          f"its destinations and the world chose another")
    return UNOBSERVED, f"unknown expectation kind {expect.kind!r}"


# --------------------------------------------------------------------------------------------
# The transition corpus — the core training row
# --------------------------------------------------------------------------------------------

_write_lock = threading.Lock()


def _transitions_dir() -> Path:
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parent / base).resolve()
    d = base / "transitions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def record_transition(*, session_id: Any, rung_id: str, action: dict[str, Any],
                      expect: Expectation, before: Observation, after: Observation,
                      changes: Optional[dict[str, Any]], verdict: str, evidence: str,
                      claimed: str, mismatch_kind: Optional[str] = None) -> Optional[Path]:
    """Append one training row. This row — not the screenshot alone, and not a reasoning
    transcript — is what the state classifier, the action-result verifier and the recovery
    selector will train on. `teacher_correction` is null until a teacher (or the operator)
    overrides a verdict; both sides are then kept (PRINCIPLES §10)."""
    row = {
        "v": 1, "ts": _utc(), "session_id": session_id, "rung": rung_id,
        "action": action, "expected": expect.as_row(),
        "before": before.as_row(), "after": after.as_row(),
        "changes": changes, "verdict": verdict, "evidence": evidence,
        "claimed": claimed,               # what the rung said about itself
        # WHICH KIND of mismatch, when there is one. Written only on a MISMATCH verdict and
        # defaulting to the verifier's own meaning, because that is the caller this function has
        # always had; the actuator passes MISMATCH_JUDGED for the other. Absent on historical
        # rows, which is honest — nobody recorded it then.
        **({"mismatch_kind": mismatch_kind or MISMATCH_WORLD} if verdict == MISMATCH else {}),
        "teacher_correction": None,
    }
    try:
        path = _transitions_dir() / f"session_{session_id}.jsonl"
        with _write_lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str) + "\n")
    except Exception:  # noqa: BLE001 — the corpus must never sink the drive it observes
        return None
    # The write-time vector rider (PLAN_inhouse_reasoner_v1 §4): the bank IS the crank. After
    # the row landed — never instead of it — both halves embed into vectors.db. Best-effort
    # like everything else on this path; the backfill CLI sweeps anything missed.
    try:
        from precedent.rider import on_transition_row
        on_transition_row(row)
    except Exception:  # noqa: BLE001 — an aid, never a dependency
        pass
    return path


def read_transitions(session_id: Any, *, limit: int = 200) -> list[dict[str, Any]]:
    """The stored rows for one session, oldest first. For review surfaces and the trainers.

    Each row gains an `index` — its LINE NUMBER in the file — which is the row's address for
    `correct_transition`. Line numbers are stable because the file is append-only; a correction
    rewrites a line in place and never reorders."""
    path = _transitions_dir() / f"session_{session_id}.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    start = max(0, len(lines) - limit)
    for i, line in enumerate(lines[start:], start=start):
        try:
            row = json.loads(line)
            row["index"] = i
            rows.append(row)
        except Exception:  # noqa: BLE001
            continue
    return rows


def list_corpora() -> list[dict[str, Any]]:
    """Every transition corpus on disk, with enough shape to pick one: key, row count, verdict
    mix, when it last grew. Keys mirror who wrote them: numeric = a cockpit session, `account-*`
    = a create-account drive, `controller-*` = a controller loop."""
    out: list[dict[str, Any]] = []
    for path in sorted(_transitions_dir().glob("session_*.jsonl")):
        key = path.name[len("session_"):-len(".jsonl")]
        verdicts: dict[str, int] = {}
        corrected = 0
        last_ts = ""
        n = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            n += 1
            verdicts[row.get("verdict") or "?"] = verdicts.get(row.get("verdict") or "?", 0) + 1
            corrected += 1 if row.get("teacher_correction") else 0
            last_ts = row.get("ts") or last_ts
        out.append({"key": key, "rows": n, "verdicts": verdicts,
                    "corrected": corrected, "last_ts": last_ts})
    return out


def correct_transition(session_id: Any, index: int, *, verdict: Optional[str], note: str,
                       by: str = "operator", expected_ts: str = "",
                       before_state: str = "", after_state: str = "") -> dict[str, Any]:
    """Write a teacher correction onto one row — BOTH SIDES KEPT (PRINCIPLES §10): the row's
    original verdict and evidence are never touched; the correction sits beside them, carrying
    who, when, the corrected verdict (None = a note without a verdict change, e.g. an explicit
    agreement) and the reviewer's note. A correction should cite what the row's own evidence
    shows — it is a training signal, not a second opinion.

    `expected_ts` guards the address: rows are addressed by line number, and refusing a write
    whose timestamp does not match means a stale review screen can never annotate the wrong row.
    Raises ValueError/IndexError on a bad address; the router turns those into 409/404.
    """
    if verdict is not None and verdict not in (CONFIRMED, MISMATCH, UNOBSERVED, READ_ONLY):
        raise ValueError(f"unknown verdict {verdict!r}")
    path = _transitions_dir() / f"session_{session_id}.jsonl"
    if not path.exists():
        raise IndexError(f"no corpus for {session_id!r}")
    with _write_lock:
        lines = path.read_text(encoding="utf-8").splitlines()
        if not 0 <= index < len(lines):
            raise IndexError(f"row {index} is out of range (corpus has {len(lines)} rows)")
        row = json.loads(lines[index])
        if expected_ts and row.get("ts") != expected_ts:
            raise ValueError("the row at this index has a different timestamp — the corpus moved "
                             "under the review screen; reload and re-check")
        row["teacher_correction"] = {
            "ts": _utc(), "by": by, "verdict": verdict, "note": note,
            "original_verdict": row.get("verdict"),
            # THE GROUND-TRUTH LABELS, when the teacher supplied them. The witnesses' own belief
            # is never overwritten — it stays in `before`/`after` as what the system thought at
            # the time, which is the whole point of the row (§10, both sides kept). These are a
            # SECOND opinion with a name attached, and the trainer prefers them because a teacher
            # who watched the drive outranks two witnesses that split.
            "before_state": before_state or None,
            "after_state": after_state or None,
            "witness_before": ((row.get("before") or {}).get("belief") or {}).get("state"),
            "witness_after": ((row.get("after") or {}).get("belief") or {}).get("state"),
        }
        lines[index] = json.dumps(row, default=str)
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(path)
    row["index"] = index
    return row


# --------------------------------------------------------------------------------------------
# run_step — the whole sequence as one call, for every path that executes anything
# --------------------------------------------------------------------------------------------

@dataclass
class StepReport:
    """What one wrapped step produced: the action's own result, plus the world's answer."""
    result: Any
    before: Observation
    after: Observation
    changes: Optional[dict[str, Any]]
    verdict: str
    evidence: str
    claimed: str

    def verification(self) -> dict[str, Any]:
        """The block responses attach beside the claim, so the cockpit can render "the action
        said ok and the page carries vjk=…" instead of a bare flag. Carries the window alert
        when the rest of the window did something this step did not account for."""
        block = {"verdict": self.verdict, "evidence": self.evidence, "claimed": self.claimed}
        alert = (self.changes or {}).get("window_alert")
        if alert:
            block["window_alert"] = alert
        return block

    @property
    def window_alert(self) -> Optional[dict[str, Any]]:
        return (self.changes or {}).get("window_alert")

    @property
    def demotes(self) -> bool:
        """True when the action claimed ok and the world disagrees — the one combination the
        verifier acts on. Callers with a rung to reopen use this; callers without one surface it."""
        return self.verdict == MISMATCH and self.claimed == "ok"


def default_claimed(result: Any) -> str:
    """Read an action's claim off the reply shapes this codebase actually returns: tier-1
    /execute replies carry `outcome`; endpoint-level results carry `ok`. Anything else is `none`
    — an unrecognised reply is not a claim (the 422-sailed-through lesson, 2026-07-24)."""
    if isinstance(result, dict):
        if result.get("outcome") is not None:
            return "ok" if result["outcome"] in ("ok", "committed_unconfirmed") else "failed"
        if "ok" in result:
            return "ok" if result.get("ok") else "failed"
    return "none"


async def run_step(act: Callable[[], Awaitable[Any]], *, action: dict[str, Any],
                   expect: Expectation, capture_post: Callable[..., Awaitable[dict]],
                   browser_url: str, tab_id: Optional[str] = None,
                   tab_url: Optional[str] = None, session_id: Any = None,
                   rung_id: str = "", collect: bool = True,
                   claimed_of: Callable[[Any], str] = default_claimed,
                   tab_for: Optional[Callable[[], Awaitable[Optional[str]]]] = None) -> StepReport:
    """Observe before → act → observe after → diff → verify → record. One call, no way to
    forget the row — recording lives INSIDE the wrapper precisely so no caller can act without
    feeding the corpus.

    `action` is the row's metadata and MUST NEVER carry a secret value: field NAMES, control
    names, rung ids — yes; a password, a typed credential — never (§4, same rule /execute
    enforces by logging targets, not values). `collect=False` is the credential-flow posture:
    identity-only observations, no artifacts.

    Best-effort by construction, like everything here: a failed observation yields `unobserved`
    and the caller's behaviour is exactly what it was before the StepRunner existed. The act
    itself is never wrapped in a try — its exceptions are the caller's own contract.
    """
    async def _tab() -> Optional[str]:
        """OBSERVE THE TAB THE WORK IS ON *NOW*, resolved per look rather than pinned once.
        An action that moves the work to a new tab — clicking Apply opens the ATS — leaves the
        after-observation pointed at the tab we started from unless this is dynamic. Measured
        live 2026-08-04: the application form sat open at smartapply while every apply-path row
        recorded the SERP's 243 AX elements and a belief about the search page. The verdicts
        survived (the diff reads the tab LIST), but the perception half of the corpus described
        the wrong page entirely."""
        if tab_for is None:
            return tab_id
        try:
            return await tab_for() or tab_id
        except Exception:  # noqa: BLE001 — resolution is an aid, never a dependency
            return tab_id

    before = await observe(capture_post, browser_url=browser_url, tab_id=await _tab(),
                           tab_url=tab_url, collect=collect, session_id=session_id)
    result = await act()
    after = await observe(capture_post, browser_url=browser_url, tab_id=await _tab(),
                          tab_url=tab_url, collect=collect, session_id=session_id)
    changes = diff(before, after, expect_new_tab=(expect.kind == "new_tab_or_nav"))
    verdict, evidence = verify(expect, changes, after)
    claimed = claimed_of(result)
    record_transition(session_id=session_id, rung_id=rung_id or str(action.get("action") or ""),
                      action=action, expect=expect, before=before, after=after, changes=changes,
                      verdict=verdict, evidence=evidence, claimed=claimed)
    return StepReport(result=result, before=before, after=after, changes=changes,
                      verdict=verdict, evidence=evidence, claimed=claimed)
