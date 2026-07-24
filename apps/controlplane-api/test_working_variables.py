"""Tests for working variables — answers computed at fill-time, not stored."""

from datetime import date

import working_variables as wv


def test_todays_date_resolves_to_today_not_a_stored_string():
    """The operator's point: 'today's date' frozen into the store is wrong the next day."""
    d = date(2026, 7, 24)
    assert wv.resolve("todays_date", today=d) == "07/24/2026"
    assert wv.resolve("todays_date", today=date(2027, 1, 2)) == "01/02/2027"


def test_when_i_can_start_is_also_today():
    """Available now = today = today's date. The two examples resolve to the same value, which is
    exactly what the operator said."""
    d = date(2026, 7, 24)
    assert wv.resolve("availability_date", today=d) == wv.resolve("todays_date", today=d)


def test_a_normal_key_is_not_a_working_variable():
    assert wv.is_working_variable("salary_expectation") is False
    assert wv.resolve("salary_expectation", today=date(2026, 7, 24)) is None


def test_effective_value_computes_working_but_keeps_stored_otherwise():
    """The one call the fill path makes. A working variable ignores whatever stale string is
    stored; a normal key uses the stored value."""
    d = date(2026, 7, 24)
    assert wv.effective_value("todays_date", "01/01/2020", today=d) == "07/24/2026"
    assert wv.effective_value("salary_expectation", "65000", today=d) == "65000"


def test_describe_marks_a_working_variable_for_the_profile_ui():
    meta = wv.describe("todays_date", today=date(2026, 7, 24))
    assert meta["working_variable"] is True
    assert meta["resolves_to"] == "07/24/2026" and meta["description"]
    assert wv.describe("salary_expectation") is None


def test_format_override_is_honoured():
    assert wv.resolve("todays_date", today=date(2026, 7, 24), fmt="%Y-%m-%d") == "2026-07-24"


def test_the_runtime_can_omit_today_and_get_a_real_date():
    """No injected date → the actual current date, formatted. (Value varies by day; shape does
    not.)"""
    got = wv.resolve("todays_date")
    assert got and len(got.split("/")) == 3
