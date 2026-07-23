"""The session window — the tab manager the controller never had (2026-07-23).

Every case here is a fault we actually hit on 2026-07-22, not a hypothetical: a capture that
photographed a stale post-apply tab, an Apply click whose new tab went unnoticed, and a Submit
pressed on a tab that had gone stale. All three were "we do not know what else is open".
"""

from controller import window as w
from interaction.decision import window_to_prompt


def _tabs(*pairs):
    return [{"tab_id": tid, "url": url} for tid, url in pairs]


# --- classification: the one distinction that cost us a poisoned corpus ----------------
def test_a_finished_application_is_terminal_not_apply():
    """post-apply lives on the SAME host as a live application.

    Calling it `apply` is precisely the mistake behind the wrong-tab capture: a finished
    confirmation tab read as work in progress, so nothing wanted to close it and `/capture`
    happily photographed it.
    """
    assert w.classify_tab("https://smartapply.indeed.com/beta/indeedapply/form/post-apply") \
        == w.ROLE_TERMINAL
    assert w.classify_tab("https://smartapply.indeed.com/beta/indeedapply/form/review-module") \
        == w.ROLE_APPLY


def test_roles_cover_the_session_shapes_we_actually_drive():
    assert w.classify_tab("https://www.indeed.com/jobs?q=reporting+analyst") == w.ROLE_SEARCH
    assert w.classify_tab("https://acme.wd5.myworkdayjobs.com/en-US/careers/job/x") == w.ROLE_APPLY
    assert w.classify_tab("https://mail.google.com/mail/u/0/#inbox") == w.ROLE_ERRAND
    assert w.classify_tab("about:blank") == w.ROLE_BLANK
    assert w.classify_tab("https://example.com/some/page") == w.ROLE_UNKNOWN


# --- the survey ------------------------------------------------------------------------
def test_the_window_reports_counts_roles_and_which_tab_we_are_on():
    win = w.survey(_tabs(("A", "https://www.indeed.com/jobs?q=x"),
                         ("B", "https://smartapply.indeed.com/beta/indeedapply/form/review-module"),
                         ("C", "https://smartapply.indeed.com/beta/indeedapply/form/post-apply")),
                   active_tab_id="B")
    assert win.count == 3
    assert win.active.role == w.ROLE_APPLY
    d = win.as_dict()
    assert d["roles"] == {"search": 1, "apply": 1, "terminal": 1}
    assert d["over_budget"] is False


def test_an_unlistable_window_is_simply_absent_rather_than_wrong():
    """No tabs is not "zero tabs" — it is "we could not see", and it must render nothing."""
    assert w.survey([]).as_dict()["count"] == 0
    assert window_to_prompt(w.survey([]).as_dict()) == ""
    assert window_to_prompt(None) == ""


# --- hygiene: the four rails ------------------------------------------------------------
def test_a_finished_tab_is_closable_and_the_live_one_is_not():
    win = w.survey(_tabs(("A", "https://www.indeed.com/jobs?q=x"),
                         ("B", "https://smartapply.indeed.com/beta/indeedapply/form/review-module"),
                         ("C", "https://smartapply.indeed.com/beta/indeedapply/form/post-apply")),
                   active_tab_id="B")
    assert [t.tab_id for t in win.closable] == ["C"]
    assert win.reasons and "holds no work" in win.reasons[0]


def test_the_active_tab_is_never_closable_even_when_it_looks_finished():
    """We drive post-apply pages: reading a confirmation is legitimate work."""
    win = w.survey(_tabs(("A", "https://www.indeed.com/jobs?q=x"),
                         ("C", "https://smartapply.indeed.com/beta/indeedapply/form/post-apply")),
                   active_tab_id="C")
    assert win.closable == ()


def test_an_unknown_tab_is_never_closable():
    """The operator shares this window. "I could not identify it" is the weakest possible reason
    to close somebody else's tab."""
    win = w.survey(_tabs(("A", "https://www.indeed.com/jobs?q=x"),
                         ("Z", "https://some-bank.example/statement")),
                   active_tab_id="A")
    assert win.closable == ()


def test_the_last_tab_is_never_closable():
    win = w.survey(_tabs(("C", "https://smartapply.indeed.com/beta/indeedapply/form/post-apply")),
                   active_tab_id="")
    assert win.closable == ()


def test_over_budget_retires_superseded_applications_but_keeps_the_newest():
    urls = [("A", "https://www.indeed.com/jobs?q=x")]
    urls += [(f"P{i}", f"https://smartapply.indeed.com/beta/indeedapply/form/step-{i}")
             for i in range(5)]
    win = w.survey(_tabs(*urls), active_tab_id="P4", budget=3)
    assert win.over_budget
    closed = {t.tab_id for t in win.closable}
    assert "P4" not in closed, "closed the tab we are driving"
    assert "P3" not in closed, "closed the newest application instead of the oldest"
    assert "A" not in closed, "closed the search tab we return to"
    assert {"P0", "P1", "P2"} <= closed


def test_hygiene_never_plans_to_close_everything():
    win = w.survey(_tabs(("A", "about:blank"), ("B", "about:blank")), active_tab_id="")
    assert len(win.closable) < 2


# --- the prompt ------------------------------------------------------------------------
def test_the_window_block_is_counts_and_roles_never_a_url_per_tab():
    """This rides in every prompt and every journaled row; a url list is the raw dump we keep out."""
    win = w.survey(_tabs(("A", "https://www.indeed.com/jobs?q=reporting+analyst&vjk=SECRET"),
                         ("B", "https://smartapply.indeed.com/beta/indeedapply/form/review-module")),
                   active_tab_id="B")
    text = window_to_prompt(win.as_dict())
    assert "tabs: 2" in text and "apply=1" in text
    assert "SECRET" not in text and "vjk" not in text
