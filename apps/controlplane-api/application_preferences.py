"""Application preferences — the operator's notes for applying, attached to the career-search domain.

As we work through applications, the operator states preferences ("target ~$130k", "no
sponsorship", "these two roles don't match, skip"). Those are operator-owned guidance, not training
data and not something Claude decides — so, like domain_settings.py, they live in one small
JSON doc (`<artifacts>/cache/application_preferences.json`) keyed by domain (default:
career_search). The apply loop reads these to shortlist/skip and to fill answers; the operator
appends to them over time.

Two shapes in one doc:
  * `structured` — durable, machine-usable preferences (comp target, sponsorship, onsite days,
    demographics stance) the shortlister/filler can key off directly.
  * `notes` — free-text, append-only observations ("Knipper Sr BI doesn't match — too BI/pharma
    ops"), each with a category + source, so the reasoning behind a skip/pick is captured for the
    teacher and for future triage.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from settings import settings

_lock = threading.Lock()

DEFAULT_DOMAIN = "career_search"


def _path() -> Path:
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parent / base).resolve()
    p = base / "cache" / "application_preferences.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# Seeded once (on first access of an empty doc) from what the operator has already told us
# (user_job_application_preferences). Editable at runtime via add_note / set_structured.
def _seed() -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        DEFAULT_DOMAIN: {
            "domain_id": DEFAULT_DOMAIN,
            "structured": {
                "target_total_comp_usd": 130000,
                "onsite_days_per_week": [1, 2],
                "needs_visa_sponsorship": False,
                "demographics_eeo": "decline",
                "consents_ok": ["privacy_policy", "background_check"],
                "role_focus": ["senior data engineer", "data warehouse", "reporting analyst",
                               "business intelligence"],
                "base_location": "Nashua, NH",
                "handpick_is_approval": True,
            },
            "notes": [
                {"id": 1, "text": "Handpick = approval to apply to that specific job.",
                 "category": "process", "source": "operator", "created_at": now},
                {"id": 2, "text": "Target ~$130k; prefer 1-2 onsite days; NO visa sponsorship; "
                 "decline demographics/EEO; OK to consent to privacy policy + background check.",
                 "category": "fit", "source": "operator", "created_at": now},
            ],
            "updated_at": now,
        }
    }


def _load() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        doc = _seed()
        _save(doc)
        return doc
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data else _seed()
    except Exception:
        return _seed()


def _save(doc: dict[str, Any]) -> None:
    _path().write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _domain(doc: dict[str, Any], domain_id: str) -> dict[str, Any]:
    d = doc.get(domain_id)
    if not isinstance(d, dict):
        d = {"domain_id": domain_id, "structured": {}, "notes": []}
        doc[domain_id] = d
    d.setdefault("structured", {})
    d.setdefault("notes", [])
    return d


def list_preferences(domain_id: str = DEFAULT_DOMAIN) -> dict[str, Any]:
    """The full preferences doc for a domain (structured + notes), always complete."""
    return _domain(_load(), domain_id)


def add_note(text: str, category: str = "fit", source: str = "operator",
             domain_id: str = DEFAULT_DOMAIN) -> dict[str, Any]:
    """Append an application-preference note. Returns the created note."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "detail": "empty note"}
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        doc = _load()
        d = _domain(doc, domain_id)
        nid = (max((n.get("id", 0) for n in d["notes"]), default=0) + 1)
        note = {"id": nid, "text": text, "category": category, "source": source, "created_at": now}
        d["notes"].append(note)
        d["updated_at"] = now
        _save(doc)
    return {"ok": True, **note}


def set_structured(updates: dict[str, Any], domain_id: str = DEFAULT_DOMAIN) -> dict[str, Any]:
    """Merge structured preferences (comp target, sponsorship, …). Returns the merged structured."""
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        doc = _load()
        d = _domain(doc, domain_id)
        d["structured"].update(updates or {})
        d["updated_at"] = now
        _save(doc)
    return d["structured"]


def preferences_spec() -> dict[str, Any]:
    """What GET /api/career_search/application_preferences returns."""
    return _load()
