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
DOMAINS: list[dict[str, Any]] = [
    {"id": "facebook_marketplace", "label": "Facebook Marketplace", "kind": "selling",
     "profile": "facebook", "host": "facebook", "capture_domain": "facebook_marketplace"},
    {"id": "indeed_jobs", "label": "Indeed", "kind": "jobs", "profile": None, "host": "indeed",
     "capture_domain": "indeed"},
]


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


def _jobs_metrics(db: Session) -> dict[str, Any]:
    try:
        jobs = db.scalars(select(ObservedJob).where(ObservedJob.platform == "indeed")).all()
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
        metrics = _selling_metrics() if d["kind"] == "selling" else _jobs_metrics(db)
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
    }
