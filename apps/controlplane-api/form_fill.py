"""Form-fill planning — map a form's live fields to values, in a bunch.

Operator, 2026-07-24: *"let's do steps in bunches now… have it step through a bunch of easy steps
we can confirm."* Rung-by-rung was right for brand-new territory; a form of ten identity fields is
not brand-new once we can see it. This plans the WHOLE step at once — every field on the page
mapped to the value we would fill it with, WHERE that value comes from, and honestly which fields
we cannot fill because we have no data.

The plan is the deliverable, separate from executing it. Seeing "here is the whole step, here is
what I have, here is what is missing" is what lets the operator confirm a bunch instead of ten
single Go's — and the missing fields are surfaced, never guessed. An address we do not hold is a
blank to ask about, not a plausible-looking street to invent onto a real application.

Value precedence, most-authoritative first:
  1. **working variable** (`todays_date`, `availability_date`) — computed now, never stored
  2. **stored answer** — what the operator saved in the profile
  3. **identity default** — name/email from the account, and the source we came from
  4. **nothing** — flagged `needs_operator`, never filled with a guess

Pure: no I/O. `routers/session_control.py` scans the live form and executes what this plans.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

import working_variables as wv

#: A field's accessible name (lowercased, substring-matched) -> the answer_key it maps to. Ordered
#: most-specific first so "phone device type" wins over "phone", and "address line 2" is not caught
#: by "address". These names are the ones Workday actually renders; they recur across tenants
#: because the automation-ids behind them are shared, which is what makes the mapping generalize.
_FIELD_TO_KEY: tuple[tuple[str, str], ...] = (
    ("first name", "first_name"),
    ("last name", "last_name"),
    ("phone device type", "phone_device_type"),
    ("phone extension", None),                    # optional; never required, skip silently
    ("country phone code", "country_phone_code"),
    ("phone number", "phone"),
    ("phone", "phone"),
    ("address line 1", "street_address"),
    ("address line 2", None),
    ("street address", "street_address"),
    ("postal code", "postal_code"),
    ("zip", "postal_code"),
    ("city", "city"),
    ("state", "state"),
    ("country", "country"),
    ("email", "email"),
    ("how did you hear", "how_did_you_hear"),
    ("preferred name", None),
    ("when can you start", "availability_date"),
    ("available", "availability_date"),
    ("start date", "availability_date"),
    ("today", "todays_date"),
    ("date signed", "todays_date"),
)

#: Sources, for the plan's provenance column and the UI's colour.
SRC_WORKING, SRC_STORED, SRC_IDENTITY, SRC_MISSING, SRC_SKIP = (
    "working_variable", "stored", "identity", "missing", "skip")


def field_answer_key(field_name: str) -> Optional[str]:
    """The answer_key a field maps to, or None if it is one we deliberately skip / do not map."""
    n = " ".join((field_name or "").lower().split())
    for needle, key in _FIELD_TO_KEY:
        if needle in n:
            return key
    return None


def plan(fields: list[dict[str, Any]], *, answers: dict[str, Any], identity: dict[str, str],
         today: Optional[date] = None) -> list[dict[str, Any]]:
    """One row per fillable field: what we would put in it and why, or why we cannot.

    `fields` are the live form fields ({role, name}); `answers` maps answer_key -> stored value;
    `identity` carries the account-derived defaults (first_name, last_name, email) and the apply
    source (how_did_you_hear). Fields we do not recognise are left out entirely — this plans what
    we can speak to, and stays silent on the rest.
    """
    rows: list[dict[str, Any]] = []
    for f in fields:
        name = (f.get("name") or "").strip()
        role = (f.get("role") or "").lower()
        if not name or role not in ("textbox", "combobox", "checkbox"):
            continue
        key = field_answer_key(name)
        if key is None:
            continue
        value, source = _resolve(key, answers=answers, identity=identity, today=today)
        rows.append({
            "field": name, "role": role, "answer_key": key,
            "value": value, "source": source,
            "fillable": source in (SRC_WORKING, SRC_STORED, SRC_IDENTITY),
            "widget": "select" if role == "combobox" else "text",
        })
    return rows


def _resolve(key: str, *, answers: dict[str, Any], identity: dict[str, str],
             today: Optional[date]) -> tuple[Optional[str], str]:
    """(value, source) for one answer_key, by the precedence in the module docstring."""
    if wv.is_working_variable(key):
        return wv.resolve(key, today=today), SRC_WORKING
    stored = answers.get(key)
    if stored not in (None, "", "None"):
        return str(stored), SRC_STORED
    ident = identity.get(key)
    if ident:
        return ident, SRC_IDENTITY
    return None, SRC_MISSING


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The header for the bunch: how many we can fill, and which fields need the operator."""
    fillable = [r for r in rows if r["fillable"]]
    missing = [r["field"] for r in rows if not r["fillable"]]
    return {"total": len(rows), "fillable": len(fillable), "missing": missing,
            "by_source": {s: sum(1 for r in rows if r["source"] == s)
                          for s in (SRC_WORKING, SRC_STORED, SRC_IDENTITY, SRC_MISSING)}}
