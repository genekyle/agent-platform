"""What do we already know about the thing we just landed on? — the data, put to work.

Built 2026-08-20. Operator: *"maximize it in terms of usage in our system so we can take advantage
of our data rather than silo it and keep it from doing nothing … we've built a lot but haven't taken
a step back to look at truly what we have."* Fair. The ATS tables were a day old and read by nobody.

This is the consumer. Given a URL, it answers the questions a drive should ask BEFORE it spends
twenty minutes: have we been here, did it work, what did it demand, and will it stop on a human?
On 2026-08-19 a PeopleAdmin drive reached an account wall after a full approach that a one-line
`blockers` answer would have predicted from the posting page.

THREE RULES IT KEEPS, all learned the expensive way:

* **Never answer without a denominator.** Every number arrives with how many observations produced
  it, and anything thin is labelled `provisional`. A mismatch rate over three acts and one over a
  hundred must not render alike.
* **Instance before vendor.** What this employer's tenant did is better evidence than what the
  vendor usually does, and the brief says which level each fact came from. Collapsing them is how
  one tenant's quirk becomes a rule.
* **Say what is NOT known.** A vendor we have never driven returns `known: False` and says so,
  rather than an empty brief that reads like a clean bill of health.
"""
from __future__ import annotations

from typing import Any, Optional

import ats_registry as reg
import ats_tenancy as ten

#: Below this, a per-vendor rate is a sighting rather than a property. Same threshold the backfill
#: uses to mark characteristics `measured` — one number, one meaning.
MEASURED_AT = 20


def brief(url: str, db) -> dict[str, Any]:
    """Everything we know about the ATS behind this URL, with its evidence and its denominators."""
    import models
    from sqlalchemy import func

    ats_id = reg.classify_ats(url) or "unknown"
    tenant, tenant_source = ten.tenant_of(url, ats_id)
    key = ten.instance_key(ats_id, tenant)
    entry = reg.get_ats(ats_id) or {}

    instance = db.get(models.AtsInstance, key)
    inst_flows = db.query(models.AtsFlow).filter_by(instance_key=key).all()
    vendor_flows = db.query(models.AtsFlow).filter_by(ats_id=ats_id).all()
    chars = db.query(models.AtsCharacteristic).filter_by(ats_id=ats_id).all()
    sibling_instances = db.query(func.count(models.AtsInstance.instance_key)).filter_by(
        ats_id=ats_id).scalar() or 0

    def rate(flows) -> tuple[Optional[float], int]:
        ok = sum(f.confirmed for f in flows)
        bad = sum(f.mismatched for f in flows)
        total = ok + bad
        return ((bad / total) if total else None), total

    inst_rate, inst_acts = rate(inst_flows)
    vend_rate, vend_acts = rate(vendor_flows)

    # OUTCOMES: "none recorded" and "none succeeded" are different sentences and must not render
    # alike. Until 2026-08-20 every flow carried `terminal = NULL`, so the brief reported
    # `submitted_flows: 0` for a vendor driven nine times — which reads as "tried nine times, never
    # finished" and meant "we do not record outcomes". Silence presenting as absence, on the one
    # surface a drive reads before it commits.
    terminals = [f.terminal for f in vendor_flows if f.terminal]
    submitted = sum(1 for t in terminals if t == "submitted")
    outcomes_known = len(terminals)

    # `auth` is the registry's promise and the ladder acts on it, so it is repeated verbatim rather
    # than re-derived here. `account` means this flow WILL stop for the operator.
    auth = entry.get("auth") or "unknown"
    blockers = ["account"] if auth == "account" else []

    known = bool(instance or vendor_flows)
    return {
        "url": url,
        "ats_id": ats_id,
        "display_name": entry.get("display_name"),
        "instance_key": key,
        "tenant": tenant,
        "tenant_source": tenant_source,
        "known": known,
        "headline": _headline(ats_id, entry, instance, inst_flows, vendor_flows, auth, known),
        "instance": {
            "seen_before": instance is not None,
            "flows": len(inst_flows),
            "observed_acts": inst_acts,
            "mismatch_rate": round(inst_rate, 3) if inst_rate is not None else None,
            "first_seen_at": getattr(instance, "first_seen_at", None),
            "last_seen_at": getattr(instance, "last_seen_at", None),
            "states_walked": sorted({s for f in inst_flows for s in (f.states or [])}),
        },
        "vendor": {
            "instances_known": sibling_instances,
            "flows": len(vendor_flows),
            "observed_acts": vend_acts,
            "mismatch_rate": round(vend_rate, 3) if vend_rate is not None else None,
            "confidence": ("measured" if vend_acts >= MEASURED_AT
                           else "provisional" if vend_acts else "never driven"),
            # None, never 0, when nothing is recorded — the caller must be able to tell them apart.
            "submitted_flows": submitted if outcomes_known else None,
            "outcomes_recorded": outcomes_known,
            "finish_rate": (round(submitted / outcomes_known, 3) if outcomes_known else None),
            "auth": auth,
        },
        "blockers": blockers,
        "characteristics": [{"kind": c.kind, "key": c.key, "value": c.value,
                             "confidence": c.confidence, "observations": c.observations,
                             "scope": "instance" if c.instance_key else "vendor"}
                            for c in chars if c.instance_key in (None, key)],
        "caveat": ("nothing recorded for this platform — treat every expectation as unverified"
                   if not known else
                   "instance evidence outranks vendor evidence; thin numbers are labelled provisional"),
    }


def _headline(ats_id, entry, instance, inst_flows, vendor_flows, auth, known) -> str:
    """One sentence an operator can act on, which is the only form this data gets read in."""
    name = entry.get("display_name") or ats_id
    # The account wall is the registry's PROMISE, known independently of whether we have driven
    # here — so it is appended to every headline including the never-driven one. Dropping it there
    # was the first thing a test caught: a platform we have not driven is exactly the one whose
    # wall we most want announced before someone spends the approach on it.
    wall = " Expect an account wall — it will stop for you." if auth == "account" else ""
    if not known:
        return f"{name}: never driven here. Nothing to expect — capture everything.{wall}"
    if instance is not None and inst_flows:
        known = [f for f in inst_flows if f.terminal]
        subs = sum(1 for f in known if f.terminal == "submitted")
        if not known:
            tail = ", outcomes not recorded"
        elif subs:
            tail = f", {subs} of {len(known)} submitted"
        else:
            tail = f", none of {len(known)} finished"
        been = f"{len(inst_flows)} flow(s) through THIS employer's tenant{tail}"
    elif instance is not None:
        been = "this tenant has been observed but never driven end to end"
    else:
        been = "new tenant of a platform we have driven"
    return f"{name}: {been}.{wall}"
