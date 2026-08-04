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
        said ok and the page carries vjk=…" instead of a bare flag."""
        return {"verdict": self.verdict, "evidence": self.evidence, "claimed": self.claimed}

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
    changes = diff(before, after)
    verdict, evidence = verify(expect, changes, after)
    claimed = claimed_of(result)
    record_transition(session_id=session_id, rung_id=rung_id or str(action.get("action") or ""),
                      action=action, expect=expect, before=before, after=after, changes=changes,
                      verdict=verdict, evidence=evidence, claimed=claimed)
    return StepReport(result=result, before=before, after=after, changes=changes,
                      verdict=verdict, evidence=evidence, claimed=claimed)
