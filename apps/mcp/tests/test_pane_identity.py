"""Which job is the pane showing — the question /open_job_card kept answering with a different one.

The switch check used to ask "did the pane CHANGE", and that is wrong in both directions: a pane
that never changed is not a failure (both engines auto-open the first result), and a pane that
changed to the wrong job is one. Measured live on Indeed 2026-07-30, session 24.
"""

from app.main_server import pane_shows


_LIVE_INDEED_PANE = {
    # Verbatim from the pane that /open_job_card called a failure while returning all of this.
    "open_job_id": "67a36d0962578890",
    "title": "Human Resources Data Analyst",
    "company": "BRISTOL COUNTY SAVINGS BANK",
    "apply_type": "quick_apply",
    "description": "ABOUT US: Bristol County Savings Bank, founded in 1846, ...",
}


def test_the_pane_we_asked_for_is_a_match_even_though_nothing_changed():
    # Indeed auto-opens the first result, so this card was already showing when we asked for it —
    # switched:false, retried:2, and 3961 chars of the RIGHT description. Not a failure.
    assert pane_shows(_LIVE_INDEED_PANE, "67a36d0962578890") is True


def test_a_pane_showing_a_different_job_is_not_a_match():
    # The near-miss guard, and the whole reason the check exists: on 2026-07-26 a click landed on
    # the wrong card and nearly applied to it.
    assert pane_shows(_LIVE_INDEED_PANE, "61d92a6a08c91d90") is False


def test_ids_compare_across_types():
    # Engines report ids as strings; callers hold whatever the queue stored.
    assert pane_shows({"open_job_id": "12345"}, 12345) is True


def test_a_pane_that_reports_no_id_is_not_a_match():
    # Absent must not read as agreement — the caller falls back to its weaker text diff, and
    # returning True here would hand every engine without an id a free pass.
    assert pane_shows({"description": "words"}, "67a36d0962578890") is False
    assert pane_shows({"open_job_id": ""}, "") is False
    assert pane_shows({}, "abc") is False
    assert pane_shows(None, "abc") is False
