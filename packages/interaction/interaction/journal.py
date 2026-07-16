"""The intent journal — one append-only row per intent. THIS IS THE TRAINING CORPUS.

Sibling of `select_stage/telemetry.py` (selections) and `runtime/loop.py`'s
`loop_steps.jsonl` (loop steps), and joinable to both on `fingerprint`. Those two record
what the LOOP decided. This one records what the ACTOR actually did — which, until now,
nothing recorded.

--------------------------------------------------------------------------------------
Why this file exists (the measurement that reordered the plan)
--------------------------------------------------------------------------------------
`docs/PLAN_execution_api.md` §1a argues that API calls are learnable and `/eval` is not,
citing the event log: `type:137 clear:92 click:80 … eval:0`. The counts are real. The
inference was not, and it was load-bearing:

  - `event_log.jsonl` is a 1000-line RING BUFFER (read → truncate → rewrite), raced by two
    processes with no cross-process lock. It drops the oldest rows on every long session.
  - An event carries `{ts, source, kind, summary, detail, domain}`. No fingerprint, no
    capture id, no session, no per-step log. Outcome is a substring of `detail`.
  - Its only consumer is `EventsConsole.jsx`, polling every 5s. NO TRAINER READS IT.
  - `record_only` (dry run) and a real drive emit byte-identical events.

So the honest scoreboard was never `eval:0` vs `type:137` — it was both are zero. The
2026-07-15 session drove a Workday application to submission and contributed 0 rows to
`loop_steps.jsonl` and 0 to `selection_telemetry.jsonl`. An action the system can't see is
one it can never learn, and an API call logged to a wall display is only marginally more
visible than a script. That is what this file fixes, and it is why it landed BEFORE the
protocol endpoints rather than after: shipping them first would have met the plan's own
Definition of Done ("finish KKR using only these — zero /eval") while still producing zero
training rows, and paid for that session a third time.

The event log stays as-is — it is a good operator wall display and a bad corpus. This is
the corpus. Different jobs, different files.
"""

from __future__ import annotations

import dataclasses
import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from interaction.contract import INTENT_SCHEMA_VERSION, Intent, Outcome, redact

_lock = threading.Lock()
_JOURNAL_NAME = "intent_journal.jsonl"


def _default_artifacts_dir() -> Path:
    """The corpus dir both apps already share.

    `controlplane-api/settings.py` has `observer_artifacts_dir = "../mcp/output"` and the
    mcp resolves the same place relative to its own `__file__` — that is how they already
    co-write `event_log.jsonl`, `loop_steps.jsonl` and `selection_telemetry.jsonl`. This
    package is installed editable from `packages/interaction/`, so the repo root is two
    parents up. `INTERACTION_ARTIFACTS_DIR` overrides (tests, and any future deployment
    that moves the corpus off the repo).
    """
    env = os.environ.get("INTERACTION_ARTIFACTS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "apps" / "mcp" / "output"


def _path() -> Path:
    p = _default_artifacts_dir() / "cache" / _JOURNAL_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class IntentRecord:
    """One intent, start to finish. Column order is grouped by the question it answers.

    Keep the columns STABLE — this is a corpus. Add, don't rename.
    """

    # --- identity
    ts: str
    schema_version: str
    intent: str                      # Intent value
    outcome: str                     # Outcome value — `ok` means VERIFIED, not un-thrown

    # --- WHAT was asked (the semantic layer; what L4 learns to emit)
    ats: Optional[str] = None
    field: Optional[str] = None
    value: Optional[str] = None      # ALWAYS passed through redact() — see contract.redact
    widget_type: Optional[str] = None

    # --- WHERE it resolved to (the addressing layer; what the recipe answered)
    addressed_by: Optional[str] = None   # "selector" | "role_name" | "backend_node_id" | "bbox"
    target: Optional[str] = None         # the resolved selector / "role:name" / "node 4821"

    # --- HOW it went (the protocol layer)
    # NB: `dataclasses.field` is spelled out because this record HAS a column called
    # `field` (the semantic field name an intent targets) — which shadows the bare import
    # inside the class body. The column name is the vocabulary's, so the import moved.
    steps: list[dict[str, Any]] = dataclasses.field(default_factory=list)  # precheck → open → select → commit
    actions: list[str] = dataclasses.field(default_factory=list)           # ActionIds it expanded into
    detail: str = ""
    driver: Optional[str] = None
    executed: bool = True            # False for record_only — the dry-run/real ambiguity
                                     # that made event-log rows indistinguishable

    # --- WHERE it happened (the join keys)
    route: str = ""                  # route_template(url) — always present, costs nothing
    fingerprint: Optional[str] = None    # opportunistic: only when an AX scan already ran
    url: Optional[str] = None
    session_id: Optional[str] = None

    # --- what it cost
    duration_ms: int = 0
    cost_usd: float = 0.0


def log_intent(
    *,
    intent: Intent | str,
    outcome: Outcome | str,
    ats: Optional[str] = None,
    field: Optional[str] = None,
    value: Optional[str] = None,
    sensitive: Optional[bool] = None,
    widget_type: Optional[str] = None,
    addressed_by: Optional[str] = None,
    target: Optional[str] = None,
    steps: Optional[list[dict[str, Any]]] = None,
    actions: Optional[list[str]] = None,
    detail: str = "",
    driver: Optional[str] = None,
    executed: bool = True,
    route: str = "",
    fingerprint: Optional[str] = None,
    url: Optional[str] = None,
    session_id: Optional[str] = None,
    duration_ms: int = 0,
    cost_usd: float = 0.0,
) -> IntentRecord:
    """Append one intent row. Best-effort — never raises into the hot path.

    Returns the record (already redacted) so a caller can echo it in its response without
    re-deriving it. `value` is redacted HERE, unconditionally, so no call site can leak a
    password by forgetting to.
    """
    rec = IntentRecord(
        ts=datetime.now(timezone.utc).isoformat(),
        schema_version=INTENT_SCHEMA_VERSION,
        intent=intent.value if isinstance(intent, Intent) else str(intent),
        outcome=outcome.value if isinstance(outcome, Outcome) else str(outcome),
        ats=ats,
        field=field,
        value=redact(value, field=field, sensitive=sensitive),
        widget_type=widget_type,
        addressed_by=addressed_by,
        target=target,
        steps=steps or [],
        actions=actions or [],
        detail=(detail or "")[:800],
        driver=driver,
        executed=executed,
        route=route,
        fingerprint=fingerprint,
        url=url,
        session_id=session_id,
        duration_ms=duration_ms,
        cost_usd=cost_usd,
    )
    try:
        with _lock:
            with _path().open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — a journal write must never break a live drive
        pass
    return rec


def read_rows(*, limit: Optional[int] = None) -> list[dict[str, Any]]:
    """Read the corpus (oldest first). `limit` returns the most recent N."""
    p = _path()
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    except Exception:  # noqa: BLE001
        return rows
    return rows[-limit:] if limit else rows


def summarize() -> dict[str, Any]:
    """Aggregate the corpus — the Phase-1 scoreboard.

    `by_outcome` is the number to watch: it is the anti-silent-success contract made
    visible. A corpus that is 100% `ok` is not a healthy corpus, it is an unverified one.
    """
    rows = read_rows()
    summary: dict[str, Any] = {
        "corpus_size": len(rows),
        "by_intent": [], "by_outcome": [], "by_widget_type": [], "by_ats": [],
        "verified_rate": 0.0, "probe_share": 0.0, "fingerprinted_rate": 0.0,
    }
    if not rows:
        return summary

    def tally(key: str) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for r in rows:
            k = r.get(key) or "?"
            counts[k] = counts.get(k, 0) + 1
        return sorted(({key: k, "count": v} for k, v in counts.items()),
                      key=lambda x: x["count"], reverse=True)

    total = len(rows)
    ok = sum(1 for r in rows if r.get("outcome") == Outcome.OK.value)
    probes = sum(1 for r in rows if r.get("intent") == Intent.PROBE.value)
    fp = sum(1 for r in rows if r.get("fingerprint"))
    summary["by_intent"] = tally("intent")
    summary["by_outcome"] = tally("outcome")
    summary["by_widget_type"] = tally("widget_type")
    summary["by_ats"] = tally("ats")
    summary["verified_rate"] = round(ok / total, 4)
    # The metric the plan is actually chasing: probe share should FALL as protocols land.
    summary["probe_share"] = round(probes / total, 4)
    summary["fingerprinted_rate"] = round(fp / total, 4)
    return summary
