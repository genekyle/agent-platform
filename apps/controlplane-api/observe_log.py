"""Observe-mode recordings, kept — so a window can be reviewed together and re-read later.

`/observe/start` + `/observe/stop` on the capture server produce a recording and hand it back once.
That is enough to diagnose in the moment and useless an hour later, which is the wrong shape for
the thing it is for: the operator asked to be able to toggle recording, then *review it with me*,
and for the system to keep it as context. A recording that exists only in one reply is a
screenshot someone forgot to save.

So a recording is an ARTIFACT. Each one is stored whole — its events in order, the focus at start
and stop, what was dropped — under the session that produced it, and can be listed, re-read and
summarised without touching the browser again.

TWO THINGS THIS DELIBERATELY DOES NOT DO:

* **It does not decide what matters.** The stored events are exactly what the page emitted, in
  order. Summaries are computed as a VIEW on read (`summarize`), never by discarding at write time
  — the four wrong diagnoses this tool was built to end all came from reasoning over a filtered
  picture, and a recorder that pre-filters is just a faster way to make that mistake.
* **It does not hold secrets.** The page-side recorder already refuses to read a password/OTP field's
  value (PRINCIPLES §4). This layer re-checks on the way in rather than trusting that, because a
  store is where a leak becomes permanent: any event whose value looks like a credential is dropped
  to a marker, and the check is cheap enough to be unconditional.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from settings import settings

_lock = threading.Lock()

#: Recordings kept per session. A window is a diagnostic, not a corpus — the tail is what gets
#: reviewed, and an unbounded directory turns every list into a scan.
MAX_RECORDINGS_PER_SESSION = 20

#: A stored value that looks like a secret, whatever the page said about it. Belt and braces over
#: the page-side refusal: this is the layer where a mistake becomes permanent.
_SECRET_MARKER = "<secret: not stored>"
_SECRET_HINT = re.compile(r"password|passwd|otp|one-time|cvc|cvv|ssn|card.?number", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path() -> Path:
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parent / base).resolve()
    p = base / "cache" / "observe_recordings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save(doc: dict[str, Any]) -> None:
    _path().write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _scrub(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop anything credential-shaped on the way IN. The page-side recorder already refuses to
    read a password field, so this should never fire — which is exactly why it is unconditional:
    the day it does fire is the day the other check was wrong."""
    out = []
    for e in events or []:
        e = dict(e)
        target = e.get("target") or {}
        looks_secret = (
            (target.get("type") or "").lower() == "password"
            or _SECRET_HINT.search(" ".join(str(target.get(k) or "") for k in ("id", "label", "ph")))
        )
        if looks_secret:
            if "value" in e:
                e["value"] = _SECRET_MARKER
            if e.get("k") in ("keydown", "keyup") and e.get("key") not in (None, "<secret>"):
                e["key"] = "<secret>"
        out.append(e)
    return out


def record(session_id: Any, payload: dict[str, Any], *, note: str = "") -> dict[str, Any]:
    """Store one drained window. Returns the stored record's header (never the whole thing)."""
    key = str(session_id)
    events = _scrub(payload.get("events") or [])
    rec = {
        "id": f"{key}-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "session_id": key,
        "note": note,
        "stored_at": _now(),
        "duration_ms": payload.get("duration_ms"),
        "dropped": payload.get("dropped", 0),
        "active_at_start": payload.get("activeAtStart"),
        "active_at_stop": payload.get("activeAtStop"),
        "events": events,
    }
    with _lock:
        doc = _load()
        entry = doc.get(key) or []
        entry.append(rec)
        doc[key] = entry[-MAX_RECORDINGS_PER_SESSION:]
        _save(doc)
    return header(rec)


def summarize(rec: dict[str, Any]) -> dict[str, Any]:
    """Counts by kind, and the handful of lines a human actually reads first. A VIEW — computed on
    read, so the stored events stay exactly what the page emitted."""
    events = rec.get("events") or []
    kinds: dict[str, int] = {}
    for e in events:
        kinds[e.get("k", "?")] = kinds.get(e.get("k", "?"), 0) + 1
    # The interaction spine: what the driver/human did and how the page answered. Mutations are the
    # bulk and rarely the story, so they are counted but not surfaced here.
    spine = [e for e in events if e.get("k") in ("click", "focus", "blur", "keydown", "input",
                                                 "change")]
    return {"kinds": kinds, "count": len(events), "spine": spine[:60],
            "spine_truncated": max(0, len(spine) - 60)}


def header(rec: dict[str, Any]) -> dict[str, Any]:
    """Everything except the events — what a list shows."""
    s = summarize(rec)
    return {k: rec.get(k) for k in ("id", "session_id", "note", "stored_at", "duration_ms",
                                    "dropped", "active_at_start", "active_at_stop")} | {
        "count": s["count"], "kinds": s["kinds"]}


def list_for(session_id: Any) -> list[dict[str, Any]]:
    return [header(r) for r in reversed(_load().get(str(session_id)) or [])]


def get(session_id: Any, recording_id: str) -> Optional[dict[str, Any]]:
    for r in _load().get(str(session_id)) or []:
        if r.get("id") == recording_id:
            return r
    return None


def latest(session_id: Any) -> Optional[dict[str, Any]]:
    recs = _load().get(str(session_id)) or []
    return recs[-1] if recs else None


def delete(session_id: Any, recording_id: str) -> bool:
    with _lock:
        doc = _load()
        recs = doc.get(str(session_id)) or []
        keep = [r for r in recs if r.get("id") != recording_id]
        if len(keep) == len(recs):
            return False
        doc[str(session_id)] = keep
        _save(doc)
        return True
