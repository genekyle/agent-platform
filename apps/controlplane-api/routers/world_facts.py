"""World-facts and orientation routes — what we already know, made reachable (§14 S16, S17).

Thin by design: the derivations live in `world_facts.py` and `orientation_context.py` (pure
composers over the recipes' own WORLD_FACTS lists, the ATS tables, and the transition corpus), so
the cockpit, a session-Claude curl, and a test all ask the same question and get the same answer.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from deps import get_db

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


@router.get("/api/orientation")
def orientation(url: str = Query(...), rung: str = Query(""),
                company: str = Query(""), job_id: str = Query(""),
                db: Session = Depends(get_db)) -> dict[str, Any]:
    """Everything already on file that bears on this step — the composed read the rungs use.

    Exposed so the teacher can ask it directly about any URL before spending a drive, which is
    also what makes it reviewable: the cockpit renders the same payload the crank cited.
    """
    import orientation_context as oc
    ctx = oc.orientation_context(db, url=url, rung=rung or None,
                                 company=company or None, job_id=job_id or None)
    return {"ok": True, **ctx, "citation": oc.cite(ctx)}
