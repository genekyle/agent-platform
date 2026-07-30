"""The orientation corpus — every "where are we" verdict, kept so the inner models can learn it.

Operator, 2026-07-30: *"make sure to teach while you're creating the solutions for our inner layers
so that's the flow for your drive."*

The observer fuses deterministic witnesses today and takes learned ones through `extra_witnesses`
tomorrow. Nothing bridges those two facts unless the verdicts are WRITTEN DOWN — a perception
witness needs (features, label) pairs, and the features are exactly what orientation already
assembles on every render. So this is the corpus, produced as a by-product of driving rather than
as a separate labelling chore. Same lesson as `project_capture_label_is_the_work`: driving without
recording pays for the drive twice.

WHAT A ROW IS, and why each field is here:

    inputs      url + host, the page-kind evidence, each witness's claim — the FEATURES a student
                would train on, at the moment of the decision and not reconstructed later
    verdict     platform / kind / state / confidence — the label the deterministic fusion produced
    mismatch    whether the rung and the world disagreed, which is the interesting minority class
    outcome     what the OPERATOR did next (`confirmed`, `corrected`, or unanswered) — the only
                signal that says the verdict was actually right, and the reason this is a corpus
                rather than a log

THE DEDUPE IS THE POINT, NOT AN OPTIMISATION. `_orient_now` fires on every panel poll, so a parked
tab would write the same verdict hundreds of times and a model trained on it would learn "whatever
we stare at longest is the truth". A verdict is recorded when it CHANGES (`fingerprint`), so the
corpus is one row per distinct observed situation — the same rule `orient_step` already follows
("a read that repeats itself is not new knowledge") and the same one the capture corpus follows
(distinct fingerprints, not raw volume).

No secrets: orientation reads page KIND and control destinations, never field values. The only
free text stored is the witnesses' own explanations, which are generated here.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from settings import settings

_lock = threading.Lock()

#: Rows kept. Generous — this is a training corpus, not a diagnostic window — but bounded, because
#: an unbounded JSONL on a laptop is a disk-full waiting for the least convenient moment.
MAX_ROWS = 5000

#: Set True ONLY by this module's own tests (see the guard in `record`). A module-level flag rather
#: than an env var because pytest re-sets PYTEST_CURRENT_TEST per test, so unsetting it does not
#: hold — and a bypass that silently stops working is worse than no bypass.
ALLOW_TEST_WRITES = False

CONFIRMED = "confirmed"      # the operator took the action the verdict proposed
CORRECTED = "corrected"      # the operator did something else — the verdict was wrong or unhelpful


def _path() -> Path:
    """Beside the other operator-owned state (company_ats.json, domain_settings.json)."""
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parent / base).resolve()
    p = base / "cache" / "orientation_corpus.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fingerprint(verdict: dict[str, Any]) -> str:
    """What makes two observations THE SAME situation.

    Host + state + mismatch-or-not. Deliberately coarse: the same landing re-read after a scroll is
    not new knowledge, but the same host reaching a different state is exactly what we want a row
    for. The query string is excluded — `?currentJobId=` changes per card and would make every
    glance look novel.
    """
    host = (urlparse(verdict.get("url") or "").hostname or "").lower()
    return f"{host}|{verdict.get('state') or ''}|{'mismatch' if verdict.get('mismatch') else 'agreed'}"


def _row(session_id: Any, verdict: dict[str, Any], *, step_job_id: str = "",
         rung: str = "") -> dict[str, Any]:
    url = verdict.get("url") or ""
    return {
        "at": _now(),
        "session_id": str(session_id),
        "fingerprint": fingerprint(verdict),
        # --- inputs (features) ---
        "host": (urlparse(url).hostname or "").lower(),
        "url": url[:300],
        "witnesses": [{"source": w.get("source"), "claim": w.get("claim"),
                       "weight": w.get("weight", 1.0)}
                      for w in (verdict.get("witnesses") or [])],
        "rung": rung,
        "job_id": step_job_id,
        # --- verdict (label) ---
        "platform": verdict.get("platform") or "",
        "kind": verdict.get("kind") or "",
        "state": verdict.get("state") or "",
        "confidence": verdict.get("confidence") or "",
        "mismatch": bool(verdict.get("mismatch")),
        "plan": [st.get("id") for st in (verdict.get("plan") or [])],
        # --- outcome, filled in later by `resolve` when the operator acts ---
        "outcome": "",
        "operator_action": "",
    }


def _read() -> list[dict[str, Any]]:
    p = _path()
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue          # a torn last line is not a reason to lose the corpus
    return out


def _write(rows: list[dict[str, Any]]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows[-MAX_ROWS:]))


def record(session_id: Any, verdict: Optional[dict[str, Any]], *, step_job_id: str = "",
           rung: str = "") -> Optional[dict[str, Any]]:
    """Append this verdict IF it is a situation we have not just recorded. Returns the row, or None.

    Called from the observer's own path, so recording costs one comparison per render and the
    corpus grows only when the world does.
    """
    if not verdict or not verdict.get("state"):
        return None
    # A TEST MUST NEVER WRITE TO A TRAINING CORPUS. The suite drives `_orient_now` against fakes,
    # and those verdicts landed in the real file the first time this was wired — six rows of
    # `workday_unreadable` / `icims_unreadable` from fixtures, indistinguishable from live
    # observations once written. Poisoned training data is the kind of defect that is invisible
    # until a model has learned it, so the guard is unconditional rather than careful.
    if os.environ.get("PYTEST_CURRENT_TEST") and not ALLOW_TEST_WRITES:
        return None
    fp = fingerprint(verdict)
    with _lock:
        rows = _read()
        for prev in reversed(rows):
            if prev.get("session_id") == str(session_id):
                # Only the LAST verdict for this session suppresses — a situation we return to
                # after being elsewhere is a genuine new observation, and the transition is the
                # part a sequencing model most needs.
                if prev.get("fingerprint") == fp:
                    return None
                break
        row = _row(session_id, verdict, step_job_id=step_job_id, rung=rung)
        rows.append(row)
        _write(rows)
    return row


def resolve(session_id: Any, *, action_id: str, agreed: bool) -> Optional[dict[str, Any]]:
    """Stamp the most recent row for this session with what the operator actually did.

    This is what turns the log into a CORPUS. A verdict nobody acted on is an unlabelled example;
    a verdict whose proposed action the operator took is a confirmation; one they overrode is the
    single most valuable row in the file, because it is a labelled mistake. The teacher's
    corrections are the training signal — this is that idea at the observer's altitude.
    """
    with _lock:
        rows = _read()
        for row in reversed(rows):
            if row.get("session_id") == str(session_id) and not row.get("outcome"):
                row["outcome"] = CONFIRMED if agreed else CORRECTED
                row["operator_action"] = action_id
                _write(rows)
                return row
    return None


def stats() -> dict[str, Any]:
    """What the corpus holds — the numbers that say whether a student can be trained on it yet.

    `distinct_situations` is the one to watch, for the same reason the capture corpus watches
    distinct fingerprints: re-reading a page we already know adds nothing a model can learn from.
    """
    rows = _read()
    by_state: dict[str, int] = {}
    for r in rows:
        st = r.get("state") or "unknown"
        by_state[st] = by_state.get(st, 0) + 1
    labelled = [r for r in rows if r.get("outcome")]
    return {
        "rows": len(rows),
        "distinct_situations": len({r.get("fingerprint") for r in rows}),
        "by_state": dict(sorted(by_state.items(), key=lambda kv: -kv[1])),
        "mismatches": sum(1 for r in rows if r.get("mismatch")),
        "labelled": len(labelled),
        "corrected": sum(1 for r in labelled if r.get("outcome") == CORRECTED),
        "path": str(_path()),
    }
