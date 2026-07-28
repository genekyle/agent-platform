"""The session window RECORDER — what happened in this session's browser, and when.

`controller/window.py` is a pure function over a tab list: given a snapshot it names each tab's
role, finds duplicates, and plans hygiene. That is the right shape for deciding, and it is
deliberately stateless — it cannot answer the question that actually bit us:

    "A second window appeared. When? What was on screen when it opened? Who opened it —
     the drive, or the operator? And is it still there?"

A snapshot cannot answer any of that, because every one of those is a question about CHANGE.
Driving LinkedIn's Google SSO (session #22, 2026-07-27) made the gap concrete three times over: the
popup appeared and nothing recorded that it had; a native passkey prompt opened and expired with no
trace at all; and when the operator did something by hand mid-flow there was no record to reconcile
against. Each time the only account of what happened was whichever screenshot someone thought to
take.

So this module keeps a LEDGER. It takes successive snapshots of a session's tabs and turns the
differences into events — `opened`, `closed`, `navigated` — each stamped, each carrying the role
and the state the tab was in. Three properties matter:

* **It records anything, whoever did it.** A diff does not care whether the drive clicked, the
  operator clicked, or the site redirected on its own. That is the whole point of the operator's
  ask: *"we need to know what happens even if I misinput and something happens."* Nothing here is
  wired to our own actions, so nothing here can be blind to theirs.
* **It is free to run.** `/list_tabs` reads the browser's own `/json/list` over a local socket —
  no page load, no bandwidth (LOW_DATA_MODE). So it runs on every observation rather than behind a
  flag someone has to remember, and a recorder that only runs when you remembered to turn it on is
  not a recorder.
* **It never closes anything.** Same posture as `plan_hygiene`: report, and let the caller decide.

WHAT IT DELIBERATELY CANNOT SEE. A native OS dialog — the passkey prompt, a file picker, a
permission sheet — has no tab and no DOM, so no tab diff will ever show it. `/native_dialog` is the
probe for that class, and `openers` below records the moment a popup appeared so the two can be
correlated after the fact. Being explicit about the blind spot is the difference between a record
and a false alibi.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from controller import window as win
from settings import settings

_lock = threading.Lock()

#: Keep a session's tail, not its life. The ledger answers "what just happened here", and an
#: unbounded file on a long session turns every read into a scan.
MAX_EVENTS_PER_SESSION = 400

OPENED = "opened"
CLOSED = "closed"
NAVIGATED = "navigated"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path() -> Path:
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parent / base).resolve()
    p = base / "cache" / "session_windows.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — a corrupt ledger must not stop a drive
        return {}


def _save(doc: dict[str, Any]) -> None:
    _path().write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _short(url: str, limit: int = 160) -> str:
    return (url or "")[:limit]


def _snapshot(tabs: list[dict[str, Any]]) -> dict[str, str]:
    """tab_id -> url, the minimum needed to diff. Tabs without an id cannot be tracked across
    reads and are dropped rather than guessed at."""
    return {str(t.get("tab_id") or t.get("id") or ""): str(t.get("url") or "")
            for t in tabs or [] if (t.get("tab_id") or t.get("id"))}


def diff(before: dict[str, str], after: dict[str, str]) -> list[dict[str, Any]]:
    """The events between two snapshots. Pure, so the whole policy is testable without a browser.

    A tab whose URL changed is `navigated`, NOT closed+opened: it is the same window and conflating
    the two would invent a popup every time a page redirected — which on an SSO flow is constantly.
    """
    events: list[dict[str, Any]] = []
    for tab_id, url in after.items():
        if tab_id not in before:
            events.append({"kind": OPENED, "tab_id": tab_id, "url": _short(url),
                           "role": win.classify_tab(url)})
        elif before[tab_id] != url:
            events.append({"kind": NAVIGATED, "tab_id": tab_id, "url": _short(url),
                           "from_url": _short(before[tab_id]), "role": win.classify_tab(url)})
    for tab_id, url in before.items():
        if tab_id not in after:
            events.append({"kind": CLOSED, "tab_id": tab_id, "url": _short(url),
                           "role": win.classify_tab(url)})
    return events


def record(session_id: Any, tabs: list[dict[str, Any]], *,
           note: str = "", actor: str = "system") -> list[dict[str, Any]]:
    """Fold this snapshot into the session's ledger and return the NEW events.

    `note`/`actor` describe what the caller was doing when the snapshot was taken — "stepped the
    auth rung", "operator pressed sso_step" — which is what turns "a window appeared" into "a
    window appeared while we were doing X". Best-effort throughout: recording is observation, and
    observation must never be the thing that breaks a drive.
    """
    key = str(session_id)
    after = _snapshot(tabs)
    try:
        with _lock:
            doc = _load()
            entry = doc.get(key) or {"session_id": key, "tabs": {}, "events": []}
            before = entry.get("tabs") or {}
            events = diff(before, after)
            for e in events:
                e["ts"] = _now()
                e["note"] = note
                e["actor"] = actor
            if events:
                entry["events"] = (entry.get("events") or []) + events
                entry["events"] = entry["events"][-MAX_EVENTS_PER_SESSION:]
            entry["tabs"] = after
            entry["updated_at"] = _now()
            doc[key] = entry
            _save(doc)
        return events
    except Exception:  # noqa: BLE001
        return []


def timeline(session_id: Any, limit: int = 100) -> list[dict[str, Any]]:
    """This session's events, newest last (a timeline reads forwards)."""
    entry = _load().get(str(session_id)) or {}
    return (entry.get("events") or [])[-limit:]


def current(session_id: Any) -> dict[str, str]:
    """The last snapshot we folded in — tab_id -> url."""
    return (_load().get(str(session_id)) or {}).get("tabs") or {}


def openers(session_id: Any, limit: int = 20) -> list[dict[str, Any]]:
    """Just the windows that APPEARED, newest last.

    The question an SSO flow actually asks — "did that click spawn a window, and which one?" — and
    the correlation point for a native dialog, which no tab diff can see on its own.
    """
    return [e for e in timeline(session_id, limit=MAX_EVENTS_PER_SESSION)
            if e.get("kind") == OPENED][-limit:]


def summarize(session_id: Any) -> dict[str, Any]:
    """A glanceable account of this session's window activity."""
    events = timeline(session_id, limit=MAX_EVENTS_PER_SESSION)
    tabs = current(session_id)
    counts: dict[str, int] = {}
    for e in events:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    return {
        "session_id": str(session_id),
        "open_tabs": len(tabs),
        "roles": {r: sum(1 for u in tabs.values() if win.classify_tab(u) == r)
                  for r in {win.classify_tab(u) for u in tabs.values()}},
        "events": counts,
        "last_event": events[-1] if events else None,
        "windows_opened": len([e for e in events if e["kind"] == OPENED]),
    }


def forget(session_id: Any) -> bool:
    """Drop a session's ledger — for a session that has been stopped and torn down."""
    with _lock:
        doc = _load()
        if str(session_id) not in doc:
            return False
        del doc[str(session_id)]
        _save(doc)
        return True
