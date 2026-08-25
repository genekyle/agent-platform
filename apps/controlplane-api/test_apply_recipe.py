

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


def test_the_workday_posting_advances_on_continue_application_once_a_draft_exists():
    """A saved draft RENAMES the posting's control, and "application" does not contain "apply".

    Measured live 2026-08-25 on SolutionHealth (wd1, JR13051): the orange control read "Continue
    Application", the substring matcher found no "apply" inside "application", and the drive
    stalled twice on a page whose only control was in plain sight.
    """
    import apply_recipe as ar
    draft = ["button|Continue Application", "button|Continue Application", "link|Read More"]
    assert ar.named_control("workday", "workday_job_posting", draft) == "Continue Application"
    # The fresh posting is untouched — this adds a name, it does not replace one.
    assert ar.named_control("workday", "workday_job_posting",
                            ["button|Apply", "button|Save"]) == "Apply"
    # Where a page renders both, the draft wins: it is the path that keeps the work already done.
    assert ar.named_control("workday", "workday_job_posting",
                            ["button|Apply", "button|Continue Application"]) == "Continue Application"
    # And the exclusions still hold on this screen — a detour is not the control.
    assert ar.named_control("workday", "workday_job_posting",
                            ["button|Apply with LinkedIn"]) == ""


def test_the_workday_posting_prediction_admits_the_draft_branch():
    """Apply opens the front door; Continue Application re-enters the FORM at the saved step. The
    recipe cannot see WHICH step from the posting, so the spread is wide on purpose."""
    import apply_recipe as ar
    spread = ar.expected_after("workday", "workday_job_posting")
    assert "workday_apply_method" in spread, "the fresh path still predicts the method modal"
    assert "workday_my_information" in spread and "workday_review" in spread, (
        "a resumed draft lands inside the form, not on the front door")


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


# --------------------------------------------------------------------- platform_known
# "We cannot place this SCREEN" and "we have never seen this PLATFORM" were reported with the same
# words, so the rare warning fired constantly and stopped being readable (operator, 2026-08-13).
def test_platform_known_covers_scripted_and_generic_ground():
    import apply_recipe as ar
    assert ar.platform_known("workday") is True        # scripted spine
    assert ar.platform_known("icims") is True
    # An off-engine registry entry with no scripted flow is still ground the generic ATS cadence
    # walks — it is not "drive it by hand" territory.
    assert ar.platform_known("company_site") is True


def test_platform_known_is_false_only_for_ground_nothing_can_drive():
    import apply_recipe as ar
    assert ar.platform_known("") is False
    assert ar.platform_known(None) is False
    assert ar.platform_known("an_ats_nobody_has_ever_driven") is False


# --------------------------------------------------------------- the two apply doors
# Employer careers sites routinely show the candidate Apply and a "current employees apply here"
# side by side. Both contain "apply", the internal one is reliably LONGER, and "longest is most
# specific" picked the door we can never walk through (live 2026-08-13, C&S Wholesale Grocers:
# the drive clicked "CURRENT C&S EMPLOYEES APPLY HERE" over "APPLY NOW" and nothing moved).
def test_the_employee_apply_door_is_never_the_candidate_one():
    import apply_recipe as ar
    names = ["APPLY NOW", "CURRENT C&S EMPLOYEES APPLY HERE",
             "SEND ME SIMILAR JOBS", "C&S Employees Apply Here "]
    assert ar._named_control(names, ["apply"]) == "APPLY NOW"


def test_a_name_that_leads_with_the_verb_beats_a_longer_one_that_buries_it():
    """The tiebreak that would have got C&S right without knowing the word "employee": a button
    whose label BEGINS with the verb is the primary action; a longer name burying it mid-phrase is
    almost always qualified."""
    import apply_recipe as ar
    assert ar._named_control(
        ["Apply now", "If you are an internal candidate please apply through this link"],
        ["apply"]) == "Apply now"


def test_longest_still_wins_when_nothing_leads_with_the_verb():
    """The older rule stays the default — it is right when the choice is within one destination."""
    import apply_recipe as ar
    assert ar._named_control(["Review", "Review your application"],
                             ["review"]) == "Review your application"


def test_a_control_the_page_does_not_have_is_still_refused():
    import apply_recipe as ar
    assert ar._named_control(["Save", "Share"], ["apply"]) == ""


# ------------------------------------------------- the page states its own step; markers infer it
def test_the_review_page_is_not_my_information():
    """REVIEW lists every section it is reviewing, so "my information" matches there too — and it
    sits above the review markers in the table. A completed application at the Submit gate
    classified as `workday_my_information`, and the cockpit offered to fill a form four screens
    behind the browser (live 2026-08-13). Workday's stepper says where it is; read that."""
    import apply_recipe as ar
    review = ("completed step 1 of 6 My Information completed step 2 of 6 My Experience "
              "completed step 3 of 6 Application Questions completed step 4 of 6 Voluntary "
              "Disclosures completed step 5 of 6 Self Identify current step 6 of 6 Review Submit")
    assert ar.map_workday_state("https://x.wd1.myworkdayjobs.com/j", review) == "workday_review"


def test_the_stepper_names_every_screen_it_is_on():
    import apply_recipe as ar
    u = "https://x.wd1.myworkdayjobs.com/j"
    assert ar.map_workday_state(u, "current step 1 of 6 My Information") == "workday_my_information"
    assert ar.map_workday_state(u, "current step 2 of 6 My Experience") == "workday_my_experience"
    assert ar.map_workday_state(u, "current step 3 of 6 Application Questions") == "workday_questions"
    # Self Identify is its own screen that our spine folds into the disclosures rung.
    assert ar.map_workday_state(
        u, "current step 5 of 6 Self Identify") == "workday_voluntary_disclosures"


def test_markers_still_serve_a_tenant_that_renders_no_stepper():
    """The stepper is evidence when present; the marker table remains the fallback, and a Workday
    origin with neither is still the job posting."""
    import apply_recipe as ar
    u = "https://x.wd1.myworkdayjobs.com/j"
    assert ar.map_workday_state(u, "Start Your Application Autofill with Resume") == "workday_apply_method"
    assert ar.map_workday_state(u, "") == "workday_job_posting"


# --- documentation about the action is not the action ------------------------------------------

def test_a_help_link_beside_the_apply_button_is_not_the_apply_button():
    """"Apply now Help" beat "Apply now" and the drive spent its click on documentation (live
    2026-08-14, MAPFRE). Both LEAD with the token, so no length rule can separate them.

    A shortest-among-leading rule was tried first and this suite refused it within the minute:
    Workday's review screen carries "Review" and "Review your application", both leading, and
    there the LONGER one is the control. Length was never the signal — what separates them is what
    the extra word MEANS, which is what the exclusion list is for.
    """
    import apply_recipe as ar
    got = ar._named_control(["Apply now Help", "Apply now", "Share"], ["apply now", "apply"])
    assert got == "Apply now"


def test_the_length_rules_are_untouched_by_that_fix():
    """Both directions that earned their tiebreaks stay exactly as they were."""
    import apply_recipe as ar
    # longest among leading — Workday's review screen
    assert ar._named_control(["Review", "Review your application"],
                             ["review"]) == "Review your application"
    # leading beats containing — the C&S employee door
    assert ar._named_control(["CURRENT C&S EMPLOYEES APPLY HERE", "APPLY NOW"],
                             ["apply now", "apply"]) == "APPLY NOW"


def test_the_two_matchers_share_only_the_context_free_exclusions():
    """ONE EXCLUSION LIST WAS TWO, AND MERGING THEM WHOLE WAS ALSO WRONG.

    `advance_control` — the fallback the ladder reaches when the recipe names nothing — carried no
    exclusions at all, so a name `_named_control` refused was reachable through it: live
    2026-08-14 on MAPFRE the first crank clicked "Apply now" and the second fell through and
    clicked "Apply now Help".

    Handing it the WHOLE apply-door list then killed "Save and Continue", because "save" is
    disqualifying beside an Apply button ("save this job") and is the legitimate advance control on
    BrassRing and Workday. Caught before shipping. Context-free judgement travels; context-specific
    judgement does not.
    """
    import apply_recipe as ar
    from controller.decide import advance_control

    # Shared: documentation and doors we can never walk through.
    assert "help" in ar.NEVER_THE_ACTION and "linkedin" in ar.NEVER_THE_ACTION
    assert set(ar.NEVER_THE_ACTION) <= set(ar.GENERIC_CONTROL_EXCLUSIONS)
    # Apply-door only.
    assert "save" in ar.GENERIC_CONTROL_EXCLUSIONS
    assert "save" not in ar.NEVER_THE_ACTION

    # The behaviour that follows, both directions.
    assert advance_control(["button|Apply now Help", "button|Apply now"]) == ""
    assert advance_control(["button|Save and Continue",
                            "button|Continue"]) == "Save and Continue"


# --- a fallback must not be able to impersonate a reading -------------------------------------
#
# Live, Eversource 2026-08-16: the tab moved to Workday's SSO chooser ("Sign in with Apple /
# Google / LinkedIn"), no marker matched, and the mapper answered `workday_job_posting` — the
# value already recorded — so reconcile's `new != recorded` test concluded the window agreed and
# the ladder kept hunting for an Apply control on a sign-in page, three presses running.

_SSO_CHOOSER = ("Back to Job Posting Data Analyst, Asset Management Technology Sign In "
                "By choosing to sign in with a social account (e.g. Google, Apple ID, LinkedIn) "
                "Sign in with Apple Sign in with Google Sign in with LinkedIn OR Sign in with email")


def test_the_url_default_says_it_is_a_default():
    import apply_recipe as ar
    state, via = ar.map_workday_state_verbose("https://x.wd1.myworkdayjobs.com/job/y", _SSO_CHOOSER)
    assert state == "workday_job_posting"          # unchanged: we still have no better name …
    assert via == ar.NAMED_BY_URL_DEFAULT          # … but it no longer passes as an observation


def test_a_real_marker_is_reported_as_observed():
    import apply_recipe as ar
    u = "https://x.wd1.myworkdayjobs.com/j"
    _, via = ar.map_workday_state_verbose(u, "Start Your Application Autofill with Resume")
    assert via == ar.NAMED_BY_MARKER
    _, via2 = ar.map_workday_state_verbose(u, "current step 1 of 6 My Information")
    assert via2 == ar.NAMED_BY_PAGE


def test_describe_marks_a_defaulted_screen_unobserved():
    import apply_recipe as ar
    guess = ar.describe_workday_tab("https://x.wd1.myworkdayjobs.com/job/y", _SSO_CHOOSER)
    assert guess["state"] == "workday_job_posting" and guess["observed"] is False

    seen = ar.describe_workday_tab("https://x.wd1.myworkdayjobs.com/j",
                                   "current step 1 of 6 My Information")
    assert seen["state"] == "workday_my_information" and seen["observed"] is True


def test_greenhouse_default_is_also_marked():
    import apply_recipe as ar
    assert ar.describe_greenhouse_tab("https://boards.greenhouse.io/x", "some prose")["observed"] is False


def test_the_plain_mapper_keeps_its_signature():
    """Existing callers read the state alone and must not change behaviour."""
    import apply_recipe as ar
    assert ar.map_workday_state("https://x.wd1.myworkdayjobs.com/j", "") == "workday_job_posting"
    assert ar.map_workday_state("https://x.myworkdayjobs.com/job/y",
                                "current step 3 of 6 Application Questions") == "workday_questions"


def test_workdays_error_page_outranks_its_own_step_rail():
    """THE STEPPER IS CHROME AND IT SURVIVES THE FAILURE (live 2026-08-24, SolutionHealth JR13051).

    Workday renders "Something went wrong — Please refresh the page and then try again" in the
    CONTENT while the progress rail above keeps showing the step you were on. The state mapper read
    the rail first, so an error page came back named `workday_voluntary_disclosures` — an ordinary
    step name for a page with no form on it. Nothing matched `*_error_retry`, so the PLATFORM_ERROR
    recovery class (promoted 2026-08-20 for pages "whose entire content is try again") never fired
    and the rung sat reporting `mismatch` until the operator read the screen himself.
    """
    import apply_recipe as ar

    rail = ("ITSM Operations Analyst My Information My Experience Application Questions "
            "Voluntary Disclosures Self Identify Review ")
    state, named_by = ar.map_workday_state_verbose(
        "https://solutionhealth.wd1.myworkdayjobs.com/x/apply/applyManually",
        rail + "Something went wrong Please refresh the page and then try again.")
    assert state == "workday_error_retry", "the rail won over the page's own failure"
    assert named_by == ar.NAMED_BY_PAGE


def test_an_ordinary_step_is_still_read_from_the_rail():
    """The guard on the guard: a normal page must not be dragged into the error class."""
    import apply_recipe as ar

    state, _ = ar.map_workday_state_verbose(
        "https://solutionhealth.wd1.myworkdayjobs.com/x",
        "My Information My Experience Voluntary Disclosures Self Identify Review My Information")
    assert state != "workday_error_retry"


def test_a_field_validation_error_is_not_a_platform_error():
    """Both halves are required — a statement of failure AND the site's own remedy. A form error
    names a FIELD and is the census's business; routing it to recovery would retry a page that is
    waiting on an answer, which is the loop this rule exists to avoid."""
    import apply_recipe as ar

    state, _ = ar.map_workday_state_verbose(
        "https://solutionhealth.wd1.myworkdayjobs.com/x",
        "My Information Errors Found Error - The field State is required and must have a value.")
    assert state != "workday_error_retry"
