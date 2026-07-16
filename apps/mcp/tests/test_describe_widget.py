"""/describe_widget's endpoint contract.

SCOPE, stated plainly: these test the Python half — how a classifier result maps onto the
outcome taxonomy and the journal. They do NOT test the DOM classifier in widget_probe.py.

That is deliberate, not laziness. The obvious move (jsdom) would be actively misleading
here: jsdom's `offsetParent` is always null, and the classifier's `vis()` helper is built on
it, so every element would read as invisible and every assertion would be validating a
fiction. PRINCIPLES §5 already says this — "deterministic detectors are cheap but easy to
get subtly wrong; validate them against the actual live pages before trusting them" — and it
was written after being burned twice by exactly this (a reCAPTCHA bframe PRESENT ≠ SHOWN).

So the classifier is validated on the live drive, and `/describe_widget` is journaled, which
is what makes that validation cheap to audit afterwards: every classification lands in the
corpus with its widget_type, and a wrong one shows up as a downstream not_opened/not_staged
row on the same field.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from interaction.contract import WidgetType

import app.main_server as ms


@pytest.fixture(autouse=True)
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    return tmp_path


def rows(tmp_path):
    p = tmp_path / "cache" / "intent_journal.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def fake_cdp(value):
    """Stand in for the CDP round-trip, returning `value` from Runtime.evaluate."""
    class _Session:
        def __init__(self, ws):
            pass

        async def send(self, method, params=None):
            return {"result": {"value": value}}

    @asynccontextmanager
    async def _connect(url, **kw):
        yield object()

    async def _discover(browser_url, tab_id=None, tab_url=None):
        return {"webSocketDebuggerUrl": "ws://x"}

    return _Session, _connect, _discover


def call_describe(monkeypatch, value, **body_kw):
    Session, connect, discover = fake_cdp(value)
    import app.observer.ax_proposer as axp
    monkeypatch.setattr(axp, "_CDPSession", Session, raising=False)
    monkeypatch.setattr(axp, "_discover_target", discover, raising=False)
    monkeypatch.setattr("websockets.connect", connect, raising=False)
    body = ms.DescribeWidgetRequest(selector="#school--0", ats="greenhouse", field="school",
                                    **body_kw)
    return asyncio.run(ms.describe_widget(body))


REACT_SELECT = {
    "found": True, "widget_type": "react_select", "label": "School *",
    "opener": {"role": "combobox", "aria_expanded": "false", "accessible_name": "School"},
    "popup": {"ref": None, "source": "document", "option_count": None},
    "options": None, "options_enumerable_by": "open",
    "commit": {"kind": "on_select", "label": None},
    "value_read_at": "[class*=singleValue]", "opens_on": "keystrokes",
    "required": True, "required_via": "aria-required", "disabled": False,
    "answered": False, "value_preview": "", "companion_selector": None,
}


def test_a_classified_widget_is_ok_and_journals_its_type(corpus, monkeypatch):
    out = call_describe(monkeypatch, REACT_SELECT)
    assert out["ok"] is True and out["outcome"] == "ok"
    assert out["widget_type"] == "react_select"
    (row,) = rows(corpus)
    assert row["intent"] == "describe" and row["widget_type"] == "react_select"
    assert row["ats"] == "greenhouse" and row["field"] == "school"
    # The classification itself is the training signal — it must survive into the corpus.
    assert row["steps"][0]["value_read_at"] == "[class*=singleValue]"
    assert row["steps"][0]["opens_on"] == "keystrokes"


def test_the_widgets_own_account_reaches_the_caller(corpus, monkeypatch):
    out = call_describe(monkeypatch, REACT_SELECT)
    # value_read_at is the field that kills a whole bug class — verifying a react-select at
    # .value "confirmed" an empty field twice.
    assert out["value_read_at"] == "[class*=singleValue]" != ".value"
    assert out["opens_on"] == "keystrokes"
    assert out["commit"] == {"kind": "on_select", "label": None}
    assert out["required"] is True


def test_describe_is_read_only_so_options_are_null_until_something_opens_it(corpus, monkeypatch):
    out = call_describe(monkeypatch, REACT_SELECT)
    assert out["options"] is None
    assert out["options_enumerable_by"] == "open"
    # Read-only means it expands to no primitives — it must not claim to have acted.
    assert rows(corpus)[0]["actions"] == []


def test_a_missing_node_is_not_found_not_an_error(corpus, monkeypatch):
    out = call_describe(monkeypatch, {"found": False, "detail": "no node matching #gone"})
    assert out["ok"] is False and out["outcome"] == "not_found"
    assert rows(corpus)[0]["outcome"] == "not_found"


def test_an_unclassifiable_widget_is_a_real_answer_but_not_ok(corpus, monkeypatch):
    # `unknown` routes to /probe, and the probe's output becomes a new widget_type. But the
    # caller cannot ACT on unknown, so it must not read as success.
    out = call_describe(monkeypatch, {**REACT_SELECT, "widget_type": WidgetType.UNKNOWN.value})
    assert out["ok"] is False
    assert out["widget_type"] == "unknown"
    assert rows(corpus)[0]["widget_type"] == "unknown"


def test_a_disabled_field_is_not_required_even_though_the_asterisk_stays(corpus, monkeypatch):
    """The KKR trap, carried through the endpoint.

    Work End date keeps its '*' AND aria-required='true' after 'Current role' is ticked, but
    the input is `disabled` => not required. `required_via` records WHICH signal decided, so
    a future disagreement is auditable instead of mysterious.
    """
    out = call_describe(monkeypatch, {**REACT_SELECT, "required": False, "disabled": True,
                                      "required_via": "disabled", "label": "End date *"})
    assert out["required"] is False
    assert out["required_via"] == "disabled"
    assert "required=False (disabled)" in out["detail"]


def test_month_year_reports_its_companion_so_the_caller_doesnt_re_derive_it(corpus, monkeypatch):
    out = call_describe(monkeypatch, {**REACT_SELECT, "widget_type": "month_year",
                                      "companion_selector": "#start-date-year-0"})
    assert out["companion_selector"] == "#start-date-year-0"


def test_a_footer_commit_is_reported_so_the_caller_knows_select_only_stages(corpus, monkeypatch):
    out = call_describe(monkeypatch, {**REACT_SELECT, "widget_type": "aria_listbox",
                                      "commit": {"kind": "footer_button", "label": "Update"}})
    assert out["commit"]["kind"] == "footer_button"
    assert out["commit"]["label"] == "Update"
