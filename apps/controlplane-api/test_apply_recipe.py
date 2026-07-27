

def test_workday_apply_method_modal_is_recognised():
    """Added 2026-07-24 after driving it live: the Start-Your-Application modal opens without a URL
    change, so it must be recognised from content or verification calls a good click 'unexpected'."""
    import apply_recipe as ar
    for txt in ("Start Your Application", "Use My Last Application", "Autofill with Resume"):
        assert ar.map_workday_state("https://x.myworkdayjobs.com/job/y", txt) == "workday_apply_method"


def test_workday_progress_reports_depth_from_submit():
    import apply_recipe as ar
    assert ar.workday_progress("workday_apply_method")["steps_to_submit"] == 8
    assert ar.workday_progress("workday_review")["at_review_gate"] is True
    assert ar.workday_progress("workday_review")["steps_to_submit"] == 0
    assert ar.workday_progress("not_a_workday_state")["recognised"] is False


def test_a_branded_successfactors_site_is_recognised_without_an_sap_host():
    import ats_registry  # noqa: F401 — imported in-body like the rest of this module
    """SAP career sites run on the EMPLOYER's domain with no SAP string in the url, so Teradyne
    classified as `company_site` on first contact (2026-07-27). The path shape is the tell."""
    import ats_registry
    assert ats_registry.classify_ats(
        "https://jobs.teradyne.com/Teradyne/job/North-Reading-Pricing-Analyst-123/") == "successfactors"


def test_the_successfactors_path_tell_does_not_claim_linkedin():
    """`/<Tenant>/search/` looked like an equally good tell until it claimed linkedin.com/jobs/search
    — a confident wrong answer about a platform we DO know. A tell that widens its own blast radius
    is worse than one tell fewer."""
    import ats_registry
    assert ats_registry.classify_ats("https://www.linkedin.com/jobs/search") == "linkedin_easy_apply"
    assert ats_registry.classify_ats("https://www.linkedin.com/jobs/view/123") == "linkedin_easy_apply"


def test_successfactors_records_the_native_dialog_that_blocked_the_window():
    import apply_recipe as ar
    """The blocker is the whole reason this ATS got a recipe before it was driven: a native browser
    dialog is invisible to CDP, so the click returns ok and nothing moves. The recipe must SAY that,
    and must not claim the notification-permission fix that was already in place and did not help."""
    lessons = ar.SUCCESSFACTORS_LESSONS
    note = lessons["native_dialog_blocked_the_window"]
    assert "invisible to cdp" in note.lower()
    assert "already block" in note.lower()          # the wrong fix is named as wrong
    assert any("native dialog" in (s.get("action") or "").lower()
               for s in ar.SUCCESSFACTORS_APPLY_RECIPE)


def test_successfactors_apply_is_a_staged_menu():
    import apply_recipe as ar
    """'Apply now' opens a menu; the button stages and the menu item acts. A click on the button
    alone reports ok and goes nowhere — which reads exactly like the native-dialog blocker and is a
    different fault."""
    menu_step = next(s for s in ar.SUCCESSFACTORS_APPLY_RECIPE
                     if s["state"] == "successfactors_apply_menu")
    assert menu_step["controls"]["apply_now"]["role"] == "link"
    assert "linkedin" in ar.SUCCESSFACTORS_LESSONS["linkedin_path_is_a_detour"].lower()
