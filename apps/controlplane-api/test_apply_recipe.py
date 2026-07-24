

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
