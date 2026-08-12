"""The dialect store — a site speaks one way, and the first widget teaches the rest.

Every case is the operator's 2026-08-11 thesis made checkable: candidates are offered in
learned → classified → cheapest order, impossibilities are dropped by tag, a win locks the
dialect, and a displaced dialect stays on the record.
"""

import importlib

import pytest


@pytest.fixture()
def dialect(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_OUTPUT_DIR", str(tmp_path))
    from app import dialect as mod
    importlib.reload(mod)
    return mod


def test_the_first_verified_win_becomes_the_way(dialect):
    """Cornerstone's four selects, replayed: before any win the order is the generic bucket;
    after ONE native win, native leads every later call — the diagnosis is paid once."""
    first = dialect.candidate_order("cornerstone", dialect.FAMILY_OPTION_SELECT)
    assert first == ["native_select", "aria_listbox", "react_select"]
    dialect.record_win("cornerstone", dialect.FAMILY_OPTION_SELECT, "native_select",
                       evidence="#EEOQuestion-1 · selected Decline to specify")
    assert dialect.learned_protocol("cornerstone", dialect.FAMILY_OPTION_SELECT) == "native_select"
    again = dialect.candidate_order("cornerstone", dialect.FAMILY_OPTION_SELECT,
                                    classified="aria_listbox")
    assert again[0] == "native_select"          # the dialect outranks the classifier's hint
    assert again[1] == "aria_listbox"


def test_the_classifier_hint_leads_when_nothing_is_learned(dialect):
    order = dialect.candidate_order("greenhouse", dialect.FAMILY_OPTION_SELECT,
                                    classified="react_select")
    assert order[0] == "react_select"
    assert set(order) == {"native_select", "aria_listbox", "react_select"}


def test_the_tag_drops_the_structurally_impossible(dialect):
    """A bare <select> can never be a react-select; a div can never be a native one. The cycle
    must not attempt the impossible — a keystroke dance on a <select> mutates focus for
    nothing."""
    assert dialect.candidate_order("x", dialect.FAMILY_OPTION_SELECT, tag="select") \
        == ["native_select"]
    assert dialect.candidate_order("x", dialect.FAMILY_OPTION_SELECT, tag="div") \
        == ["aria_listbox", "react_select"]
    # A learned dialect that contradicts the tag is dropped too — the node outranks the record.
    dialect.record_win("x", dialect.FAMILY_OPTION_SELECT, "native_select")
    assert "native_select" not in dialect.candidate_order(
        "x", dialect.FAMILY_OPTION_SELECT, tag="div")


def test_a_different_winner_displaces_the_dialect_on_the_record(dialect):
    """Sites redesign. The new winner takes the seat; the displaced protocol and its win count
    stay in history — both sides of the change kept, per the Open Brain rule."""
    dialect.record_win("acme", dialect.FAMILY_OPTION_SELECT, "aria_listbox")
    dialect.record_win("acme", dialect.FAMILY_OPTION_SELECT, "aria_listbox")
    dialect.record_win("acme", dialect.FAMILY_OPTION_SELECT, "react_select")
    row = dialect.all_dialects()["acme::option_select"]
    assert row["protocol"] == "react_select" and row["wins"] == 1
    assert row["history"][0]["protocol"] == "aria_listbox"
    assert row["history"][0]["wins"] == 2


def test_unknown_platform_and_corrupt_store_stay_harmless(dialect):
    assert dialect.learned_protocol("", dialect.FAMILY_OPTION_SELECT) is None
    dialect.record_win("", dialect.FAMILY_OPTION_SELECT, "native_select")   # no-op, no crash
    dialect._store_path().write_text("{not json")
    assert dialect.all_dialects() == {}          # corrupt store = empty prior, never a crash
