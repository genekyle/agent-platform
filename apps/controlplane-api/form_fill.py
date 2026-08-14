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

import re
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
    # BARE "address", for forms that do not say "line 1" — SAP's candidate profile calls the
    # street field exactly "Address", so the most important address field on that form matched
    # nothing at all (measured live 2026-07-30: 11 of 34 controls recognised, this among the
    # misses). It MUST sit below the specific ones so "Address Line 2" still resolves to None,
    # and below "email" because "Email Address" contains it — put this line above ("email",
    # "email") and every SAP profile email becomes a street address.
    ("address", "street_address"),
    # "How did you hear" is a PROMPT, not a text field — it renders as a textbox but a free-typed
    # value is invalid (the bunch-fill left it red on 2026-07-24). It must be SELECTED via
    # apply_prompt_select (source-aware), so it is deliberately not mapped for text fill here.
    ("how did you hear", None),
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
    """The answer_key a field maps to, or None if it is one we deliberately skip / do not map.

    Matched on WORD BOUNDARIES, not bare substrings. The bare `in` matched "city" inside
    "Ethni-CITY", and the first fill plan drawn over a real EEO block mapped the ethnicity
    dropdown to the operator's home town — "Ethnicity: Concord" one Execute away from being an
    answer on a federal self-identification form (caught in the plan preview, live 2026-08-11 on
    Cornerstone). A needle must appear as whole words; "Email Address" still matches "email" and
    "Address Line 1" still matches "address line 1", but a needle buried inside another word is
    another word.
    """
    n = " ".join((field_name or "").lower().split())
    for needle, key in _FIELD_TO_KEY:
        if re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", n):
            return key
    return None


def plan(fields: list[dict[str, Any]], *, answers: dict[str, Any], identity: dict[str, str],
         today: Optional[date] = None) -> list[dict[str, Any]]:
    """One row per fillable field: what we would put in it and why, or why we cannot.

    `fields` are the live form fields ({role, name}); `answers` maps answer_key -> stored value;
    `identity` carries the account-derived defaults (first_name, last_name, email) and the apply
    source (how_did_you_hear). Fields we do not recognise are left out entirely — this plans what
    we can speak to, and stays silent on the rest.

    AMBIGUOUS NAMES ARE NOT FILLABLE. The executor addresses fields by accessible NAME, so when a
    page shows the same name more than once every one of those rows would type into whichever node
    resolves first — the same value, into one field, as many times as the name repeats, leaving the
    others empty and reporting success each time. SAP's profile with all sections open renders
    three "Country" controls and two "Company Name"s (measured live 2026-07-30). Such rows are kept
    and marked rather than dropped, because "we found it and cannot safely address it" is a
    different fact from "it is not there", and only the first one tells the operator what to do.
    """
    rows: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for f in fields:
        name = (f.get("name") or "").strip()
        role = (f.get("role") or "").lower()
        if not name or role not in ("textbox", "combobox", "checkbox"):
            continue
        key = field_answer_key(name)
        if key is None:
            continue
        seen[name] = seen.get(name, 0) + 1
        value, source = _resolve(key, answers=answers, identity=identity, today=today)
        rows.append({
            "field": name, "role": role, "answer_key": key,
            "value": value, "source": source,
            "fillable": source in (SRC_WORKING, SRC_STORED, SRC_IDENTITY),
            "widget": "select" if role == "combobox" else "text",
            # Rides along for fields the ACCESSIBLE NAME cannot address — a census-derived row
            # whose input is anonymous in the AX tree (Cornerstone's contact block). None for
            # AX-named fields, whose name is the address.
            "selector": f.get("selector"),
        })
    for r in rows:
        r["ambiguous"] = seen[r["field"]] > 1
        if r["ambiguous"]:
            r["fillable"] = False
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
    """The header for the bunch: how many we can fill, and which fields need the operator.

    `missing` is keyed on the SOURCE, not on `fillable`, and the distinction is load-bearing now
    that a row can be unfillable for a second reason. "We hold no address for you" is a request
    for data; "there are three controls called Country" is a request for a different addressing
    mode. Folding the two would send the operator to fill in a value they have already given.
    """
    fillable = [r for r in rows if r["fillable"]]
    missing = [r["field"] for r in rows if r["source"] == SRC_MISSING]
    ambiguous = sorted({r["field"] for r in rows if r.get("ambiguous")})
    # WHAT THIS PASS WILL ACTUALLY TYPE, counted apart from what it can plan.
    #
    # The bunch pass is text-only by design — dropdowns commit through their own widget protocol —
    # and the surface said so in prose two lines above a button reading "Fill the 6 ready
    # field(s)". It typed five. Measured live 2026-08-14 on Boston Children's: Country was planned
    # `fillable: true` with the right stored value, counted in the promise, and skipped by the
    # executor exactly as documented. The count and the promise disagreed, and the operator is the
    # one who finds out.
    #
    # A promise is what the presser will get, so it is counted from the same predicate the
    # executor loops on. `deferred` is not a failure and reads as its own line: those fields are
    # answerable, just through a different door.
    typed = [r for r in fillable if r.get("widget") == "text"]
    deferred = [r["field"] for r in fillable if r.get("widget") != "text"]
    return {"total": len(rows), "fillable": len(fillable), "missing": missing,
            "ambiguous": ambiguous,
            "will_type": len(typed), "deferred_to_widget": deferred,
            "by_source": {s: sum(1 for r in rows if r["source"] == s)
                          for s in (SRC_WORKING, SRC_STORED, SRC_IDENTITY, SRC_MISSING)}}


# ---------------------------------------------------------------------------------------------
# ACCORDION FORMS — reading whether the form is even open before believing what it says.
# ---------------------------------------------------------------------------------------------

def section_status(ats: str, candidates: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Which of this ATS's declared section bars are open, from one AX scan. None if flat.

    Pure on purpose: the browser-facing half is one `/ax_scan` call, and everything that can be
    got wrong here — matching a bar whose name carries a count, treating "not expandable" as
    "closed", calling a form empty that is merely shut — is decidable from the candidate list
    alone, so it is testable without a live page.

    Matching mirrors `_resolve_ax_node` (strip/lower, role-gated, exact then substring) because a
    bar this reports as open must be the same node /execute would click. `expanded=None` means
    the node never claimed to be expandable, which is reported as `unknown` and never folded into
    `closed` — a bar we cannot read is not a bar we know is shut.
    """
    import apply_fields
    decl = apply_fields.section_bars(ats)
    if decl is None:
        return None

    def nm(c: dict[str, Any]) -> str:
        return (c.get("caption") or c.get("name") or "").strip().lower()

    def find(role: Optional[str], name: str) -> Optional[dict[str, Any]]:
        want_role, want = (role or "").strip().lower(), (name or "").strip().lower()
        pool = [c for c in candidates
                if not want_role or (c.get("role") or "").strip().lower() == want_role]
        return next((c for c in pool if nm(c) == want),
                    next((c for c in pool if want and want in nm(c)), None))

    rows: list[dict[str, Any]] = []
    for key in decl["sections"]:
        entry = apply_fields.resolve(ats, key)
        hit = find(entry.get("role"), entry.get("name") or "")
        exp = hit.get("expanded") if hit else None
        rows.append({
            "field": key,
            # The LIVE name, count and all — and it must read BOTH keys. The raw proposer calls it
            # `caption`, the /ax_scan endpoint projects that to `name`, and this function is fed
            # from the endpoint. Reading only `caption` silently fell back to our generic name, so
            # the operator saw "Jobs Applied" where the page said "Jobs Applied (2)".
            "label": (hit or {}).get("caption") or (hit or {}).get("name") or entry.get("name"),
            "present": hit is not None,
            "state": "unknown" if hit is None or exp is None else ("open" if exp else "closed"),
        })
    closed = [r for r in rows if r["state"] == "closed"]
    return {
        "ats": ats,
        "sections": rows,
        "closed": [r["field"] for r in closed],
        "open": [r["field"] for r in rows if r["state"] == "open"],
        "unknown": [r["field"] for r in rows if r["state"] == "unknown"],
        "expand_all": decl.get("expand_all"),
        "all_open": bool(rows) and not closed,
        "page": decl.get("page"),
    }


def sections_caveat(status: Optional[dict[str, Any]], planned: int) -> str:
    """The sentence a fill plan owes the operator when the form was read shut.

    Empty string when there is nothing to warn about. This exists so that "0 fields" and "0
    fields because nine sections are closed" cannot read the same on screen — the whole failure
    was a confident, accurate summary of a page nobody had opened.
    """
    if not status or not status["closed"]:
        return ""
    n = len(status["closed"])
    return (f"{n} section{'s' if n != 1 else ''} still closed — a closed section's fields are "
            f"absent from the scan entirely, so this plan describes only what is open"
            + (". Nothing is open yet." if planned == 0 else f" ({planned} field(s))."))
