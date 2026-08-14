"""Landing classification — the re-orientation step for leaving Indeed.

The fixtures are REAL text measured on live landings, not invented prose. That matters more here
than usual: the whole module exists because the page we assumed we were reading was not the page
the content was on.
"""

import apply_landing as al

#: The top document of the first real iCIMS landing (jobs-joslin.icims.com, 2026-07-26): 691
#: characters of the hospital's own homepage. The job is nowhere in it.
_JOSLIN_TOP = """Skip Branding Skip to main content Global Menu FIND AN EXPERT MAKE AN APPOINTMENT
DONATE NOW Main navigation PATIENT CARE RESEARCH PROFESSIONAL EDUCATION ABOUT SUPPORT JOSLIN
SEARCH HOME ABOUT CAREERS Stay informed with the latest in Diabetes Care, Research, and
Development. Joslin Diabetes Center, Inc. One Joslin Place Boston, MA 02215 HARVARD MEDICAL
SCHOOL AFFILIATE © 2019 JOSLIN DIABETES CENTER PRIVACY POLICY TERMS OF USE SITEMAP"""

#: ...and what was actually inside #icims_content_iframe.
_JOSLIN_FRAME = """Skip to Main Content Welcome page Returning Candidate? Log back in!
Healthcare Data Analyst (Clinic Administration) Location US-MA-Boston Job ID N3915-26
# Positions 1 Category Clinic Pos. Type Full Time Overview The Healthcare Data Analyst will join
a collaborative team at Joslin Diabetes Center. Responsibilities include reporting and analysis.
Qualifications Bachelor's degree required. Apply for this job online"""


def test_the_content_frame_wins_over_the_wrapper():
    """The finding that made this module necessary: the top document is branding and the job is in
    a frame. Classifying the wrapper would call a real job landing a marketing page."""
    text, source = al.pick_content(_JOSLIN_TOP, [
        {"id": "icims_content_iframe", "readable": True, "text": _JOSLIN_FRAME,
         "width": 1249, "height": 1654},
        {"id": "a2a_sm_ifr", "readable": False, "text": None, "width": 0, "height": 0},
    ])
    assert source == "icims_content_iframe"
    assert "Healthcare Data Analyst" in text


def test_an_unreadable_frame_is_skipped_not_preferred():
    text, source = al.pick_content("some real text here", [
        {"id": "x", "readable": False, "text": None, "width": 1249, "height": 1654}])
    assert source == "top" and text == "some real text here"


def test_no_frames_means_the_top_document_is_all_there_is():
    text, source = al.pick_content("plain ats page", [])
    assert source == "top" and text == "plain ats page"


def test_the_live_icims_landing_is_a_job_posting():
    text, source = al.pick_content(_JOSLIN_TOP, [
        {"id": "icims_content_iframe", "readable": True, "text": _JOSLIN_FRAME,
         "width": 1249, "height": 1654}])
    landing = al.classify_kind(text, source=source)
    assert landing.kind == al.JOB_POSTING
    assert landing.evidence, "a classification must say what it saw"


def test_the_wrapper_alone_classifies_as_nothing_useful():
    """Proving the failure this module prevents: fed only the top document, there is no honest
    call to make — and `unknown` is the honest one."""
    assert al.classify_kind(_JOSLIN_TOP).kind == al.UNKNOWN


def test_empty_is_unreadable_not_unknown():
    """"Nothing to read" and "read it and could not tell" are different, and lead to different
    next moves — one needs a better probe, the other needs a human."""
    assert al.classify_kind("").kind == al.UNREADABLE


# --- precedence: a page trips several kinds at once ----------------------------------
def test_a_confirmation_is_never_read_as_a_posting():
    text = "Thank you for applying. Job description. Overview. Responsibilities."
    assert al.classify_kind(text).kind == al.CONFIRMATION


def test_a_closed_requisition_outranks_its_own_description():
    text = "Overview responsibilities qualifications. This job is no longer available."
    assert al.classify_kind(text).kind == al.GONE


def test_an_account_gate_is_not_a_form_the_agent_may_fill():
    text = "Returning candidate? Log back in! Create an account to continue."
    assert al.classify_kind(text).kind == al.ACCOUNT_GATE


def test_a_careers_listing_is_not_the_job_we_came_for():
    text = "Search jobs. Current openings. Filter by location. Sort by date."
    assert al.classify_kind(text).kind == al.JOB_LIST


def test_a_single_common_word_is_not_enough_evidence():
    """'Overview' appears on half the pages on the internet. One marker cannot carry a broad
    kind — only the decisive ones (a confirmation, a dead requisition) get to win on one."""
    assert al.classify_kind("Overview").kind == al.UNKNOWN


# --- the state id --------------------------------------------------------------------
def test_the_state_joins_platform_and_kind():
    assert al.landing_state("icims", al.JOB_POSTING) == "icims_job_posting"
    assert al.landing_state("company_site", al.JOB_LIST) == "company_site_job_list"


def test_an_employers_own_page_gets_the_same_kinds_as_a_named_ats():
    """The reason the axes are split. A company careers page cannot be recognised by host — every
    employer has a different one — so the platform axis gives up and the content axis still works.
    """
    text = "Job description. Overview. Responsibilities. Qualifications. Apply now."
    kind = al.classify_kind(text).kind
    assert kind == al.JOB_POSTING
    assert al.landing_state("company_site", kind) == "company_site_job_posting"


def test_a_zero_size_frame_is_a_tracker_not_the_content():
    """The frame beside the real one on the live page was 0x0 — a share/analytics pixel. Size is
    the structural tell: a wrapper delegates its BODY to one large frame."""
    text, source = al.pick_content("wrapper chrome", [
        {"id": "a2a_sm_ifr", "readable": True, "text": "x" * 5000, "width": 0, "height": 0}])
    assert source == "top"


def test_a_sparse_content_frame_still_beats_a_chatty_wrapper():
    """iCIMS's email gate is three lines, deliberately. A volume threshold excluded exactly the
    state we had just driven to and fell back to the wrapper's boilerplate — the frame's size on
    screen is the structural signal, not how much it says."""
    text, source = al.pick_content(_JOSLIN_TOP, [
        {"id": "icims_content_iframe", "readable": True, "width": 1249, "height": 1654,
         "text": "Welcome page\nEnter Your Information\nEmail\nApplication FAQs\n"
                 "Software Powered by ICIMS\nProtected by hCaptcha"}])
    assert source == "icims_content_iframe"
    assert al.classify_kind(text, source=source).kind == al.ACCOUNT_GATE


def test_a_frame_that_rendered_nothing_is_not_the_content():
    text, source = al.pick_content("real wrapper text", [
        {"id": "empty", "readable": True, "width": 1249, "height": 1654, "text": "  "}])
    assert source == "top"


# --- the credential form is an account gate on every platform -----------------------------------

#: The BrassRing sign-in wall as it actually renders, captured live 2026-08-14 (Boston Children's,
#: req 85104BR). A fixture rather than a paraphrase: the wording IS the evidence, and a paraphrase
#: tests the paraphrase.
BRASSRING_SIGN_IN = """Opportunityawaits...
Skip to main content
Job search Work at BCH Diversity & Inclusion
Back
Sign In
Sign in using username and password
Fields marked with an asterisk (*) are required.
*Username
*Password
Show password
Forgot Username or Password?
Sign in
Don't have an account yet?
 Privacy Practices Terms of Use
 Infinite Talent Privacy Statement
"""


def test_a_bare_credential_form_is_an_account_gate():
    """The table had "already have an account" and not its inverse, "sign in to continue" and not
    a sign-in form's own instruction — so this page classified as UNKNOWN, the ladder said
    "genuinely new territory", and the account rung sat staged and unreachable beside it.

    These are the phrases a login wall carries and essentially nothing else does: a password
    recovery link exists to recover a password, an invitation to create an account is offered
    where one is required.
    """
    landing = al.classify_kind(BRASSRING_SIGN_IN)
    assert landing.kind == al.ACCOUNT_GATE
    assert al.landing_state("brassring", landing.kind) == "brassring_account_gate"
    assert landing.evidence, "a classification with no evidence is a guess"


def test_a_recovery_link_in_a_forms_footer_does_not_make_it_a_login():
    """WEIGHED, not decisive — the reason the iCIMS header ("Returning Candidate? / Log back in!")
    did not turn a job posting into an account gate. A real application form outweighs a stray
    recovery link, and it must, or every ATS footer becomes a wall."""
    form = """Application
* Indicates a required field
First Name*
Last Name*
Upload your resume
Work Experience
Voluntary Disclosures
Personal Information
Forgot password?
"""
    assert al.classify_kind(form).kind == al.APPLICATION_FORM


#: BrassRing's application step 1, signed in, nothing filled — captured live 2026-08-14.
BRASSRING_FORM_0PCT = """Job successfully saved
Job search Work at BCH Diversity & Inclusion Candidate Zone  Sign Out
Back
Contact, Resume, Education and Experience
Percent of application completed
0%
Analyst I, Healthcare Data
Fields marked with an asterisk (*) are required.
* First name
Middle name
* Last name
* Address line 1
* City
* State/Region/Province
* Zip/Postal code
* Country/Region
* Home phone
"""


def test_a_page_measuring_its_own_incompleteness_is_not_a_confirmation():
    """THE WORST MISCLASSIFICATION THIS SYSTEM CAN MAKE, met live 2026-08-14.

    "Percent of application completed 0%" contains the substring "application complete", which was
    a DECISIVE confirmation marker — so the first screen of an untouched application classified as
    a SENT one, `steps_to_submit: 0`, on a form listing nine empty required fields. Marking a job
    applied-to that was never sent removes it from every future search and the operator never
    learns why.

    Fixed twice over on purpose: the marker now needs the copula a confirmation actually uses, and
    a progress meter blocks a decisive confirmation regardless. No confirmation page reports what
    percentage of itself is done.
    """
    landing = al.classify_kind(BRASSRING_FORM_0PCT)
    assert landing.kind == al.APPLICATION_FORM, landing.evidence
    assert al.landing_state("brassring", landing.kind) == "brassring_application_form"


def test_a_real_confirmation_is_still_decisive():
    """The guard must not cost us the thing it guards. A sent application still reads as sent."""
    for page in ("Thank you for applying. Your application has been submitted.",
                 "Thank You! You have successfully applied to Analyst I, Healthcare Data",
                 "We have received your application and will be in touch.",
                 "Your application is complete."):
        assert al.classify_kind(page).kind == al.CONFIRMATION, page


def test_a_confirmation_that_also_shows_a_step_counter_still_defers():
    """The guard is deliberately broad — "Step 2 of 6" is the same claim as a percentage. A page
    still walking its own stepper has not finished, whatever else it says."""
    mid = "Step 2 of 6  Application submitted successfully for the previous section"
    assert al.classify_kind(mid).kind != al.CONFIRMATION
