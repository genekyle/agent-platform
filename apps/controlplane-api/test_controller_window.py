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
    """Over budget with applications on DIFFERENT hosts — genuinely different jobs in flight, so
    it thins by age (never the newest, never the active, never search) rather than by duplicate."""
    urls = [("A", "https://www.indeed.com/jobs?q=x")]
    urls += [(f"P{i}", f"https://co{i}.wd5.myworkdayjobs.com/job/step") for i in range(5)]
    win = w.survey(_tabs(*urls), active_tab_id="P4", budget=3)
    assert win.over_budget
    closed = {t.tab_id for t in win.closable}
    assert "P4" not in closed, "closed the tab we are driving"
    assert "P3" not in closed, "closed the newest application instead of the oldest"
    assert "A" not in closed, "closed the search tab we return to"
    assert {"P0", "P1", "P2"} <= closed


def test_many_apply_tabs_on_one_host_collapse_to_the_driven_one():
    """Same host is the stronger signal than budget: five smartapply tabs are one application with
    four orphans, so driving one closes the other four regardless of the budget."""
    urls = [(f"P{i}", f"https://smartapply.indeed.com/beta/indeedapply/form/step-{i}")
            for i in range(5)]
    win = w.survey(_tabs(*urls), active_tab_id="P4", budget=3)
    closed = {t.tab_id for t in win.closable}
    assert closed == {"P0", "P1", "P2", "P3"}, "should keep only the driven tab"


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


# --- the closing half needs a CALL SITE, or the capability does not exist ---------------
def test_the_window_endpoints_are_registered():
    """The miss this file is guarding against: `tidy_window` was written, tested and unreachable.

    A capability with no call site is a capability the system does not have — the 2026-07-16
    corpus reckoning one altitude up, and exactly what the operator saw as "it may be in but I
    see lack of functionality".
    """
    import main

    paths = {f"{m} {r.path}" for r in main.app.routes
             for m in (getattr(r, "methods", None) or ()) if m not in ("HEAD", "OPTIONS")}
    assert "POST /api/controller/window" in paths
    assert "POST /api/controller/window/tidy" in paths


def test_a_drive_can_be_asked_to_tidy_and_defaults_not_to():
    from routers.controller import RunBody

    assert RunBody(browser_url="x", tab_id="t").tidy is False
    assert RunBody(browser_url="x", tab_id="t", tidy=True).tidy is True


# --- health: two tabs, one application (live 2026-07-23) --------------------------------
def _apply_tabs():
    """The real shape that prompted this: two smartapply tabs, one Nichols application, at
    different steps — an orphan from an earlier Apply re-entry, plus the search tab."""
    return _tabs(
        ("WORK", "https://smartapply.indeed.com/beta/indeedapply/form/resume-module/profile-work-experience/append"),
        ("RESUME", "https://smartapply.indeed.com/beta/indeedapply/form/resume-selection-module/resume-selection"),
        ("SEARCH", "https://www.indeed.com/jobs?q=reporting+analyst"),
    )


def test_two_apply_tabs_on_one_host_are_flagged_as_an_anomaly():
    """A given ATS runs ONE apply flow per session, so a second apply tab is not a second job —
    it is an orphan, and the window's health must say so rather than count it as normal clutter."""
    win = w.survey(_apply_tabs())
    assert win.health == "warn"
    kinds = {a.kind for a in win.anomalies}
    assert w.ANOMALY_DUPLICATE_APPLICATION in kinds


def test_a_duplicate_application_is_not_auto_closed_when_we_are_driving_neither():
    """Between drives we do not know which tab holds the work; closing the wrong one discards it.
    Flag, and leave the choice to the operator — never guess."""
    win = w.survey(_apply_tabs())          # no active tab
    assert win.closable == ()
    a = next(a for a in win.anomalies if a.kind == w.ANOMALY_DUPLICATE_APPLICATION)
    assert a.resolvable is False and a.keeper == ""


def test_the_orphan_is_closed_when_we_are_driving_the_other():
    """Driving one of them makes it the keeper — the orphaned twin becomes closable, even though
    both are `apply` and the window is not over budget."""
    win = w.survey(_apply_tabs(), active_tab_id="WORK")
    closed = {t.tab_id for t in win.closable}
    assert closed == {"RESUME"}, "should close the orphan and keep the tab we are driving"
    a = next(a for a in win.anomalies if a.kind == w.ANOMALY_DUPLICATE_APPLICATION)
    assert a.resolvable and a.keeper == "WORK"


def test_exact_duplicates_are_resolvable_without_an_active_tab():
    """Identical copies have no 'more advanced' one to lose, so any keeper is safe."""
    win = w.survey(_tabs(
        ("A", "https://acme.wd5.myworkdayjobs.com/job/x"),
        ("B", "https://acme.wd5.myworkdayjobs.com/job/x"),
        ("S", "https://www.indeed.com/jobs?q=x"),
    ))
    a = next(a for a in win.anomalies if a.kind == w.ANOMALY_EXACT_DUPLICATE)
    assert a.resolvable
    assert len(win.closable) == 1 and win.closable[0].tab_id in {"A", "B"}


def test_two_different_workday_tenants_are_not_a_duplicate():
    """Different companies use different Workday hosts, so two apply tabs across hosts are two
    real applications, not an anomaly — the host is what makes 'same application' meaningful."""
    win = w.survey(_tabs(
        ("A", "https://acme.wd5.myworkdayjobs.com/job/x"),
        ("B", "https://globex.wd1.myworkdayjobs.com/job/y"),
    ))
    assert not [a for a in win.anomalies if a.kind == w.ANOMALY_DUPLICATE_APPLICATION]


def test_a_healthy_window_reports_ok():
    win = w.survey(_tabs(
        ("A", "https://smartapply.indeed.com/beta/indeedapply/form/review-module"),
        ("S", "https://www.indeed.com/jobs?q=x"),
    ), active_tab_id="A")
    assert win.health == "ok"
    assert win.as_dict()["health"] == "ok"


# --- fresh start: provisioning is not hygiene ---------------------------------------------
def _infos(*pairs):
    return tuple(w.TabInfo(tab_id=tid, url=url, role=w.classify_tab(url))
                 for tid, url in pairs)


def test_fresh_start_clears_everything_but_one_landing_tab():
    """A persistent profile restores its old w. On a fresh session all of it is inherited,
    so all of it goes — except one tab to land on, because the last tab cannot be closed."""
    tabs = _infos(("a", "https://smartapply.indeed.com/beta/indeedapply/form/resume-module"),
                 ("b", "https://www.indeed.com/jobs?q=reporting+analyst&l=Manchester%2C+NH"),
                 ("c", "about:blank"))
    to_close, keeper, reasons = w.plan_fresh_start(tabs)
    assert keeper.tab_id == "c"                      # blank survives — discarding it costs nothing
    assert {t.tab_id for t in to_close} == {"a", "b"}
    assert len(reasons) == 2 and all("previous session" in r for r in reasons)


def test_fresh_start_never_keeps_an_apply_flow_as_the_survivor():
    """The survivor gets navigated away, so it must be the tab least likely to hold work."""
    tabs = _infos(("a", "https://smartapply.indeed.com/beta/indeedapply/form/resume-module"),
                 ("b", "https://www.indeed.com/jobs?q=x"))
    _to_close, keeper, _why = w.plan_fresh_start(tabs)
    assert keeper.role != w.ROLE_APPLY


def test_fresh_start_would_close_the_only_search_tab_where_hygiene_would_not():
    """The distinction that earns a separate function. plan_hygiene protects the only search tab
    because mid-drive it is home base; at provisioning it is just last session's stale search."""
    tabs = _infos(("a", "about:blank"),
                 ("b", "https://www.indeed.com/jobs?q=stale+search"))
    hygiene_closable, _why = w.plan_hygiene(tabs)
    assert "b" not in {t.tab_id for t in hygiene_closable}
    fresh_closable, keeper, _r = w.plan_fresh_start(tabs)
    assert "b" in {t.tab_id for t in fresh_closable} and keeper.tab_id == "a"


def test_fresh_start_on_an_already_clean_window_is_an_empty_plan():
    to_close, keeper, reasons = w.plan_fresh_start(_infos(("a", "about:blank")))
    assert to_close == () and reasons == () and keeper.tab_id == "a"


def test_fresh_start_on_no_tabs_proposes_nothing():
    assert w.plan_fresh_start(()) == ((), None, ())


def test_inherited_work_flags_apply_and_errand_tabs_only():
    """These are the tabs a clean start must not silently discard — a half-finished application
    is someone's work, and provisioning should ask before throwing it away."""
    tabs = _infos(("a", "https://smartapply.indeed.com/beta/indeedapply/form/resume-module"),
                 ("b", "https://www.indeed.com/jobs?q=x"),
                 ("c", "about:blank"))
    assert {t.tab_id for t in w.inherited_work(tabs)} == {"a"}
