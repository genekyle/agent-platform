"""The resolver's DATA: field -> addressing + widget shape + vocabulary.

    The model says WHAT. The recipe says WHERE. The API says HOW.

This module is the WHERE. `apply_recipe.py` stays the FLOW (steps, states, branches, and the
hard-won prose lessons); this is the part code executes.

--------------------------------------------------------------------------------------
Why this exists as a new module rather than more dicts in apply_recipe.py
--------------------------------------------------------------------------------------
The recipes were INERT. Not "inconsistently shaped" — inert: no code path read any ATS
recipe's `fields`/`selectors` entry. Their only consumer was `recipe_spec()`, which
serialises them to JSON for one GET endpoint that shows them to the model. They are
documentation, and call sites re-hardcode them by hand — `routers/career_search.py`
re-implements Workday create-account field matching with inline substrings and cites
`apply_recipe.WORKDAY_CREATE_ACCOUNT_RECIPE` only in a docstring.

And they were not one shape. Across four sites there were SIX addressing shapes under two
different step keys (`fields` vs `selectors`), with three selector languages sharing one key
(CSS, a regex in `not_found_text`, and a Playwright `text=` pseudo-selector). Nothing could
have resolved against that, which is why nothing tried.

So the fix isn't "unify the schema" — it is "make the recipe executable at all", and that
wants a data structure with tests, not more prose.

--------------------------------------------------------------------------------------
Where the knowledge came from
--------------------------------------------------------------------------------------
Every entry below is transcribed from something already proven live and written down in
`apply_recipe.GREENHOUSE_LESSONS` / `WORKDAY_LESSONS` / the step `selectors`. Nothing here
is invented. The lessons remain the narrative; this is the machine-readable projection of
the mechanical parts of them (which widget, where its truth lives, what it calls things).
When the two disagree, the lessons are the history and this is the contract.

`widget_type` is a HINT, not an assertion: /describe_widget still verifies against the live
page. A hint that turns out wrong is the flywheel finding a drift, which is the point.
"""

from __future__ import annotations

from typing import Any, Optional

from interaction.contract import WidgetType

#: How a field is addressed. The five addressing modes `/execute` accepts collapse to these
#: two, because they are the only two that survive a DOM reshuffle:
#:   role_name — CDP-AX role + accessible name -> backend_node_id at act time (PRINCIPLES §6)
#:   selector  — CSS, for elements the AX tree cannot name (a hidden <input type=file>)
#: `bbox` and a raw `backend_node_id` are deliberately NOT addressing modes here: a bbox goes
#: stale on any reflow and a node id churns between select and act. They remain on /execute as
#: a mechanism-tier input; the recipe never speaks them.
ADDRESSED_BY_ROLE_NAME = "role_name"
ADDRESSED_BY_SELECTOR = "selector"


def _f(
    *,
    ats: str,
    widget_type: WidgetType,
    selector: Optional[str] = None,
    role: Optional[str] = None,
    name: Optional[str] = None,
    answer_key: Optional[str] = None,
    vocabulary: Optional[dict[str, str]] = None,
    commit: Optional[str] = None,
    note: str = "",
    optional: bool = False,
) -> dict[str, Any]:
    """One field entry. `addressed_by` is DERIVED, never hand-written — a hand-written
    discriminator is a thing that can disagree with the data it discriminates."""
    if selector and (role or name):
        raise ValueError(f"{ats}: a field is addressed one way — got both selector and role/name")
    if not selector and not name:
        raise ValueError(f"{ats}: a field needs a selector or an accessible name")
    return {
        "ats": ats,
        "addressed_by": ADDRESSED_BY_SELECTOR if selector else ADDRESSED_BY_ROLE_NAME,
        "selector": selector,
        "role": role,
        "name": name,
        "widget_type": widget_type.value,
        "answer_key": answer_key,
        "vocabulary": vocabulary or {},
        "commit": commit,          # None = applies on select; else the footer button's label
        "note": note,
        "optional": optional,
    }


# --- GREENHOUSE ---------------------------------------------------------------------
# Selectors from GREENHOUSE_APPLY_RECIPE step 0; widget types from GREENHOUSE_LESSONS
# ("react_select_widgets", "date_inputs", "phone_country_field").
GREENHOUSE_FIELDS: dict[str, dict[str, Any]] = {
    "first_name": _f(ats="greenhouse", selector="#first_name", widget_type=WidgetType.TEXT,
                     answer_key="first_name"),
    "last_name": _f(ats="greenhouse", selector="#last_name", widget_type=WidgetType.TEXT,
                    answer_key="last_name"),
    "email": _f(ats="greenhouse", selector="#email", widget_type=WidgetType.TEXT,
                answer_key="email"),
    # #country is the PHONE country code (renders "+1"), NOT the address country. Naming it
    # `phone_country` here rather than `country` is the whole reason a resolver beats a
    # selector: the id lies, and the field name is where we get to tell the truth once.
    "phone_country": _f(ats="greenhouse", selector="#country", widget_type=WidgetType.REACT_SELECT,
                        note="phone country CODE, not the address country (GREENHOUSE_LESSONS)"),
    "phone": _f(ats="greenhouse", selector="#phone", widget_type=WidgetType.TEXT,
                answer_key="phone"),
    "location_city": _f(ats="greenhouse", selector="#candidate-location",
                        widget_type=WidgetType.REACT_SELECT, answer_key="location",
                        note="autocomplete — pick the suggestion; exact-match (/Concord/ picked "
                             "'Concordia, Entre Rios, Argentina')"),
    "resume": _f(ats="greenhouse", selector="#resume", widget_type=WidgetType.FILE,
                 note="input[type=file]; DOM.setFileInputFiles (a click opens an OS dialog CDP "
                      "cannot drive)"),
    "cover_letter": _f(ats="greenhouse", selector="#cover_letter", widget_type=WidgetType.FILE,
                       optional=True),
    "company_name": _f(ats="greenhouse", selector="#company-name-0", widget_type=WidgetType.TEXT,
                       note="current/most-recent employer"),
    "title": _f(ats="greenhouse", selector="#title-0", widget_type=WidgetType.TEXT),
    # MONTH and YEAR are DIFFERENT widgets — month is a react-select wanting the NAME
    # ("08" yields zero options), year is a plain number input. One `month_year` field rather
    # than two, because the caller's intent is one date; /set_date owns the two-widget dance.
    "work_start_date": _f(ats="greenhouse", selector="#start-date-month-0",
                          widget_type=WidgetType.MONTH_YEAR,
                          note="month=react-select (name, not '08'); year=#start-date-year-0 number input"),
    "work_end_date": _f(ats="greenhouse", selector="#end-date-month-0",
                        widget_type=WidgetType.MONTH_YEAR,
                        note="`disabled` when 'Current role' is ticked — and the '*' and "
                             "aria-required STAY. disabled beats the asterisk."),
    "school": _f(ats="greenhouse", selector="#school--0", widget_type=WidgetType.REACT_SELECT,
                 answer_key="education_school",
                 vocabulary={"University of Santo Tomas": "Other"},
                 note="UST is genuinely absent from the list (verified: Ateneo de Manila IS "
                      "present, so the absence is real) -> Other. Never invent credentials."),
    "degree": _f(ats="greenhouse", selector="#degree--0", widget_type=WidgetType.REACT_SELECT,
                 answer_key="education_degree",
                 vocabulary={"Bachelor of Science": "Bachelor's Degree"}),
    "discipline": _f(ats="greenhouse", selector="#discipline--0", widget_type=WidgetType.REACT_SELECT,
                     answer_key="education_discipline",
                     vocabulary={"Sports Science": "Kinesiology"},
                     note="the operator volunteered this mapping unprompted — the alias table "
                          "asking to exist"),
    "education_start_date": _f(ats="greenhouse", selector="#start-month--0",
                               widget_type=WidgetType.MONTH_YEAR, answer_key="education_start_date"),
    "education_end_date": _f(ats="greenhouse", selector="#end-month--0",
                             widget_type=WidgetType.MONTH_YEAR, answer_key="education_end_date"),
    "submit": _f(ats="greenhouse", role="button", name="APPLY", widget_type=WidgetType.UNKNOWN),
}

# --- WORKDAY ------------------------------------------------------------------------
# data-automation-id selectors are STABLE ACROSS TENANTS (Workday renders them from one
# component library) — which is what makes this recipe transferable rather than
# State-Street-specific (WORKDAY_LESSONS).
WORKDAY_FIELDS: dict[str, dict[str, Any]] = {
    "email": _f(ats="workday", role="textbox", name="Email Address", widget_type=WidgetType.TEXT,
                answer_key="email"),
    "password": _f(ats="workday", role="textbox", name="Password", widget_type=WidgetType.TEXT,
                   note="OPERATOR-ONLY. The agent never types passwords (PRINCIPLES / the "
                        "account boundary). Present so the Account Manager can resolve it."),
    "verify_password": _f(ats="workday", role="textbox", name="Verify New Password",
                          widget_type=WidgetType.TEXT, note="OPERATOR-ONLY — as above."),
    "acknowledge": _f(ats="workday", role="checkbox",
                      name="I confirm that I have read and acknowledge",
                      widget_type=WidgetType.CHECKBOX_GROUP),
    # The honeypot is DATA the resolver must know about, so a future "fill every field" pass
    # can be made to refuse it by name rather than by luck.
    "honeypot_do_not_fill": _f(ats="workday", role="textbox",
                               name="Enter website. This input is for robots only",
                               widget_type=WidgetType.TEXT,
                               note="BOT HONEYPOT — never fill. Present so it can be refused "
                                    "explicitly instead of accidentally skipped."),
    "first_name": _f(ats="workday", selector="[data-automation-id='legalName--firstName']",
                     widget_type=WidgetType.TEXT, answer_key="first_name"),
    "how_did_you_hear": _f(ats="workday", selector="[data-automation-id=formField-source]",
                           widget_type=WidgetType.PROMPT_HIERARCHICAL,
                           note="nested prompt (Online Source -> Indeed). Opens on a native "
                                "node-click, searches on TRUSTED per-char keys."),
    "phone_device_type": _f(ats="workday",
                            selector="[data-automation-id='formField-phoneType'] button",
                            widget_type=WidgetType.ARIA_LISTBOX,
                            vocabulary={"Mobile": "Mobile"},
                            note="applies on select — no footer commit"),
    "previous_worker": _f(ats="workday", selector="[data-automation-id=candidateIsPreviousWorker]",
                          widget_type=WidgetType.RADIO_GROUP),
    "resume": _f(ats="workday", selector="input[type=file]", widget_type=WidgetType.FILE),
    "next": _f(ats="workday", selector="button[data-automation-id=bottom-navigation-next-button]",
               widget_type=WidgetType.UNKNOWN),
    "sign_in_submit": _f(ats="workday", selector="[data-automation-id=signInSubmitButton]",
                         widget_type=WidgetType.UNKNOWN),
    "create_account_submit": _f(ats="workday", role="button", name="Create Account",
                                widget_type=WidgetType.UNKNOWN),
}

# --- INDEED -------------------------------------------------------------------------
INDEED_FIELDS: dict[str, dict[str, Any]] = {
    # The distance pill is the one field we have with a FOOTER COMMIT — selecting only
    # STAGES the value; Update is what applies it. This is why `commit` is a column.
    "distance": _f(ats="indeed", widget_type=WidgetType.ARIA_LISTBOX,
                   selector="#radius_filter_button, button[id*=radius], [aria-label*='Distance' i]",
                   commit="Update",
                   note="staged-commit: the footer Update commits AND navigates, so it cannot "
                        "be confirmed from inside the page — confirm from the URL."),
}

# --- iCIMS --------------------------------------------------------------------------
# Transcribed from the live AX scan of jobs-joslin.icims.com 2026-07-26 ("Basic Information",
# step 1 of 4). Two properties make iCIMS unlike every ATS above, and both are load-bearing:
#
#  * THE FORM LIVES INSIDE `#icims_content_iframe` on the employer's branded wrapper. Role+name
#    addressing crosses that frame (the AX scan flattens frames); a CSS selector does NOT
#    (`_resolve_node_by_selector` runs `DOM.querySelector` on the top document alone). So every
#    entry here is addressed by role+name — deliberately, not by preference. An unscoped selector
#    or a loose name match reaches the WRAPPER first: the hospital's newsletter box is also a
#    `textbox` and its name is "Enter your email address here", which is why the field below is
#    the exact name "Email" and nothing looser.
#  * STEP 1 IS THE ACCOUNT AND THE APPLICATION AT ONCE. There is no separate create-account page:
#    name, email, login, password and the resume sit on one form behind one "Submit Profile".
#    `create_account_submit` is therefore also the application's first commit — which is why the
#    account rung on iCIMS cannot be "get past the wall, then start the form".
#
# The password fields carry the site's own rule in their accessible name (min 8, 1 alphabetic,
# 1 lower, 1 upper, 1 numeric, 1 special). Kept verbatim: it is the exact-match target AND the
# constraint any generated credential must satisfy.
_ICIMS_PW_RULE = ("Minimum 8 characters, 1 alphabetic, 1 lowercase, 1 uppercase, 1 numeric, "
                  "1 special character(s)")

ICIMS_FIELDS: dict[str, dict[str, Any]] = {
    "first_name": _f(ats="icims", role="textbox", name="First Name",
                     widget_type=WidgetType.TEXT, answer_key="first_name"),
    "last_name": _f(ats="icims", role="textbox", name="Last Name",
                    widget_type=WidgetType.TEXT, answer_key="last_name"),
    "email": _f(ats="icims", role="textbox", name="Email", widget_type=WidgetType.TEXT,
                answer_key="email",
                note="EXACT name. The branded wrapper's newsletter box ('Enter your email address "
                     "here') is the first email textbox in document order — a substring match on "
                     "'email' typed the operator's address into the hospital mailing list."),
    # iCIMS wants a username SEPARATE from the email. We put the same address in both: one
    # credential to remember, and the account convention is already "one shared address".
    "login": _f(ats="icims", role="textbox", name="Login", widget_type=WidgetType.TEXT,
                answer_key="email", note="the account's USERNAME, distinct from the email field"),
    "password": _f(ats="icims", role="textbox", name=f"Password: {_ICIMS_PW_RULE}",
                   widget_type=WidgetType.TEXT),
    "verify_password": _f(ats="icims", role="textbox",
                          name=f"Password (Re-enter): {_ICIMS_PW_RULE}",
                          widget_type=WidgetType.TEXT),
    "resume": _f(ats="icims", role="button",
                 name="My Computer (Opens new window) Or please select your resume from one of "
                      "the following:",
                 widget_type=WidgetType.FILE, optional=True,
                 note="NOT starred on the form — the resume is a profile PREFILL alternative to "
                      "the social-SSO buttons, not a required field of step 1. The control is a "
                      "button parked off-screen (x=-3833) fronting a file input; treat it as FILE "
                      "and set files on the input rather than clicking (a click opens an OS "
                      "dialog CDP cannot drive)."),
    "create_account_submit": _f(ats="icims", role="button", name="Submit Profile",
                                widget_type=WidgetType.UNKNOWN,
                                note="creates the account AND commits step 1. Not the "
                                     "application's final Submit — that is the last portal form."),

    # --- Candidate Profile (step 2 of 5). Driven live 2026-07-27. -------------------------------
    "profile_resume": _f(ats="icims", role="button",
                         name="My Computer (Opens new window) Please upload your resume "
                              "(max size: 5 MB)",
                         widget_type=WidgetType.FILE,
                         note="REQUIRED here (unlike step 1). The control is the file input "
                              "itself, parked off-screen; `upload` sets files on it directly. "
                              "'Upload Resume at a later time' is the escape hatch if no resume "
                              "is held."),
    "profile_phone": _f(ats="icims", role="textbox", name="Number", widget_type=WidgetType.TEXT,
                        answer_key="phone"),
    # THE DUPLICATE NAME. Both the phone and the address type render as a combobox named exactly
    # "Type", and role+name resolution takes the first in document order — the phone one. That is
    # correct for this entry and WRONG for the address, which is why the address type is not
    # addressable here at all: see the note on `profile_address_type`.
    "profile_phone_type": _f(ats="icims", role="combobox", name="Type",
                             widget_type=WidgetType.NATIVE_SELECT,
                             answer_key="phone_device_type",
                             note="the FIRST 'Type' in document order (phones)."),
    "profile_address_type": _f(ats="icims", role="combobox", name="Type",
                               widget_type=WidgetType.UNKNOWN,
                               note="THE SECOND 'Type' — NOT REACHABLE by this addressing. Role + "
                                    "accessible name cannot distinguish it from the phone's, and a "
                                    "selector cannot cross the iCIMS frame. Resolve it from a scan "
                                    "taken immediately before acting (order by bbox y) and drive "
                                    "it by backend_node_id. Recorded here so the ambiguity is "
                                    "DATA rather than a surprise on the next drive."),
    "profile_street": _f(ats="icims", role="textbox", name="Street", widget_type=WidgetType.TEXT,
                         answer_key="street_address"),
    "profile_city": _f(ats="icims", role="textbox", name="City", widget_type=WidgetType.TEXT,
                       answer_key="city"),
    "profile_zip": _f(ats="icims", role="textbox", name="Zip", widget_type=WidgetType.TEXT,
                      answer_key="postal_code"),
    "profile_country": _f(ats="icims", role="combobox", name="Country",
                          widget_type=WidgetType.UNKNOWN, answer_key="country",
                          note="searchable custom widget, not a native select: options exist only "
                               "while open. Set this BEFORE state — state stays empty until a "
                               "country is chosen."),
    "profile_state": _f(ats="icims", role="combobox", name="State", widget_type=WidgetType.UNKNOWN,
                        answer_key="state",
                        note="searchable custom widget with a WINDOWED list (25 of 50 states "
                             "rendered). Open it, type the full state NAME into its own '— Type "
                             "to Search —' box with real keystrokes, then click the option by "
                             "accessible name. Wants 'New Hampshire', not 'NH'."),
    "profile_source": _f(ats="icims", role="combobox", name="How did you hear about us?",
                         widget_type=WidgetType.NATIVE_SELECT, answer_key="how_did_you_hear",
                         vocabulary={"Indeed": "Indeed.com"},
                         note="native select, employer-specific option list — Joslin spells Indeed "
                              "'Indeed.com'."),
    "profile_submit": _f(ats="icims", role="button", name="Submit Profile",
                         widget_type=WidgetType.UNKNOWN),

    # --- EEO (step 3 of 5) -----------------------------------------------------------------
    "eeo_decline": _f(ats="icims", role="checkbox", name="I do not wish to self-identify",
                      widget_type=WidgetType.CHECKBOX_GROUP,
                      note="satisfies the starred Gender/Race/Veteran selects on its own — the "
                           "step submits with all three left unselected (verified live)."),
    "eeo_submit": _f(ats="icims", role="button", name="Submit", widget_type=WidgetType.UNKNOWN),
}

# --- SAP SUCCESSFACTORS ---------------------------------------------------------------
# Transcribed from the live create-account form on career41.sapsf.com (Teradyne tenant),
# 2026-07-28. Reached from the employer's own job page via Apply now -> Apply Now; the SIGN-IN
# gate comes first and offers "Create an account".
#
# THE REAL SAP HOST IS sapsf.com. The job page lives on the employer's domain (jobs.teradyne.com)
# and hands off here, so the two halves of one application sit on two different hosts.
#
# Password rules are stated ON the form and are tighter than most: 8-18 characters, at least one
# upper and one lower, at least one number OR punctuation, no spaces or unicode. Verified that the
# derived credential satisfies all five BEFORE submitting — a rejected password costs a submit and
# leaves a half-made account.
SUCCESSFACTORS_FIELDS: dict[str, dict[str, Any]] = {
    "email": _f(ats="successfactors", role="textbox", name="Email Address: *",
                widget_type=WidgetType.TEXT, answer_key="email"),
    "verify_email": _f(ats="successfactors", role="textbox", name="Retype Email Address: *",
                       widget_type=WidgetType.TEXT, answer_key="email"),
    "password": _f(ats="successfactors", role="textbox", name="Choose Password: *",
                   widget_type=WidgetType.TEXT,
                   note="8-18 chars, >=1 upper, >=1 lower, >=1 number or punctuation, no space "
                        "or unicode — the form states the rules; check before submitting"),
    "verify_password": _f(ats="successfactors", role="textbox", name="Retype Password: *",
                          widget_type=WidgetType.TEXT),
    "first_name": _f(ats="successfactors", role="textbox", name="First Name: *",
                     widget_type=WidgetType.TEXT, answer_key="first_name"),
    "last_name": _f(ats="successfactors", role="textbox", name="Last Name: *",
                    widget_type=WidgetType.TEXT, answer_key="last_name"),
    "country": _f(ats="successfactors", role="combobox", name="Country/Region of Residence",
                  widget_type=WidgetType.NATIVE_SELECT, answer_key="country"),
    # REQUIRED consent. The operator's stored consents_ok includes privacy_policy, which is what
    # this is; it is still recorded as its own field so the acceptance is deliberate and visible
    # rather than a checkbox swept up by a fill-everything pass.
    #
    # NOT VERIFIED TO ACCEPT IN ONE CLICK. On the live page it renders as an underlined LINK
    # ("Read and accept the data privacy statement.") whose AX role is button, and SAP names it
    # 'Read AND ACCEPT' — which reads like it opens a statement to be read and accepted, not like a
    # toggle. Nobody has clicked it: accepting a data-privacy agreement is the operator's to give,
    # so the create leg stops before it. If it does open a modal, `confirms` clicking it once will
    # leave that modal open and the submit will fail — treat that as expected until someone
    # watches it happen and writes down what they saw.
    # REQUIRED, and a STAGED WIDGET: the link OPENS a "Data Privacy Consent Statement" dialog, and
    # the acceptance happens on the Accept button INSIDE it. Driven live 2026-07-28.
    #
    # ADDRESSED BY SELECTOR, and that is the whole lesson. The accessible name AX offers for this
    # row is the composite "Terms of Use Read and accept the data privacy statement. Required" —
    # the label, the control and the required-marker fused into one node. Clicking THAT node does
    # not open the dialog: it NAVIGATES BACK TO THE SIGN-IN GATE and takes the whole half-filled
    # form with it, while /execute reports `outcome: ok`. The real control is a child anchor,
    # `<a id="dataPrivacyId" role="button">` with no href, and only the selector reaches it.
    # This is the AX-finds-elements-not-widgets case in its most expensive form.
    "terms": _f(ats="successfactors", selector="#dataPrivacyId",
                widget_type=WidgetType.CHECKBOX_GROUP,
                commit="Accept",
                note="OPENER. Clicking it opens the consent dialog — and ONLY once the rest of "
                     "the form validates; on an incomplete form the same click just paints the "
                     "required-field errors. So this step must run AFTER every other field, "
                     "password included. Never address it by accessible name (see above)."),
    "terms_accept": _f(ats="successfactors", role="button", name="Accept",
                       widget_type=WidgetType.UNKNOWN,
                       note="The COMMIT, inside the consent dialog. Beside it sit Decline and "
                            "Print. Not disabled by a scroll gate — the statement fits the dialog "
                            "— but confirm from OUTSIDE afterwards: the row must read 'Data "
                            "privacy statement has been accepted.'"),
    # The SAME dialog, arriving on its own after a successful sign-in — see
    # `successfactors_policy_gate`. Same control, named separately because the two are different
    # MOMENTS: one is staged by a click we made, this one interrupts us, and a recipe that cannot
    # tell them apart will wait for an opener that is never coming.
    "policy_gate_accept": _f(ats="successfactors", role="button", name="Accept",
                             widget_type=WidgetType.UNKNOWN,
                             note="Post-sign-in policy gate. UNPROMPTED — no opener precedes it. "
                                  "Left unaccepted it drops the session back to the sign-in wall "
                                  "(observed 2026-07-29: dialog gone, logged_in false, "
                                  "loginFlowRequired=true), so it must be cleared immediately "
                                  "after the sign-in submit, before any other rung."),
    # MARKETING — and they arrive CHECKED. This said "both default-off" until 2026-07-28, when the
    # live form was actually looked at: both boxes are ticked on first render. That made the whole
    # protection backwards. The design here was "a field this driver never names is one it can
    # never tick by accident" — true, and useless, because the danger was never that we would tick
    # them. It was that SAP already had. Omitting them meant CONSENTING by default, against the
    # operator's stored marketing_contact_consent=No, and nothing would ever have said so: the
    # account gets made, the application goes through, and the marketing email starts arriving.
    #
    # So they are REFUSED ACTIVELY now (_ACCOUNT_FORMS "refusals"), driven through /check_group with
    # an empty value set, which unticks by click and re-reads the DOM to confirm. Addressed by
    # SELECTOR because that is what /check_group takes, and because the accessible name of the
    # first one is "Notification:" — the label of the row, not of the control.
    "opt_in_job_notifications": _f(ats="successfactors", selector="#fbclc_emailEnabled",
                                   widget_type=WidgetType.CHECKBOX_GROUP, optional=True,
                                   note="MARKETING — 'Receive new job posting notifications'. "
                                        "ARRIVES CHECKED; must be actively unticked "
                                        "(marketing_contact_consent=No)."),
    "opt_in_career_news": _f(ats="successfactors", selector="#fbclc_campaignEmailEnabled",
                             widget_type=WidgetType.CHECKBOX_GROUP, optional=True,
                             note="MARKETING — 'Hear more about career opportunities'. ARRIVES "
                                  "CHECKED; must be actively unticked."),
    "create_account_submit": _f(ats="successfactors", role="button", name="Create Account",
                                widget_type=WidgetType.UNKNOWN),
    # The sign-in leg, on the gate that precedes this form.
    "signin_email": _f(ats="successfactors", role="textbox", name="Email Address:",
                       widget_type=WidgetType.TEXT, answer_key="email"),
    "signin_password": _f(ats="successfactors", role="textbox", name="Password:",
                          widget_type=WidgetType.TEXT),
    "sign_in_submit": _f(ats="successfactors", role="button", name="Sign In",
                         widget_type=WidgetType.UNKNOWN),

    # ----------------------------------------------------------------------------------------
    # CANDIDATE PROFILE — a DIFFERENT form from the create-account leg above, on the signed-in
    # /portalcareer page (state successfactors_candidate_profile). Measured live 2026-07-30.
    #
    # THE ACCORDION IS A PRECONDITION, NOT DECORATION. The page is nine collapsed section bars.
    # A collapsed section's fields are ABSENT FROM THE AX TREE — the collapsed scan returned 25
    # candidates and not one textbox; expanding "Profile Information" took it to 41 with all
    # thirteen. So addressing any field below without expanding its section first does not
    # mis-click, it returns NOT_FOUND, and the recipe reads as stale when it is merely early.
    # Expand the section, THEN address the field.
    #
    # The bars themselves need no selector treatment — this was the open question and the answer
    # is clean. Each is a real <button> whose accessible name is its visible label, and each
    # carries aria-expanded, so the open/closed state is READ, never inferred from geometry.
    # (They expose no aria-controls, so a bar does not name its content region; scoping a scan to
    # one section is still unsolved and is why the fields below are addressed page-wide.)
    # ----------------------------------------------------------------------------------------
    #
    # THE WHITESPACE ARTIFACT, and why it is survivable. Every REQUIRED label holds a
    # `<span class="requiredField" aria-hidden="true">*</span>`. aria-hidden strips the asterisk
    # from the accessible name but NOT the space around it, so AX reports " First Name" with a
    # leading space while optional "Middle Name" has none. Names are written clean below because
    # `_resolve_ax_node` strips both sides before comparing (main_server.py:190) — verified, not
    # assumed. Do NOT use that space as a required-detector: it is absent on Country and
    # State / Province, which are required but take their name from aria-label instead of the
    # label element. `aria-required` is the honest signal.
    "profile_expand_all": _f(ats="successfactors", role="button", name="Expand all sections",
                             widget_type=WidgetType.UNKNOWN,
                             note="Opens all nine sections at once — the cheapest way to make "
                                  "every field addressable before a scan. Its twin is 'Collapse "
                                  "all sections'; both are <a role=button>, not <button>."),
    "profile_section_profile_information": _f(
        ats="successfactors", role="button", name="Profile Information",
        widget_type=WidgetType.UNKNOWN,
        note="Section bar. Click toggles aria-expanded; verified false->true live, with the bars "
             "below shifting ~514px down. Holds the thirteen identity fields below."),
    "profile_section_employment_history": _f(
        ats="successfactors", role="button", name="Employment History",
        widget_type=WidgetType.UNKNOWN, note="Section bar — contents not yet mapped."),
    "profile_section_formal_education": _f(
        ats="successfactors", role="button", name="Formal Education",
        widget_type=WidgetType.UNKNOWN, note="Section bar — contents not yet mapped."),
    # MATCH THIS ONE BY PREFIX. Its accessible name carries a live COUNT — "Jobs Applied (2)" —
    # so an exact match breaks the moment an application lands. Same shape as LinkedIn's
    # `button "Location Greater Boston"`: a control whose name carries its value.
    "profile_section_jobs_applied": _f(
        ats="successfactors", role="button", name="Jobs Applied",
        widget_type=WidgetType.UNKNOWN,
        note="Section bar, NAME CARRIES A COUNT ('Jobs Applied (2)'). Resolves on the resolver's "
             "substring fallback, never on exact match — do not 'fix' this by pasting today's "
             "count in. The count is also the cheapest read of how many applications this "
             "tenant thinks we have sent."),
    "profile_first_name": _f(ats="successfactors", role="textbox", name="First Name",
                             widget_type=WidgetType.TEXT, answer_key="first_name"),
    "profile_middle_name": _f(ats="successfactors", role="textbox", name="Middle Name",
                              widget_type=WidgetType.TEXT, optional=True),
    "profile_last_name": _f(ats="successfactors", role="textbox", name="Last Name",
                            widget_type=WidgetType.TEXT, answer_key="last_name"),
    "profile_address": _f(ats="successfactors", role="textbox", name="Address",
                          widget_type=WidgetType.TEXT, answer_key="street_address"),
    "profile_city": _f(ats="successfactors", role="textbox", name="City",
                       widget_type=WidgetType.TEXT, answer_key="city"),
    # ROLE-GATED ON PURPOSE, and this is load-bearing. Country and State each appear TWICE under
    # ONE accessible name: the `input[role=combobox]` that holds the value, and a
    # `<button id="81:_selectButton">` that opens the picker. `_resolve_ax_node` takes exact[0]
    # in document order when the role is not given — a coin flip between typing the value and
    # opening a dropdown. This is the "five links named Show all" trap with two candidates
    # instead of five, and the role is what disambiguates it. Never drop the role here.
    #
    # WIDGET SHAPE IS DELIBERATELY UNKNOWN, not a guess. It is an `input[role=combobox]`
    # (aria-expanded, editable, value read straight off `.value` — held "United States" /
    # "New Hampshire" live) paired with a sibling icon button. That is neither a native select
    # nor a react-select, and calling it either would dispatch the wrong protocol at it.
    # UNKNOWN routes to /describe_widget, which is what this enum's own docstring prescribes for
    # a shape not yet in it; the probe's answer is what earns a new member.
    "profile_country": _f(ats="successfactors", role="combobox", name="Country",
                          widget_type=WidgetType.UNKNOWN, answer_key="country",
                          note="input[role=combobox] + sibling opener button SHARING its name — "
                               "role gating is mandatory. Typing likely fetches per keystroke; "
                               "treat as a data cost in low-data mode."),
    "profile_state": _f(ats="successfactors", role="combobox", name="State / Province",
                        widget_type=WidgetType.UNKNOWN, answer_key="state",
                        note="Same doubled-name shape as profile_country. Role gating mandatory."),
    "profile_zip": _f(ats="successfactors", role="textbox", name="Postal Code",
                      widget_type=WidgetType.TEXT, answer_key="postal_code"),
    "profile_primary_phone": _f(ats="successfactors", role="textbox", name="Primary Phone",
                                widget_type=WidgetType.TEXT, answer_key="phone",
                                note="DOM name is `cellPhone`; the label is not."),
    "profile_alternate_phone": _f(ats="successfactors", role="textbox", name="Alternate Phone",
                                  widget_type=WidgetType.TEXT, optional=True,
                                  note="DOM name is `homePhone`."),
    "profile_email": _f(ats="successfactors", role="textbox", name="Email",
                        widget_type=WidgetType.TEXT, answer_key="email"),
    "profile_current_company": _f(ats="successfactors", role="textbox", name="Current Company",
                                  widget_type=WidgetType.TEXT,
                                  note="REQUIRED (aria-required), and no answer_key covers it — "
                                       "the operator's profile has nowhere to put it yet."),
    "profile_current_title": _f(ats="successfactors", role="textbox", name="Current Title",
                                widget_type=WidgetType.TEXT, optional=True),
    # ONE Save for the whole accordion, sitting below the last bar — not one per section.
    "profile_save": _f(ats="successfactors", role="button", name="Save",
                       widget_type=WidgetType.UNKNOWN,
                       note="Commits the WHOLE profile, every section. Never clicked live yet; "
                            "the profile already held the operator's real values on first sight, "
                            "so nothing has needed saving."),
}

_BY_ATS: dict[str, dict[str, dict[str, Any]]] = {
    "greenhouse": GREENHOUSE_FIELDS,
    "workday": WORKDAY_FIELDS,
    "indeed": INDEED_FIELDS,
    "icims": ICIMS_FIELDS,
    "successfactors": SUCCESSFACTORS_FIELDS,
}


class FieldNotFound(KeyError):
    """Raised when (ats, field) doesn't resolve. Maps to Outcome.NOT_FOUND."""


def resolve(ats: str, field: str) -> dict[str, Any]:
    """(ats, field) -> the field's addressing + widget shape + vocabulary.

    THE function that turns the recipe from prose into data. The model says
    `select_option("phone_device_type", "Mobile")`; this says where it is and what it is.

    Raises FieldNotFound rather than returning None: a missing field is a stale recipe and
    must be loud (Outcome.NOT_FOUND), not a None that flows on and fails as something else
    three layers down.
    """
    table = _BY_ATS.get((ats or "").strip().lower())
    if table is None:
        raise FieldNotFound(f"unknown ats {ats!r} (known: {', '.join(sorted(_BY_ATS))})")
    entry = table.get((field or "").strip().lower())
    if entry is None:
        raise FieldNotFound(
            f"{ats}: no field {field!r}. Known: {', '.join(sorted(table))}. "
            f"If the form really has it, add it here — do not hardcode a selector at the "
            f"call site (that is how the recipe went inert)."
        )
    return entry


def known_fields(ats: str) -> list[str]:
    return sorted(_BY_ATS.get((ats or "").strip().lower(), {}))


def known_ats() -> list[str]:
    return sorted(_BY_ATS)


# --- password policies ------------------------------------------------------------------
#: What each ATS will ACCEPT as a password, as data rather than prose.
#:
#: These rules were already written down — three times, in `ats_registry`, in `apply_recipe`'s
#: lessons, and in the `password` field's own note — and no code read any of them. The
#: credential we type is DERIVED (company initials + a shared suffix), so whether it satisfies
#: a given ATS is a property of the COMPANY NAME, not of anything the operator chose: Teradyne
#: has one initial, so its password is exactly 8 characters, which is SAP's floor. A company
#: whose name yields a single initial and a shorter suffix produces a password that is rejected
#: at submit — and a rejected password is not a free retry. It costs a submit, and leaves a
#: half-made account that the next run has no way to tell from a made one.
#:
#: So the check runs BEFORE anything is typed. Absent = no stated rules; that means "we have not
#: read this form's rules", not "anything goes", and `check_password` says so by returning no
#: violations while `has_policy` stays False.
PASSWORD_POLICIES: dict[str, dict[str, Any]] = {
    # Stated on the live create-account form (career41.sapsf.com, Teradyne tenant, 2026-07-28).
    "successfactors": {
        "min_length": 8, "max_length": 18,
        "require_upper": True, "require_lower": True, "require_digit_or_punct": True,
        "no_whitespace": True, "ascii_only": True,
        "source": "stated on the form: 8-18 characters, at least one upper and one lower, at "
                  "least one number or punctuation, no spaces or unicode",
    },
    # WORKDAY_LESSONS: min 8, >=1 alphabetic, >=1 lower, >=1 upper, >=1 numeric, >=1 special.
    "workday": {
        "min_length": 8,
        "require_upper": True, "require_lower": True,
        "require_digit": True, "require_punct": True,
        "no_whitespace": True,
        "source": "WORKDAY_LESSONS password_rules",
    },
}


def has_policy(ats: str) -> bool:
    """Whether we have actually read this ATS's stated password rules. Distinct from 'the
    password passes': an unread policy must not read as a clean bill of health."""
    return (ats or "").strip().lower() in PASSWORD_POLICIES


def check_password(ats: str, password: str) -> list[str]:
    """Every way `password` violates this ATS's stated rules, in the form's own terms.

    Returns ALL violations rather than the first, because the caller's job is to tell a human
    what to change about the derivation, and one at a time turns that into a guessing game.
    The password itself is never included in a message — these strings end up in an operator-
    facing detail and in a mini-step, and §4 does not have a "but it was rejected" exemption.
    """
    p = PASSWORD_POLICIES.get((ats or "").strip().lower())
    if not p:
        return []
    pw = password or ""
    bad: list[str] = []
    if len(pw) < p.get("min_length", 0):
        bad.append(f"shorter than the {p['min_length']}-character minimum (it is {len(pw)})")
    if p.get("max_length") and len(pw) > p["max_length"]:
        bad.append(f"longer than the {p['max_length']}-character maximum (it is {len(pw)})")
    if p.get("require_upper") and not any(c.isupper() for c in pw):
        bad.append("no uppercase letter")
    if p.get("require_lower") and not any(c.islower() for c in pw):
        bad.append("no lowercase letter")
    if p.get("require_digit") and not any(c.isdigit() for c in pw):
        bad.append("no digit")
    if p.get("require_punct") and not any(not c.isalnum() and not c.isspace() for c in pw):
        bad.append("no punctuation character")
    if p.get("require_digit_or_punct") and not any(
            c.isdigit() or (not c.isalnum() and not c.isspace()) for c in pw):
        bad.append("no number or punctuation character")
    if p.get("no_whitespace") and any(c.isspace() for c in pw):
        bad.append("contains whitespace")
    if p.get("ascii_only") and not pw.isascii():
        bad.append("contains non-ASCII characters")
    return bad


def addressing_for(ats: str, field: str) -> dict[str, Any]:
    """Just the addressing half — what a tier-2 protocol endpoint needs passed to it.

    Tier-2 endpoints are 'widget-shaped, site-agnostic' by the plan's own tiering, so they
    CANNOT take a site-specific `field` name; they take resolved addressing. Phase 2's
    "zero selectors" means zero in the calls the MODEL makes — the intent surface resolves,
    then hands tier 2 the address.
    """
    e = resolve(ats, field)
    return {"addressed_by": e["addressed_by"], "selector": e["selector"],
            "role": e["role"], "name": e["name"], "widget_type": e["widget_type"],
            "commit": e["commit"]}
