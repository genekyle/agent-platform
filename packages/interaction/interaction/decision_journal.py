"""The decision journal — one append-only row per decide() call. THE CONTROLLER'S CORPUS.

Sibling of `journal.py` (the intent/actor corpus), joinable to it on `route`/`fingerprint`.
`journal.py` records what the ACTOR did; this records what the REASONER decided and whether
it was right. Same rules, because they are the same discipline:

  - append-only, best-effort, NEVER raises into the hot path (a journal write must not break
    a live drive);
  - values are redacted HERE, unconditionally, so no call site can leak a secret by forgetting;
  - **no row without a join key** — the spine rule. Enforced against `route` (route_template,
    always present), not the opportunistic AX `fingerprint`; see `decision.py` for why.

A golden row (M4) and a shadow row (M5) are ordinary rows with a flag set — the corpus stays
one file, and `golden=True` / `shadow=True` are mechanically filterable.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from interaction.contract import redact

if TYPE_CHECKING:      # type-only: the journal must not depend on the supervisor at runtime
    from interaction.authority import AuthorityVerdict
    from interaction.delta import StateDelta
    from interaction.supervision import SupervisorVerdict
from interaction.decision import (
    DECISION_SCHEMA_VERSION,
    Bundle,
    Decision,
    DecisionRecord,
    bundle_digest,
    is_real_rationale,
    looks_like_selector,
    replay_snapshot,
)

_lock = threading.Lock()
_JOURNAL_NAME = "decision_journal.jsonl"


def _default_artifacts_dir() -> Path:
    """The corpus dir both apps share — same resolution as `journal._default_artifacts_dir`,
    so the decision journal sits beside the intent journal in `mcp/output/cache/`."""
    env = os.environ.get("INTERACTION_ARTIFACTS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "apps" / "mcp" / "output"


def _path() -> Path:
    p = _default_artifacts_dir() / "cache" / _JOURNAL_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _redact_params(params: Optional[dict]) -> dict:
    """A params dict as it may be journaled: the carried value redacted, and any
    selector-shaped key OR value dropped (invariant #10 — addressing must never reach the
    corpus, or a policy trained on it learns the wrong altitude).

    A Decision's params are shaped `{field: <name>, value: <the value>, ...}`, so the
    `value` entry's sensitivity is governed by the SIBLING `field` name, not by the key
    "value" — the same split `log_intent(field=, value=)` makes. Any other string entry is
    redacted defensively by its own key (a stray secret under an odd key still gets caught).
    """
    if not isinstance(params, dict):
        return {}
    field_name = params.get("field") if isinstance(params.get("field"), str) else None
    out: dict[str, Any] = {}
    for k, v in params.items():
        if looks_like_selector(k) or looks_like_selector(v):
            continue  # addressing — never journal it
        if isinstance(v, str):
            out[k] = redact(v, field=(field_name if k == "value" else k))
        else:
            out[k] = v
    return out


def _staleness_signal(stale: Optional[dict], name: str) -> Optional[float]:
    """One raw signal value off a serialized Staleness, or None. Defensive for the same reason
    `_belief_axis` is: the dict crosses a JSON boundary and a malformed one must not take the
    journal down — a decision that was made is worth recording without its staleness."""
    if not isinstance(stale, dict):
        return None
    for sig in stale.get("signals") or ():
        if isinstance(sig, dict) and sig.get("name") == name:
            v = sig.get("value")
            return float(v) if isinstance(v, (int, float)) else None
    return None


def _belief_axis(belief: Optional[dict], axis: str) -> Optional[float]:
    """One uncertainty axis off a serialized BeliefState, or None. Defensive because `belief` is
    a plain dict crossing a JSON boundary: an axis nobody assessed is absent, not zero, and
    reading a missing axis as 0.0 would journal 'certain' where we meant 'never asked'."""
    if not isinstance(belief, dict):
        return None
    value = (belief.get("uncertainty") or {}).get(axis)
    return float(value) if isinstance(value, (int, float)) else None


def record_for(
    decision: Decision,
    bundle: Bundle,
    *,
    outcome: Optional[str] = None,
    landed_state: Optional[str] = None,
    verified: Optional[bool] = None,
    golden: bool = False,
    proposed: Optional[Decision] = None,
    shadow: bool = False,
    session_id: Optional[str] = None,
    duration_ms: int = 0,
    cost_usd: float = 0.0,
    verdict: Optional["SupervisorVerdict"] = None,
    delta: Optional["StateDelta"] = None,
    authority: Optional["AuthorityVerdict"] = None,
    outcome_detail: str = "",
) -> DecisionRecord:
    """Build a DecisionRecord from a Decision + the Bundle it decided on. PURE — no IO, no
    time; `log_decision` stamps `ts`. Keeps construction replayable from journaled inputs."""
    return DecisionRecord(
        ts="",
        schema_version=DECISION_SCHEMA_VERSION,
        intent=decision.intent,
        rung=decision.rung,
        outcome=outcome,
        params=dict(decision.params or {}),
        confidence=decision.confidence,
        escalate=decision.escalate,
        rationale=decision.rationale,
        evidence=tuple(decision.evidence),
        expected_next=tuple(decision.expected_next),
        landed_state=landed_state,
        verified=verified,
        bundle_digest=bundle_digest(bundle),
        task=bundle.task,
        state=bundle.state,
        ats=bundle.ats,
        route=bundle.route,
        fingerprint=bundle.fingerprint,
        url=bundle.url,
        golden=golden,
        proposed_intent=proposed.intent if proposed else None,
        proposed_params=dict(proposed.params) if proposed else None,
        proposed_rung=proposed.rung if proposed else None,
        # §10: the proposal's OWN reasoning, kept beside the teacher's — the correction's contrast.
        proposed_rationale=proposed.rationale if proposed else None,
        proposed_evidence=tuple(proposed.evidence) if proposed else (),
        shadow=shadow,
        # EVERY row carries the snapshot, not just golden/shadow ones (changed 2026-07-20).
        # Restricting it to replay cases meant that of 45 real journalled decisions, **4** could be
        # re-run — the other 41 recorded what was decided with no way to reconstruct what it was
        # decided FROM, which is exactly half a training row. A distilled L4 learns the mapping
        # `Bundle -> Decision`; a corpus that stores only the right-hand side cannot teach it.
        # `replay_snapshot` is PII-free and selector-free by construction (route, not url; no
        # lessons; sanitised unanswered) and adds ~300 bytes, so there was never a reason beyond
        # the original narrow framing of "replay cases".
        bundle_snapshot=replay_snapshot(bundle),
        # The supervisor's verdict on this action, and the graded label it rests on. Optional so
        # every existing caller (and every row already journaled) stays valid.
        supervisor_class=verdict.failure_class if verdict else None,
        supervisor_recovery=verdict.proposed_recovery if verdict else None,
        supervisor_stuck=verdict.stuck_signal if verdict else None,
        supervisor_rung=verdict.rung if verdict else None,
        supervisor_rationale=verdict.rationale if verdict else "",
        supervisor_evidence=tuple(verdict.evidence) if verdict else (),
        delta_moved=delta.moved if delta is not None else None,
        delta_churn=delta.churn if delta is not None else None,
        # Perception, flattened off the Bundle at the same single choke point every other
        # cross-cutting field is copied at — so no seam can journal a decision and forget who
        # said where it was made (PLAN_perception_v1 §3.3).
        # Staleness, flattened at the same choke point for the same reason: a seam must not be
        # able to journal a decision without recording how old the view was when it was made.
        # PROTOTYPE — the raw ages are what will fit the thresholds; the level is today's guess.
        staleness_level=(bundle.staleness or {}).get("level") if bundle.staleness else None,
        staleness_verdict=(bundle.staleness or {}).get("verdict") if bundle.staleness else None,
        staleness_rules=(bundle.staleness or {}).get("rules_version") if bundle.staleness else None,
        staleness_idle_s=_staleness_signal(bundle.staleness, "idle_s"),
        staleness_page_age_s=_staleness_signal(bundle.staleness, "page_age_s"),
        belief_state=(bundle.belief or {}).get("state") if bundle.belief else None,
        belief_agreement=(bundle.belief or {}).get("agreement") if bundle.belief else None,
        belief_novelty=_belief_axis(bundle.belief, "novelty"),
        belief_state_uncertainty=_belief_axis(bundle.belief, "state"),
        # Authority, flattened at the SAME single choke point every other cross-cutting field is
        # copied at — so no seam can journal a decision without recording who was allowed to make
        # it. `control_mode` in particular feeds promotion back into `maturity.derive`, which is
        # what makes the ladder self-reinforcing instead of hand-maintained.
        control_mode=authority.mode if authority else None,
        authority_reason=authority.reason if authority else "",
        transition_maturity=authority.maturity if authority else None,
        authority_axis=authority.blocking_axis if authority else "",
        reach_gaps=tuple(authority.gaps) if authority else (),
        escalation_axis=decision.escalation_axis,
        session_id=session_id,
        duration_ms=duration_ms,
        cost_usd=cost_usd,
        # What the decision was looking at, flattened at the SAME choke point — basenames only
        # (durable, servable), no pixels. None on collect=False credential rows by construction:
        # an empty CapturedTurn yields Bundle.capture=None upstream, so nothing leaks here.
        capture_artifact=(bundle.capture or {}).get("artifact") if bundle.capture else None,
        capture_screenshot=(bundle.capture or {}).get("screenshot_filename") if bundle.capture else None,
        outcome_detail=outcome_detail or "",
    )


def log_decision(record: DecisionRecord) -> Optional[DecisionRecord]:
    """Append one decision row. Best-effort — never raises into the hot path.

    Enforces the spine rule (`route` required) and redacts params (values + proposed_params)
    before the row lands. Returns the redacted record (or None if refused/failed) so a caller
    can echo it without re-deriving.
    """
    try:
        if not (record.route or "").strip():
            # The spine rule: a row that cannot join to anything is not a corpus row.
            return None
        if not record.ts:
            record.ts = datetime.now(timezone.utc).isoformat()
        record.params = _redact_params(record.params)
        if record.proposed_params is not None:
            record.proposed_params = _redact_params(record.proposed_params)
        with _lock:
            with _path().open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        return record
    except Exception:  # noqa: BLE001 — a journal write must never break a live drive
        return None


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


def recent_for_run(session_id: str, *, k: int = 5) -> tuple[dict, ...]:
    """The last k (intent, field, outcome) rows for one run — the Bundle's history half.
    Reads the journal so the loop's `recent` survives a process restart mid-run."""
    if not session_id:
        return ()
    rows = [r for r in read_rows() if r.get("session_id") == session_id and not r.get("shadow")]
    tail = rows[-k:]
    return tuple(
        {"intent": r.get("intent"), "field": (r.get("params") or {}).get("field"),
         "outcome": r.get("outcome")}
        for r in tail
    )


def summarize() -> dict[str, Any]:
    """Aggregate the corpus — the controller scoreboard, mirror of `journal.summarize`.

    `verified_rate` is the number to watch: an unverified corpus is not a healthy one. Rung
    and escalation shares are the promotion signals PLAN §6 gates on.
    """
    rows = read_rows()
    summary: dict[str, Any] = {
        "corpus_size": len(rows),
        "by_rung": [], "by_intent": [], "by_state": [], "by_ats": [],
        "verified_rate": 0.0, "escalation_rate": 0.0, "golden_count": 0, "shadow_count": 0,
        # §10 (the Open Brain): of the rows that are SUPPOSED to teach (the teacher's demonstrations
        # + golden corrections), how many actually carry reasoning. Trends to 1.0 once the teaching
        # seams solicit a real "why"; a persistent gap means reasoning is being paid for and dropped.
        "teach_row_count": 0, "reasoned_rate": 0.0, "unreasoned_teach_count": 0,
    }
    if not rows:
        return summary

    def tally(key: str) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for r in rows:
            c = r.get(key) or "?"
            counts[c] = counts.get(c, 0) + 1
        return sorted(({key: k, "count": v} for k, v in counts.items()),
                      key=lambda x: x["count"], reverse=True)

    acted = [r for r in rows if not r.get("escalate")]
    verifiable = [r for r in acted if r.get("verified") is not None]
    summary["by_rung"] = tally("rung")
    summary["by_intent"] = tally("intent")
    summary["by_state"] = tally("state")
    summary["by_ats"] = tally("ats")
    summary["escalation_rate"] = round(sum(1 for r in rows if r.get("escalate")) / len(rows), 4)
    if verifiable:
        summary["verified_rate"] = round(
            sum(1 for r in verifiable if r.get("verified")) / len(verifiable), 4)
    summary["golden_count"] = sum(1 for r in rows if r.get("golden"))
    summary["shadow_count"] = sum(1 for r in rows if r.get("shadow"))
    # §10: reasoning coverage on the teaching rows (teacher-rung or golden corrections).
    teach_rows = [r for r in rows if r.get("golden") or r.get("rung") == "teacher"]
    if teach_rows:
        reasoned = sum(1 for r in teach_rows if is_real_rationale(r.get("rationale")))
        summary["teach_row_count"] = len(teach_rows)
        summary["reasoned_rate"] = round(reasoned / len(teach_rows), 4)
        summary["unreasoned_teach_count"] = len(teach_rows) - reasoned
    return summary
