"""Replay — re-run journaled/curated bundles through `decide()` offline. The regression suite.

Two entry points:
  - `run_cases(cases, ...)`   — a curated fixture of (bundle, expectation) pairs. Every controller
    correction becomes a permanent case here, so a decide() change that would re-break a taught
    state fails a test instead of shipping. Deterministic, offline, FREE (no model rung in CI).
  - `replay_journal(rows, ...)` — re-run the bundles snapshotted on golden/shadow rows and check
    decide() still reproduces the journaled decision (deterministic rungs only).

`make controller-evals` runs the deterministic half. The model-rung replay is gated behind an
explicit flag (it spends), and is never in the default CI path.
"""

from __future__ import annotations

from typing import Any, Optional

from controller.decide import DecisionReasoner, ProgramLookup, decide
from controller import programs as programs_mod
from interaction.decision import Bundle, Decision

_DETERMINISTIC_RUNGS = frozenset({"recipe", "cache"})


def bundle_from_snapshot(snap: dict[str, Any]) -> Bundle:
    """Reconstruct a Bundle from a replay snapshot (or a fixture case's `bundle` block)."""
    return Bundle(
        task=snap.get("task", ""),
        goal_text=snap.get("goal_text", ""),
        done=bool(snap.get("done", False)),
        url=snap.get("url", "") or snap.get("route", ""),
        route=snap.get("route", "") or snap.get("url", ""),
        state=snap.get("state"),
        is_branch=bool(snap.get("is_branch", False)),
        human_required=bool(snap.get("human_required", False)),
        ats=snap.get("ats"),
        fingerprint=snap.get("fingerprint"),
        branch_note=snap.get("branch_note", ""),
        recipe_step=snap.get("recipe_step"),
        next_action=snap.get("next_action"),
        expected_next=tuple(snap.get("expected_next") or ()),
        lessons=snap.get("lessons", ""),
        unanswered=tuple(snap.get("unanswered") or ()),
        recent=tuple(snap.get("recent") or ()),
        phase=snap.get("phase"),
    )


def run_cases(cases: list[dict[str, Any]], *, programs: Optional[ProgramLookup] = None,
              model: Optional[DecisionReasoner] = None) -> dict[str, Any]:
    """Run each fixture case through decide() and check its expectations.

    A case = `{"name", "bundle": {...}, "expect": {"intent"?, "rung"?, "escalate"?, "field"?}}`.
    Only the keys present in `expect` are checked, so a case can pin just the rung, or the full
    decision. Returns a report with per-case pass/fail and the mismatches.
    """
    store = programs or programs_mod.ProgramStore()
    results: list[dict[str, Any]] = []
    passed = 0
    for case in cases:
        bundle = bundle_from_snapshot(case.get("bundle") or {})
        decision = decide(bundle, programs=store, model=model)
        exp = case.get("expect") or {}
        mism: list[str] = []
        if "intent" in exp and decision.intent != exp["intent"]:
            mism.append(f"intent {decision.intent!r} != {exp['intent']!r}")
        if "rung" in exp and decision.rung != exp["rung"]:
            mism.append(f"rung {decision.rung!r} != {exp['rung']!r}")
        if "escalate" in exp and decision.escalate != exp["escalate"]:
            mism.append(f"escalate {decision.escalate} != {exp['escalate']}")
        if "field" in exp and (decision.params or {}).get("field") != exp["field"]:
            mism.append(f"field {(decision.params or {}).get('field')!r} != {exp['field']!r}")
        ok = not mism
        passed += 1 if ok else 0
        results.append({"name": case.get("name", "?"), "ok": ok, "mismatches": mism,
                        "got": {"intent": decision.intent, "rung": decision.rung,
                                "escalate": decision.escalate}})
    return {"total": len(cases), "passed": passed, "failed": len(cases) - passed,
            "results": results}


def replay_journal(rows: list[dict[str, Any]], *, programs: Optional[ProgramLookup] = None,
                   include_model: bool = False) -> dict[str, Any]:
    """Re-run the bundles snapshotted on golden/shadow rows through decide(); check the
    DETERMINISTIC rungs still reproduce the journaled decision. Model-rung rows are skipped
    unless `include_model` (they spend and are non-deterministic)."""
    store = programs or programs_mod.ProgramStore()
    cases = [r for r in rows if r.get("bundle_snapshot")]
    checked = reproduced = 0
    mismatches: list[dict[str, Any]] = []
    for r in cases:
        journaled_intent = r.get("proposed_intent") or r.get("intent")
        journaled_rung = r.get("proposed_rung") or r.get("rung")
        if journaled_rung not in _DETERMINISTIC_RUNGS and not include_model:
            continue
        decision = decide(bundle_from_snapshot(r["bundle_snapshot"]), programs=store)
        checked += 1
        if decision.intent == journaled_intent:
            reproduced += 1
        else:
            mismatches.append({"state": r.get("state"), "was": journaled_intent,
                               "now": decision.intent})
    return {"snapshotted_rows": len(cases), "checked": checked, "reproduced": reproduced,
            "mismatches": mismatches,
            "reproduce_rate": round(reproduced / checked, 4) if checked else None}
