"""Controller routes — the reasoner served over HTTP, and the read surfaces the cockpit shows.

`POST /api/controller/decide_model` is the invariant-#6 boundary: the model behind an endpoint,
so a local L4 replaces Haiku as a deployment change. The GET routes expose the decision corpus,
the compiled programs, and the scoreboard so the operator can SEE the controller working — what
it decided, at which rung, what it escalated, and how well it agrees with the teacher.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Optional

import httpx
from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from controller import metrics as controller_metrics
from controller import programs as programs_mod
from controller.bundle import build_bundle
from controller.decide import decide
from controller.reason import HaikuReasoner
from interaction import decision_journal
from interaction.contract import Outcome
from interaction.decision import Bundle, Decision, bundle_to_prompt
from settings import settings

router = APIRouter()


_SEQ_FIELDS = ("expected_next", "unanswered", "recent")


def _bundle_from_dict(d: dict[str, Any]) -> Bundle:
    """Reconstruct a Bundle from a posted/journaled dict, coercing the sequence fields back to
    tuples and ignoring unknown keys (schema-forward-compatible)."""
    known = {f for f in Bundle.__dataclass_fields__}          # noqa: SLF001 (public enough)
    clean = {k: v for k, v in (d or {}).items() if k in known}
    for k in _SEQ_FIELDS:
        if k in clean and clean[k] is not None:
            clean[k] = tuple(tuple(x) if isinstance(x, list) else x for x in clean[k]) \
                if k == "expected_next" else tuple(clean[k])
    # required positional-ish fields with safe defaults if a partial bundle was posted
    clean.setdefault("task", "")
    clean.setdefault("goal_text", "")
    clean.setdefault("done", False)
    clean.setdefault("url", "")
    clean.setdefault("route", "")
    clean.setdefault("state", None)
    clean.setdefault("is_branch", False)
    clean.setdefault("human_required", False)
    return Bundle(**clean)


class DecideModelBody(BaseModel):
    bundle: dict[str, Any]
    budget_limit: Optional[float] = None


@router.post("/api/controller/decide_model")
def decide_model(body: DecideModelBody) -> dict[str, Any]:
    """Rung 1: a Bundle in, a Decision out, via Haiku (budget-gated). The response's `decision`
    is already strict-parsed; a rejected/over-budget call returns an escalation, never a raise."""
    bundle = _bundle_from_dict(body.bundle)
    reasoner = HaikuReasoner(budget_limit=body.budget_limit)
    decision = reasoner(bundle)
    return {
        "decision": {
            "intent": decision.intent, "params": decision.params,
            "confidence": decision.confidence, "rung": decision.rung,
            "rationale": decision.rationale, "evidence": list(decision.evidence),
            "expected_next": list(decision.expected_next),
            "escalate": decision.escalate,
        },
        "cost_usd": reasoner.last_cost_usd,
    }


@router.post("/api/controller/decide")
def decide_cascade(body: DecideModelBody) -> dict[str, Any]:
    """The FULL cascade for a posted bundle — rung 0 (programs) then, only if asked, the model.
    Deterministic and free by default (model=None): a 'what would the controller do here?' probe
    the cockpit uses without spending. Set `budget_limit` present to allow the Haiku rung."""
    bundle = _bundle_from_dict(body.bundle)
    reasoner = HaikuReasoner(budget_limit=body.budget_limit) if body.budget_limit is not None else None
    decision = decide(bundle, programs=programs_mod.ProgramStore(), model=reasoner)
    return {"decision": {
        "intent": decision.intent, "params": decision.params, "confidence": decision.confidence,
        "rung": decision.rung, "rationale": decision.rationale,
        "evidence": list(decision.evidence),
        "expected_next": list(decision.expected_next), "escalate": decision.escalate}}


@router.get("/api/controller/summary")
def summary() -> dict[str, Any]:
    """The scoreboard: corpus size, per-rung / per-outcome, verified & escalation rates, program
    count, and per-scenario shadow agreement — everything the cockpit's controller panel needs."""
    js = decision_journal.summarize()
    progs = programs_mod.list_programs()
    return {
        **js,
        "programs": [{"task": p.task, "state": p.state, "steps": len(p.steps),
                      "guard_fields": list(p.guard_fields), "expected_exit": list(p.expected_exit),
                      "stale": p.stale, "verified_at": p.verified_at} for p in progs],
        "program_count": len(progs),
        "agreement": controller_metrics.shadow_agreement(decision_journal.read_rows()),
    }


@router.get("/api/controller/decisions")
def decisions(limit: int = 100, session_id: Optional[str] = None) -> dict[str, Any]:
    """The recent decision feed (newest first) — what the reasoner decided, at which rung, and
    whether it verified. Optionally scoped to one run."""
    rows = decision_journal.read_rows()
    if session_id:
        rows = [r for r in rows if r.get("session_id") == session_id]
    rows = list(reversed(rows))[:limit]
    return {"decisions": rows, "count": len(rows)}


class RunBody(BaseModel):
    browser_url: str
    tab_id: str
    task: str = "indeed_apply"
    goal_text: str = ""
    mode: str = "rung0_only"          # rung0_only ($0) | assisted (Haiku rung allowed)
    max_steps: int = 12
    session_id: str = ""
    min_confidence: float = 0.85      # assisted: the unwatched-action bar


@router.post("/api/controller/run")
async def run_live(body: RunBody) -> dict[str, Any]:
    """Drive one task on a live tab through observe → decide → act → verify. THE call site.

    `run_controller` existed, was tested, and had never been pointed at a real tab — every
    reference to it was a test — which is why the only live surface was the step-wise
    teach/observe → teach/commit pair, i.e. one operator press per step by construction.

    Modes:
      * `rung0_only` (default) — no model is wired, so only COMPILED PROGRAMS act. Anything
        without a program escalates. $0, and the honest measure of how far rung 0 carries a task.
      * `assisted` — the Haiku rung may also propose, auto-approved only above `min_confidence`
        (see `teach.auto_reviewer`); below it, a human gets it.

    Gates that stay loud in BOTH modes, because "less push-button" must never mean "fewer
    safeguards": SUBMIT is held for the operator (CONSEQUENTIAL_INTENTS), human-required states
    (sign-in / create-account) are structurally undriveable, BLOCKED hands straight over, and two
    escalations in a row stop the drive. Every escalation raises a real handoff alert.
    """
    from controller.live_actuator import LiveActuator
    from controller.loop import run_controller
    from controller.teach import auto_reviewer
    from runtime import handoff as handoff_mod

    if body.mode not in ("rung0_only", "assisted"):
        return {"ok": False, "detail": f"unknown mode {body.mode!r} (rung0_only | assisted)"}

    actuator = LiveActuator(base_url=settings.capture_server_url, browser_url=body.browser_url,
                            tab_id=body.tab_id, task=body.task, goal_text=body.goal_text,
                            driver="humanized")
    model = HaikuReasoner() if body.mode == "assisted" else None

    trail: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []

    def _on_step(bundle: Bundle, decision: Decision, result: Any) -> None:
        trail.append({
            "state": bundle.state, "url": bundle.url, "rung": decision.rung,
            "intent": decision.intent, "params": decision.params,
            "confidence": decision.confidence, "escalate": decision.escalate,
            "rationale": decision.rationale,
            "outcome": getattr(result, "outcome", None),
            "landed_state": getattr(result, "landed_state", None),
            "expected_next": list(decision.expected_next),
        })

    def _on_review(bundle: Bundle, decision: Decision, review: Any) -> None:
        reviews.append({"state": bundle.state, "intent": decision.intent,
                        "confidence": decision.confidence, "verdict": str(review.action.value)})

    def _on_consequential(bundle: Bundle, decision: Decision) -> None:
        held.append({"state": bundle.state, "intent": decision.intent,
                     "detail": "held for the operator — Submit is always yours"})

    result = await run_in_threadpool(
        run_controller, actuator,
        programs=programs_mod.ProgramStore(), model=model,
        session_id=body.session_id or f"run-{body.task}", max_steps=body.max_steps,
        reviewer=auto_reviewer(min_confidence=body.min_confidence, on_review=_on_review),
        on_escalate=handoff_mod.escalation_callback(
            task_goal=body.goal_text or body.task, reason="unexpected_state"),
        on_consequential=_on_consequential, on_step=_on_step)

    # The number the whole exercise is for: how much ran without a human.
    acted = [t for t in trail if not t["escalate"]]
    autonomous = [t for t in acted if t["rung"] == "recipe"]
    # Verified means BOTH halves, exactly as loop._verify defines it. Checking only the landed
    # state counted a `not_found` click as a success in the first live run — a metric that
    # flatters itself is worse than no metric.
    verified = [t for t in acted
                if t["outcome"] == Outcome.OK.value
                and t["landed_state"] and t["landed_state"] in t["expected_next"]]
    # PROGRESS, not just success. A verified action that leaves the page where it was has
    # achieved nothing, and reporting it as autonomy is how a treadmill scores 100% (live,
    # 2026-07-19: 8 verified Continue clicks on one blocked page). Count the steps that actually
    # moved us.
    progressed = sum(1 for a, b in zip(trail, trail[1:]) if a["url"] != b["url"])
    return {
        "ok": True, "status": result.status, "reason": result.reason, "steps": result.steps,
        "mode": body.mode,
        "autonomy": {
            "steps_total": len(trail),
            "steps_acted": len(acted),
            "steps_rung0": len(autonomous),
            "steps_verified": len(verified),
            "steps_progressed": progressed,
            "escalations": len([t for t in trail if t["escalate"]]),
            "rung0_share": round(len(autonomous) / len(trail), 3) if trail else 0.0,
        },
        "held_for_operator": held, "reviews": reviews, "trail": trail,
    }


class CompileProgramsBody(BaseModel):
    save: bool = False          # default is a DRY RUN — see the docstring
    limit: int = 1000


@router.post("/api/controller/programs/compile")
def compile_programs(body: CompileProgramsBody) -> dict[str, Any]:
    """Turn verified journal rows into rung-0 intent programs — the crank that converts the
    teacher's expensive proven work into the $0 path. Run it after a teaching drive.

    Defaults to a DRY RUN (`save=false`): it reports exactly what would compile and what was
    rejected *and why*. That matters because rung 0 is the rung that replays WITHOUT asking
    anyone, so a bad program is worse than no program — it must be inspectable before it fires.
    Missing exits inherit the recipe's edges so a compiled program is verifiable on replay.
    """
    import apply_recipe
    rows = decision_journal.read_rows(limit=body.limit)
    return programs_mod.compile_all_from_journal(
        rows, save=body.save, expected_exit_for=apply_recipe.expected_next_for)


@router.get("/api/controller/programs")
def programs() -> dict[str, Any]:
    """The compiled intent programs — the $0 rung-0 library, growing as the teacher drives."""
    progs = programs_mod.list_programs()
    return {"programs": [{"task": p.task, "state": p.state, "steps": list(p.steps),
                          "guard_fields": list(p.guard_fields),
                          "expected_exit": list(p.expected_exit), "stale": p.stale,
                          "compiled_from": list(p.compiled_from), "verified_at": p.verified_at}
                         for p in progs], "count": len(progs)}


class ObserveBody(BaseModel):
    """Preview the controller on a live tab (read-only) or on a supplied url+text. Never acts."""
    task: str = "indeed_apply"
    goal_text: str = ""
    tab_url: Optional[str] = None       # live: fetch scan_required for this tab (read-only, free)
    tab_id: Optional[str] = None
    url: Optional[str] = None           # manual: use this url directly (no browser needed)
    page_text: str = ""                 # manual: page text for Workday/Greenhouse state
    allow_model: bool = False           # allow the Haiku rung (budget-gated); default free
    budget_limit: Optional[float] = None


async def _fetch_scan(tab_url: str, tab_id: Optional[str]) -> tuple[Optional[list], str, str]:
    """Read-only /scan_required for one tab. Degrades gracefully: returns (None, url, detail) if
    the capture server is unreachable so the preview still builds a bundle from the URL alone."""
    payload = {"tab_url": tab_url, "tab_id": tab_id} if tab_id else {"tab_url": tab_url}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(f"{settings.capture_server_url}/scan_required", json=payload)
            data = r.json()
        return data.get("unanswered"), tab_url, data.get("detail", "")
    except Exception as exc:  # noqa: BLE001 — a preview must not require a live browser
        return None, tab_url, f"scan_required unavailable ({type(exc).__name__}); state from URL only"


@router.post("/api/controller/observe")
async def observe(body: ObserveBody) -> dict[str, Any]:
    """OBSERVE -> DECIDE for one tab, WITHOUT acting — the cockpit's 'watch it think' surface.

    Safe by construction: reads the tab read-only (a local CDP socket, free even in low-data mode),
    and the model rung is off unless `allow_model` is set. Returns the Bundle (and its frozen
    prompt serialization) plus the Decision the controller would make.
    """
    url = body.tab_url or body.url or ""
    scan: Optional[list] = None
    scan_detail = ""
    if body.tab_url:
        scan, url, scan_detail = await _fetch_scan(body.tab_url, body.tab_id)

    tail = decision_journal.read_rows(limit=5)
    bundle = build_bundle(body.task, url, body.page_text, goal_text=body.goal_text,
                          scan=scan, journal_tail=tail)

    reasoner = HaikuReasoner(budget_limit=body.budget_limit) if body.allow_model else None
    decision = await run_in_threadpool(
        decide, bundle, programs=programs_mod.ProgramStore(), model=reasoner)

    return {
        "bundle": dataclasses.asdict(bundle),
        "prompt": bundle_to_prompt(bundle),
        "decision": {
            "intent": decision.intent, "params": decision.params,
            "confidence": decision.confidence, "rung": decision.rung,
            "rationale": decision.rationale, "evidence": list(decision.evidence),
            "expected_next": list(decision.expected_next),
            "escalate": decision.escalate,
        },
        "scan_detail": scan_detail,
        "model_cost_usd": reasoner.last_cost_usd if reasoner else 0.0,
    }


# --- The live TEACHING surface (PLAN_controller_v1 §4; PRINCIPLES §9 + §10 — the Open Brain) --------
# The owed live-teaching drive, reachable over HTTP: the teacher decides, we ACT it through the
# LiveActuator (humanized/trusted driving) and JOURNAL a DecisionRecord carrying the teacher's
# rationale + evidence — so the reasoning transfers into the students' corpus. SUBMIT is HELD for the
# operator (the human owns the irreversible). Stateless: each call re-observes the live tab first, so
# the row records the exact bundle acted on and never a stale one (PRINCIPLES §1).

class TeachObserveBody(BaseModel):
    browser_url: str
    tab_id: str
    task: str = "indeed_apply"
    goal_text: str = ""


class TeachDecisionIn(BaseModel):
    intent: str
    params: dict[str, Any] = {}
    rationale: str = ""
    evidence: list[str] = []
    expected_next: list[str] = []
    confidence: float = 1.0


class TeachCommitBody(BaseModel):
    browser_url: str
    tab_id: str
    task: str = "indeed_apply"
    goal_text: str = ""
    decision: TeachDecisionIn
    proposed: Optional[TeachDecisionIn] = None   # the backstop's competing take (golden contrast, §10)
    session_id: Optional[str] = None


def _live_actuator(body: Any):
    """A LiveActuator on the humanized/trusted driver — the system-wide standard for real driving."""
    from controller.live_actuator import LiveActuator
    return LiveActuator(base_url=settings.capture_server_url, browser_url=body.browser_url,
                        tab_id=body.tab_id, task=body.task, goal_text=body.goal_text,
                        driver="humanized")


def _decision_from(d: TeachDecisionIn, *, rung: str) -> Decision:
    return Decision(intent=d.intent, params=dict(d.params or {}), confidence=d.confidence,
                    rung=rung, rationale=d.rationale, evidence=tuple(d.evidence),
                    expected_next=tuple(d.expected_next))


@router.post("/api/controller/teach/observe")
def teach_observe(body: TeachObserveBody) -> dict[str, Any]:
    """Read-only observe of the live tab + what the deterministic cascade would do (the teacher's
    cue). Never acts. The teacher reads this, authors a Decision, and posts it to /teach/commit."""
    actuator = _live_actuator(body)
    bundle = actuator.observe()
    suggestion = decide(bundle, programs=programs_mod.ProgramStore(), model=None)
    return {
        "state": bundle.state, "ats": bundle.ats, "url": bundle.url, "done": bundle.done,
        "human_required": bundle.human_required, "is_branch": bundle.is_branch,
        "unanswered": [dict(u) for u in bundle.unanswered],
        "prompt": bundle_to_prompt(bundle),
        "suggestion": {"intent": suggestion.intent, "params": suggestion.params,
                       "rung": suggestion.rung, "rationale": suggestion.rationale,
                       "escalate": suggestion.escalate},
    }


@router.post("/api/controller/teach/commit")
def teach_commit(body: TeachCommitBody) -> dict[str, Any]:
    """Act ONE teacher-authored Decision through the humanized LiveActuator and JOURNAL it with the
    teacher's rationale + evidence (the Open Brain). SUBMIT is HELD — the operator owns it."""
    from controller.loop import CONSEQUENTIAL_INTENTS

    decision = _decision_from(body.decision, rung="teacher")
    proposed = _decision_from(body.proposed, rung="model") if body.proposed is not None else None

    actuator = _live_actuator(body)
    bundle = actuator.observe()   # fresh: the row records the bundle actually acted on

    # A teacher who didn't spell out where this should land INHERITS the recipe's edges. Without
    # this, `expected_next` is empty, so `verified` below stays None and the loop's "landed
    # somewhere we didn't expect" trigger is inert — which is exactly what the first 48 journal
    # rows show (measured 2026-07-19: expected_next=[] on every one, verified=None on 37).
    if bundle.expected_next:
        if not decision.expected_next:
            decision = dataclasses.replace(decision, expected_next=tuple(bundle.expected_next))
        if proposed is not None and not proposed.expected_next:
            proposed = dataclasses.replace(proposed, expected_next=tuple(bundle.expected_next))

    # HUMAN GATE — never act a consequential intent (Submit). Journal it held, hand to the operator.
    if decision.intent in CONSEQUENTIAL_INTENTS:
        rec = decision_journal.record_for(decision, bundle, proposed=proposed,
                                          golden=bool(proposed), outcome=None,
                                          session_id=body.session_id)
        decision_journal.log_decision(rec)
        return {"held": True, "intent": decision.intent, "journaled": True,
                "detail": "SUBMIT held for the operator (consequential gate, PRINCIPLES §9)"}

    result = actuator.act(decision)
    landed = result.landed_state
    verified: Optional[bool] = None
    if decision.expected_next:
        verified = (landed in decision.expected_next) and (result.outcome == Outcome.OK.value)
    rec = decision_journal.record_for(
        decision, bundle, proposed=proposed, golden=bool(proposed),
        outcome=result.outcome, landed_state=landed, verified=verified,
        session_id=body.session_id, cost_usd=getattr(result, "cost_usd", 0.0))
    saved = decision_journal.log_decision(rec)
    return {"held": False, "intent": decision.intent, "outcome": result.outcome,
            "landed_state": landed, "verified": verified, "detail": result.detail,
            "journaled": saved is not None}
