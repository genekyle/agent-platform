"""The StepRunner — observe before, act, observe after, verify. No rung marks itself complete.

PLAN_step_runner.md, operator-authored 2026-08-03. The shape it replaces: every rung recorded its
own outcome and the ladder advanced on that claim — "every action counted as a positive flag" —
which is how open_pane failed three times over a working pane and an account card was offered over
a finished review page. The rung's outcome is now a CLAIM; the world settles it.

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


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


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

    def as_row(self) -> dict[str, Any]:
        return {"ts": self.ts, "ok": self.ok, "url": self.url, "title": self.title,
                "tabs": self.tabs, "ax_count": self.ax_count,
                "candidates": [(c.get("role"), c.get("name")) for c in self.candidates],
                "belief": self.belief, "artifact": self.artifact,
                "screenshot": self.screenshot}


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
        obs.candidates = [{"role": c.get("role"), "name": c.get("name")}
                          for c in (scan.get("candidates") or []) if c.get("name")]
        obs.ax_count = int(scan.get("count") or len(obs.candidates))
        obs.url = str(scan.get("target_url") or "")
        obs.ok = bool(tabs_res.get("ok", True)) and bool(scan.get("ok", True))
    except Exception:  # noqa: BLE001 — an observation must never sink the step it observes
        return obs

    if collect and obs.ok:
        # The corpus grows on every look (the always-be-collecting directive, 2026-07-22) — and
        # the screenshot this writes is what lets the visual witness testify in shadow.
        try:
            cap = await capture_post("/capture", {
                "browser_url": browser_url, "tab_id": tab_id, "tab_url": tab_url,
                "scenario": "step_runner",
                "task_context": {"session_id": session_id} if session_id else None}, timeout=30.0)
            obs.artifact = cap.get("filename")
            obs.screenshot = cap.get("screenshot") or cap.get("screenshot_path")
        except Exception:  # noqa: BLE001
            pass
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

def diff(before: Observation, after: Observation) -> Optional[dict[str, Any]]:
    """What changed between two looks. None when either side was blind — a diff against a failed
    observation is not evidence, and pretending it is would let a dead tab veto a good rung."""
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
    }


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
    """A rung's declared postcondition. `kind` picks the check; the fields feed it."""
    kind: str                              # url_param | new_tab_or_nav | read_only
    param: str = ""                        # url_param: the query key…
    value: str = ""                        # …and the value it must hold
    hosts_hint: tuple[str, ...] = ()       # new_tab_or_nav: hosts that count as "the application"

    def as_row(self) -> dict[str, Any]:
        return {"kind": self.kind, "param": self.param, "value": self.value,
                "hosts_hint": list(self.hosts_hint)}


def expectation_for(rung_id: str, *, external_id: str = "") -> Expectation:
    """The apply ladder's predictions, one per rung. Declared HERE, before the act, because an
    expectation invented after looking at the outcome is a rationalisation, not a prediction
    (PRINCIPLES §13)."""
    if rung_id == "open_pane":
        # Opening a card puts the job's id in the SERP's own URL (?vjk= on Indeed; LinkedIn's
        # currentJobId is carried the same way by the pane check itself).
        return Expectation(kind="url_param", param="vjk", value=external_id)
    if rung_id == "enter_apply":
        # Clicking Apply either opens the application's own tab or navigates this one to it.
        return Expectation(kind="new_tab_or_nav",
                           hosts_hint=("smartapply.indeed.com", "myworkdayjobs", "greenhouse.io",
                                       "icims.com", "successfactors", "sapsf.com"))
    # verify_identity / classify / account read the world; they change nothing.
    return Expectation(kind="read_only")


def verify(expect: Expectation, d: Optional[dict[str, Any]],
           after: Observation) -> tuple[str, str]:
    """(verdict, evidence). Only ever demotes a claimed success; never promotes a failure."""
    if expect.kind == "read_only":
        return READ_ONLY, "read-only rung — pair kept for the corpus, nothing to verify"
    if d is None:
        return UNOBSERVED, "could not observe both sides — the claim stands unchallenged"
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
        if moved:
            return MISMATCH, ("something moved, but not to an application host: "
                              + "; ".join((m.get("url") or m.get("to") or "")[:60] for m in moved[:3]))
        return MISMATCH, "no tab opened and none navigated — the click left the window unchanged"
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
                      claimed: str) -> Optional[Path]:
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
        "teacher_correction": None,
    }
    try:
        path = _transitions_dir() / f"session_{session_id}.jsonl"
        with _write_lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str) + "\n")
        return path
    except Exception:  # noqa: BLE001 — the corpus must never sink the drive it observes
        return None


def read_transitions(session_id: Any, *, limit: int = 200) -> list[dict[str, Any]]:
    """The stored rows for one session, oldest first. For review surfaces and the trainers."""
    path = _transitions_dir() / f"session_{session_id}.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return rows
