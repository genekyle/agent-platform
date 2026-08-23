"""Command Center — the cross-domain rollup behind the cockpit landing page.

One cheap read that answers, for the whole platform at once: which domains are healthy,
which need me, and what just happened. It COMPOSES data that already exists (channel CDP
probe, inventory overview, observed-jobs counts, durable handoffs) rather than inventing a
new store — the pieces are the source of truth, this is just the glance.

Every source is wrapped best-effort: a dead capture server or an empty inventory file must
degrade one tile, never blank the whole page.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

import domain_settings
from models import ObservedJob, TrainingSession

# The domains that have a real workspace today. Kept deliberately small and backend-owned so
# the rollup never depends on the UI catalog; "coming soon" domains are a pure-UI concern.
#
# `platform` is the value ObservedJob.platform carries for a jobs domain — the registry id
# (`indeed_jobs`) and the scrape's platform tag (`indeed`) are deliberately different strings, and
# every "which jobs are this domain's?" query needs the latter. Kept here so there is ONE mapping.
DOMAINS: list[dict[str, Any]] = [
    {"id": "facebook_marketplace", "label": "Facebook Marketplace", "kind": "selling",
     "profile": "facebook", "host": "facebook", "capture_domain": "facebook_marketplace"},
    {"id": "indeed_jobs", "label": "Indeed", "kind": "jobs", "profile": None, "host": "indeed",
     "capture_domain": "indeed", "platform": "indeed", "search_tab": "indeed.com/jobs",
     # Indeed has a Distance pill; LinkedIn's results page has no distance control at all
     # (measured 2026-07-30). A sweep must not refuse to gather over a filter that cannot exist.
     "distance_filter": True,
     "spa": False},
    # LinkedIn — the second career-search AGGREGATOR, a sibling of Indeed under the Career Search
    # group (not an ATS). Same `kind`, so it inherits the whole jobs operating pattern; only the
    # host/platform differ. Its login is a real per-domain account (`linkedin_default`), unlike
    # Indeed's session-scoped auth, but the tile doesn't care — that's the accounts registry's job.
    {"id": "linkedin_jobs", "label": "LinkedIn", "kind": "jobs", "profile": None, "host": "linkedin",
     "capture_domain": "linkedin", "platform": "linkedin", "search_tab": "linkedin.com/jobs",
     # LinkedIn is a SINGLE-PAGE APP: the query, the filters and the pagination all mutate history
     # with pushState and re-render the list in place. Nothing navigates, so no caller may use "the
     # context tore down" or "the URL changed" as proof an action landed — they must compare a
     # SIGNATURE of the results before and after. See `/await_results` in the capture server.
     "distance_filter": False,
     "spa": True},
    # Gmail — the first member of the `google` PROVIDER group, and the first domain here that
    # exists to be CALLED rather than driven for its own sake: other domains detour into it for a
    # login code and return. Its `profile` is the provider's shared one, so `connected` answers
    # "is the Google session live?" for every member that follows it, not just for Gmail.
    #
    # `kind: "errands"` is load-bearing, not a label. The tile branch below was a binary — selling,
    # else jobs — so a domain with no case of its own still got an answer, and it was Indeed's.
    # Gmail would have reported "Jobs found: 0" while querying ObservedJob for a platform that will
    # never exist. That is the Career-Search lesson at a new altitude: adding a sibling is how you
    # find out which code was quietly speaking for everyone.
    {"id": "gmail", "label": "Gmail", "kind": "errands", "profile": "google", "host": "gmail",
     "capture_domain": "gmail"},
]

_BY_ID = {d["id"]: d for d in DOMAINS}


def platform_for(domain_id: str) -> str:
    """ObservedJob.platform for a jobs domain id (`indeed_jobs` -> `indeed`). Falls back to the id
    itself so an unregistered domain reads as its own platform rather than silently as Indeed's."""
    return _BY_ID.get(domain_id, {}).get("platform") or domain_id


def search_tab_url(domain_id: str) -> str:
    """The URL SUBSTRING that identifies this domain's search-results tab — what every capture call
    passes as `tab_url` to pick the right tab out of a session's Chrome.

    Narrow on purpose (`linkedin.com/jobs`, not `linkedin.com`): once an apply is in flight a
    session holds several tabs on the same host, and a bare host matches the wrong one. That exact
    trap already cost a drive on Indeed — see the capture note in LEARNINGS about a bare
    `indeed.com` matching several pages.
    """
    return _BY_ID.get(domain_id, {}).get("search_tab") or ""


def is_spa(domain_id: str) -> bool:
    """Does this domain re-render in place instead of navigating? On a SPA an action's effect has
    to be confirmed from the CONTENT (a results signature), because there is no navigation to
    observe — and a sleep-then-read silently extracts the previous page."""
    return bool(_BY_ID.get(domain_id, {}).get("spa"))


def _attribute_domain(row: dict[str, Any]) -> Optional[str]:
    """Best-effort: which domain does a handoff belong to? Infer from the page URL it stopped
    on (cheap and reliable), falling back to None so it still counts platform-wide."""
    hay = f"{row.get('url') or ''} {row.get('tab_url') or ''} {row.get('task_goal') or ''}".lower()
    for d in DOMAINS:
        if d["host"] in hay:
            return d["id"]
    return None


def _channel_connected(db: Session, profile: Optional[str]) -> Optional[bool]:
    """CDP-reachability of a channel's persistent browser, or None when the domain has no
    channel browser (e.g. Indeed runs inside a training session, not a channel)."""
    if not profile:
        return None
    try:
        import channel_browser
        session = db.scalar(
            select(TrainingSession)
            .where(TrainingSession.persistent_profile == profile)
            .order_by(TrainingSession.id.desc()).limit(1)
        )
        if session is None:
            return False
        return channel_browser.cdp_reachable(session.chrome_debug_port)
    except Exception:
        return None


def _has_active_session(db: Session, host: str) -> bool:
    try:
        rows = db.scalars(select(TrainingSession).where(TrainingSession.status == "active")).all()
        return any(host in (s.domain_id or "").lower() for s in rows)
    except Exception:
        return False


def _selling_metrics() -> dict[str, Any]:
    try:
        import inventory
        ov = inventory.overview()
        return {
            "primary": {"label": "Active listings", "value": ov.get("active_listings", 0)},
            "chips": [
                {"label": "Items", "value": ov.get("total_items", 0)},
                {"label": "Queued", "value": ov.get("queued", 0)},
                {"label": "Needs attention", "value": ov.get("needs_attention", 0), "warn": True},
            ],
            "needs_attention": int(ov.get("needs_attention", 0) or 0),
        }
    except Exception:
        return {"primary": {"label": "Active listings", "value": 0}, "chips": [], "needs_attention": 0}


def _jobs_metrics(db: Session, platform: str) -> dict[str, Any]:
    try:
        jobs = db.scalars(select(ObservedJob).where(ObservedJob.platform == platform)).all()
        applied = sum(1 for j in jobs if j.application_status == "applied")
        return {
            "primary": {"label": "Jobs found", "value": len(jobs)},
            "chips": [
                {"label": "Applied", "value": applied},
                {"label": "Companies", "value": len({j.company for j in jobs if j.company})},
            ],
            "needs_attention": 0,
        }
    except Exception:
        return {"primary": {"label": "Jobs found", "value": 0}, "chips": [], "needs_attention": 0}


def _errand_metrics() -> dict[str, Any]:
    """What an errand domain is asked for, and how often it had to give up.

    A provider member is measured by the favours it does other domains, not by anything it owns —
    so the headline is requests served, and the number that actually matters is `escalated`: an
    errand that escalates is a drive somewhere else that stopped and is waiting on a human.
    """
    try:
        import errand_log
        stats = errand_log.recent_stats()
    except Exception:  # best-effort like every other source here — never blank the landing
        return {"primary": {"label": "Errands served", "value": 0}, "chips": [], "needs_attention": 0}
    return {
        "primary": {"label": "Errands served", "value": stats["served"]},
        "chips": [
            {"label": "Codes found", "value": stats["ok"]},
            {"label": "Escalated", "value": stats["escalated"], "warn": True},
        ],
        "needs_attention": stats["escalated"],
    }


def _recent_activity(handoffs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A merged, newest-first feed across domains — handoffs (what needed a human) plus the
    inventory action log — so the landing answers 'what just happened' at a glance."""
    feed: list[dict[str, Any]] = []
    for h in handoffs[:20]:
        feed.append({
            "ts": h.get("ts"),
            "domain_id": _attribute_domain(h),
            "kind": "handoff",
            "status": "error" if h.get("status") != "resolved" else "ok",
            "message": h.get("why") or h.get("detail") or "Agent needed a human.",
        })
    try:
        import inventory
        for e in inventory.list_log(limit=20):
            feed.append({
                "ts": e.get("timestamp"),
                "domain_id": "facebook_marketplace",
                "kind": e.get("action_type") or "activity",
                "status": e.get("status") or "info",
                "message": e.get("message") or "",
            })
    except Exception:
        pass
    feed.sort(key=lambda r: r.get("ts") or "", reverse=True)
    return feed[:18]


def _training_metrics(db: Session) -> dict[str, dict[str, int]]:
    """Per-CAPTURE-domain labeling backlog: how many captures still need a golden label (draft)
    vs. how many are done (reviewed/approved). Keyed by TrainingCapture.domain_id. This is the
    flywheel's headline signal — surfaced on the landing so 'what to label' isn't buried."""
    from sqlalchemy import func

    from models import TrainingCapture

    try:
        rows = db.execute(
            select(
                TrainingCapture.domain_id,
                func.count().filter(TrainingCapture.review_status == "draft"),
                func.count().filter(TrainingCapture.review_status.in_(("reviewed", "approved"))),
            ).group_by(TrainingCapture.domain_id)
        ).all()
    except Exception:  # best-effort like every other source here — never blank the landing
        return {}
    return {dom: {"to_label": int(d or 0), "labeled": int(r or 0)} for dom, d, r in rows}


def _latest_grounding_accuracy() -> Optional[float]:
    """Best-effort read of the newest grounding model's target_accuracy (the 'is the flywheel
    working?' number). None if nothing's been trained yet."""
    import json
    from pathlib import Path

    from settings import settings

    try:
        models_dir = Path(settings.observer_artifacts_dir) / "models"
        runs = sorted(models_dir.glob("*__grounding_linear_v1"), reverse=True)
        for run in runs:
            metrics = run / "metrics.json"
            if metrics.exists():
                return float(json.loads(metrics.read_text()).get("target_accuracy"))
    except Exception:
        pass
    return None


def build_summary(db: Session) -> dict[str, Any]:
    """The whole landing in one payload: per-domain health tiles, the open-attention count,
    and a cross-domain activity feed."""
    from runtime import handoff as handoff_mod

    try:
        open_handoffs = handoff_mod.list_handoffs(open_only=True, limit=100)
    except Exception:
        open_handoffs = []
    try:
        all_handoffs = handoff_mod.list_handoffs(open_only=False, limit=40)
    except Exception:
        all_handoffs = list(open_handoffs)

    attention_by_domain: dict[str, int] = {}
    for h in open_handoffs:
        did = _attribute_domain(h)
        if did:
            attention_by_domain[did] = attention_by_domain.get(did, 0) + 1

    training_by_domain = _training_metrics(db)

    tiles: list[dict[str, Any]] = []
    for d in DOMAINS:
        # Dispatch on the domain's OWN kind, with no else-branch that answers for a kind it has
        # never heard of. A new kind that lands here gets empty metrics and an honest tile rather
        # than another domain's numbers — see the note on `gmail` in DOMAINS above.
        if d["kind"] == "selling":
            metrics = _selling_metrics()
        elif d["kind"] == "jobs":
            metrics = _jobs_metrics(db, platform_for(d["id"]))
        elif d["kind"] == "errands":
            metrics = _errand_metrics()
        else:
            metrics = {"primary": None, "chips": [], "needs_attention": 0}
        connected = _channel_connected(db, d["profile"])
        active = connected if connected is not None else _has_active_session(db, d["host"])
        attention = attention_by_domain.get(d["id"], 0) + int(metrics.get("needs_attention", 0) or 0)

        if attention > 0:
            status = "attention"
        elif active:
            status = "ready"
        else:
            status = "idle"

        tiles.append({
            "id": d["id"],
            "label": d["label"],
            "kind": d["kind"],
            "status": status,
            "connected": connected,
            "attention_count": attention,
            "automation_mode": domain_settings.get_settings(d["id"])["automation_mode"],
            "primary": metrics.get("primary"),
            "chips": metrics.get("chips", []),
            # Flywheel signal, surfaced right on the domain tile (#1 training-UI overhaul).
            "training": training_by_domain.get(d["capture_domain"], {"to_label": 0, "labeled": 0}),
        })

    # The teacher's OWN queues, surfaced on the landing. Handoffs above are the runtime queue;
    # the transition label queue and parked drives are DIFFERENT queues and rendered nowhere on
    # /overview until the 2026-08-22 reach-parity audit named the gap. Best-effort like every
    # other source: None means "could not read", never a fabricated 0.
    try:
        from routers.transitions import build_label_queue
        transition_queue = len(build_label_queue())
    except Exception:
        transition_queue = None
    try:
        from controller import inbox as inbox_mod
        parks_open = inbox_mod.counts()["open"]
    except Exception:
        parks_open = None

    return {
        "domains": tiles,
        "attention_open_count": len(open_handoffs),
        "activity": _recent_activity(all_handoffs),
        # Cross-domain flywheel rollup for the landing's headline KPIs.
        "flywheel": {
            "to_label_total": sum(t["to_label"] for t in training_by_domain.values()),
            "labeled_total": sum(t["labeled"] for t in training_by_domain.values()),
            "grounding_accuracy": _latest_grounding_accuracy(),
        },
        # The teacher's worklists (distinct from runtime handoffs — see the note above).
        "teacher": {"transition_queue": transition_queue, "parks_open": parks_open},
    }


def has_distance_filter(domain_id: str) -> bool:
    """Does this engine expose a distance/radius control at all?

    The 50-mile floor exists so we never gather a 5-mile Indeed radius by accident. On an engine
    with no distance control the floor has nothing to bite on, and refusing to gather would be
    enforcing a rule about a widget that does not exist — which is how LinkedIn's sweep stopped
    before it read a single card (2026-07-30). Unknown engines default True: the conservative
    direction is to keep asking for the filter, not to quietly drop the floor.
    """
    d = _BY_ID.get(domain_id)
    return bool((d or {}).get("distance_filter", True))
