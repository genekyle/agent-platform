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


#: Intents that change nothing on the page. A "program" made only of these replays to no effect,
#: so it is not a program — it is a rung-0 no-op that would mask a state needing a real decision.
_NO_OP_INTENTS = frozenset({"observe", "describe", "scan_required", "resolve_answer"})

#: States whose action is chosen by the TASK TARGET, not by the page — which job to open comes
#: from the approved job on the blackboard, not from a fixed control. Compiling these produces a
#: program that clicks one specific job title ("full details of Analyst - Actuarial Financial
#: Reporting") and fails on every future drive. Site truth belongs in the data layer, so the
#: exclusion is a named list here rather than a heuristic guess at compile time.
NON_COMPILABLE_STATES = frozenset({
    "indeed_job_posting", "indeed_search_results", "indeed_home",
})


def rejection_reason(rows: list[dict[str, Any]]) -> Optional[str]:
    """Why this (task, state) must NOT become a rung-0 program — or None if it may.

    Guarding at compile time matters more than guarding at replay: a bad program is worse than no
    program, because rung 0 is the rung that runs WITHOUT asking anyone.
    """
    good = _compilable_rows(rows)
    if not good:
        return "no verified-ok rows"
    state = good[0].get("state")
    if state in NON_COMPILABLE_STATES:
        return (f"{state!r} is target-parameterised — the action depends on which item is being "
                f"pursued, not on a stable control")
    if not [r for r in good if r.get("intent") not in _NO_OP_INTENTS]:
        return "every step is a no-op (observe/describe) — nothing to replay"
    return None


def _compilable_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows
            if r and not r.get("escalate") and r.get("outcome") == "ok"
            and (r.get("verified") in (True, None))]


def compile_from_journal(rows: list[dict[str, Any]], *,
                         expected_exit: tuple[str, ...] = ()) -> Optional[IntentProgram]:
    """Turn a verified-OK decision sequence for ONE (task, state) into an IntentProgram.

    `rows` are DecisionRecord dicts (journal order). They must all share (task, state) and be
    non-escalated; only VERIFIED-OK rows are compiled (an unverified step is not a proven step).
    guard_fields = the fields the steps targeted; expected_exit defaults to the last row's
    expected_next (the advance step's expectation) unless the caller pins it.

    Returns None when `rejection_reason` objects — a bad program is worse than no program, because
    rung 0 replays without asking anyone.
    """
    if rejection_reason(rows) is not None:
        return None
    good = [r for r in _compilable_rows(rows) if r.get("intent") not in _NO_OP_INTENTS]
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


def compile_all_from_journal(rows: list[dict[str, Any]], *, save: bool = False,
                             expected_exit_for: Optional[Any] = None) -> dict[str, Any]:
    """Group journal rows by (task, state) and compile each into a rung-0 program.

    This is the crank that turns the teacher's expensive verified work into the $0 path, and it
    had simply never been run: 12 verified steps sat in the journal while `programs/` was empty
    (measured 2026-07-19), so every drive paid full price for steps already proven.

    Rejections are REPORTED, not skipped silently — "nothing compiled" and "everything was
    rejected for a reason" look identical otherwise, and the reason is the interesting part.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        task, state = r.get("task"), r.get("state")
        if task and state:
            groups.setdefault((task, state), []).append(r)

    compiled: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for (task, state), rs in groups.items():
        reason = rejection_reason(rs)
        if reason is None:
            program = compile_from_journal(rs)
            if program is None:
                reason = "no compilable steps after filtering"
            else:
                # A program with no expected_exit can't be verified on replay — rung 0 would run
                # blind. Rows journaled before expected_next was carried through (every row of
                # the first corpus) inherit the recipe's edges here, the same rule the model and
                # teacher rungs follow. Never fabricated: an absent recipe entry stays empty.
                if not program.expected_exit and expected_exit_for is not None:
                    inherited = tuple(expected_exit_for(state) or ())
                    if inherited:
                        program = replace(program, expected_exit=inherited)
                if save:
                    save_program(program)
                compiled.append({"task": task, "state": state, "steps": len(program.steps),
                                 "guard_fields": list(program.guard_fields),
                                 "expected_exit": list(program.expected_exit),
                                 "intents": [s.get("intent") for s in program.steps]})
                continue
        rejected.append({"task": task, "state": state, "reason": reason})
    return {"compiled": compiled, "rejected": rejected, "saved": bool(save)}


def recompile_from_new_evidence(rows: list[dict[str, Any]], *,
                                expected_exit_for: Optional[Any] = None) -> dict[str, Any]:
    """The AUTOMATIC crank — compile only where the journal holds evidence the program has not seen.

    `compile_all_from_journal` had exactly one caller: a manual endpoint defaulting to a dry run.
    Meanwhile `mark_stale` fires automatically — so staleness was a one-way door, and the measured
    cost was 26 of 45 teacher parks on ONE state (`indeed_apply_questions`) whose compiled program
    sat condemned while the journal held fresh verified teacher steps for it (2026-08-20 audit).

    The safety rule that makes this automatic rather than reckless: a (task, state) recompiles
    ONLY when a verified-ok row exists with `ts` newer than the program's `verified_at`.

      * no program yet            -> compile (the original crank, now with a caller)
      * stale + newer evidence    -> recompile — the pardon comes from NEW proof
      * stale + nothing newer     -> SKIP. Recompiling the same rows would resurrect the exact
                                     program the world just proved wrong, and rung 0 replays
                                     without asking anyone.
      * fresh + nothing newer     -> skip; there is nothing to learn.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        task, state = r.get("task"), r.get("state")
        if task and state:
            groups.setdefault((task, state), []).append(r)

    out = {"compiled": [], "rejected": [], "skipped": [], "saved": True}
    for (task, state), rs in groups.items():
        newest = max((r.get("ts") or "" for r in _compilable_rows(rs)), default="")
        existing = load_program(task, state)
        if existing is not None and not (newest and newest > (existing.verified_at or "")):
            out["skipped"].append({
                "task": task, "state": state,
                "reason": ("stale, and no evidence newer than the condemned compile — "
                           "staleness stands" if existing.stale
                           else "no evidence newer than the compiled program")})
            continue
        one = compile_all_from_journal(rs, save=True, expected_exit_for=expected_exit_for)
        out["compiled"].extend(one["compiled"])
        out["rejected"].extend(one["rejected"])
    return out


def recompile_now() -> dict[str, Any]:
    """`recompile_from_new_evidence` over the live journal with the recipe's edges — the zero-arg
    form both automatic callers share (train-on-label, and the end of a controller run). Lazy
    imports because this module is otherwise dependency-free and the callers are background hooks.
    """
    import apply_recipe
    from interaction import decision_journal

    rows = decision_journal.read_rows(limit=1000)
    return recompile_from_new_evidence(rows, expected_exit_for=apply_recipe.expected_next_for)
