"""The escape ladder: fit the list, never invent — and never fit a question that is a claim."""

import prompt_escape as pe


def test_the_truth_wins_whenever_the_list_holds_it():
    opts = ["University of Santo Tomas", "Other", "Not Applicable"]
    got = pe.plan("School or University", "University of Santo Tomas", opts)
    assert got["resolution"] == "truth" and got["value"] == "University of Santo Tomas"


def test_the_most_specific_escape_is_preferred_over_a_bare_other():
    # BPS's list offered both; the qualified one carries more meaning to a human reader.
    opts = ["Boston College", "Other", "Other Foreign Educational Institution"]
    got = pe.plan("School or University", "University of Santo Tomas", opts)
    assert got["value"] == "Other Foreign Educational Institution"


def test_an_escape_is_only_ever_an_option_the_list_actually_offered():
    # No hatch present => escalate. Inventing "Other" would be typing a value the site never had.
    got = pe.plan("School or University", "University of Santo Tomas", ["Boston College", "MIT"])
    assert got["resolution"] == "escalate" and got["value"] is None


def test_a_load_bearing_question_refuses_the_ladder_even_when_a_hatch_exists():
    """THE EXPENSIVE DIRECTION. 'Other' on a school list is a shrug; on sponsorship or citizenship
    it asserts something the operator never said, on exactly the class of question that
    disqualifies (the 08-21 radio that silently held Yes)."""
    for label in ("Will you now or in the future require sponsorship?",
                  "Are you a U.S. citizen?", "Voluntary Self-Identification of Disability",
                  "What is your desired salary?"):
        got = pe.plan(label, "No", ["Yes", "No thanks", "Other", "Prefer not to say"])
        assert got["resolution"] == "escalate", label
        assert pe.is_load_bearing(label) is True


def test_other_matches_the_option_not_a_word_inside_one():
    # "Mother's maiden name" and "Another campus" both contain 'other'; neither is an escape.
    got = pe.plan("School or University", "UST", ["Mother's maiden name", "Another campus"])
    assert got["resolution"] == "escalate"


def test_the_decision_always_explains_itself():
    # Every branch carries a why: the record must say the answer was FITTED, not stated.
    for opts in (["University of Santo Tomas"], ["Other"], ["MIT"]):
        assert pe.plan("School or University", "University of Santo Tomas", opts)["why"]


EDU = ("If you do not require education for your role or you do not see your school listed, "
       "please type OTHER and hit the ENTER button for the option to populate.")
CERT = ("If there are no certifications required or if you do not have the required certification, "
        "please select NO CERTIFICATION NEEDED and hit enter button for the option to populate.")
GUIDANCE = ("If you do not require education for your role, please select the most recent "
            "completed degree under degree type.")


def test_the_section_that_governs_the_field_supplies_the_token():
    """THE LESSON THAT COST A WRONG FILL (live 2026-08-24, SolutionHealth JR13051). Both
    instructions sit on ONE screen, inches apart, and name DIFFERENT tokens. Education's OTHER was
    typed into the certification field: it looked filled and committed nothing, because that list
    has no such entry. The instruction is per-SECTION, so the correlation is the whole answer."""
    assert pe.plan("School or University", "University of Santo Tomas", [], EDU)["value"] == "OTHER"
    assert pe.plan("Certification", "none", [], CERT)["value"] == "NO CERTIFICATION NEEDED"


def test_guidance_prose_is_not_mistaken_for_a_token():
    # "please select the most recent completed degree" tells a human what to do; it names no value.
    assert pe.stated_escape(GUIDANCE) is None
    assert pe.stated_escape("please select your highest degree earned") is None


def test_a_stated_token_never_overrides_the_load_bearing_guard():
    """A site telling us to type a token would not make it a true answer about sponsorship.

    The options here deliberately EXCLUDE the operator's real answer — with "No" present the right
    result is `truth`, which is what the first draft of this test accidentally asserted against.
    The guard only has to hold when the truth is absent, which is the only time an escape is even
    considered."""
    got = pe.plan("Will you now or in the future require sponsorship?", "No",
                  ["Yes", "Other", "Prefer not to say"], CERT)
    assert got["resolution"] == "escalate"
    # And with the truth present it must simply answer truthfully, guard or no guard.
    ok = pe.plan("Will you now or in the future require sponsorship?", "No", ["Yes", "No"], CERT)
    assert ok["resolution"] == "truth" and ok["value"] == "No"


def test_the_stated_token_outranks_the_generic_ladder():
    # The ladder would have offered "Other"; the section says NO CERTIFICATION NEEDED, so it wins.
    got = pe.plan("Certification", "none", ["Other", "Not Applicable"], CERT)
    assert got["resolution"] == "stated" and got["value"] == "NO CERTIFICATION NEEDED"
