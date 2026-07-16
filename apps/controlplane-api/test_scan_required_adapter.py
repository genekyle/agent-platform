"""_scan_required_fields: /scan_required's rows -> build_form_state's shape -> the gate.

This adapter feeds `form_complete_gate`, the invariant that makes the model structurally
unable to mark a form done with an empty required field. It replaced `/scan_form`, so the
thing worth pinning is that the gate's VERDICT is preserved across the narrowing (every
field -> only the unsatisfied required ones).

The live KKR form validated the happy path on 2026-07-16 (1 unanswered attestation ->
gate ok=False, correctly). It had nothing INVALID on it, so the filled-but-invalid path —
the one that would silently drop a blocker — is only covered here.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import main
from apply_state_store import build_form_state, form_complete_gate


def _fake_post(payload, *, status: int = 200, raises: bool = False):
    """Stub httpx.AsyncClient so the adapter sees `payload` from /scan_required."""
    class _Resp:
        status_code = status

        def raise_for_status(self):
            if status >= 400:
                raise httpx.HTTPStatusError("boom", request=None, response=None)

        def json(self):
            return payload

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            if raises:
                raise httpx.ConnectError("capture server down")
            return _Resp()

    return _Client


def _run(payload, **kw):
    import httpx as _httpx
    orig = _httpx.AsyncClient
    _httpx.AsyncClient = _fake_post(payload, **kw)
    try:
        return asyncio.run(main._scan_required_fields("http://127.0.0.1:9322"))
    finally:
        _httpx.AsyncClient = orig


def test_an_unanswered_required_field_blocks_the_gate():
    # The live KKR case, frozen: one unanswered react-select attestation.
    ff = _run({"ok": True, "unanswered": [{
        "field": "I confirm that my application materials...", "selector": "#question_17811150004",
        "kind": "react_select", "required_via": "aria-required",
        "value_read_at": "[class*=singleValue]", "answered": False, "valid": True,
        "value_preview": ""}]})
    gate = form_complete_gate(build_form_state(ff))
    assert gate.ok is False
    assert gate.unsatisfied[0]["field_id"] == "#question_17811150004"
    assert gate.unsatisfied[0]["reason"] == "empty"


def test_an_empty_list_means_the_form_is_complete():
    """THE claim the narrowing rests on.

    /scan_form returned every field and the gate filtered; /scan_required returns only the
    unsatisfied ones. `ok = not unsatisfied`, so an empty list is "nothing blocks" — the same
    verdict, from a smaller input.
    """
    gate = form_complete_gate(build_form_state(_run({"ok": True, "unanswered": []})))
    assert gate.ok is True
    assert gate.unsatisfied == []


def test_a_filled_but_INVALID_required_field_still_blocks():
    """The regression the narrowing could have introduced.

    The gate's rule is `satisfied = (not required) or (filled and valid)`, so "filled but
    invalid" is a distinct blocker from "empty". If /scan_required only ever reported
    unanswered fields, an invalid value would vanish and the gate would pass a bad form.
    Hence `answered` is carried through rather than assumed False.
    """
    ff = _run({"ok": True, "unanswered": [{
        "field": "Email*", "selector": "#email", "kind": "input",
        "required_via": "required-attr", "value_read_at": ".value",
        "answered": True, "valid": False, "value_preview": "not-an-email"}]})
    assert ff[0]["filled"] is True and ff[0]["valid"] is False
    gate = form_complete_gate(build_form_state(ff))
    assert gate.ok is False
    assert gate.unsatisfied[0]["reason"] == "invalid"   # NOT "empty"


def test_every_row_is_marked_required_because_the_endpoint_only_returns_required_fields():
    ff = _run({"ok": True, "unanswered": [
        {"field": "A", "selector": "#a", "kind": "input", "answered": False},
        {"field": "B", "selector": None, "kind": "checkbox_group", "answered": False}]})
    assert all(f["required"] for f in ff)
    # A group with no id still needs a stable field_id — fall back to the label, or the gate
    # would key two unrelated blockers to the same empty string.
    assert ff[1]["field_id"] == "B"


def test_valid_defaults_true_when_the_scanner_omits_it():
    ff = _run({"ok": True, "unanswered": [{"field": "A", "selector": "#a", "answered": False}]})
    assert ff[0]["valid"] is True


def test_an_unreachable_scanner_returns_None_not_an_empty_list():
    """None and [] must not be confused: reconcile() treats None as "leave prior form_state"
    and [] as "the form is COMPLETE". A down capture server returning [] would silently
    unblock the gate — a safety failure wearing a plausible costume.
    """
    assert _run({}, raises=True) is None


def test_a_scanner_that_reports_not_ok_returns_None():
    assert _run({"ok": False, "detail": "target not found"}) is None
