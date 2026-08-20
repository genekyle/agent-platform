"""Get rows INTO the ATS tables — historically from the corpus, and live as flows end.

Two entry points, one module because they write the same rows:
  * `backfill(db)` — the historical pass over the transition corpus and the observer traces;
  * `record_flow(db, ...)` — the LIVE write, called when an application reaches a terminal flag.

The live path is what closes the gap the backfill has to apologise for.

The corpus is 356 append-only rows keyed by `session_id`, carrying before/after URL + belief +
verdict. It does NOT carry a job id, so a BACKFILLED flow cannot be attributed to the application it
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
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import ats_registry as reg
import ats_tenancy as ten
from settings import settings

#: A gap longer than this inside one (session, instance) starts a new flow. Chosen to be longer
#: than any single application takes to drive and shorter than a session's idle stretches.
FLOW_GAP = timedelta(minutes=45)

#: Resolved through the SAME seam as every other artifact reader (`settings.observer_artifacts_dir`,
#: env-overridable as OBSERVER_ARTIFACTS_DIR). The first version computed the root from `__file__`,
#: which made this the one reader that ignored the data-root envs: served from a worktree it
#: globbed a directory that does not exist and returned a successful-looking no-op
#: (`{"rows": 0, ..., "written": true}`) — found 2026-08-20.
_OUTPUT = settings.observer_artifacts_dir
DEFAULT_CORPUS = os.path.join(_OUTPUT, "transitions")
DEFAULT_TRACES = os.path.join(_OUTPUT, "observer-traces")

#: Sidecars — `.ax`, `.meta`, `.vision` — carry no key of their own and belong to the primary trace
#: whose filename they share. Skipped here; the convention is noted in ANALYSIS_data_silos.md as an
#: unenforced relationship worth making explicit.
_SIDECAR_MARKERS = (".ax.", ".meta.", ".vision.")


def _deep_url(node: Any, depth: int = 0) -> str:
    """The first http(s) URL anywhere in a trace.

    THIS FUNCTION IS THE WHOLE POINT OF THE TRACE BACKFILL. 675MB of observer traces read as
    "no join key" for months because the URL is not at the top level — captures put it at
    `.acquisition.page_identity.url`, and the path differs by trace kind. A top-level `.get("url")`
    returns None on 1141 of 1141 files; searching nested finds one on 1093 of them (96%).
    A negative result needs its search scope in the sentence, or it reads as universal.
    """
    if depth > 5:
        return ""
    if isinstance(node, dict):
        for key in ("url", "page_url", "tab_url"):
            val = node.get(key)
            if isinstance(val, str) and val.startswith("http"):
                return val
        for val in node.values():
            got = _deep_url(val, depth + 1)
            if got:
                return got
    elif isinstance(node, list):
        for val in node[:3]:
            got = _deep_url(val, depth + 1)
            if got:
                return got
    return ""


def read_traces(directory: str = DEFAULT_TRACES) -> list[dict[str, Any]]:
    """Primary observer traces as `{url, kind, ts, session_id}` — the silo, opened.

    Returns the thin projection rather than the trace bodies: this pass exists to establish WHICH
    ATS instances we have observed, and holding 675MB in memory to learn that would be silly.
    """
    out: list[dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        base = os.path.basename(path)
        if any(marker in base for marker in _SIDECAR_MARKERS):
            continue
        try:
            with open(path) as fh:
                body = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        url = _deep_url(body)
        if not url:
            continue
        out.append({"url": url,
                    "kind": base.split("__")[-1].replace(".json", ""),
                    "ts": base.split("__")[0].replace("-00-00", "+00:00"),
                    "session_id": body.get("session_id")})
    return out


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


def fold_traces(instances: dict[str, InstanceAgg], traces: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Add instances the observer traces saw but the transition corpus never did.

    Traces are OBSERVATIONS, not flows: a trace says "we looked at this page", not "we drove an
    application here". So they widen the instance table and the first/last-seen window, and they
    deliberately do NOT create `AtsFlow` rows — inflating the flow denominator with page views is
    how a sighting would become a driven application.
    """
    from urllib.parse import urlparse
    seen: dict[str, int] = {}
    for t in traces:
        url = t.get("url") or ""
        ats_id = reg.classify_ats(url) or "unknown"
        tenant, source = ten.tenant_of(url, ats_id)
        key = ten.instance_key(ats_id, tenant)
        seen[key] = seen.get(key, 0) + 1
        inst = instances.get(key)
        if inst is None:
            inst = instances[key] = InstanceAgg(key, ats_id, tenant, source,
                                                host=(urlparse(url).hostname or "").lower(),
                                                sample_url=url[:1000])
        when = None
        try:
            when = datetime.fromisoformat(str(t.get("ts")))
        except Exception:
            pass
        if when:
            inst.first_seen = min(inst.first_seen or when, when)
            inst.last_seen = max(inst.last_seen or when, when)
    return seen


def backfill(db, directory: str = DEFAULT_CORPUS, *, dry_run: bool = False,
             traces_dir: Optional[str] = DEFAULT_TRACES) -> dict[str, Any]:
    """Write instances, flows and derived characteristics. Idempotent by key."""
    import models

    rows = read_corpus(directory)
    instances, flows = aggregate(rows)
    trace_counts: dict[str, int] = {}
    # A missing traces dir must be a FACT in the result, not a silent omission — served from a
    # worktree without the data-root env this used to no-op with every number at zero and
    # `written: true`, which reads as "the corpus is empty" instead of "I looked nowhere".
    traces_dir_missing = bool(traces_dir) and not os.path.isdir(traces_dir)
    if traces_dir and not traces_dir_missing:
        trace_counts = fold_traces(instances, read_traces(traces_dir))
    chars = derive_characteristics(instances, flows)
    for key, n in trace_counts.items():
        inst = instances[key]
        chars.append({"ats_id": inst.ats_id, "instance_key": key, "kind": "state_seen",
                      "key": "observer_traces", "value": str(n), "confidence": "measured",
                      "evidence": f"{n} observer trace(s) resolved to this instance by URL",
                      "observations": n})
    if dry_run:
        return {"rows": len(rows), "traces": sum(trace_counts.values()),
                "instances": len(instances), "flows": len(flows),
                "characteristics": len(chars), "written": False,
                **({"traces_dir_missing": True} if traces_dir_missing else {})}

    for inst in instances.values():
        obj = db.get(models.AtsInstance, inst.instance_key)
        if obj is None:
            obj = models.AtsInstance(instance_key=inst.instance_key)
            db.add(obj)
        obj.ats_id, obj.tenant, obj.tenant_source = inst.ats_id, inst.tenant, inst.tenant_source
        obj.host, obj.sample_url = inst.host, inst.sample_url
        obj.first_seen_at, obj.last_seen_at = inst.first_seen, inst.last_seen

    # Flows this pass authored before are replaced — re-running the same corpus must not double
    # the denominator, which is the number everything else reasons from. But ONLY the rows the
    # backfill wrote (job_key and terminal both None, by its own construction): a LIVE row from
    # `record_flow` carries the job and the outcome, and the first version of this sweep deleted
    # those too — the next backfill erased the only row in the table that answered "did anyone
    # ever get through this ATS", one day after the live write shipped (found 2026-08-20).
    sids = {f.session_id for f in flows}
    live_rows: dict[tuple[Optional[int], str], Any] = {}
    if sids:
        for row in db.query(models.AtsFlow).filter(models.AtsFlow.session_id.in_(list(sids))):
            if row.terminal is None and row.job_key is None:
                db.delete(row)
            else:
                # `record_flow` is idempotent per (session, instance, job); a collision here is a
                # re-flag of the same application, and the later write already won on the row.
                live_rows[(row.session_id, row.instance_key)] = row
        db.flush()

    # A corpus flow that matches a surviving live row ENRICHES it rather than sitting beside it
    # and doubling the count: the corpus knows the states walked and the verdict tallies, the live
    # row knows the job and the terminal — they are two halves of one application. Gap-splitting
    # can yield several corpus flows per (session, instance); the live row was written at the
    # terminal, so the LATEST-ended one is its other half and the rest insert as backfill rows.
    _floor = datetime.min.replace(tzinfo=timezone.utc)
    latest: dict[tuple[Optional[int], str], Any] = {}
    for f in flows:
        k = (f.session_id, f.instance_key)
        if k in live_rows:
            cur = latest.get(k)
            if cur is None or ((f.ended_at or f.started_at or _floor)
                               > (cur.ended_at or cur.started_at or _floor)):
                latest[k] = f
    for f in flows:
        k = (f.session_id, f.instance_key)
        live = live_rows.get(k) if latest.get(k) is f else None
        if live is not None:
            live.states = f.states or live.states
            live.transitions, live.confirmed = f.transitions, f.confirmed
            live.mismatched, live.corrections = f.mismatched, f.corrections
            live.started_at = live.started_at or f.started_at
            live.ended_at = live.ended_at or f.ended_at
        else:
            db.add(models.AtsFlow(instance_key=f.instance_key, ats_id=f.ats_id,
                                  session_id=f.session_id,
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
    return {"rows": len(rows), "traces": sum(trace_counts.values()),
            "instances": len(instances), "flows": len(flows),
            "characteristics": len(chars), "written": True,
            **({"traces_dir_missing": True} if traces_dir_missing else {}),
            "caveat": "backfilled flows carry job_key=None — the corpus does not record job identity"}


# --- the live write --------------------------------------------------------------------------
def record_flow(db, *, url: str, job_key: Optional[str], terminal: str,
                session_id: Optional[int] = None, platform: str = "",
                states: Optional[list[str]] = None,
                started_at: Optional[datetime] = None,
                ended_at: Optional[datetime] = None) -> Optional[str]:
    """Record one finished application against its ATS instance. Returns the instance key.

    Called from `apply_flag`, which is the ONE place that knows all four things at once: the job,
    the terminal, the session, and the tab it ended on. Every earlier attempt to reconstruct this
    join after the fact failed on the same missing column — the transition corpus records states
    without job identity, so 63 backfilled flows carry `job_key = None` and no outcome at all.

    That absence had a cost the day it was measured: the brief reported `submitted_flows: 0` for a
    vendor driven nine times, which reads as "never finished" and meant "never recorded". A flow
    written here carries its outcome, so the next drive's pre-flight can say whether anyone has ever
    got through this ATS — the single most decision-relevant fact before entering one.

    Best-effort and idempotent per (session, instance, job): a bookkeeping row must never be able to
    fail the terminal it is describing.
    """
    import models

    if not url or not terminal:
        return None
    try:
        ats_id = platform or reg.classify_ats(url) or "unknown"
        tenant, source = ten.tenant_of(url, ats_id)
        key = ten.instance_key(ats_id, tenant)

        inst = db.get(models.AtsInstance, key)
        if inst is None:
            from urllib.parse import urlparse
            inst = models.AtsInstance(instance_key=key, ats_id=ats_id, tenant=tenant,
                                      tenant_source=source,
                                      host=(urlparse(url).hostname or "").lower(),
                                      sample_url=url[:1000])
            db.add(inst)
        if hasattr(inst, "last_seen_at"):
            inst.last_seen_at = models.utcnow()

        row = (db.query(models.AtsFlow)
               .filter_by(instance_key=key, session_id=session_id, job_key=job_key)
               .first())
        if row is None:
            row = models.AtsFlow(instance_key=key, ats_id=ats_id, session_id=session_id,
                                 job_key=job_key)
            db.add(row)
        row.terminal = terminal
        if states:
            row.states = list(dict.fromkeys(states))
        # A live row with no timestamps sorted into an undefined slot and contributed nothing to
        # any windowed count. `started_at` is the first mini-step when the caller has one; the end
        # is now by definition — this function runs at the terminal flag.
        row.started_at = row.started_at or started_at
        row.ended_at = ended_at or models.utcnow()
        db.flush()
        return key
    except Exception:  # noqa: BLE001 — never let bookkeeping break a recorded terminal
        # LOGGED, not swallowed. The first version of this catch hid an AttributeError for a full
        # test run and the function simply returned None — a silent no-op is the worst shape a
        # best-effort write can take, because nothing downstream can tell "nothing to do" from
        # "broken". Make failures loud even when they are non-fatal.
        logging.getLogger(__name__).exception("record_flow failed for %s (job=%s)", url, job_key)
        return None
