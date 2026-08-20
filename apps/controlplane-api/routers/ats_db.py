"""The ATS database — vendors, their instances, what we measured, and the flows we drove.

Read surface over the three tables added 2026-08-20 (`docs/ANALYSIS_ats_corpus.md`). Before them,
ATS knowledge lived in a hardcoded list, one denormalised string column, and 356 orphan transition
rows that could not be joined to the 22 applications they belonged to.

Everything here reports its DENOMINATOR. A mismatch rate over three acts and one over a hundred are
different claims, and a surface that renders them identically is how a sighting becomes a rule —
the over-generalisation the operator flagged on 2026-08-19.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

import ats_registry as reg
import models
from db import get_db

router = APIRouter()


@router.get("/api/ats")
async def list_ats(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Every vendor we know, joined to what we have actually driven through it.

    The vendor catalogue stays in `ats_registry` (small, curated, reviewed); this joins it to the
    measured rows so "we know about Greenhouse" and "we have driven Greenhouse" stop looking alike.
    """
    inst_counts = dict(db.query(models.AtsInstance.ats_id, func.count())
                       .group_by(models.AtsInstance.ats_id).all())
    flow_rows = db.query(models.AtsFlow.ats_id, func.count(), func.sum(models.AtsFlow.confirmed),
                         func.sum(models.AtsFlow.mismatched)).group_by(models.AtsFlow.ats_id).all()
    flows = {r[0]: {"flows": r[1], "confirmed": int(r[2] or 0), "mismatched": int(r[3] or 0)}
             for r in flow_rows}

    out = []
    for entry in reg.ATS_PLATFORMS:
        ats_id = entry["ats_id"]
        f = flows.get(ats_id, {"flows": 0, "confirmed": 0, "mismatched": 0})
        observed = f["confirmed"] + f["mismatched"]
        out.append({
            "ats_id": ats_id,
            "display_name": entry.get("display_name"),
            "auth": entry.get("auth"),
            "hosts": entry.get("hosts", []),
            "instances": inst_counts.get(ats_id, 0),
            "flows": f["flows"],
            "observed_acts": observed,
            "mismatch_rate": round(f["mismatched"] / observed, 3) if observed else None,
            # Said out loud, because a rate with no denominator is the thing we are trying to stop.
            "rate_confidence": ("measured" if observed >= 20 else
                                "provisional" if observed else "never driven"),
        })
    out.sort(key=lambda r: (-r["flows"], -r["instances"], r["ats_id"]))
    return {"vendors": out,
            "note": "instances/flows are what we DROVE; the catalogue is what we know OF."}


@router.get("/api/ats/instances")
async def list_instances(ats_id: Optional[str] = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    """One row per employer tenant — the axis a hostname cannot carry."""
    q = db.query(models.AtsInstance)
    if ats_id:
        q = q.filter(models.AtsInstance.ats_id == ats_id)
    rows = q.order_by(models.AtsInstance.ats_id, models.AtsInstance.tenant).all()
    flows = dict(db.query(models.AtsFlow.instance_key, func.count())
                 .group_by(models.AtsFlow.instance_key).all())
    return {"instances": [{
        "instance_key": r.instance_key, "ats_id": r.ats_id, "tenant": r.tenant,
        "tenant_source": r.tenant_source, "employer": r.employer, "host": r.host,
        "flows": flows.get(r.instance_key, 0),
        "first_seen_at": r.first_seen_at, "last_seen_at": r.last_seen_at,
    } for r in rows]}


@router.get("/api/ats/{ats_id}")
async def ats_detail(ats_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """One vendor: its catalogue entry, its tenants, its measured characteristics, its flows.

    `spine_observed` is the recipe as WALKED — the distinct states each flow passed through, in
    order. It is the evidence a planner should reason from, and it is deliberately a list of
    observations rather than a merged canonical path: merging three flows into one "the Workday
    spine" is exactly where a quirk becomes a law.
    """
    entry = reg.get_ats(ats_id) or {}
    instances = db.query(models.AtsInstance).filter_by(ats_id=ats_id).all()
    chars = db.query(models.AtsCharacteristic).filter_by(ats_id=ats_id).order_by(
        models.AtsCharacteristic.kind, models.AtsCharacteristic.key).all()
    flows = db.query(models.AtsFlow).filter_by(ats_id=ats_id).order_by(
        models.AtsFlow.started_at).all()
    return {
        "ats_id": ats_id,
        "catalogue": {k: entry.get(k) for k in ("display_name", "auth", "hosts", "recipe", "notes")},
        "instances": [{"instance_key": i.instance_key, "tenant": i.tenant, "host": i.host,
                       "employer": i.employer} for i in instances],
        "characteristics": [{"kind": c.kind, "key": c.key, "value": c.value,
                             "confidence": c.confidence, "observations": c.observations,
                             "evidence": c.evidence} for c in chars],
        "flows": len(flows),
        "spine_observed": [{"instance_key": f.instance_key, "session_id": f.session_id,
                            "states": f.states, "transitions": f.transitions,
                            "confirmed": f.confirmed, "mismatched": f.mismatched,
                            "terminal": f.terminal} for f in flows],
        "caveat": ("spine_observed is per-flow on purpose — merging flows into one canonical path "
                   "is where a single tenant's quirk becomes a rule about the vendor"),
    }


@router.post("/api/ats/backfill")
async def run_backfill(dry_run: bool = False, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Re-read the transition corpus into the tables. Idempotent by key; flows are replaced."""
    import ats_backfill
    return ats_backfill.backfill(db, dry_run=dry_run)
