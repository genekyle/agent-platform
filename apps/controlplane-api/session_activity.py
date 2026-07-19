"""Session Activity — merge every log the system writes into ONE chronological, normalized feed:
reasoning (the Open Brain), actions, events, escalations, errors, and API touches. The operator's
window into what OUR system is doing and WHY — not just Claude's. Career Search scoped first.

All sources stamp `ts` in the same ISO-8601 UTC format, so the merge is concat → sort(ts, desc); no
parsing needed (verified across decision_journal / intent journal / event_log / handoffs on disk).
Rows without a `domain` field get one inferred from ats / task / url (the command_center pattern).
"""

from __future__ import annotations

from typing import Any, Optional

# ATS ids + task/url hints that mean "Career Search" (rows on the journals carry no domain field).
_CAREER_ATS = {"workday", "greenhouse", "icims", "taleo", "lever", "successfactors", "smartrecruiters",
               "ashby", "workable", "brassring", "phenom", "indeed_quick_apply"}
_CAREER_TASK_HINTS = ("indeed", "ats_login", "workday", "greenhouse", "apply", "career")
_CAREER_HOST_HINTS = ("myworkdayjobs", "indeed.com", "greenhouse.io", "icims.com", "bilh.org")


def _infer_domain(*, ats=None, task=None, url=None, route=None, domain=None) -> Optional[str]:
    if domain:
        return domain
    if (ats or "").lower() in _CAREER_ATS:
        return "career_search"
    if any(h in (task or "").lower() for h in _CAREER_TASK_HINTS):
        return "career_search"
    if any(h in f"{url or ''} {route or ''}".lower() for h in _CAREER_HOST_HINTS):
        return "career_search"
    return None


# --- per-source normalizers → the common shape {ts, kind, source, domain, session, title, detail, meta}
def _from_decisions(rows) -> list[dict]:
    out = []
    for r in rows:
        esc = bool(r.get("escalate"))
        state = r.get("state")
        out.append({
            "ts": r.get("ts"), "kind": "escalation" if esc else "reasoning", "source": "reasoning",
            "domain": _infer_domain(ats=r.get("ats"), task=r.get("task"), url=r.get("url"), route=r.get("route")),
            "session": r.get("session_id"),
            "title": ("↑ escalate" if esc else r.get("intent") or "decide") + (f" · {state}" if state else ""),
            "detail": r.get("rationale") or "",
            "meta": {"rung": r.get("rung"), "state": state, "ats": r.get("ats"), "task": r.get("task"),
                     "outcome": r.get("outcome"), "verified": r.get("verified"),
                     "evidence": list(r.get("evidence") or [])},
        })
    return out


def _from_intents(rows) -> list[dict]:
    out = []
    for r in rows:
        ok = (r.get("outcome") == "ok")
        field = r.get("field")
        out.append({
            "ts": r.get("ts"), "kind": "action" if ok else "error", "source": "action",
            "domain": _infer_domain(ats=r.get("ats"), url=r.get("url"), route=r.get("route")),
            "session": r.get("session_id"),
            "title": (r.get("intent") or "act") + (f" · {field}" if field else ""),
            "detail": (r.get("detail") or "") + (f"  → {r.get('outcome')}" if r.get("outcome") else ""),
            "meta": {"outcome": r.get("outcome"), "ats": r.get("ats"), "driver": r.get("driver"),
                     "widget": r.get("widget_type"), "executed": r.get("executed")},
        })
    return out


def _from_events(rows) -> list[dict]:
    out = []
    for r in rows:
        k = (r.get("kind") or "").lower()
        out.append({
            "ts": r.get("ts"), "kind": "error" if k == "error" else "event", "source": r.get("source") or "event",
            "domain": r.get("domain"), "session": None,
            "title": r.get("summary") or k or "event", "detail": r.get("detail") or "",
            "meta": {"kind": r.get("kind")},
        })
    return out


def _from_handoffs(rows) -> list[dict]:
    out = []
    for r in rows:
        out.append({
            "ts": r.get("ts"), "kind": "escalation", "source": "handoff",
            "domain": _infer_domain(url=r.get("url"), task=r.get("task_goal")),
            "session": r.get("training_session_id"),
            "title": ("handoff · " + str(r.get("escalation_reason") or r.get("status") or "")).strip(" ··"),
            "detail": r.get("why") or r.get("detail") or "",
            "meta": {"status": r.get("status"), "suggestion": r.get("suggestion"), "url": r.get("url")},
        })
    return out


def _from_api(rows) -> list[dict]:
    return [{"ts": r.get("ts"), "kind": "api", "source": "api", "domain": None, "session": None,
             "title": f"{r.get('method')} {r.get('path')}", "detail": f"{r.get('status')} · {r.get('ms')}ms",
             "meta": {"status": r.get("status"), "ms": r.get("ms")}} for r in rows]


def build_feed(*, domain: Optional[str] = None, session: Optional[str] = None,
               kinds: Optional[set] = None, include_api: bool = False, limit: int = 200) -> list[dict]:
    """The merged, normalized, newest-first feed. Best-effort per source (a missing/unreadable log
    never sinks the whole feed)."""
    entries: list[dict] = []
    for loader in (
        lambda: _from_decisions(__import__("interaction.decision_journal", fromlist=["read_rows"]).read_rows()),
        lambda: _from_intents(__import__("interaction.journal", fromlist=["read_rows"]).read_rows()),
        lambda: _from_events(__import__("event_log").list_events(limit=1000)),
        lambda: _from_handoffs(__import__("runtime.handoff", fromlist=["list_handoffs"]).list_handoffs(limit=100)),
    ):
        try:
            entries += loader()
        except Exception:  # noqa: BLE001 — one bad source must not blank the feed
            pass
    if include_api:
        try:
            import api_access
            entries += _from_api(api_access.recent(300))
        except Exception:  # noqa: BLE001
            pass

    if domain:
        def _keep(e: dict) -> bool:
            d = e.get("domain")
            if d == domain:
                return True
            # undomained reasoning/action/escalation/api are Career-Search by construction today
            return d is None and e.get("source") in ("reasoning", "action", "handoff", "api")
        entries = [e for e in entries if _keep(e)]
    if session:
        entries = [e for e in entries if str(e.get("session")) == str(session)]
    if kinds:
        entries = [e for e in entries if e.get("kind") in kinds]

    entries = [e for e in entries if e.get("ts")]
    entries.sort(key=lambda e: e.get("ts") or "", reverse=True)
    return entries[:limit]
