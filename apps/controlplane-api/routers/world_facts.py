"""World-facts routes — the staleness report that finds the rot before a drive does (§14, S16).

Thin by design: the derivations live in `world_facts.py` (pure over the recipes' own WORLD_FACTS
lists and the transition corpus), so the cockpit, a session-Claude curl, and a test all ask the
same question and get the same answer.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/world_facts")
def list_world_facts() -> dict[str, Any]:
    """Every registered world-fact, whole — dates, evidence, history (retractions keep both
    sides), and how a drive re-verifies each."""
    import world_facts as wfm
    facts = wfm.collect()
    return {"ok": True, "facts": sorted(facts.values(), key=lambda f: f["id"]),
            "migrated_modules": list(wfm._MIGRATED_MODULES)}


@router.get("/api/world_facts/staleness")
def world_fact_staleness() -> dict[str, Any]:
    """Which claims about a surface predate the last drive on that surface? Ranked most-outdriven
    first — the top entry is the next re-verify drive. Rank, never expire: a claim does not
    become false on a timer, it becomes worth re-checking when the world has been touched."""
    import world_facts as wfm
    return wfm.staleness_report()
