"""Intent programs — the fuel for rung 0. Compile a verified decision SEQUENCE into a
replayable list of intents, keyed by (task, state).

--------------------------------------------------------------------------------------
Why this exists (PLAN_controller_v1 §3): recipe steps are inert PROSE
--------------------------------------------------------------------------------------
`INDEED_APPLY_RECIPE` says `"autofill (atomic) + Continue"` — no code reads it. Hand-
translating prose into a new executable schema is a rewrite that re-inertifies the moment a
site changes. The proven move is compile-on-first-drive: the first time through a state the
expensive rungs (model/teacher) decide step by step and journal each verified-OK decision;
a verified sequence within one (task, state) is saved HERE as an intent program; the next
visit, rung 0 replays it for $0. The teacher's expensive work becomes the cheap path
mechanically — the flywheel's first turn happens inside the controller.

--------------------------------------------------------------------------------------
Programs never store literal values (the PII discipline, same as the journal's redact())
--------------------------------------------------------------------------------------
A step's params carry a FIELD NAME, never the answer. The value is resolved at replay time
from the answer store (`application_answers` / `apply_fields`), keyed by field. So a committed
program file contains zero PII by construction — grep-clean of the operator's name/email — and
`compile_from_journal` drops any `value` it sees rather than trusting the caller.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from interaction.decision import looks_like_selector

PROGRAM_SCHEMA_VERSION = "v1"

#: Params a program step may carry — SEMANTIC only. `field` (which field to act on),
#: `control` (the accessible name of a button/link), `value_ref` (a KEY into the answer
#: store, never a value). Anything else — especially `value` or a selector — is dropped.
_STEP_PARAM_KEEP = ("field", "control", "value_ref")


@dataclass(frozen=True)
class IntentProgram:
    """A replayable sequence of intents for one (task, state). Recipe-layer DATA."""

    task: str
    state: str
    guard_fields: tuple[str, ...]         # the unanswered field-set this program expects
    steps: tuple[dict[str, Any], ...]     # ordered [{intent, params}]; field-steps carry params.field
    expected_exit: tuple[str, ...]        # states a clean replay should land on
    version: str = PROGRAM_SCHEMA_VERSION
    compiled_from: tuple[str, ...] = ()   # the bundle digests it was compiled from
    verified_at: str = ""
    stale: bool = False

    def key(self) -> tuple[str, str]:
        return (self.task, self.state)


# --- store: one JSON per (task, state) ----------------------------------------------
def _programs_dir() -> Path:
    """`apps/controlplane-api/programs/` by default; `CONTROLLER_PROGRAMS_DIR` overrides
    (tests, and any deployment that moves the recipe data off the repo)."""
    env = os.environ.get("CONTROLLER_PROGRAMS_DIR")
    if env:
        p = Path(env)
    else:
        p = Path(__file__).resolve().parent.parent / "programs"
    p.mkdir(parents=True, exist_ok=True)
    return p


_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(s: str) -> str:
    return _SLUG.sub("-", (s or "").strip().lower()).strip("-") or "x"


def _program_path(task: str, state: str) -> Path:
    return _programs_dir() / f"{_slug(task)}__{_slug(state)}.json"


def _sanitize_step(step: dict[str, Any]) -> dict[str, Any]:
    """A program step as it may be committed: intent + semantic params only (no value, no
    selector). This is the PII/addressing guard applied at the boundary, not trusted upstream."""
    intent = step.get("intent")
    raw_params = step.get("params") if isinstance(step.get("params"), dict) else {}
    params: dict[str, Any] = {}
    for k in _STEP_PARAM_KEEP:
        if k in raw_params and raw_params[k] is not None and not looks_like_selector(raw_params[k]):
            params[k] = raw_params[k]
    return {"intent": intent, "params": params}


def save_program(program: IntentProgram) -> Path:
    """Persist a program, re-sanitising every step at the boundary (defence in depth)."""
    safe = IntentProgram(
        task=program.task, state=program.state,
        guard_fields=tuple(program.guard_fields),
        steps=tuple(_sanitize_step(s) for s in program.steps),
        expected_exit=tuple(program.expected_exit),
        version=program.version,
        compiled_from=tuple(program.compiled_from),
        verified_at=program.verified_at or datetime.now(timezone.utc).isoformat(),
        stale=program.stale,
    )
    path = _program_path(program.task, program.state)
    path.write_text(json.dumps(asdict(safe), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_program(task: str, state: str) -> Optional[IntentProgram]:
    """The program for (task, state), or None. A stale program is returned (the caller decides);
    a malformed file is treated as absent rather than raising into decide()."""
    path = _program_path(task, state)
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return IntentProgram(
            task=d["task"], state=d["state"],
            guard_fields=tuple(d.get("guard_fields") or ()),
            steps=tuple(d.get("steps") or ()),
            expected_exit=tuple(d.get("expected_exit") or ()),
            version=d.get("version", PROGRAM_SCHEMA_VERSION),
            compiled_from=tuple(d.get("compiled_from") or ()),
            verified_at=d.get("verified_at", ""),
            stale=bool(d.get("stale", False)),
        )
    except Exception:  # noqa: BLE001 — a bad program file must not break the loop
        return None


def mark_stale(task: str, state: str) -> Optional[IntentProgram]:
    """Flag a program stale (a replay step's outcome diverged). Next visit escalates to rung 1,
    which recompiles on success. Returns the updated program, or None if there was none."""
    prog = load_program(task, state)
    if prog is None:
        return None
    stale = replace(prog, stale=True)
    save_program(stale)
    return stale


def list_programs() -> list[IntentProgram]:
    out: list[IntentProgram] = []
    for path in sorted(_programs_dir().glob("*.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            out.append(IntentProgram(
                task=d["task"], state=d["state"],
                guard_fields=tuple(d.get("guard_fields") or ()),
                steps=tuple(d.get("steps") or ()),
                expected_exit=tuple(d.get("expected_exit") or ()),
                version=d.get("version", PROGRAM_SCHEMA_VERSION),
                compiled_from=tuple(d.get("compiled_from") or ()),
                verified_at=d.get("verified_at", ""), stale=bool(d.get("stale", False))))
        except Exception:  # noqa: BLE001
            continue
    return out


class ProgramStore:
    """A dict-like view over the on-disk programs, so `decide()` takes one injected object it
    can look up (task, state) in — and tests can pass a plain dict of the same shape."""

    def get(self, task: str, state: str) -> Optional[IntentProgram]:
        return load_program(task, state)


# --- compile-on-first-drive ----------------------------------------------------------
def _field_of(step: dict[str, Any]) -> Optional[str]:
    p = step.get("params") if isinstance(step.get("params"), dict) else {}
    return p.get("field")


def compile_from_journal(rows: list[dict[str, Any]], *,
                         expected_exit: tuple[str, ...] = ()) -> Optional[IntentProgram]:
    """Turn a verified-OK decision sequence for ONE (task, state) into an IntentProgram.

    `rows` are DecisionRecord dicts (journal order). They must all share (task, state) and be
    non-escalated; only VERIFIED-OK rows are compiled (an unverified step is not a proven step).
    guard_fields = the fields the steps targeted; expected_exit defaults to the last row's
    expected_next (the advance step's expectation) unless the caller pins it.
    """
    good = [r for r in rows
            if r and not r.get("escalate") and r.get("outcome") == "ok"
            and (r.get("verified") in (True, None))]
    if not good:
        return None
    task = good[0].get("task")
    state = good[0].get("state")
    if not task or not state:
        return None
    if any(r.get("task") != task or r.get("state") != state for r in good):
        raise ValueError("compile_from_journal: rows span more than one (task, state)")

    steps = tuple(_sanitize_step({"intent": r.get("intent"), "params": r.get("params")})
                  for r in good)
    guard = tuple(dict.fromkeys(
        f for s in steps if (f := _field_of(s))))
    exit_states = expected_exit or tuple(good[-1].get("expected_next") or ())
    digests = tuple(dict.fromkeys(r.get("bundle_digest") for r in good if r.get("bundle_digest")))
    return IntentProgram(
        task=task, state=state, guard_fields=guard, steps=steps,
        expected_exit=exit_states, compiled_from=digests,
        verified_at=datetime.now(timezone.utc).isoformat(), stale=False)
