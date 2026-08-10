"""The teacher inbox — the seat that was missing.

--------------------------------------------------------------------------------------
Why this file is the point of the whole plan
--------------------------------------------------------------------------------------
`Reviewer` has had exactly two implementations:

  * `teach.cli_reviewer` — blocks on `input()`. A human at a TTY, on EVERY non-recipe step.
  * `teach.auto_reviewer` — a confidence floor that never asks anybody.

and `teach_session.propose()/commit()` asks the teacher every single turn, including turns a
compiled program would have run for free. So the settings were *ask always* or *never ask*, and an
escalation ENDED the drive (`STATUS_ESCALATED`). There was nowhere for the local Claude agent to
answer a question, which is why sessions kept falling back to hand-rolled scripts around the API.
That was never indiscipline; it was a missing seam, and PRINCIPLES §11 has listed it as owed.

This is that seam: a durable request/response queue the teacher can service over HTTP while a
drive is still running, with the drive PARKED rather than dead.

--------------------------------------------------------------------------------------
A file, deliberately
--------------------------------------------------------------------------------------
Append-only JSONL beside the journals. It survives the process restart that is this repo's real
collision risk, it costs no infrastructure, it is greppable when something goes wrong, and it is
readable on a tethered connection. A request and its response are separate rows overlaid by id —
the same durable-overlay shape `runtime/handoff.py` already uses for resolve markers, so there is
one pattern here rather than two.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from interaction.decision import Decision, is_real_rationale
from interaction.lesson import Lesson

_lock = threading.Lock()
_INBOX_NAME = "teacher_inbox.jsonl"

#: How long a parked drive waits before giving up and behaving exactly as it did before this
#: existed (`emit_escalation` + stop). Bounded on purpose: an unbounded park would turn "the
#: teacher is away" into a hung worker, and the pre-existing behaviour is a perfectly good floor.
DEFAULT_PARK_SECONDS = 300.0

#: How often a parked drive re-reads the queue. A file stat + tail is cheap; this is not a hot loop.
POLL_SECONDS = 1.0


class RequestKind(str, Enum):
    """What the drive is asking for. The three map onto the three modes that need a teacher."""

    #: YELLOW — "here is my proposal, approve or correct it."
    REVIEW = "review"
    #: ORANGE — "I cannot choose here. Give me ONE bounded action; I will perform and verify it."
    INSTRUCT = "instruct"
    #: RED — "I cannot operate this. Take the wheel until one of these stop conditions is met."
    TAKEOVER = "takeover"


class ResponseAction(str, Enum):
    APPROVE = "approve"          # act on the proposal as-is
    CORRECT = "correct"          # act on `decision` instead; journals a golden row
    INSTRUCT = "instruct"        # ORANGE: `decision` is the bounded action for us to perform
    ESCALATE = "escalate"        # not mine either — hand further up
    TAKEOVER_DONE = "takeover_done"   # RED: I drove, we are at a checkpoint, resume locally
    ABORT = "abort"              # stop the drive


#: A RED handoff without an exit condition means "finish the application", which is the teacher
#: replacing the driver rather than borrowing the wheel. These are the defaults every ticket
#: carries unless the caller narrows them.
DEFAULT_STOP_CONDITIONS: tuple[str, ...] = (
    "the immediate obstacle is resolved",
    "a state the recipe already knows is reached",
    "three actions in a row fail",
    "an irreversible decision (Submit, account creation, payment) comes up — that is the human's",
)


@dataclass
class TeacherRequest:
    """One question the drive is parked on. Carries everything needed to answer it WITHOUT
    reading the drive's memory — the escalation package, not a pointer to one."""

    id: str
    ts: str
    kind: str                                   # a RequestKind value
    status: str = "open"                        # open | answered | expired
    session_id: str = ""
    task: str = ""
    state: Optional[str] = None
    url: str = ""
    mode: str = ""                              # the ControlMode that produced this
    maturity: str = ""
    authority_reason: str = ""
    #: The frozen serialisations, so the teacher reads exactly what the policy reads.
    bundle_prompt: str = ""
    belief_prompt: str = ""
    verdict_prompt: str = ""
    authority_prompt: str = ""
    #: The LOCAL system's prediction — never omitted. §4 of the design: the inner layers are
    #: scored on every turn the teacher is paid for, or they are never scored where it counts.
    prediction: dict = field(default_factory=dict)
    reach_gaps: list[str] = field(default_factory=list)
    stop_conditions: list[str] = field(default_factory=list)
    #: Filled by `respond`.
    response: Optional[dict] = None
    answered_at: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _default_artifacts_dir() -> Path:
    env = os.environ.get("INTERACTION_ARTIFACTS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "apps" / "mcp" / "output"


def _path() -> Path:
    p = _default_artifacts_dir() / "cache" / _INBOX_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _append(row: dict[str, Any]) -> None:
    with _lock:
        with _path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_overlaid() -> dict[str, dict[str, Any]]:
    """Every request, with later rows for the same id overlaid onto the first — the same durable
    pattern `runtime/handoff.py` uses, so a response never rewrites history."""
    p = _path()
    if not p.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:  # noqa: BLE001 — one bad line must not blind the queue
            continue
        rid = row.get("id")
        if rid:
            out.setdefault(rid, {}).update(row)
    return out


# --- the drive side -----------------------------------------------------------------
def ask(*, kind: str, session_id: str = "", task: str = "", state: Optional[str] = None,
        url: str = "", mode: str = "", maturity: str = "", authority_reason: str = "",
        bundle_prompt: str = "", belief_prompt: str = "", verdict_prompt: str = "",
        authority_prompt: str = "", prediction: Optional[dict] = None,
        reach_gaps: Optional[list[str]] = None,
        stop_conditions: Optional[list[str]] = None) -> TeacherRequest:
    """Post a question and return it. Does NOT wait — `wait_for` does that, so a caller can post
    and keep working if it ever wants to."""
    # The inbox is a SECOND durable store (cache/teacher_inbox.jsonl, served whole by
    # /teacher/pending), and attended mode routes every YELLOW review through it — so the local
    # prediction's params get the SAME redaction the decision journal applies at log time. The
    # bundle/belief prompts are already value-free by construction (field names only); params
    # were the one place a typed answer rode in verbatim (caught in review, 2026-08-10).
    from interaction.decision_journal import _redact_params

    prediction = dict(prediction or {})
    if prediction.get("params"):
        prediction["params"] = _redact_params(prediction["params"])
    request = TeacherRequest(
        id=f"tr_{uuid.uuid4().hex[:12]}",
        ts=datetime.now(timezone.utc).isoformat(),
        kind=kind, session_id=session_id, task=task, state=state, url=url, mode=mode,
        maturity=maturity, authority_reason=authority_reason,
        bundle_prompt=bundle_prompt, belief_prompt=belief_prompt,
        verdict_prompt=verdict_prompt, authority_prompt=authority_prompt,
        prediction=prediction, reach_gaps=list(reach_gaps or []),
        stop_conditions=list(stop_conditions
                             or (DEFAULT_STOP_CONDITIONS if kind == RequestKind.TAKEOVER.value
                                 else ())),
    )
    _append(request.as_dict())
    return request


def wait_for(request_id: str, *, timeout: float = DEFAULT_PARK_SECONDS,
             poll: float = POLL_SECONDS, sleep=time.sleep,
             now=time.monotonic) -> Optional[dict[str, Any]]:
    """Park until this request is answered, or the timeout expires.

    Returns the response dict, or None on timeout. **None is not a failure mode to paper over** —
    the caller's contract is that a timeout reproduces exactly the behaviour that existed before
    the inbox did (raise the handoff, stop the drive). Nobody listening must never become worse
    than nobody asking.

    `sleep`/`now` are injected so the whole park/resume path is testable offline in microseconds.
    """
    deadline = now() + max(0.0, timeout)
    while True:
        row = _read_overlaid().get(request_id)
        if row and row.get("response"):
            return row["response"]
        if now() >= deadline:
            _append({"id": request_id, "status": "expired",
                     "answered_at": datetime.now(timezone.utc).isoformat()})
            return None
        sleep(poll)


def ask_and_wait(*, timeout: float = DEFAULT_PARK_SECONDS, poll: float = POLL_SECONDS,
                 sleep=time.sleep, now=time.monotonic, **kwargs) -> tuple[TeacherRequest,
                                                                         Optional[dict]]:
    request = ask(**kwargs)
    return request, wait_for(request.id, timeout=timeout, poll=poll, sleep=sleep, now=now)


# --- the teacher side ---------------------------------------------------------------
def pending(limit: int = 20) -> list[dict[str, Any]]:
    """Open questions, oldest first — oldest because a parked drive is waiting on the oldest one
    and answering newest-first would starve it."""
    rows = [r for r in _read_overlaid().values() if r.get("status") == "open"]
    rows.sort(key=lambda r: r.get("ts") or "")
    return rows[:limit] if limit else rows


def get(request_id: str) -> Optional[dict[str, Any]]:
    return _read_overlaid().get(request_id)


class InboxError(ValueError):
    """A malformed response. Loud, because a silently-dropped answer parks the drive until it
    times out and the operator has no idea why."""


def respond(request_id: str, *, action: str, decision: Optional[Decision] = None,
            lesson: Optional[Lesson] = None, rationale: str = "",
            new_tab_id: str = "", note: str = "") -> dict[str, Any]:
    """Answer a parked question. Validates before it writes.

    The validation is not ceremony. `instruct` and `correct` are the two actions that put an
    action into the drive, and both are required to carry a REAL rationale (§10): the teacher's
    reasoning is the training signal, and `cli_reviewer` hardcoding "operator correction" is
    exactly how every human correction used to teach reasoning-blind.
    """
    row = _read_overlaid().get(request_id)
    if row is None:
        raise InboxError(f"unknown request {request_id!r}")
    if row.get("status") != "open":
        raise InboxError(f"request {request_id!r} is already {row.get('status')}")
    if action not in {a.value for a in ResponseAction}:
        raise InboxError(f"unknown action {action!r}")

    needs_decision = action in (ResponseAction.CORRECT.value, ResponseAction.INSTRUCT.value)
    if needs_decision and decision is None:
        raise InboxError(f"{action!r} must carry the decision the executor should perform")
    if needs_decision and not is_real_rationale(rationale or (decision.rationale if decision
                                                              else "")):
        raise InboxError(
            "a correction or instruction must carry real reasoning — the WHY is the training "
            "signal, and a placeholder teaches WHAT with no rule to generalise from (§10)")

    payload: dict[str, Any] = {"action": action, "rationale": rationale}
    if decision is not None:
        payload["decision"] = {
            "intent": decision.intent, "params": dict(decision.params or {}),
            "confidence": decision.confidence, "rung": "teacher",
            "rationale": rationale or decision.rationale,
            "expected_next": list(decision.expected_next),
            "evidence": list(decision.evidence),
        }
    if lesson is not None:
        payload["lesson"] = lesson.as_dict()
    # "The work is over there now." A teacher who watched a click open a window is the only
    # party that knows it; without this the loop keeps observing the tab it was constructed with
    # and a successful takeover reads as no progress (live, 2026-07-22).
    if new_tab_id:
        payload["new_tab_id"] = str(new_tab_id)
    # The operator's own words about THIS stop — "this ATS always asks X", "skip the resume step
    # here". Added with the coaching pane (2026-07-23) because the operator's situational
    # knowledge was the one input to this system with nowhere to live: it was said in chat and
    # lost. Carried into the acting decision's rationale, so it lands in the journal as evidence
    # and becomes lesson material rather than scrollback (§10).
    if note:
        payload["note"] = str(note)

    answered_at = datetime.now(timezone.utc).isoformat()
    _append({"id": request_id, "status": "answered", "response": payload,
             "answered_at": answered_at})
    return payload


def decision_from_response(response: Optional[dict]) -> Optional[Decision]:
    """Rebuild the teacher's Decision from a response payload, re-stamped `rung="teacher"` so the
    corpus shows who really decided — the same discipline as `teach.correct()`."""
    if not response:
        return None
    d = response.get("decision")
    if not isinstance(d, dict) or not d.get("intent"):
        return None
    note = str(response.get("note") or "")
    why = d.get("rationale", "")
    if note:
        why = f"{why} | operator: {note}" if why else f"operator: {note}"
    return Decision(intent=d["intent"], params=dict(d.get("params") or {}),
                    confidence=float(d.get("confidence", 1.0)), rung="teacher",
                    rationale=why,
                    expected_next=tuple(d.get("expected_next") or ()),
                    evidence=tuple(d.get("evidence") or ()) + (("operator_note",) if note else ()))


def lesson_from_response(response: Optional[dict]) -> Optional[Lesson]:
    if not response:
        return None
    raw = response.get("lesson")
    return Lesson.from_dict(raw) if isinstance(raw, dict) else None


# --- the Reviewer adapter -----------------------------------------------------------
def inbox_reviewer(*, session_id: str = "", timeout: float = DEFAULT_PARK_SECONDS,
                   poll: float = POLL_SECONDS, sleep=time.sleep, now=time.monotonic,
                   on_timeout=None):
    """A `teach.Reviewer` over this queue — YELLOW served by the same transport as ORANGE/RED.

    Deliberately built HERE rather than by editing `controller/teach.py`: that module is claimed
    by the operator's in-flight teacher-endpoints work, and the `Reviewer` protocol is injectable
    precisely so a new transport is an addition rather than a change.

    On timeout it ESCALATES rather than approving. Nobody listening must never mean "act
    unsupervised" — that would invert the gate this whole plan exists to install.
    """
    from controller import teach

    def _review(bundle, decision):
        request, response = ask_and_wait(
            kind=RequestKind.REVIEW.value, session_id=session_id, task=bundle.task,
            state=bundle.state, url=bundle.url,
            prediction={"intent": decision.intent, "params": dict(decision.params or {}),
                        "confidence": decision.confidence, "rung": decision.rung,
                        "rationale": decision.rationale,
                        "expected_next": list(decision.expected_next)},
            timeout=timeout, poll=poll, sleep=sleep, now=now)
        if response is None:
            if on_timeout is not None:
                on_timeout(request, bundle, decision)
            return teach.escalate()
        action = response.get("action")
        if action == ResponseAction.APPROVE.value:
            return teach.approve()
        if action == ResponseAction.ABORT.value:
            return teach.abort()
        if action == ResponseAction.CORRECT.value:
            corrected = decision_from_response(response)
            return teach.correct(corrected) if corrected else teach.escalate()
        return teach.escalate()

    return _review
