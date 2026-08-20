"""Fill the ATS tables from the transition corpus — and be honest about what the corpus cannot say.

The corpus is 356 append-only rows keyed by `session_id`, carrying before/after URL + belief +
verdict. It does NOT carry a job id, so a backfilled flow cannot be attributed to the application it
belonged to. That is a finding, not a workaround: **job identity is missing at the point where the
states are recorded**, which is precisely why 356 traces and 22 applications never joined. Backfill
writes `job_key = None` and says so rather than guessing from timing.

Flow segmentation, therefore, is `(session_id, instance_key)` split on a long idle gap. Good enough
to count flows per instance, which is the denominator `apply_requirements` needs; not good enough to
say which job. New flows should be written live with the job_key attached — see the module note.
"""
from __future__ import annotations

import glob
import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

import ats_registry as reg
import ats_tenancy as ten

#: A gap longer than this inside one (session, instance) starts a new flow. Chosen to be longer
#: than any single application takes to drive and shorter than a session's idle stretches.
FLOW_GAP = timedelta(minutes=45)

DEFAULT_CORPUS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "apps", "mcp", "output", "transitions")


def _ts(row: dict[str, Any]) -> Optional[datetime]:
    raw = row.get("ts") or ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def read_corpus(directory: str = DEFAULT_CORPUS) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(directory, "session_*.jsonl"))):
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue          # a truncated tail is normal on an append-only file
    rows.sort(key=lambda r: (r.get("ts") or ""))
    return rows


@dataclass
class InstanceAgg:
    instance_key: str
    ats_id: str
    tenant: str
    tenant_source: str
    host: str = ""
    sample_url: str = ""
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None


@dataclass
class FlowAgg:
    instance_key: str
    ats_id: str
    session_id: Optional[int]
    states: list[str] = field(default_factory=list)
    transitions: int = 0
    confirmed: int = 0
    mismatched: int = 0
    corrections: int = 0
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


def _url_of(row: dict[str, Any], side: str) -> str:
    return ((row.get(side) or {}) or {}).get("url") or ""


def _state_of(row: dict[str, Any], side: str) -> str:
    return (((row.get(side) or {}).get("belief") or {}) or {}).get("state") or ""


def aggregate(rows: Iterable[dict[str, Any]]) -> tuple[dict[str, InstanceAgg], list[FlowAgg]]:
    """Corpus rows -> (instances, flows). Pure: no database, so it is testable and re-runnable."""
    instances: dict[str, InstanceAgg] = {}
    open_flows: dict[tuple[Optional[int], str], FlowAgg] = {}
    flows: list[FlowAgg] = []

    for row in rows:
        url = _url_of(row, "before") or _url_of(row, "after")
        if not url:
            continue
        ats_id = reg.classify_ats(url) or "unknown"
        tenant, source = ten.tenant_of(url, ats_id)
        key = ten.instance_key(ats_id, tenant)
        when = _ts(row)

        inst = instances.get(key)
        if inst is None:
            from urllib.parse import urlparse
            inst = instances[key] = InstanceAgg(key, ats_id, tenant, source,
                                                host=(urlparse(url).hostname or "").lower(),
                                                sample_url=url[:1000])
        if when:
            inst.first_seen = min(inst.first_seen or when, when)
            inst.last_seen = max(inst.last_seen or when, when)

        sid = row.get("session_id")
        fkey = (sid, key)
        flow = open_flows.get(fkey)
        if flow is not None and when and flow.ended_at and (when - flow.ended_at) > FLOW_GAP:
            flows.append(flow)
            flow = None
        if flow is None:
            flow = open_flows[fkey] = FlowAgg(key, ats_id, sid, started_at=when)

        flow.transitions += 1
        verdict = row.get("verdict")
        if verdict == "confirmed":
            flow.confirmed += 1
        elif verdict == "mismatch":
            flow.mismatched += 1
        if row.get("teacher_correction"):
            flow.corrections += 1
        for side in ("before", "after"):
            st = _state_of(row, side)
            if st and st not in flow.states:
                flow.states.append(st)
        if when:
            flow.ended_at = when

    flows.extend(open_flows.values())
    return instances, flows


def derive_characteristics(instances: dict[str, InstanceAgg], flows: list[FlowAgg]
                           ) -> list[dict[str, Any]]:
    """Vendor-level facts the corpus can support, each with its evidence and its denominator.

    Only two kinds are derived here, because only two are honestly derivable from traces alone:
    how the vendor encodes tenancy, and how often our prediction missed. Requirements come from
    `apply_requirements` (it reads pages, not traces) and auth comes from meeting a wall.
    """
    per_vendor: dict[str, dict[str, int]] = defaultdict(lambda: {"confirmed": 0, "mismatched": 0,
                                                                 "flows": 0, "instances": 0})
    for f in flows:
        v = per_vendor[f.ats_id]
        v["confirmed"] += f.confirmed
        v["mismatched"] += f.mismatched
        v["flows"] += 1
    for inst in instances.values():
        per_vendor[inst.ats_id]["instances"] += 1

    out: list[dict[str, Any]] = []
    for ats_id, agg in sorted(per_vendor.items()):
        rule = ten.RULES.get(ats_id)
        if rule is not None:
            out.append({"ats_id": ats_id, "instance_key": None, "kind": "tenancy",
                        "key": "tenant_style", "value": rule.style,
                        "confidence": "measured" if rule.measured else "assumed",
                        "evidence": rule.why, "observations": agg["instances"]})
        observed = agg["confirmed"] + agg["mismatched"]
        if observed:
            rate = agg["mismatched"] / observed
            out.append({"ats_id": ats_id, "instance_key": None, "kind": "mismatch_rate",
                        "key": "predicted_vs_observed", "value": f"{rate:.2f}",
                        # A rate over a handful of acts is a sighting, not a property of the vendor.
                        "confidence": "measured" if observed >= 20 else "assumed",
                        "evidence": (f"{agg['mismatched']} mismatch of {observed} observed acts "
                                     f"across {agg['flows']} flow(s), {agg['instances']} instance(s)"),
                        "observations": observed})
    return out


def backfill(db, directory: str = DEFAULT_CORPUS, *, dry_run: bool = False) -> dict[str, Any]:
    """Write instances, flows and derived characteristics. Idempotent by key."""
    import models

    rows = read_corpus(directory)
    instances, flows = aggregate(rows)
    chars = derive_characteristics(instances, flows)
    if dry_run:
        return {"rows": len(rows), "instances": len(instances), "flows": len(flows),
                "characteristics": len(chars), "written": False}

    for inst in instances.values():
        obj = db.get(models.AtsInstance, inst.instance_key)
        if obj is None:
            obj = models.AtsInstance(instance_key=inst.instance_key)
            db.add(obj)
        obj.ats_id, obj.tenant, obj.tenant_source = inst.ats_id, inst.tenant, inst.tenant_source
        obj.host, obj.sample_url = inst.host, inst.sample_url
        obj.first_seen_at, obj.last_seen_at = inst.first_seen, inst.last_seen

    # Flows are replaced wholesale for the sessions being backfilled — re-running the same corpus
    # must not double the denominator, which is the number everything else reasons from.
    sids = {f.session_id for f in flows}
    if sids:
        db.query(models.AtsFlow).filter(models.AtsFlow.session_id.in_(list(sids))).delete(
            synchronize_session=False)
    for f in flows:
        db.add(models.AtsFlow(instance_key=f.instance_key, ats_id=f.ats_id, session_id=f.session_id,
                              job_key=None, terminal=None, states=f.states,
                              transitions=f.transitions, confirmed=f.confirmed,
                              mismatched=f.mismatched, corrections=f.corrections,
                              started_at=f.started_at, ended_at=f.ended_at))

    for c in chars:
        q = db.query(models.AtsCharacteristic).filter_by(
            ats_id=c["ats_id"], instance_key=c["instance_key"], kind=c["kind"], key=c["key"])
        obj = q.first()
        if obj is None:
            obj = models.AtsCharacteristic(ats_id=c["ats_id"], instance_key=c["instance_key"],
                                           kind=c["kind"], key=c["key"])
            db.add(obj)
        obj.value, obj.confidence = c["value"], c["confidence"]
        obj.evidence, obj.observations = c["evidence"], c["observations"]
    db.commit()
    return {"rows": len(rows), "instances": len(instances), "flows": len(flows),
            "characteristics": len(chars), "written": True,
            "caveat": "backfilled flows carry job_key=None — the corpus does not record job identity"}
