"""ListingDraft — the operator's inputs for a Marketplace listing + the fill provider.

"Create a listing based off said inputs" needs somewhere to hold those inputs and a way to
feed them into the create-listing flow. A ListingDraft is that record: the fields a
Facebook Marketplace item-for-sale listing needs (title, price, category, condition,
description, location, photos). The operator fills it in the dashboard; the runtime loop,
when driving the create-listing task, pulls each form field's value from the draft via
`value_for_field` (the same keyword-match idea as the application-answer autofill, kept
local because listings are a small, fixed field set).

Drafts persist to `<artifacts>/cache/listing_drafts.json`. Storage is intentionally simple
(a JSON list keyed by id) — this is operator-entered data, not high-volume.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from settings import settings

_lock = threading.Lock()

# Draft field -> substrings that identify the matching on-screen form field. First match wins,
# checked in this order so the more specific keys (e.g. "price") are tried before generic ones.
_FIELD_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("title", ("title", "what are you selling", "item name")),
    ("price", ("price", "cost", "amount")),
    ("category", ("category",)),
    ("condition", ("condition",)),
    ("description", ("description", "tell buyers", "details")),
    ("location", ("location", "city", "where")),
]

_REQUIRED = ("title", "price")


@dataclass
class ListingDraft:
    id: str
    title: str = ""
    price: str = ""
    category: str = ""
    condition: str = ""
    description: str = ""
    location: str = ""
    photos: list[str] = field(default_factory=list)   # local file paths to upload
    status: str = "draft"                              # draft | listed
    created_at: str = ""
    updated_at: str = ""

    def missing_required(self) -> list[str]:
        return [k for k in _REQUIRED if not (getattr(self, k) or "").strip()]


def value_for_field(draft: ListingDraft, question: str) -> Optional[str]:
    """Return the draft value for an on-screen form field identified by `question`
    (its label / aria-name / placeholder). None when nothing matches — the loop then
    escalates that field rather than typing a wrong value."""
    q = (question or "").lower()
    for key, keywords in _FIELD_KEYWORDS:
        if any(k in q for k in keywords):
            val = (getattr(draft, key, "") or "").strip()
            return val or None
    return None


# --- Persistence -------------------------------------------------------------
def _path() -> Path:
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parent / base).resolve()
    p = base / "cache" / "listing_drafts.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> list[dict[str, Any]]:
    p = _path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(rows: list[dict[str, Any]]) -> None:
    _path().write_text(json.dumps(rows, indent=2), encoding="utf-8")


def list_drafts() -> list[dict[str, Any]]:
    return _load()


def get_draft(draft_id: str) -> Optional[ListingDraft]:
    row = next((r for r in _load() if r.get("id") == draft_id), None)
    return ListingDraft(**row) if row else None


def upsert(data: dict[str, Any]) -> ListingDraft:
    """Create (no id) or update (matching id) a draft. Returns the stored record."""
    now = datetime.now(timezone.utc).isoformat()
    fields = {"title", "price", "category", "condition", "description", "location",
              "photos", "status"}
    clean = {k: v for k, v in (data or {}).items() if k in fields}
    with _lock:
        rows = _load()
        did = (data or {}).get("id")
        existing = next((r for r in rows if r.get("id") == did), None) if did else None
        if existing:
            existing.update(clean)
            existing["updated_at"] = now
            draft = ListingDraft(**existing)
        else:
            draft = ListingDraft(id=f"lst_{uuid.uuid4().hex[:10]}", created_at=now,
                                 updated_at=now, **clean)
            rows.append(asdict(draft))
        _save(rows)
    return draft
