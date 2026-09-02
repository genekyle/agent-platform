"""The session scorecard — one read that answers "did today move the needle?".

The docs already name the measures: a session is judged by rows banked, labels written and parks
answered (PROJECT_STATUS.md); the promotion unit is per-scenario shadow agreement against the
displayed gate (CONTROLLER_PROMOTION.md: >= 90% over >= 25 paired steps, scenario = ats:state);
the witness corpus grows only from live captures. Until 2026-08-22 those numbers rendered on
three different screens with three different denominators, and several rendered nowhere at all
(the reflection audit's cockpit-reach-parity finding). This router COMPOSES the existing sources
— it derives everything on read from the same functions the working surfaces use, so the
scorecard can never disagree with the queues it summarizes.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import step_runner as sr
from controller import inbox as inbox_mod
from controller import metrics as controller_metrics
from deps import get_db
from interaction import decision_journal
from models import Application, ApplicationEvent, AtsFlow
from routers.transitions import build_label_queue

router = APIRouter()

#: The DISPLAYED promotion gate (CONTROLLER_PROMOTION.md) — since 2026-08-23 a TWO-BAR gate:
#: loose (intent) agreement AND exact (control-identity) agreement, each over its own >= 25
#: window, judged per scenario (ats:state) — never a global average. The constants live in
#: `controller/metrics.py` (the same module that computes eligibility), so the scorecard reads
#: them rather than growing a second copy that can drift. Displayed, not enforced: the ladder in
#: `controller/maturity.py` is what `authority()` acts on, and that open decision is recorded in
#: the promotion doc's 2026-08-20 entry. The scorecard shows the gate the operator reads; it
#: must not imply the code branches on it.
PROMOTION_MIN_AGREEMENT = controller_metrics.PROMOTION_LOOSE_BAR
PROMOTION_MIN_N = controller_metrics.PROMOTION_MIN_N

#: Where the outcome clock started — the 2026-08-22 reflection audit measured the ledger
#: write-only after submit (0 outcomes recorded, 5/68 flows closed). The week's progress is
#: judged against this snapshot. A recorded FACT, deliberately frozen: it never updates, so the
#: distance travelled stays visible after the matcher starts writing.
OUTCOMES_BASELINE = {"date": "2026-08-22", "outcomes_recorded": 0,
                     "flows_closed": 5, "flows_total": 68}


def _day(ts: Any) -> str:
    """The LOCAL calendar day of a stored (UTC) timestamp. Rows are stamped UTC; the operator's
    "today" is the machine's day — a row banked at 9pm ET must not fall into tomorrow because
    midnight UTC passed (caught on this endpoint's own first live read, 2026-08-22)."""
    try:
        return datetime.fromisoformat(str(ts)).astimezone().date().isoformat()
    except (ValueError, TypeError):
        return str(ts or "")[:10]


def _apps_by_week(apps: list[Application], *, weeks: int = 8) -> list[dict[str, Any]]:
    """Applications per ISO week (Monday start), oldest first, zero-filled so a quiet week is a
    visible 0 rather than a missing bar. Dated by `applied_at` when the mark-applied flow stamped
    it, else `created_at` — the row's own birth, never today's."""
    monday = datetime.now().astimezone().date()
    monday -= timedelta(days=monday.weekday())
    buckets = [{"week_start": (monday - timedelta(weeks=i)).isoformat(), "count": 0}
               for i in range(weeks - 1, -1, -1)]
    index = {b["week_start"]: b for b in buckets}
    for app in apps:
        stamp = app.applied_at or app.created_at
        if stamp is None:
            continue
        local = stamp.astimezone() if stamp.tzinfo else stamp
        week = (local.date() - timedelta(days=local.weekday())).isoformat()
        if week in index:
            index[week]["count"] += 1
    return buckets


def _since(ts: Any, cutoff: datetime) -> bool:
    """Is a stored (usually UTC-ISO) timestamp inside the window? Unparseable -> False: a row
    whose age cannot be read must not inflate a trailing-window rate."""
    try:
        stamp = datetime.fromisoformat(str(ts))
        if stamp.tzinfo is None:
            stamp = stamp.astimezone()
        return stamp >= cutoff
    except (ValueError, TypeError):
        return False


def _in_house_from_rows(rows: list[dict], cutoff: datetime) -> dict[str, Any]:
    """The headline the 2026-08-31 reroute changed to: of the ACTED decisions in the window
    (shadow rows are measurements, not acts), what share was made by an in-house rung — anything
    that is not the teacher and not a human hand-up."""
    window = [r for r in rows if not r.get("shadow") and _since(r.get("ts"), cutoff)]
    by_rung: dict[str, int] = {}
    for r in window:
        rung = str(r.get("rung") or "?")
        by_rung[rung] = by_rung.get(rung, 0) + 1
    outside = by_rung.get("teacher", 0) + by_rung.get("human", 0)
    return {"window_days": 7, "n": len(window), "by_rung": by_rung,
            "share": round(1 - outside / len(window), 4) if window else None}


def _precedent_shadow_from_rows(rows: list[dict], cutoff: datetime) -> dict[str, Any]:
    """How the in-house seat is doing IN SHADOW (§11 item 2): of the window's shadow pairs, how
    often the precedent rung proposed at all (coverage — the rest were honest abstentions) and
    how often a proposal matched the teacher's intent (agreement, the loose bar's analogue)."""
    pairs = [r for r in rows if r.get("shadow") and _since(r.get("ts"), cutoff)]
    proposed = [r for r in pairs if r.get("proposed_rung") == "precedent"]
    agree = sum(1 for r in proposed if r.get("proposed_intent") == r.get("intent"))
    return {"window_days": 7, "shadow_pairs": len(pairs), "proposed": len(proposed),
            "coverage": round(len(proposed) / len(pairs), 4) if pairs else None,
            "agreement": round(agree / len(proposed), 4) if proposed else None}


def _autonomy_from_transitions(window_rows: list[dict], applied_keys: set[str]) -> dict[str, Any]:
    """`full_run_autonomy` v1 (§11): of the window's application runs, how many reached their
    submit with ZERO operator-initiated rows. A run = a job whose rows include a confirmed
    submit rung, or whose job_key the ledger marked applied in the window. Touches count every
    operator-initiated row on that job — the submit press included, deliberately: that press is
    exactly what per-scenario graduation removes. Stop-state escalations (captcha/2FA) are not
    rows the operator INITIATES, so they do not count as touches here; the park they open is
    already reported beside this."""
    per_job: dict[str, dict[str, int]] = {}
    unlinked_operator_rows = 0
    for row in window_rows:
        action = row.get("action") or {}
        job = str(action.get("job_id") or "")
        operator = str(action.get("initiator") or "") == "operator"
        if not job:
            unlinked_operator_rows += 1 if operator else 0
            continue
        slot = per_job.setdefault(job, {"touches": 0, "submitted": 0})
        slot["touches"] += 1 if operator else 0
        if row.get("rung") == "submit" and row.get("verdict") == "confirmed":
            slot["submitted"] = 1
    runs = {job: s for job, s in per_job.items()
            if s["submitted"] or job in applied_keys}
    zero = sum(1 for s in runs.values() if s["touches"] == 0)
    touches = [s["touches"] for s in runs.values()]
    return {
        "window_days": 7,
        "definition": "share of application runs reaching submit with zero operator-initiated "
                      "rows (stop-states excluded by construction; the operator's submit press "
                      "counts as a touch until its scenario graduates)",
        "runs_measured": len(runs),
        "zero_touch": zero,
        "full_run_autonomy": round(zero / len(runs), 4) if runs else None,
        "avg_touches_per_run": round(sum(touches) / len(touches), 2) if touches else None,
        "jobs_seen": len(per_job),
        "unlinked_operator_rows": unlinked_operator_rows,
    }


def _witness_census() -> Optional[dict[str, Any]]:
    """The perception corpus census (labeled / from_transitions / with_screenshot …).
    Best-effort: the scorecard must render even when the witness corpus cannot load."""
    try:
        from perception.dataset import load_rows

        _, census = load_rows()
        return census
    except Exception:  # noqa: BLE001 — a dead source degrades one panel, never the page
        return None


@router.get("/api/learning/scorecard")
def learning_scorecard(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Everything the operator needs to judge a session and the road to promotion, in one read:
    today's banked work, the label-queue backlog, open/answered parks, per-scenario agreement
    against the displayed gate, the witness census, and applications per week."""
    today = datetime.now().astimezone().date().isoformat()

    # Totals come from `list_corpora()`, which counts EVERY row; the per-row walk below is only
    # for TODAY's slice, so its newest-tail `limit` cannot freeze the totals at 1000/corpus while
    # the Transitions landing reports the true count (the review's screens-drift finding).
    week_cutoff = datetime.now().astimezone() - timedelta(days=7)
    rows_total = rows_today = labels_total = labels_today = 0
    window_rows: list[dict[str, Any]] = []   # the trailing week's slice, for autonomy (§11)
    for corpus in sr.list_corpora():
        rows_total += corpus.get("rows", 0)
        labels_total += corpus.get("corrected", 0)
        for row in sr.read_transitions(corpus["key"], limit=1000):
            if _day(row.get("ts")) == today:
                rows_today += 1
            if _since(row.get("ts"), week_cutoff):
                window_rows.append(row)
            correction = row.get("teacher_correction")
            if correction and _day(correction.get("ts")) == today:
                labels_today += 1

    queue = build_label_queue()
    by_reason: dict[str, int] = {}
    for item in queue:
        by_reason[item["why_queued"]] = by_reason.get(item["why_queued"], 0) + 1

    parks = inbox_mod.counts()

    # Eligibility is the METRIC's verdict (`is_promotable` — both bars, each over its own
    # window), never recomputed here: a second copy of the gate is a second place for it to
    # drift. `passes` is kept as an alias of `eligible` for the screen's existing read, with a
    # loose-only fallback for a journal serialized before the exact bar existed.
    decision_rows = decision_journal.read_rows()
    agreement = controller_metrics.shadow_agreement(decision_rows)
    scenarios = [{**s,
                  "passes": s.get("eligible",
                                  s["n"] >= PROMOTION_MIN_N
                                  and s["agreement"] >= PROMOTION_MIN_AGREEMENT),
                  "n_needed": max(0, PROMOTION_MIN_N - s["n"]),
                  "exact_n_needed": max(0, controller_metrics.PROMOTION_MIN_EXACT_N
                                        - s.get("exact_n", 0))}
                 for s in agreement.get("by_scenario", [])]

    apps = list(db.scalars(select(Application)).all())
    by_week = _apps_by_week(apps)

    # Post-submit visibility — the tandem's other two measures (flows closed, outcomes recorded).
    # Event kinds are reported GENERICALLY (kind → count), so the Gmail outcome matcher's new
    # kinds appear here the day it lands, with no change to this endpoint or the screen.
    events_by_kind = {kind: n for kind, n in
                      db.execute(select(ApplicationEvent.kind, func.count())
                                 .group_by(ApplicationEvent.kind)).all()}
    # "Outcomes recorded" = events the WORLD sent back (source='gmail' — the inbox matcher's
    # writes), not merely any non-applied kind: an operator-typed note is bookkeeping, an email
    # from the employer is an outcome. The matcher lane named this the sharper count (2026-08-23).
    outcomes_recorded = db.scalar(select(func.count()).select_from(ApplicationEvent)
                                  .where(ApplicationEvent.source == "gmail")) or 0
    flows_total = db.scalar(select(func.count()).select_from(AtsFlow)) or 0
    flows_closed = db.scalar(select(func.count()).select_from(AtsFlow)
                             .where(AtsFlow.terminal.isnot(None))) or 0

    # The in-house seat (PLAN_inhouse_reasoner_v1 §5/§11): the numbers that must move —
    # % decisions in-house, the precedent rung's shadow standing, and full_run_autonomy.
    applied_keys = {a.job_key for a in apps
                    if a.applied_at is not None
                    and (a.applied_at.astimezone() if a.applied_at.tzinfo else
                         a.applied_at.replace(tzinfo=week_cutoff.tzinfo)) >= week_cutoff}
    in_house = {
        "decisions": _in_house_from_rows(decision_rows, week_cutoff),
        "precedent_shadow": _precedent_shadow_from_rows(decision_rows, week_cutoff),
        "autonomy": _autonomy_from_transitions(window_rows, applied_keys),
        "graduated_scenarios": sum(1 for s in scenarios if s.get("passes")),
    }

    return {
        "day": today,
        "in_house": in_house,
        # The docs' measure of a session — rows banked, labels written, parks answered.
        "session": {
            "rows_banked": {"today": rows_today, "total": rows_total},
            "labels_written": {"today": labels_today, "total": labels_total},
            "parks": {"open": parks["open"], "answered_today": parks["answered_today"],
                      "answered_total": parks["answered"], "expired": parks["expired"]},
        },
        "label_queue": {"remaining": len(queue), "by_reason": by_reason},
        "promotion": {
            "gate": {"min_agreement": PROMOTION_MIN_AGREEMENT, "min_n": PROMOTION_MIN_N,
                     "bars": agreement.get("bars"),
                     "unit": "ats:state", "enforced": False,
                     "note": "the displayed TWO-BAR gate (CONTROLLER_PROMOTION.md): loose AND "
                             "exact, each over its own window; authority() enforces the "
                             "maturity ladder, not this number"},
            "overall": {"agreement": agreement.get("agreement", 0.0),
                        "n": agreement.get("n", 0),
                        "exact_agreement": agreement.get("exact_agreement", 0.0),
                        "exact_n": agreement.get("exact_n", 0),
                        "exact_unscoreable": agreement.get("exact_unscoreable", 0)},
            "scenarios": scenarios,
        },
        "witnesses": _witness_census(),
        "applications": {"total": len(apps),
                         "this_week": by_week[-1]["count"] if by_week else 0,
                         "by_week": by_week},
        "outcomes": {
            "events_by_kind": events_by_kind,
            "outcomes_recorded": outcomes_recorded,
            "flows": {"total": flows_total, "closed": flows_closed},
            "baseline": OUTCOMES_BASELINE,
        },
    }
