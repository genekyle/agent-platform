"""The tier-2 protocol endpoints' contracts.

As with test_describe_widget, these test the PYTHON half — dispatch, outcome mapping, and
the refusals. The page-side JS is validated on the live drive (PRINCIPLES §5; jsdom's
offsetParent is always null, so a fake-DOM test here would validate a fiction).

The refusals are the most valuable tests in this file. Every bug on 2026-07-15 was something
reporting success that didn't happen, and the fix is an endpoint that would rather say
"I don't know" than guess.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

import app.main_server as ms


@pytest.fixture(autouse=True)
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    return tmp_path


def rows(tmp_path):
    p = tmp_path / "cache" / "intent_journal.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def route(*, describe=None, focus=None, option=None, single_value=None, year=None, default=None):
    """Build a Runtime.evaluate responder that dispatches on DISTINCTIVE markers.

    Naive substring matching does not work here and the reason is worth recording: the
    classifier JS (widget_probe.DESCRIBE_WIDGET_JS) itself CONTAINS the string
    "singleValue" — it is in its own VALUE_READ_AT table — so a responder keyed on
    "singleValue" answers the classify call with a value-read result. Each marker below is
    chosen to appear in exactly one of the expressions under test.
    """
    def responder(expr: str):
        if "VALUE_READ_AT" in expr:                 # only the classifier defines this table
            return describe
        if "el.focus()" in expr:                    # only _FOCUS_AND_OPEN_JS focuses
            return focus
        if "role=option" in expr:                   # only _find_option_js enumerates options
            return option
        if "closest('[class*=select__control]" in expr:   # only _read_single_value_js
            return single_value
        if "HTMLInputElement.prototype" in expr and "dispatchEvent" in expr:
            return year                             # the set_year expression
        return default
    return responder


def wire_cdp(monkeypatch, responder):
    """Route every Runtime.evaluate through `responder(expression) -> value`."""
    class _Session:
        def __init__(self, ws):
            pass

        async def send(self, method, params=None):
            if method == "Runtime.evaluate":
                return {"result": {"value": responder((params or {}).get("expression", ""))}}
            return {}

    @asynccontextmanager
    async def _connect(url, **kw):
        yield object()

    async def _discover(browser_url, tab_id=None, tab_url=None):
        return {"webSocketDebuggerUrl": "ws://x"}

    import app.observer.ax_proposer as axp
    monkeypatch.setattr(axp, "_CDPSession", _Session, raising=False)
    monkeypatch.setattr(axp, "_discover_target", _discover, raising=False)
    monkeypatch.setattr("websockets.connect", _connect, raising=False)


# --- /select_option -----------------------------------------------------------------
def test_select_option_refuses_to_guess_a_protocol_for_an_unknown_widget(corpus, monkeypatch):
    """The single most important refusal in the API.

    An unclassified widget driven by a guessed protocol is how every one of 2026-07-15's
    bugs started. Refusing is the whole point of dispatching on widget_type.
    """
    wire_cdp(monkeypatch, lambda e: {"found": True, "widget_type": "unknown"})
    out = asyncio.run(ms.select_option(ms.SelectOptionRequest(
        selector="#weird", value="Yes", ats="greenhouse", field="mystery")))
    assert out["ok"] is False
    assert out["outcome"] == "not_found"
    assert "refusing to guess" in out["detail"]
    assert rows(corpus)[0]["widget_type"] == "unknown"


def test_select_option_classifies_when_the_caller_doesnt_say(corpus, monkeypatch):
    # "dispatch on widget_type instead of the caller knowing" — the caller may omit it.
    wire_cdp(monkeypatch, route(
        describe={"found": True, "widget_type": "react_select",
                  "commit": {"kind": "on_select", "label": None}},
        focus={"ok": True, "x": 10, "y": 20, "expanded": "false"},
        option={"found": True, "text": "United States", "count": 3},
        single_value="United States"))
    out = asyncio.run(ms.select_option(ms.SelectOptionRequest(
        selector="#country", value="United States", ats="greenhouse", field="phone_country")))
    assert out["ok"] is True and out["widget_type"] == "react_select"
    assert rows(corpus)[0]["widget_type"] == "react_select"


def test_react_select_verifies_at_singlevalue_not_at_dot_value(corpus, monkeypatch):
    """The bug that bought this rule: verifying at .value 'confirmed' an empty field twice.

    Here the option click succeeds but singleValue stays empty — .value would have said
    fine. The protocol must call that not_staged.
    """
    wire_cdp(monkeypatch, route(
        focus={"ok": True, "x": 10, "y": 20, "expanded": "false"},
        option={"found": True, "text": "United States", "count": 3},
        single_value=""))          # the pick did NOT take
    out = asyncio.run(ms.select_option(ms.SelectOptionRequest(
        selector="#country", value="United States", widget_type="react_select")))
    assert out["ok"] is False
    assert out["outcome"] == "not_staged"
    assert "singleValue is empty" in out["detail"]
    assert out["steps"][-1]["value_read_at"] == "[class*=singleValue]"


def test_react_select_distinguishes_never_opened_from_word_not_in_the_list(corpus, monkeypatch):
    # Different caller moves: not_opened => the widget_type is wrong; no_option => it's a
    # vocabulary miss for /resolve_answer. Collapsing them is what made failures unactionable.
    wire_cdp(monkeypatch, route(
        focus={"ok": True, "x": 1, "y": 1, "expanded": "false"},
        option={"found": False, "count": 0, "sample": []}))
    out = asyncio.run(ms.select_option(ms.SelectOptionRequest(
        selector="#country", value="Atlantis", widget_type="react_select")))
    assert out["outcome"] == "not_opened"

    wire_cdp(monkeypatch, route(
        focus={"ok": True, "x": 1, "y": 1, "expanded": "false"},
        option={"found": False, "count": 12, "sample": ["Albania", "Algeria"]}))
    out = asyncio.run(ms.select_option(ms.SelectOptionRequest(
        selector="#country", value="Atlantis", widget_type="react_select")))
    assert out["outcome"] == "no_option"
    assert "resolve_answer" in out["detail"]


def test_react_select_catches_the_wrong_option_taking(corpus, monkeypatch):
    """/Concord/ picked 'Concordia, Entre Rios, Argentina' over 'Concord, New Hampshire'.

    Even with exact-match on the click, the commit check re-reads what actually landed.
    """
    wire_cdp(monkeypatch, route(
        focus={"ok": True, "x": 1, "y": 1, "expanded": "false"},
        option={"found": True, "text": "Concordia", "count": 5},
        single_value="Concordia, Entre Rios, Argentina"))
    out = asyncio.run(ms.select_option(ms.SelectOptionRequest(
        selector="#loc", value="Concord, New Hampshire", widget_type="react_select")))
    assert out["outcome"] == "not_staged"
    assert "wrong option took" in out["detail"]


# --- /check_group -------------------------------------------------------------------
def test_check_group_maps_its_code_onto_the_taxonomy(corpus, monkeypatch):
    wire_cdp(monkeypatch, lambda e: {"ok": True, "code": "ok", "detail": "checked", "log": [],
                                     "checked": ["No"]})
    out = asyncio.run(ms.check_group(ms.CheckGroupRequest(
        selector="#question_1[]_a", values=["No"], ats="greenhouse", field="restrictions")))
    assert out["ok"] is True and out["checked"] == ["No"]
    assert rows(corpus)[0]["widget_type"] == "checkbox_group"


def test_check_group_refuses_when_the_selector_spans_two_groups(corpus, monkeypatch):
    # Fail LOUD on ambiguity — refusing beats guessing (job-boards.greenhouse.io matched two
    # targets and refusing was right there too).
    wire_cdp(monkeypatch, lambda e: {"ok": False, "code": "ambiguous",
                                     "detail": "selector spans 2 checkbox groups"})
    out = asyncio.run(ms.check_group(ms.CheckGroupRequest(selector="#q", values=["No"])))
    assert out["ok"] is False and out["outcome"] == "ambiguous"


def test_check_group_reports_a_vocabulary_miss_with_what_was_available(corpus, monkeypatch):
    wire_cdp(monkeypatch, lambda e: {"ok": False, "code": "no_option",
                                     "detail": 'no option(s) ["Maybe"]',
                                     "options": ["Yes, non-compete", "No"]})
    out = asyncio.run(ms.check_group(ms.CheckGroupRequest(selector="#q", values=["Maybe"])))
    assert out["outcome"] == "no_option"
    # "No" must not have matched "Yes, non-compete" — exact match, and the caller gets to see
    # the real option list to resolve against.
    assert out["options"] == ["Yes, non-compete", "No"]


# --- /scan_required -----------------------------------------------------------------
def test_scan_required_reports_an_empty_form_as_a_real_answer(corpus, monkeypatch):
    wire_cdp(monkeypatch, lambda e: {"unanswered": [], "url": "https://x/apply"})
    out = asyncio.run(ms.scan_required(ms.ScanRequiredRequest(ats="greenhouse")))
    assert out["ok"] is True and out["count"] == 0
    assert out["detail"] == "all required fields answered"


def test_scan_required_lists_what_is_required_and_unanswered(corpus, monkeypatch):
    wire_cdp(monkeypatch, lambda e: {"unanswered": [
        {"field": "School *", "selector": "#school--0", "kind": "input",
         "required_via": "aria-required", "value_read_at": "[class*=singleValue]", "answered": False},
        {"field": "Work restrictions *", "selector": "#question_1", "kind": "checkbox_group",
         "required_via": "group", "value_read_at": "checked", "answered": False},
    ], "url": "https://x/apply"})
    out = asyncio.run(ms.scan_required(ms.ScanRequiredRequest(ats="greenhouse")))
    assert out["count"] == 2
    # The checkbox group is present — the old scan missed groups entirely.
    assert any(u["kind"] == "checkbox_group" for u in out["unanswered"])
    assert rows(corpus)[0]["intent"] == "scan_required"


# --- /set_date ----------------------------------------------------------------------
def test_set_date_refuses_the_workday_segmented_gap_instead_of_trying_anyway(corpus, monkeypatch):
    """WORKDAY_LESSONS lists this as an unsolved live gap.

    The sub-fields are linked and auto-advance; CDP typing scrambles across them ('12//',
    '//2012'). A protocol that tried anyway would produce garbage and report success.
    BLOCKED = escalate to the operator, which is the honest answer.
    """
    wire_cdp(monkeypatch, lambda e: {"found": True, "widget_type": "segmented_date"})
    out = asyncio.run(ms.set_date(ms.SetDateRequest(
        selector="[data-automation-id=dateSectionMonth-input]", month=3, year=2026, ats="workday")))
    assert out["ok"] is False
    assert out["outcome"] == "blocked"
    assert "route to the operator" in out["detail"]
    assert rows(corpus)[0]["outcome"] == "blocked"


def test_set_date_translates_a_month_number_to_the_name_the_widget_wants(corpus, monkeypatch):
    """Typing '08' into Greenhouse's month yields ZERO options — it wants 'August'.

    The caller says month=8 and never has to know that. That translation living in the API
    rather than in the caller's head is the entire thesis.
    """
    typed: list[str] = []
    base = route(
        describe={"found": True, "widget_type": "month_year",
                  "companion_selector": "#start-date-year-0"},
        focus={"ok": True, "x": 1, "y": 1, "expanded": "false"},
        option={"found": True, "text": "August", "count": 12},
        single_value="August", year="2015")

    def responder(expr):
        if "role=option" in expr:
            typed.append(expr)
        return base(expr)

    wire_cdp(monkeypatch, responder)
    out = asyncio.run(ms.set_date(ms.SetDateRequest(
        selector="#start-date-month-0", month=8, year=2015, ats="greenhouse",
        field="work_start_date")))
    assert out["ok"] is True
    assert "August 2015" in out["detail"]
    assert any("August" in e for e in typed), "the month must be searched by NAME, not '08'"
    assert not any('"08"' in e for e in typed), "typing '08' yields zero options"


def test_set_date_reports_a_half_set_date_rather_than_claiming_success(corpus, monkeypatch):
    # Month took, year input missing. Silently returning ok would leave a half-set date that
    # LOOKS filled — the exact silent-success shape.
    wire_cdp(monkeypatch, route(
        describe={"found": True, "widget_type": "month_year", "companion_selector": None},
        focus={"ok": True, "x": 1, "y": 1, "expanded": "false"},
        option={"found": True, "text": "August", "count": 12},
        single_value="August"))
    out = asyncio.run(ms.set_date(ms.SetDateRequest(selector="#m", month=8, year=2015)))
    assert out["ok"] is False and out["outcome"] == "not_found"
    assert "HALF SET" in out["detail"]


def test_set_date_rejects_an_impossible_month_before_touching_the_page(corpus, monkeypatch):
    wire_cdp(monkeypatch, lambda e: {})
    out = asyncio.run(ms.set_date(ms.SetDateRequest(selector="#m", month=13, year=2015)))
    assert out["ok"] is False and out["outcome"] == "no_option"
