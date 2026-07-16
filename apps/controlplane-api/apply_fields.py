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

_BY_ATS: dict[str, dict[str, dict[str, Any]]] = {
    "greenhouse": GREENHOUSE_FIELDS,
    "workday": WORKDAY_FIELDS,
    "indeed": INDEED_FIELDS,
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
