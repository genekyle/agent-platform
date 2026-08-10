"""Search routes — the per-query view of what a session's work actually yielded.

The read surface for `models.Search` (the architecture rule of 2026-08-10: the session is the
browser, the search is the query — sightings and applications hang off the SEARCH). The cockpit
asks two questions here: "what searches has this session run?" and "what did that search find?".
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import searches as searches_mod
from deps import get_db

router = APIRouter()


@router.get("/api/searches")
def list_searches(session_id: Optional[int] = None, limit: int = 50,
                  db: Session = Depends(get_db)) -> dict[str, Any]:
    """Searches newest-first, each with its yield (jobs found, applications made). Scope with
    `session_id` for one session's history — many searches per session is the normal shape."""
    rows = searches_mod.summarize(db, session_id=session_id, limit=max(1, min(limit, 200)))
    return {"searches": rows, "count": len(rows)}


@router.get("/api/searches/{search_id}/jobs")
def search_jobs(search_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Every sighting this one search surfaced, with card facts and triage status."""
    from models import Search

    if db.get(Search, search_id) is None:
        raise HTTPException(status_code=404, detail=f"no search {search_id}")
    jobs = searches_mod.jobs_for(db, search_id)
    return {"search_id": search_id, "jobs": jobs, "count": len(jobs)}


@router.post("/api/searches/{search_id}/close")
def close_search(search_id: int, status: str = "exhausted",
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    """Mark a search finished (exhausted | abandoned). Never touches the session — keeping the
    browser and its signed-in state alive across queries is the point of the split."""
    row = searches_mod.close_search(db, search_id, status=status)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no search {search_id}")
    db.commit()
    return {"ok": True, "id": row.id, "status": row.status}
