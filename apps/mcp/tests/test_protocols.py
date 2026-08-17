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
from interaction.contract import Outcome


@pytest.fixture(autouse=True)
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    # The dialect store keys on MCP_OUTPUT_DIR; without this, a test's verified win writes into
    # the LIVE store and a later test reads it as a learned prior — a cross-test leak measured
    # the day the store was born (greenhouse::react_select, 4 phantom wins).
    monkeypatch.setenv("MCP_OUTPUT_DIR", str(tmp_path))
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
        if "HTMLSelectElement.prototype" in expr:   # only the native-select protocol sets via it
            return default
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
def test_select_option_cycles_an_unknown_widget_and_reports_every_attempt(corpus, monkeypatch):
    """The refusal, superseded by the dialect cycle (operator, 2026-08-11).

    The old rule — "widget_type=unknown → refuse" — existed because a GUESSED protocol acted
    blind. The cycle is not a guess: each candidate verifies at the widget's own truth before
    claiming anything, fails cleanly on the wrong shape, and the whole attempt list rides out
    in `tried`. What survives from the old rule is its spirit: nothing here reports a success
    that did not happen, and an unknown widget that defeats every protocol comes back a loud,
    enumerated failure — not a silent shrug and not a lucky click.
    """
    wire_cdp(monkeypatch, lambda e: {"found": True, "widget_type": "unknown"})
    out = asyncio.run(ms.select_option(ms.SelectOptionRequest(
        selector="#weird", value="Yes", ats="greenhouse", field="mystery")))
    assert out["ok"] is False
    assert [t["protocol"] for t in out["tried"]] \
        == ["native_select", "aria_listbox", "react_select", "text_menu"]
    assert all(t["outcome"] != "ok" for t in out["tried"])
    # The strongest hypothesis's failure is the reported verdict; the journal keeps the row.
    # LAST row, not first: the cycle's aria attempt runs through /widget_select, which journals
    # its own inner row before select_option's lands.
    assert out["via_protocol"] == "native_select"
    assert rows(corpus)[-1]["widget_type"] == "unknown"


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
def _segmented(reads):
    """A CDP responder for the segmented-date protocol: classify, then the one write+read call."""
    def responder(expr):
        if "dateSection" in expr and "HTMLInputElement" in expr:
            return {"found": True, **reads}
        return {"found": True, "widget_type": "segmented_date"}
    return responder


def test_set_date_drives_the_workday_segmented_date_without_typing_a_keystroke(corpus, monkeypatch):
    """Was BLOCKED, and the refusal was right about TYPING.

    The sub-fields are linked and auto-advance, so CDP click+type+backspace scrambles across them
    ('12//', '//2012'). Writing each segment through the native value setter has no keystrokes for
    the auto-advance to race — the same primitive the `month_year` year input has always used here.
    """
    seen: list[str] = []

    def responder(expr):
        seen.append(expr)
        if "dateSection" in expr and "HTMLInputElement" in expr:
            return {"found": True, "month": {"value": "09"}, "day": {"value": "01"},
                    "year": {"value": "2026"}}
        return {"found": True, "widget_type": "segmented_date"}

    wire_cdp(monkeypatch, responder)
    out = asyncio.run(ms.set_date(ms.SetDateRequest(
        selector="#q--date", month=9, day=1, year=2026, ats="workday")))
    assert out["ok"] is True and out["outcome"] == "ok"
    assert "09/01/2026" in out["detail"]
    # The whole point: the segments are WRITTEN, never typed. One call, native setter, no keys.
    writes = [e for e in seen if "dateSection" in e and "HTMLInputElement" in e]
    assert len(writes) == 1, "three round trips would let the widget reformat between segments"
    assert "HTMLInputElement.prototype, 'value'" in writes[0]
    assert "dispatchKeyEvent" not in writes[0] and "insertText" not in writes[0]


def test_a_segmented_date_that_did_not_all_take_is_never_reported_as_set(corpus, monkeypatch):
    """The old BLOCKED was protecting against '12//' being reported as success. That bar stays:
    every segment is re-read after all three are written, and a partial date says so."""
    wire_cdp(monkeypatch, _segmented({"month": {"value": "12"}, "day": {"value": ""},
                                      "year": {"value": "2026"}}))
    out = asyncio.run(ms.set_date(ms.SetDateRequest(
        selector="#q--date", month=9, day=1, year=2026, ats="workday")))
    assert out["ok"] is False and out["outcome"] == "not_staged"
    assert "month reads '12'" in out["detail"] and "day reads ''" in out["detail"]
    assert "PARTIAL" in out["detail"]


def test_a_padded_segment_is_the_same_date_not_a_failure(corpus, monkeypatch):
    """Workday pads Month/Day to two digits; a string compare would fail a correct date."""
    wire_cdp(monkeypatch, _segmented({"month": {"value": "09"}, "day": {"value": "01"},
                                      "year": {"value": "2026"}}))
    out = asyncio.run(ms.set_date(ms.SetDateRequest(
        selector="#q--date", month=9, day=1, year=2026, ats="workday")))
    assert out["ok"] is True


def test_a_segmented_date_without_a_day_is_refused_rather_than_half_set(corpus, monkeypatch):
    wire_cdp(monkeypatch, _segmented({}))
    out = asyncio.run(ms.set_date(ms.SetDateRequest(
        selector="#q--date", month=9, year=2026, ats="workday")))
    assert out["ok"] is False and out["outcome"] == "no_option"
    assert "needs a day" in out["detail"]


def test_a_segmented_date_whose_sub_inputs_are_absent_is_not_found(corpus, monkeypatch):
    def responder(expr):
        if "dateSection" in expr and "HTMLInputElement" in expr:
            return {"found": False, "detail": "no Day sub-input"}
        return {"found": True, "widget_type": "segmented_date"}
    wire_cdp(monkeypatch, responder)
    out = asyncio.run(ms.set_date(ms.SetDateRequest(
        selector="#q--date", month=9, day=1, year=2026, ats="workday")))
    assert out["ok"] is False and out["outcome"] == "not_found"
    assert "no Day sub-input" in out["detail"]


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


def test_a_segment_that_holds_the_value_but_is_marked_invalid_is_not_a_set_date(corpus, monkeypatch):
    """WORKDAY_LESSONS: on Workday text fields the React value-setter "leaves aria-invalid=true;
    must TYPE". Right-looking and rejected is worse than empty — only one of those is visible."""
    wire_cdp(monkeypatch, _segmented({"month": {"value": "09", "invalid": "true"},
                                      "day": {"value": "01"}, "year": {"value": "2026"}}))
    out = asyncio.run(ms.set_date(ms.SetDateRequest(
        selector="#q--date", month=9, day=1, year=2026, ats="workday")))
    assert out["ok"] is False and out["outcome"] == "not_staged"
    assert "aria-invalid" in out["detail"]


def test_the_date_field_itself_being_invalid_fails_even_when_every_segment_reads_right(corpus,
                                                                                       monkeypatch):
    wire_cdp(monkeypatch, _segmented({"month": {"value": "09"}, "day": {"value": "01"},
                                      "year": {"value": "2026"}, "container_invalid": "true"}))
    out = asyncio.run(ms.set_date(ms.SetDateRequest(
        selector="#q--date", month=9, day=1, year=2026, ats="workday")))
    assert out["ok"] is False and "date field itself" in out["detail"]

# --- the text-menu protocol (role-less, tap-driven menus) ----------------------------------------
class _FakeCDP:
    """Routes Runtime.evaluate by which of the protocol's three expressions it is, and records
    every dispatched mouse event so a test can assert the taps actually happened."""

    def __init__(self, *, opened, hit, reads):
        self.opened, self.hit = opened, hit
        self.reads = list(reads)          # successive values of the opener's label
        self.taps: list[tuple[int, int]] = []

    async def send(self, method, params=None):
        params = params or {}
        if method == "Input.dispatchMouseEvent":
            if params.get("type") == "mousePressed":
                self.taps.append((params.get("x"), params.get("y")))
            return {}
        if method != "Runtime.evaluate":
            return {}
        expr = params.get("expression", "")
        if "opener has no box" in expr:
            return {"result": {"value": self.opened}}
        if "tapTarget" in expr:
            return {"result": {"value": self.hit}}
        return {"result": {"value": self.reads.pop(0) if self.reads else None}}


def _run_text_menu(**kw):
    from app.protocols import text_menu_pick
    cdp = _FakeCDP(**kw)
    outcome, steps, detail = asyncio.run(
        text_menu_pick(cdp, selector="[data-id=q]", value="No", settle_seconds=0))
    return cdp, outcome, steps, detail


def test_text_menu_taps_open_taps_the_option_and_confirms_at_the_opener():
    """WAHVE, measured 2026-08-17: React 15 + Material-UI v0 renders no <select>, no role and no
    shadow root — the opener is a div and the items are bare divs inside a span[tabindex]. Both
    gestures must be TRUSTED mouse events: MUI v0 rides react-tap-event-plugin, which synthesises
    its tap from real mousedown+mouseup, so a JS .click() highlights the row and commits nothing.
    """
    cdp, outcome, steps, detail = _run_text_menu(
        opened={"ok": True, "x": 500, "y": 300, "before": "Select"},
        hit={"found": True, "count": 40, "x": 517, "y": 523},
        reads=["No"])
    assert outcome is Outcome.OK
    assert cdp.taps == [(500, 300), (517, 523)], "open, then the option — both as real taps"
    assert "verified at the opener" in detail
    assert steps[-1]["value_read_at"] == ".dropdown-label"


def test_text_menu_reports_an_unchanged_opener_rather_than_claiming_the_pick():
    """The failure mode this whole family is prone to: the mechanism completes and the value
    never lands. Compared against the VALUE, not merely against `before` — a menu that closed on
    the wrong item also changes the label, and "it moved" is not "it is right"."""
    _, outcome, _, detail = _run_text_menu(
        opened={"ok": True, "x": 500, "y": 300, "before": "Select"},
        hit={"found": True, "count": 40, "x": 517, "y": 523},
        reads=["Select"])
    assert outcome is Outcome.NOT_STAGED
    assert "unchanged" in detail


def test_text_menu_calls_a_wrong_commit_not_staged_too():
    _, outcome, _, detail = _run_text_menu(
        opened={"ok": True, "x": 500, "y": 300, "before": "Select"},
        hit={"found": True, "count": 40, "x": 517, "y": 523},
        reads=["Yes"])
    assert outcome is Outcome.NOT_STAGED
    assert "'Yes'" in detail and "unchanged" not in detail


def test_text_menu_separates_nothing_rendered_from_your_word_is_not_here():
    """The distinction `aria_listbox` got wrong on this very widget: it reported `not_opened`
    about a menu that was plainly open on the screenshot, because absence of a SELECTOR match was
    being reported as absence of a popup. Two different claims, two different caller moves."""
    _, outcome, _, _ = _run_text_menu(
        opened={"ok": True, "x": 5, "y": 5, "before": "Select"},
        hit={"found": False, "count": 0}, reads=[])
    assert outcome is Outcome.NOT_OPENED

    _, outcome2, _, detail2 = _run_text_menu(
        opened={"ok": True, "x": 5, "y": 5, "before": "Select"},
        hit={"found": False, "count": 40, "sample": ["Yes", "Maybe"]}, reads=[])
    assert outcome2 is Outcome.NO_OPTION
    assert "Yes" in detail2


def test_text_menu_refuses_two_tap_targets_wearing_the_same_words():
    """A loose match is a guess wearing a structural costume — and matching by visible TEXT is
    the loosest addressing in this codebase, so it owes the strictest ambiguity refusal."""
    cdp, outcome, _, detail = _run_text_menu(
        opened={"ok": True, "x": 5, "y": 5, "before": "Select"},
        hit={"found": False, "ambiguous": 2, "sample": ["No", "No"]}, reads=[])
    assert outcome is Outcome.AMBIGUOUS
    assert "refusing to guess" in detail
    assert cdp.taps == [(5, 5)], "opened, and then refused — never a second tap"


def test_text_menu_says_so_when_the_opener_is_not_there():
    _, outcome, _, detail = _run_text_menu(
        opened={"ok": False, "detail": "no node matching [data-id=q]"}, hit={}, reads=[])
    assert outcome is Outcome.NOT_FOUND
    assert "no node matching" in detail
