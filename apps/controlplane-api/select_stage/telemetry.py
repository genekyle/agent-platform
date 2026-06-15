"""SelectionTelemetry — one row per selection. THIS IS THE TRAINING CORPUS.

Every selection (cache hit, Haiku pick, or escalation) logs a row with the exact
feature set the LATER cheap local layers will train on: fingerprint→page_state
(tiny classifier), candidates→pick (micro-model), trajectories (diffusion). Keep
the columns stable. Storage: <artifacts>/cache/selection_telemetry.jsonl.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from settings import settings

from .schema import SELECTOR_SCHEMA_VERSION, SelectionResult

_lock = threading.Lock()


def _path() -> Path:
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parents[1] / base).resolve()
    p = base / "cache" / "selection_telemetry.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def log_selection(
    *,
    result: SelectionResult,
    route: str,
    task_goal: str,
    candidate_count: int,
    cache_status: str,           # "hit" | "miss"
    tokens: dict[str, int],
    budget: dict[str, Any],
    verifier: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Append one telemetry row. Best-effort — never raises into the hot path."""
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "schema_version": SELECTOR_SCHEMA_VERSION,
        "fingerprint": result.fingerprint,
        "route": route,
        "task_goal": task_goal,
        "candidate_count": candidate_count,
        "selected": {
            "action_id": result.action_id.value,
            "target_backend_node_id": result.target_backend_node_id,
        },
        "confidence": result.confidence,
        "needs_human": result.needs_human,
        "reason_code": result.reason_code.value,
        "layer": result.layer,
        "cache": cache_status,
        "tokens": tokens,
        "cost_usd": result.cost_usd,
        "budget": budget,
        "verifier": verifier or {},
        "escalation_reason": result.reason_code.value if result.needs_human else None,
        "meta": meta or {},
    }
    try:
        with _lock:
            with _path().open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
    except Exception:
        pass


def _trajectory_path() -> Path:
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parents[1] / base).resolve()
    p = base / "cache" / "cursor_trajectories.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def record_trajectory(payload: dict[str, Any]) -> int:
    """Append one recorded human cursor trajectory to the corpus and return the
    new total. This is the ground-truth data the diffusion input-model trains on
    (start, target_box, viewport, path points + timestamps, endpoint, label)."""
    row = {"ts": datetime.now(timezone.utc).isoformat(), **(payload or {})}
    path = _trajectory_path()
    with _lock:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return sum(1 for _ in path.open(encoding="utf-8"))


def trajectory_count() -> int:
    p = _trajectory_path()
    if not p.exists():
        return 0
    try:
        return sum(1 for line in p.open(encoding="utf-8") if line.strip())
    except Exception:
        return 0


def _load_rows() -> list[dict[str, Any]]:
    p = _path()
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    except Exception:
        return []
    return rows


def summarize(*, recent_limit: int = 30) -> dict[str, Any]:
    """Aggregate the telemetry corpus for the Lab dashboard — the flywheel
    metrics: cache-hit rate, escalation rate, cost-per-task, layer/reason mix,
    daily trend. This is the same corpus the later local layers train on."""
    rows = _load_rows()
    total = len(rows)
    summary: dict[str, Any] = {
        "corpus_size": total,
        "totals": {"selections": 0, "cache_hits": 0, "escalations": 0, "cost_usd": 0.0},
        "rates": {"cache_hit": 0.0, "escalation": 0.0, "avg_cost_usd": 0.0},
        "by_layer": [], "by_reason": [], "by_day": [], "recent": [],
    }
    if not rows:
        return summary

    hits = sum(1 for r in rows if r.get("cache") == "hit")
    esc = sum(1 for r in rows if r.get("needs_human"))
    cost = sum(r.get("cost_usd", 0.0) for r in rows)
    by_layer: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    by_day: dict[str, dict[str, float]] = {}
    for r in rows:
        by_layer[r.get("layer", "?")] = by_layer.get(r.get("layer", "?"), 0) + 1
        by_reason[r.get("reason_code", "?")] = by_reason.get(r.get("reason_code", "?"), 0) + 1
        day = str(r.get("ts", ""))[:10]
        d = by_day.setdefault(day, {"selections": 0, "cost_usd": 0.0, "cache_hits": 0})
        d["selections"] += 1
        d["cost_usd"] += r.get("cost_usd", 0.0)
        d["cache_hits"] += 1 if r.get("cache") == "hit" else 0

    summary["totals"] = {"selections": total, "cache_hits": hits, "escalations": esc,
                         "cost_usd": round(cost, 6)}
    summary["rates"] = {
        "cache_hit": round(hits / total, 4),
        "escalation": round(esc / total, 4),
        "avg_cost_usd": round(cost / total, 6),
    }
    summary["by_layer"] = sorted(({"layer": k, "count": v} for k, v in by_layer.items()),
                                 key=lambda x: x["count"], reverse=True)
    summary["by_reason"] = sorted(({"reason_code": k, "count": v} for k, v in by_reason.items()),
                                  key=lambda x: x["count"], reverse=True)
    summary["by_day"] = sorted(({"day": k, **v, "cost_usd": round(v["cost_usd"], 6)}
                                for k, v in by_day.items()), key=lambda x: x["day"])
    summary["recent"] = list(reversed(rows))[:recent_limit]
    return summary
