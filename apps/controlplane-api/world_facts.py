"""World-facts — dated, evidenced claims about SITES, kept apart from code-facts (§14).

SESSION 16. A code-fact ("the scroll endpoint is /scroll_job_list") is true until we change it,
and the suite goes red if we do. A world-fact ("the results list is virtualised") is true until
LINKEDIN changes it, with no line of this repo moving — `linkedin_recipe` held both in one dict,
a `blocked_on` outlived its bug by twelve days and planned a session, and the test suite was
asserting the stale prose. This module gives world-facts a shape that can rot VISIBLY:

    fact(id=…, claim=…, evidence_class=MEASURED|HYPOTHESIS|UNVERIFIED|RETRACTED,
         observed_at="YYYY-MM-DD", drive={session, date}, evidence=…, surface={platform, hosts},
         recheck=…, history=[…])

Three rules, each learned expensively:
  * **A fact cannot be minted undated or unevidenced** — the constructor refuses, because an
    undated claim is prose wearing a data shape (the thing being replaced).
  * **Both sides of a correction stay** (§10/§13/§14): a retraction or downgrade appends to
    `history`, never deletes — a stage that silently disappears is indistinguishable from one
    nobody got round to.
  * **No TTL.** A claim does not become false on a timer; it becomes *worth re-checking* when the
    world has been TOUCHED since it was written. `staleness_report()` ranks by exactly that —
    facts whose surface has been driven after `observed_at`, most-outdriven first — which is how
    the virtualisation claim would have surfaced weeks before it cost a session.

The store is the recipes' own `WORLD_FACTS` lists (site truth stays with its recipe, in the data
layer); `collect()` gathers the migrated ones. LEARNINGS keeps the reasoning; this is its
queryable twin, never its replacement.
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlsplit

EVIDENCE_CLASSES = ("MEASURED", "HYPOTHESIS", "UNVERIFIED", "RETRACTED")

_ID = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Recipe modules that have been migrated to the shape. ONE pilot on purpose (the brief); a
#: module listed here must expose `WORLD_FACTS: list[dict]`.
_MIGRATED_MODULES = ("linkedin_recipe",)


def fact(*, id: str, claim: str, evidence_class: str, observed_at: str,
         drive: Optional[dict[str, Any]] = None, evidence: str = "",
         surface: dict[str, Any], recheck: str = "",
         history: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """Constructor-validator: the only way a world-fact enters the store, so the shape IS the
    enforcement point. Raises ValueError with the failing field named."""
    if not _ID.match(id or ""):
        raise ValueError(f"world-fact id must be dotted lowercase (got {id!r})")
    if not (claim or "").strip():
        raise ValueError(f"{id}: a fact needs a claim")
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(f"{id}: evidence_class must be one of {EVIDENCE_CLASSES} "
                         f"(got {evidence_class!r})")
    if not _DATE.match(observed_at or ""):
        raise ValueError(f"{id}: observed_at must be YYYY-MM-DD — an undated claim is prose "
                         f"wearing a data shape (got {observed_at!r})")
    if evidence_class == "MEASURED" and not (evidence or "").strip():
        raise ValueError(f"{id}: MEASURED without evidence is just a confident sentence — quote "
                         f"the reading, name the capture, or downgrade to HYPOTHESIS")
    hosts = [h for h in (surface or {}).get("hosts", []) if h]
    if not (surface or {}).get("platform") or not hosts:
        raise ValueError(f"{id}: surface needs a platform and at least one host — staleness is "
                         f"a join against where we have DRIVEN, and an unanchored fact can never "
                         f"be flagged for re-check")
    if evidence_class != "RETRACTED" and not (recheck or "").strip():
        raise ValueError(f"{id}: how would a drive re-verify this? A fact without a recheck can "
                         f"only rot silently (RETRACTED facts are exempt — they are history)")
    return {
        "id": id, "claim": claim.strip(), "evidence_class": evidence_class,
        "observed_at": observed_at, "drive": dict(drive or {}),
        "evidence": (evidence or "").strip(),
        "surface": {"platform": surface["platform"], "hosts": list(hosts),
                    "state": (surface or {}).get("state")},
        "recheck": (recheck or "").strip(),
        "history": [dict(h) for h in (history or [])],
    }


def collect() -> dict[str, dict[str, Any]]:
    """Every registered world-fact, keyed by id. Imports the migrated recipes lazily (they import
    this module for the constructor). Duplicate ids are refused — one authority per claim (§15)."""
    import importlib

    out: dict[str, dict[str, Any]] = {}
    for mod_name in _MIGRATED_MODULES:
        mod = importlib.import_module(mod_name)
        for f in getattr(mod, "WORLD_FACTS", []):
            if f["id"] in out:
                raise ValueError(f"duplicate world-fact id {f['id']!r} — one authority per claim")
            out[f["id"]] = f
    return out


def _last_drive_by_host() -> tuple[dict[str, str], int, str]:
    """{host: newest transition ts} over every corpus, plus row count and the resolved root —
    the honesty pair (an empty answer must never read as a clean one)."""
    import step_runner as sr

    host_last: dict[str, str] = {}
    rows = 0
    for c in sr.list_corpora():
        for row in sr.read_transitions(c["key"], limit=1000):
            rows += 1
            ts = str(row.get("ts") or "")
            for side in ("before", "after"):
                url = (row.get(side) or {}).get("url") or ""
                host = (urlsplit(url).netloc or "").lower()
                if host and ts > host_last.get(host, ""):
                    host_last[host] = ts
    return host_last, rows, str(sr._transitions_dir())


def _days_between(a: str, b: str) -> Optional[int]:
    from datetime import date

    try:
        return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days
    except ValueError:
        return None


def staleness_report() -> dict[str, Any]:
    """Which claims about a surface predate the last drive on that surface?

    Ranked by how much later the last drive was — the top entry is the claim most exposed to rot,
    and re-verifying it live is the drive this report exists to choose. Rank, never expire: a
    fact nothing has driven past is FRESH-BY-SILENCE, listed separately, because "the world has
    not been touched" and "the claim was outdriven" must not render alike.
    """
    host_last, rows, root = _last_drive_by_host()
    facts = collect()
    outdriven: list[dict[str, Any]] = []
    fresh: list[dict[str, Any]] = []
    retracted = 0
    for f in facts.values():
        if f["evidence_class"] == "RETRACTED":
            retracted += 1
            continue
        last = max((host_last.get(h, "") for h in f["surface"]["hosts"]), default="")
        entry = {
            "id": f["id"], "claim": f["claim"], "evidence_class": f["evidence_class"],
            "observed_at": f["observed_at"], "surface": f["surface"],
            "last_drive_on_surface": last[:19] or None,
            "recheck": f["recheck"],
        }
        days = _days_between(f["observed_at"], last) if last else None
        if days is not None and days > 0:
            entry["outdriven_by_days"] = days
            outdriven.append(entry)
        else:
            fresh.append(entry)
    outdriven.sort(key=lambda e: -e["outdriven_by_days"])
    fresh.sort(key=lambda e: e["observed_at"])
    return {
        "ok": True, "root": root, "corpus_rows": rows,
        "facts": len(facts), "retracted_kept": retracted,
        "outdriven": outdriven, "fresh_by_silence": fresh,
        "migrated_modules": list(_MIGRATED_MODULES),
        "note": ("outdriven = the surface has been driven AFTER the claim was observed; those are "
                 "the claims to re-verify, top first. Two honest limits: unmigrated recipes still "
                 "carry prose claims this report cannot see (the pilot is deliberate, SESSION 16), "
                 "and the join reads the TRANSITION corpus — a drive that banks no transition rows "
                 "(a pure sweep) is invisible to it, so 'fresh by silence' can be fresher than it "
                 "looks, never staler."),
    }
