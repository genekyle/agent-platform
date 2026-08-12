

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
    # THE URL VERBATIM FROM THE OPEN TAB, not one written from memory. The first version of this
    # test used an invented two-segment path, passed, and missed the four-segment page that was on
    # screen at the time — SAP appends a numeric job id as its own segment.
    live = ("https://jobs.teradyne.com/Teradyne/job/North-Reading-Pricing-Marketing-Operations-"
            "Analyst-%28Teradyne%2C-N_-Reading-MA%29-MA/1385295400/?codes=WBIND")
    assert ats_registry.classify_ats(live) == "successfactors"


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
    note = ar.SUCCESSFACTORS_LESSONS["alert_blocks_the_tab"]
    assert "alert()" in note                         # the MECHANISM, not "some native dialog"
    assert "dialog_guard" in note                    # and the only thing that actually works
    assert "cannot be dismissed after" in note.lower()
    # The recipe must start the guard BEFORE driving — after the fact is too late, by construction.
    assert "dialog_guard" in (ar.SUCCESSFACTORS_APPLY_RECIPE[0].get("action") or "")


def test_successfactors_apply_is_a_staged_menu():
    import apply_recipe as ar
    """'Apply now' opens a menu; the button stages and the menu item acts. A click on the button
    alone reports ok and goes nowhere — which reads exactly like the native-dialog blocker and is a
    different fault."""
    menu_step = next(s for s in ar.SUCCESSFACTORS_APPLY_RECIPE
                     if s["state"] == "successfactors_apply_menu")
    assert menu_step["controls"]["apply_now"]["role"] == "link"
    assert "linkedin" in ar.SUCCESSFACTORS_LESSONS["linkedin_path_is_a_detour"].lower()


def test_the_successfactors_account_legs_say_which_one_has_been_walked():
    """Mapped and driven are different claims. The create leg was driven to the form on Teradyne
    (2026-07-28); the returning-candidate leg was only read off the gate. A leg we have not walked
    must read as unknown, not as covered — the same rule that keeps iCIMS's sign_in absent."""
    from apply_recipe import SUCCESSFACTORS_ACCOUNT_LOOP as loop
    assert loop["needs_creation"]["button"] == "Create Account"
    assert loop["needs_creation"]["state"] == "successfactors_create_account"
    assert "NOT YET DRIVEN" in loop["created"]["note"]


def test_the_create_form_is_a_state_of_its_own_in_the_recipe():
    # The gate and the form are confusable — both SAP-chrome "Career Opportunities" pages with an
    # email and a password box. Collapsing them is how a recipe drives the wrong one.
    from apply_recipe import SUCCESSFACTORS_APPLY_RECIPE as steps
    states = [s["state"] for s in steps]
    assert "successfactors_account_gate" in states
    assert "successfactors_create_account" in states
    gate = next(s for s in steps if s["state"] == "successfactors_account_gate")
    assert "successfactors_create_account" in gate["expect"]
    # Every step still numbered in order after the insertion.
    assert [s["step"] for s in steps] == list(range(len(steps)))


def test_the_password_rules_lesson_matches_the_policy_that_enforces_it():
    """The rules lived as prose in three files and nothing read them. Now that code does, the prose
    and the data must not be able to drift apart."""
    import apply_fields
    from apply_recipe import SUCCESSFACTORS_LESSONS
    policy = apply_fields.PASSWORD_POLICIES["successfactors"]
    lesson = SUCCESSFACTORS_LESSONS["password_rules"]
    assert str(policy["min_length"]) in lesson
    assert str(policy["max_length"]) in lesson
    assert "apply_fields.PASSWORD_POLICIES" in lesson


def test_flow_progress_counts_the_indeed_spine_under_either_platform_name():
    """`classify_landing` answers `indeed` where the terminal table says `indeed_quick_apply`.
    One canonicalisation, or a submitted application does not read as done under one of them."""
    import apply_recipe as ar
    for platform in ("indeed", "indeed_quick_apply"):
        p = ar.flow_progress("indeed_apply_resume_selection", platform=platform)
        assert p["recognised"] and p["steps_to_submit"] == 6 and not p["at_review_gate"]
        assert ar.flow_progress("indeed_apply_review", platform=platform)["at_review_gate"] is True
        assert ar.flow_progress("indeed_apply_submitted", platform=platform)["done"] is True
    assert ar.flow_progress("not_a_state", platform="indeed")["recognised"] is False
    assert ar.flow_progress("indeed_apply_review", platform="brand_new_ats")["recognised"] is False


def test_the_two_control_lexicons_can_never_reach_each_other():
    """The advance lexicon must not be able to press Submit, and the submit lexicon must not
    settle for a Continue. Keeping them in one list is how a guess ends up sending an
    application."""
    import apply_recipe as ar
    from controller.decide import advance_control
    assert advance_control(["button|Submit your application"]) == ""
    assert ar.submit_control(["button|Continue", "button|Save and Continue"]) == ""
    # And each finds its own, as the page renders it, longest match winning.
    assert advance_control(["button|Continue", "button|Save and Continue"]) == "Save and Continue"
    assert ar.submit_control(["button|Submit your application"]) == "Submit your application"


def test_the_recipe_names_the_action_and_the_states_it_expects():
    import apply_recipe as ar
    assert ar.advance_action("indeed", "indeed_apply_resume_selection") == "Continue"
    assert "indeed_apply_review" in ar.expected_after("indeed", "indeed_apply_demographics")
    assert ar.advance_action("workday", "workday_review") == ""      # not this recipe's spine


def test_the_advance_matcher_never_presses_a_negation():
    """Found live 2026-08-06 on Indeed's resume editor. The page offered {Save, Don't save, Report
    an issue, Close}; `_ADVANCE_CONTROLS` matched "Save" by substring and the longest-match
    tie-break — right for "Continue" vs "Save and Continue", where longer means more specific —
    handed back "Don't save", where longer means the opposite. The click was journaled as an
    advance."""
    from controller.decide import advance_control
    assert advance_control(["button|Save", "button|Don't save", "button|Close"]) == "Save"
    assert advance_control(["button|Don't save"]) == "", "a page with ONLY the negation advances nothing"
    for negation in ("Do not continue", "Cancel and continue", "Discard and continue",
                     "Continue without saving", "Never continue"):
        assert advance_control([f"button|{negation}"]) == "", f"{negation!r} must not read as advance"
    # AND the exit dressed as an advance, found one screen later on the same drive: "Save and
    # close" matched the lexicon's bare "Save" and opened Indeed's "Save application progress
    # before you exit" modal. Not a negation, so the negation guard alone could not see it.
    assert advance_control(["button|Save and close", "button|Report an issue"]) == ""
    for exit_ctrl in ("Save and finish later", "Continue later", "Save and exit", "Go back"):
        assert advance_control([f"button|{exit_ctrl}"]) == "", f"{exit_ctrl!r} leaves the flow"
    # The tie-break it was built for still works.
    assert advance_control(["button|Continue", "button|Save and Continue"]) == "Save and Continue"


def test_the_recipe_outranks_the_generic_lexicon_on_a_screen_it_knows():
    """Live 2026-08-06: Indeed's highlights screen advances on "Review details", which the generic
    lexicon cannot reach — and the only entry it CAN match there is "Save and close", the exit."""
    import apply_recipe as ar
    from controller.decide import advance_control
    page = ["button|Save and close", "button|Go back", "button|Review details",
            "button|Report an issue"]
    assert advance_control(page) == "", "the lexicon has nothing safe to offer on this screen"
    assert ar.named_control("indeed", "indeed_apply_resume_highlights", page) == "Review details"
    # A recipe naming a button the page does not have is stale — the honest answer is "", never a
    # click on whatever else was lying around.
    assert ar.named_control("indeed", "indeed_apply_resume_highlights",
                            ["button|Save and close"]) == ""
    # Screens the recipe does not name a control for fall through to the lexicon.
    assert ar.named_control("indeed", "indeed_apply_resume_selection", ["button|Continue"]) == ""


# --- THE GENERIC ATS CADENCE — the fuzzy spine every unmapped platform shares --------------------
# Operator, 2026-08-11: unknown third-party applications are "generally the same steps … a fuzzy
# path that may diverge but the cadence is still somewhat the same." These tests are that sentence
# made executable: an ATS nobody has scripted still counts along land → gate → form → review →
# submitted, in its own state names, with the review gate operator-only. Cornerstone (Boston
# College, bc.csod.com) is the platform that forced it — the drive dead-ended at "genuinely new
# territory" on a page whose SHAPE we knew perfectly well.

def test_an_unmapped_ats_counts_along_the_shared_cadence():
    import apply_recipe as ar
    p = ar.flow_progress("cornerstone_job_posting", platform="cornerstone")
    assert p["recognised"] is True and p["via"] == "generic_ats"
    assert p["steps_to_submit"] == 3          # gate → form → review is the upper bound from land
    review = ar.flow_progress("cornerstone_review", platform="cornerstone")
    assert review["at_review_gate"] is True and review["steps_to_submit"] == 0
    assert ar.flow_progress("cornerstone_confirmation", platform="cornerstone")["done"] is True
    assert ar.gate_state("cornerstone") == "cornerstone_review"


def test_the_generic_cadence_does_not_leak_where_it_has_no_business():
    import apply_recipe as ar
    # A platform WITH a scripted flow keeps it — graduating out of the generic path.
    assert ar.flow_progress("workday_review", platform="workday").get("via") is None
    assert ar.flow_progress("indeed_job_posting", platform="indeed").get("via") is None
    # An engine's on-page apply never falls through to the ATS cadence.
    assert ar.flow_progress("linkedin_easy_apply_review",
                            platform="linkedin_easy_apply")["recognised"] is False
    # A platform the registry does not know is not a platform — stay unrecognised.
    assert ar.flow_progress("mystery_review", platform="mystery")["recognised"] is False
    # A state that does not parse as <platform>_<kind> stays unrecognised too.
    assert ar.flow_progress("cornerstone_weird", platform="cornerstone")["recognised"] is False


def test_the_generic_describer_names_the_state_from_content_signals():
    """URL names the platform, content names the kind, the state is their join — the same
    synthesis the observer uses, now in the vocabulary the ladder counts along."""
    import apply_recipe as ar
    posting = ("Business Intelligence Analyst/Developer. Job description: reporting and analytics. "
               "Qualifications: SQL. Apply now.")
    d = ar.describe_for_ats("cornerstone", "https://bc.csod.com/ux/ats/careersite/2/home", posting)
    assert d["state"] == "cornerstone_job_posting" and d["via"] == "generic_ats"
    assert "cornerstone_account_gate" in d["expected_next"]
    wall = "Sign in to continue. Create an account. Already have an account?"
    assert ar.describe_for_ats("cornerstone", "https://bc.csod.com/x", wall)["state"] \
        == "cornerstone_account_gate"
    review = "Review your application before submitting. Application summary."
    assert ar.describe_for_ats("cornerstone", "https://bc.csod.com/x", review)["state"] \
        == "cornerstone_review"
    # Unreadable content stays honest — no kind, no cadence, no counting. And a non-answer does
    # not wear a state name: the bundle's contract is None-degradation from the bare "unknown",
    # and "cornerstone_unreadable" walking into it as a state id is how a blank page started
    # reading as a recognised screen (caught by test_controller_bundle on the same day).
    blank = ar.describe_for_ats("cornerstone", "https://bc.csod.com/x", "")
    assert blank["state"] == "unknown" and blank["kind"] == "unreadable"


def test_the_generic_posting_control_is_apply_and_never_an_sso_detour():
    """The advance lexicon deliberately cannot reach "Apply", so the spine names it — most
    specific first, with the exclusions keeping the classic wrong buttons unreachable."""
    import apply_recipe as ar
    page = ["button|Apply Now", "button|Share", "button| Save Job", "link|Sign In",
            "link|Create Profile", "link| Back to Search"]
    assert ar.named_control("cornerstone", "cornerstone_job_posting", page) == "Apply Now"
    # An SSO detour is not the apply control, even when it is the only apply-ish name left.
    sso = ["button|Apply with LinkedIn", "button|Share"]
    assert ar.named_control("cornerstone", "cornerstone_job_posting", sso) == ""
    # And the wall screen names NO control on purpose — it is the account rung's business.
    assert ar.named_control("cornerstone", "cornerstone_account_gate",
                            ["button|Sign In", "button|Create Account"]) == ""


def test_the_generic_prediction_is_wide_and_says_so():
    import apply_recipe as ar
    spread = ar.expected_after("cornerstone", "cornerstone_job_posting")
    assert "cornerstone_account_gate" in spread and "cornerstone_application_form" in spread
    assert ar.expected_after("cornerstone", "cornerstone_review") == ("cornerstone_confirmation",)


def test_the_gate_presses_a_button_never_a_heading():
    """Cornerstone renders a 'Submit Application' SECTION HEADING above the footer's real
    'Submit' button; longest-match over roleless names clicked the heading and the gate
    no-oped (live 2026-08-11). Pressable roles only — and roleless identities stay eligible."""
    import apply_recipe as ar
    page = ["heading|Submit Application", "button|Submit", "button|Cancel", "button|Save"]
    assert ar.submit_control(page) == "Submit"
    # Roleless identities (older scans) still qualify.
    assert ar.submit_control(["Submit your application"]) == "Submit your application"
    # A page with only the heading has no pressable gate — the honest answer is none.
    assert ar.submit_control(["heading|Submit Application"]) == ""
