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

#: The DISPLAYED promotion gate (CONTROLLER_PROMOTION.md): agreement >= 90% over >= 25 paired
#: teacher steps, judged per scenario (ats:state) — never a global average. Displayed, not
#: enforced: the ladder in `controller/maturity.py` is what `authority()` acts on, and that
#: open decision is recorded in the promotion doc's 2026-08-20 entry. The scorecard shows the
#: gate the operator reads; it must not imply the code branches on it.
PROMOTION_MIN_AGREEMENT = 0.90
PROMOTION_MIN_N = 25

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

    rows_total = rows_today = labels_total = labels_today = 0
    for corpus in sr.list_corpora():
        for row in sr.read_transitions(corpus["key"], limit=1000):
            rows_total += 1
            if _day(row.get("ts")) == today:
                rows_today += 1
            correction = row.get("teacher_correction")
            if correction:
                labels_total += 1
                if _day(correction.get("ts")) == today:
                    labels_today += 1

    queue = build_label_queue()
    by_reason: dict[str, int] = {}
    for item in queue:
        by_reason[item["why_queued"]] = by_reason.get(item["why_queued"], 0) + 1

    parks = inbox_mod.counts()

    agreement = controller_metrics.shadow_agreement(decision_journal.read_rows())
    scenarios = [{**s,
                  "passes": (s["n"] >= PROMOTION_MIN_N
                             and s["agreement"] >= PROMOTION_MIN_AGREEMENT),
                  "n_needed": max(0, PROMOTION_MIN_N - s["n"])}
                 for s in agreement.get("by_scenario", [])]

    apps = list(db.scalars(select(Application)).all())
    by_week = _apps_by_week(apps)

    # Post-submit visibility — the tandem's other two measures (flows closed, outcomes recorded).
    # Event kinds are reported GENERICALLY (kind → count), so the Gmail outcome matcher's new
    # kinds appear here the day it lands, with no change to this endpoint or the screen.
    events_by_kind = {kind: n for kind, n in
                      db.execute(select(ApplicationEvent.kind, func.count())
                                 .group_by(ApplicationEvent.kind)).all()}
    outcomes_recorded = sum(n for kind, n in events_by_kind.items() if kind != "applied")
    flows_total = db.scalar(select(func.count()).select_from(AtsFlow)) or 0
    flows_closed = db.scalar(select(func.count()).select_from(AtsFlow)
                             .where(AtsFlow.terminal.isnot(None))) or 0

    return {
        "day": today,
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
                     "unit": "ats:state", "enforced": False,
                     "note": "the displayed gate (CONTROLLER_PROMOTION.md); authority() enforces "
                             "the maturity ladder, not this number"},
            "overall": {"agreement": agreement.get("agreement", 0.0),
                        "n": agreement.get("n", 0)},
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
