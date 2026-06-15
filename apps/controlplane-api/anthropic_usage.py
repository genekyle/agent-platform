"""Self-logged Anthropic API usage + cost tracking.

The Anthropic Console (console.anthropic.com -> Usage & Cost) is the
authoritative org-wide dashboard, but it can't see *our* context — which call
was a SoM pick vs a verification glance, which capture/task it belonged to. So
we log every call ourselves: one JSONL row per call with token counts and the
cost we compute from current pricing. This is the data behind the flywheel's
cost-per-task metric, and it's free (no Admin API key needed).

Storage: a JSONL appended at <artifacts>/usage/anthropic_usage.jsonl. Cheap,
no DB migration, trivially greppable. summarize() aggregates it for the UI.

Pricing is per-MTok and MUST be kept current — see the claude-api skill. Haiku
4.5 = $1 in / $5 out; cached input writes are 1.25x base, cache reads 0.1x.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from settings import settings

# $ per 1M tokens. Update when Anthropic changes pricing or we add models.
PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {
        "input": 1.0,
        "output": 5.0,
        "cache_write": 1.25,   # 5-minute cache write = 1.25x base input
        "cache_read": 0.10,    # cache read = 0.1x base input
    },
}

_write_lock = threading.Lock()


def _usage_path() -> Path:
    # observer_artifacts_dir defaults to a RELATIVE path ("../mcp-mock/output"),
    # which resolves differently depending on CWD (the server runs from
    # apps/controlplane-api, but a script may run from repo root). Anchor relative
    # paths to this package dir so every writer/reader hits the same file.
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parent / base).resolve()
    path = base / "usage" / "anthropic_usage.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_pricing(model: str) -> Optional[dict[str, float]]:
    """The API returns a DATED snapshot id (e.g. 'claude-haiku-4-5-20251001'),
    while PRICING is keyed by the alias ('claude-haiku-4-5'). Match exact first,
    then by alias-prefix so dated ids price correctly."""
    if model in PRICING:
        return PRICING[model]
    for alias, rates in PRICING.items():
        if model.startswith(alias):
            return rates
    return None


def compute_cost(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """Dollar cost for one call. Unknown models cost 0 (logged, not priced) so a
    new model never crashes the tracker — just update PRICING."""
    rates = _resolve_pricing(model)
    if not rates:
        return 0.0
    return round(
        (input_tokens / 1_000_000) * rates["input"]
        + (output_tokens / 1_000_000) * rates["output"]
        + (cache_write_tokens / 1_000_000) * rates.get("cache_write", rates["input"])
        + (cache_read_tokens / 1_000_000) * rates.get("cache_read", rates["input"]),
        6,
    )


def record_usage(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
    purpose: str = "unknown",
    meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Append one usage row. `purpose` tags what the call was for (e.g.
    'som_pick', 'verify', 'smoke_test') so cost can be sliced per stage.
    Best-effort: never raise into the caller's hot path."""
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cache_write_tokens": int(cache_write_tokens),
        "cache_read_tokens": int(cache_read_tokens),
        "cost_usd": compute_cost(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_write_tokens=cache_write_tokens,
            cache_read_tokens=cache_read_tokens,
        ),
        "purpose": purpose,
        "meta": meta or {},
    }
    try:
        with _write_lock:
            with _usage_path().open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
    except Exception:
        pass  # telemetry must never break the call it's measuring
    return row


def record_from_response(response: Any, *, purpose: str = "unknown", meta: Optional[dict] = None) -> dict[str, Any]:
    """Convenience: pull token counts straight off an Anthropic Messages
    response's `.usage` and record them."""
    u = getattr(response, "usage", None)
    return record_usage(
        model=getattr(response, "model", "") or "",
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        purpose=purpose,
        meta=meta,
    )


def _load_rows() -> list[dict[str, Any]]:
    path = _usage_path()
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    except Exception:
        return []
    return rows


# --- Budget guard -----------------------------------------------------------
# Hard cap on AUTONOMOUS spend over a rolling 7-day window. The cheap cascade
# layers (deterministic -> cache -> classifier -> micro-model) reduce how often
# we reach Haiku; THIS is the guarantee that no-human spend stays bounded. When
# over budget, callers must escalate to a human instead of calling the API.
def _weekly_limit(limit: Optional[float]) -> float:
    if limit is not None:
        return float(limit)
    return float(getattr(settings, "anthropic_weekly_budget_usd", 5.0) or 0.0)


def weekly_spend(now: Optional[datetime] = None) -> float:
    """Sum of cost_usd over the trailing 7 days (rolling, not calendar week)."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)
    total = 0.0
    for row in _load_rows():
        ts = row.get("ts", "")
        try:
            when = datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when >= cutoff:
            total += row.get("cost_usd", 0.0)
    return round(total, 6)


def budget_status(limit: Optional[float] = None) -> dict[str, Any]:
    lim = _weekly_limit(limit)
    spent = weekly_spend()
    remaining = round(lim - spent, 6)
    return {
        "period": "rolling_7d",
        "limit_usd": lim,
        "spent_usd": spent,
        "remaining_usd": remaining,
        "fraction_used": round(spent / lim, 4) if lim > 0 else 1.0,
        "over_budget": spent >= lim if lim > 0 else True,
    }


class BudgetExceededError(RuntimeError):
    """Raised when an autonomous Claude call would exceed the weekly budget.
    Callers should catch this and escalate to a human rather than proceed."""

    def __init__(self, status: dict[str, Any]) -> None:
        self.status = status
        super().__init__(
            f"Weekly Claude budget exceeded: ${status['spent_usd']:.4f} / "
            f"${status['limit_usd']:.2f} ({status['period']}). Escalate to human."
        )


def enforce_budget(limit: Optional[float] = None) -> dict[str, Any]:
    """Call BEFORE any autonomous Claude request. Raises BudgetExceededError if
    the rolling-7d spend is already at/over the cap. Returns the status otherwise."""
    status = budget_status(limit)
    if status["over_budget"]:
        raise BudgetExceededError(status)
    return status


def _empty_summary() -> dict[str, Any]:
    return {
        "configured": bool((settings_anthropic_key())),
        "totals": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        "by_day": [],
        "by_purpose": [],
        "by_model": [],
        "recent": [],
    }


def settings_anthropic_key() -> str:
    """Whether a key is configured (for the UI's 'key configured?' badge) without
    exposing the value. Prefer the .env-loaded Settings, fall back to env."""
    import os
    return (getattr(settings, "anthropic_api_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")).strip()


def summarize(*, recent_limit: int = 50) -> dict[str, Any]:
    """Aggregate the JSONL into totals + per-day / per-purpose / per-model rollups
    and the most recent calls. Reads the whole file (small for an n=1 cadence;
    revisit if it ever grows large)."""
    summary = _empty_summary()
    rows = _load_rows()
    if not rows:
        return summary

    by_day: dict[str, dict[str, float]] = {}
    by_purpose: dict[str, dict[str, float]] = {}
    by_model: dict[str, dict[str, float]] = {}
    tot = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    def bump(bucket: dict[str, dict[str, float]], key: str, row: dict[str, Any]) -> None:
        b = bucket.setdefault(key, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
        b["calls"] += 1
        b["input_tokens"] += row.get("input_tokens", 0)
        b["output_tokens"] += row.get("output_tokens", 0)
        b["cost_usd"] += row.get("cost_usd", 0.0)

    for row in rows:
        tot["calls"] += 1
        tot["input_tokens"] += row.get("input_tokens", 0)
        tot["output_tokens"] += row.get("output_tokens", 0)
        tot["cost_usd"] += row.get("cost_usd", 0.0)
        bump(by_day, str(row.get("ts", ""))[:10], row)
        bump(by_purpose, str(row.get("purpose", "unknown")), row)
        bump(by_model, str(row.get("model", "")), row)

    tot["cost_usd"] = round(tot["cost_usd"], 6)

    def rollup(bucket: dict[str, dict[str, float]], key_name: str) -> list[dict[str, Any]]:
        out = []
        for k, v in bucket.items():
            out.append({key_name: k, **v, "cost_usd": round(v["cost_usd"], 6)})
        return out

    summary["totals"] = tot
    summary["by_day"] = sorted(rollup(by_day, "day"), key=lambda r: r["day"], reverse=True)
    summary["by_purpose"] = sorted(rollup(by_purpose, "purpose"), key=lambda r: r["cost_usd"], reverse=True)
    summary["by_model"] = sorted(rollup(by_model, "model"), key=lambda r: r["cost_usd"], reverse=True)
    summary["recent"] = list(reversed(rows))[:recent_limit]
    return summary
