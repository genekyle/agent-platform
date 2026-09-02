"""The screener Q→A corpus — the answer head's training faucet (PLAN_inhouse_reasoner_v1 §11
item 3).

One append-only row per QUESTION the apply flow answered (or failed to): the question as the
widget posed it, the options it offered, the canonical answer we hold, how the semantic
cascade resolved it (`resolve_answer.Resolution`), and what the act's outcome was. Questions
repeat heavily across employers — this corpus is what makes the `answer` head cheap to train
and, before any training, lets the precedent store retrieve past answers to repeat questions.

Same discipline as every corpus here: append-only, best-effort (a journal write must never
sink the fill it observes), values redacted AT THE WRITE via `interaction.contract.redact` so
no call site can leak a secret by forgetting, corrections keep both sides (PRINCIPLES §10).
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from interaction.contract import redact

_lock = threading.Lock()
_FILE = "qa_journal.jsonl"


def _qa_dir() -> Path:
    """Env override first (tests; worktree sessions pointing at the main checkout's data —
    the same rule the vector rider follows), then the corpus root every other journal uses."""
    env = os.environ.get("PRECEDENT_DATA_ROOT")
    if env:
        base = Path(env)
    else:
        from settings import settings

        base = Path(settings.observer_artifacts_dir)
        if not base.is_absolute():
            base = (Path(__file__).resolve().parent / base).resolve()
    d = base / "qa"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path() -> Path:
    return _qa_dir() / _FILE


def record_qa(*, session_id: Any, job_id: str = "", ats: str = "", state: str = "",
              field: str, question_text: str, options: Any = (),
              canonical: str = "", resolution: Optional[dict] = None,
              outcome: str = "", detail: str = "", initiator: str = "") -> Optional[dict]:
    """Append one Q→A row. Returns the row as written, or None when the write failed.

    Sensitivity is settled HERE, unconditionally: the canonical answer and the resolved value
    pass through `redact(field=...)`, and a row whose value the redactor masked carries
    `sensitive: true` — the QUESTION still banks (its distribution is trainable), the secret
    never does. A credential wall's password field can flow through this seam safely."""
    try:
        red_canonical = redact(str(canonical or ""), field=field)
        sensitive = bool(canonical) and red_canonical != str(canonical)
        res = dict(resolution or {})
        if res.get("value") is not None:
            res["value"] = redact(str(res["value"]), field=field)
        row = {
            "v": 1,
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "job_id": str(job_id or ""),
            "ats": str(ats or ""),
            "state": str(state or ""),
            "field": str(field),
            "question_text": str(question_text or field),
            "options": [str(o) for o in (options or ()) if o][:24],
            "canonical": red_canonical,
            "sensitive": sensitive,
            "resolution": res,          # value/method/confidence/rationale/needs_human
            "outcome": str(outcome or ""),
            "detail": str(detail or "")[:200],
            "initiator": str(initiator or ""),
            "teacher_correction": None,
        }
        with _lock:
            with _path().open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001 — the corpus must never sink the fill it observes
        return None
    try:
        from precedent.rider import on_qa_row

        on_qa_row(row)
    except Exception:  # noqa: BLE001 — an aid, never a dependency
        pass
    return row


def read_qa(*, limit: int = 500) -> list[dict[str, Any]]:
    """Rows oldest-first, each with its `index` (line number — the correction address; stable
    because the file is append-only and corrections rewrite in place)."""
    p = _path()
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    lines = p.read_text(encoding="utf-8").splitlines()
    start = max(0, len(lines) - limit)
    for i, line in enumerate(lines[start:], start=start):
        try:
            row = json.loads(line)
            row["index"] = i
            rows.append(row)
        except Exception:  # noqa: BLE001
            continue
    return rows


def correct_qa(index: int, *, value: str, by: str, note: str = "") -> Optional[dict]:
    """The teacher/operator overrides an answer. Both sides kept (§10): the original
    resolution stays; the correction rides beside it, redacted by the row's own field."""
    p = _path()
    if not p.exists():
        return None
    with _lock:
        lines = p.read_text(encoding="utf-8").splitlines()
        if not 0 <= index < len(lines):
            return None
        try:
            row = json.loads(lines[index])
        except Exception:  # noqa: BLE001
            return None
        row["teacher_correction"] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "by": by,
            "value": redact(str(value), field=row.get("field")),
            "note": str(note or ""),
            "original": dict(row.get("resolution") or {}),
        }
        lines[index] = json.dumps(row, ensure_ascii=False, default=str)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return row
