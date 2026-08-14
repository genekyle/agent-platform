"""Every JS blob that resolves a TARGET must carry the shared tells.

`__findAll` (frame-aware matching) lives in `app/js_common.py` and is injected into each blob by
substituting a `__WIDGET_TELLS__` placeholder. A blob that calls `__findAll` without it is not a
syntax error at import time — it is a page-side `ReferenceError` at CALL time, which comes back as
`Runtime.evaluate` returning no value, which the endpoints render as a bare `outcome: "error"`
with an EMPTY detail. It reads exactly like a stale recipe blaming the page.

CHECK_GROUP_JS shipped that way (2026-08-11 → 2026-08-13), so the required-consent checkbox step
never ran on any tenant. It surfaced as a Workday signup that filled email/password/verify and
then refused to submit — which the operator reasonably read as "it couldn't find the Create
Account button". One missing line, invisible for two days, found only by driving it.

This is the enforcement point: the module is scanned as text, so a new blob cannot forget.
"""
import re

from app import protocols


def _blobs():
    return {name: value for name, value in vars(protocols).items()
            if name.endswith("_JS") and isinstance(value, str)}


def test_every_blob_that_calls_findall_has_its_tells_injected():
    offenders = [name for name, src in _blobs().items()
                 if "__findAll" in src and "const __findAll" not in src]
    assert not offenders, (
        f"{offenders} call __findAll without the shared tells — every call will throw "
        f"ReferenceError on the page and report a bare 'error'. Add `__WIDGET_TELLS__` to the "
        f"blob and substitute it at the bottom of protocols.py."
    )


def test_no_placeholder_is_left_unsubstituted():
    left = [name for name, src in _blobs().items() if "__WIDGET_TELLS__" in src]
    assert not left, f"{left} still contain an unsubstituted __WIDGET_TELLS__ placeholder"


def test_check_group_reports_a_missing_control_as_not_found():
    """`not_found` is what lets a caller tell "there is no such box" from "the box would not
    tick" — the distinction a consent step has to make, because tenants of one ATS differ on
    whether the box exists at all."""
    assert re.search(r"code:\s*'not_found'", protocols.CHECK_GROUP_JS)


# --------------------------------------------------------------- role/name target resolution
# `_resolve_ax_node` ranks exact -> leading -> anywhere. The middle tier is load-bearing: a field's
# accessible name is its label plus the widget's decoration ("State Select One Required"), so a
# prefix reaches the real control while a bare substring reaches any field whose VALUE contains the
# word. Live 2026-08-13: asking for "State" opened the COUNTRY prompt, because "state" sits inside
# "Country United States of America Required" and that node came first.
import asyncio
from unittest.mock import patch

import app.main_server as ms

_WORKDAY_MY_INFORMATION = [
    {"role": "button", "name": "Country United States of America Required", "backend_node_id": 1},
    {"role": "button", "name": "State Select One Required", "backend_node_id": 2},
    {"role": "button", "name": "Phone Device Type Mobile Required", "backend_node_id": 3},
]


def _resolve(name, role=None, cands=None):
    rows = _WORKDAY_MY_INFORMATION if cands is None else cands

    async def _fake(**_kw):
        return rows

    with patch("app.observer.ax_proposer.propose_ax_candidates", _fake):
        return asyncio.run(ms._resolve_ax_node("http://x", None, None, role, name))


def test_a_label_that_leads_beats_one_that_merely_contains():
    assert _resolve("State") == 2, "'state' is inside 'United States' — the leading match wins"
    assert _resolve("Country") == 1
    assert _resolve("Phone Device Type") == 3


def test_an_exact_name_still_wins_outright():
    assert _resolve("State", cands=[
        {"role": "button", "name": "State Select One", "backend_node_id": 9},
        {"role": "button", "name": "State", "backend_node_id": 7}]) == 7


def test_a_substring_is_still_reachable_when_nothing_leads():
    assert _resolve("State", cands=[
        {"role": "button", "name": "Please choose your State now", "backend_node_id": 5}]) == 5


def test_a_name_that_leads_two_controls_is_refused_not_guessed():
    """The incident this rule is written from: on Workday's My Information "Country" LEADS both
    'Country State of Palestine Required' and 'Country Phone Code'. The first match won silently
    and changed the operator's Country out from under them, re-rendering the form localized.
    A tier with more than one candidate means the name does not identify a control."""
    assert _resolve("Country", cands=[
        {"role": "button", "name": "Country United States of America Required", "backend_node_id": 1},
        {"role": "textbox", "name": "Country Phone Code", "backend_node_id": 4}]) is None


def test_an_unambiguous_name_still_resolves_next_to_its_neighbours():
    """The refusal must not swallow the ordinary case — 'Country Phone Code' names exactly one."""
    cands = [
        {"role": "button", "name": "Country United States of America Required", "backend_node_id": 1},
        {"role": "textbox", "name": "Country Phone Code", "backend_node_id": 4}]
    assert _resolve("Country Phone Code", cands=cands) == 4
    assert _resolve("State", cands=cands + [
        {"role": "button", "name": "State Select One Required", "backend_node_id": 2}]) == 2
