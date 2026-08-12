"""The account form, as DATA — and as a program the inner layers can step.

This is the `_ACCOUNT_FORMS` table lifted out of `routers/session_control.py`, plus the one thing
it could not do from inside a router: describe itself as an ordered sequence of intents.

--------------------------------------------------------------------------------------
Why it moved, and why the sequence matters
--------------------------------------------------------------------------------------
`_drive_account_form` is imperative — four loops and a submit, inside an HTTP handler. That makes
the account rung INVISIBLE to everything that learns. It never passes through `decide()`, so it
writes no DecisionRecords, so `controller.programs.compile_from_journal` has nothing to compile,
so rung 0 has nothing to replay. Every other rung on the ladder gets cheaper the second time it
runs; this one costs exactly the same forever, and the flow that most needs to be repeatable
across employers — one account per company, dozens of companies — was the one flow that could not
be.

The fix is not to rewrite the executor. It is to stop keeping the sequence only in control flow.
`program_for(ats, leg)` renders the SAME table the driver executes into `{intent, params}` steps
in the order the driver performs them, so the recipe layer, the controller and the operator are
all reading one description of what happens. A test pins the two together, because a program that
drifts from the driver is worse than no program: rung 0 replays without asking anyone.

--------------------------------------------------------------------------------------
What a step may carry (`controller.programs._sanitize_step` enforces this independently)
--------------------------------------------------------------------------------------
A step names a FIELD, never a value and never a selector. `apply_fields` resolves the field to its
addressing at act time — which is what lets the marketing refusals below travel as
`opt_in_job_notifications` rather than `#fbclc_emailEnabled`, and lets a site re-render its DOM
without invalidating a stored program.

`value_ref` says WHERE the value comes from, and the credential refs are deliberately not answer
keys: `account.username` / `account.password` resolve from the encrypted vault at replay time, so
a committed program contains no credential and no hint of one — the same guarantee the answer-store
refs give for PII.
"""

from __future__ import annotations

from typing import Any

import apply_fields

#: Value sources that are NOT answer-store keys. The credential half of an account form comes from
#: the vault (see `ats_accounts.record_credentials`), and saying so in the ref keeps a program file
#: free of anything that could be mistaken for the secret itself.
VAULT_REFS = ("account.username", "account.password")

_VALUE_REF = {"username": "account.username", "password": "account.password"}

#: WHICH fields a create-account form has, per ATS, and where each value comes from. The ADDRESSING
#: is not here — it is in `apply_fields`, resolved by (ats, field), because a second place that says
#: where a field lives is a second place that can be wrong about it.
#:
#: This table exists because the driver used to BE Workday: three hardcoded accessible names and a
#: "Create Account" button. iCIMS wants six fields, calls the button "Submit Profile", and puts a
#: username field beside the email — so the first genuinely different ATS could not be driven at
#: all, which is the reverse of what a recipe system is for.
#:
#: Keyed by LEG then ATS. Signing in was the missing half: the system could CREATE an account by
#: typing a generated password and then could not USE it, because only the create leg was wired —
#: so an ATS we already had an active account for still stopped at a manual handoff. Nothing about
#: the operator's directive distinguishes the two (their account, their machine, their job search);
#: the gates that hold are the same ones either way, a captcha or a verification code.
ACCOUNT_FORMS: dict[str, dict[str, dict[str, Any]]] = {
    "create_account": {
        "workday": {
            "fields": (("email", "username"), ("password", "password"),
                       ("verify_password", "password")),
            # CHECKS — required boxes that must end ON before the submit. The consent's label is
            # TENANT PROSE ("I consent" / "I confirm that I have read and acknowledge"), so it is
            # addressed by Workday's own stable automation-id and checked label-independently.
            # The walk had NO checkbox step at all: the recipe's prose said "CHECK the acknowledge
            # checkbox" since 07-12 and nothing ever executed it — SolutionHealth's tenant made it
            # required and Create Account bounced with "Please check the box to continue"
            # (live 2026-08-11).
            "checks": ("acknowledge",),
            "submit": "create_account_submit",
        },
        "icims": {
            # Step 1 of 4 IS the account form: identity and credential on one page (ICIMS_FIELDS).
            "fields": (("first_name", "first_name"), ("last_name", "last_name"),
                       ("email", "username"), ("login", "username"),
                       ("password", "password"), ("verify_password", "password")),
            "submit": "create_account_submit",
        },
        "successfactors": {
            "fields": (("email", "username"), ("verify_email", "username"),
                       ("password", "password"), ("verify_password", "password"),
                       ("first_name", "first_name"), ("last_name", "last_name")),
            # SAP wants a country before it will take the form, and a data-privacy acceptance.
            "selects": (("country", "country"),),
            # Both arrive CHECKED on the live form. Naming them so they are never touched was the
            # old protection, and it was pointed the wrong way — the site had already ticked them.
            "refusals": ("opt_in_job_notifications", "opt_in_career_news"),
            # A STAGED consent: (opener, commit, proof). The opener raises a dialog, the commit is
            # the Accept inside it, and the proof is text that only appears on the page OUTSIDE the
            # dialog once it has been accepted. Three parts because each one failed differently on
            # the way to learning it — the opener had to stop being an accessible name, the commit
            # lives in a widget AX does not connect to its opener, and the dialog closing is not
            # evidence of consent (Decline closes it too).
            "confirms": (("terms", "terms_accept", "Data privacy statement has been accepted."),),
            "submit": "create_account_submit",
        },
    },
    "sign_in": {
        "workday": {
            # THE FORM HAS TO BE ON SCREEN BEFORE IT CAN BE FILLED. Workday serves Create Account
            # and Sign In from ONE url, showing whichever the tenant defaults to, with a button to
            # switch. SolutionHealth defaults to Create Account — so the sign-in leg typed the
            # credential into the CREATE form's shared Email/Password boxes and then could not find
            # its submit: "Filled the form but could not click '[data-automation-id=
            # signInSubmitButton]' (not_found)" (live 2026-08-12). Third instance of the same
            # pattern in two days: the recipe has described `toggle_to_sign_in` since 07-12, and
            # nothing executed it — like the consent checkbox before it.
            #
            # CONDITIONAL, and conditional on a MEASUREMENT: the submit control's own presence says
            # whether this leg's form is already showing. Pressing a toggle that is not needed would
            # switch a correct page to the wrong form.
            "toggle": ("sign_in_toggle", "sign_in_submit"),
            "fields": (("email", "username"), ("password", "password")),
            "submit": "sign_in_submit",
        },
        "successfactors": {
            "fields": (("signin_email", "username"), ("signin_password", "password")),
            "submit": "sign_in_submit",
            # AN INTERSTITIAL, not a step of the form: the Data Privacy Consent dialog arrives on
            # its own once the sign-in lands, with no opener to click and nothing on the form that
            # predicts it. It is the same dialog the signup raises, at a different MOMENT — and a
            # recipe that could not tell those apart would sit waiting for an opener that is never
            # coming.
            #
            # It has to be cleared immediately, because leaving it drops the whole session: observed
            # 2026-07-29 — dialog dismissed unaccepted, and the tab was back at the sign-in wall
            # with logged_in false and loginFlowRequired=true. An authenticated session is not a
            # thing you can come back to later here.
            "interstitials": (("policy_gate_accept", "successfactors_policy_gate"),),
        },
    },
}


def form_for(ats: str, leg: str) -> dict[str, Any] | None:
    """The form spec for one (ats, leg), or None if we have not mapped it. None is the honest
    answer: driving an unmapped ATS on another one's field names produces "could not fill 'Email
    Address'", which reads like a moved field rather than like a platform nobody has scanned."""
    return (ACCOUNT_FORMS.get((leg or "").strip().lower()) or {}).get((ats or "").strip().lower())


def known_ats(leg: str) -> list[str]:
    return sorted(ACCOUNT_FORMS.get((leg or "").strip().lower()) or {})


def program_steps(ats: str, leg: str) -> list[dict[str, Any]]:
    """The form as an ordered intent sequence, in the order the driver performs it.

    Order is part of the recipe, not an implementation detail: SAP will not accept the form until
    the country is chosen, and a refusal has to land before the submit or it refuses nothing. So
    this mirrors `_drive_account_form`'s sequence exactly — fills, selects, refusals, confirms,
    submit — and `test_account_forms` asserts the two stay in step.
    """
    form = form_for(ats, leg)
    if form is None:
        return []
    steps: list[dict[str, Any]] = []
    toggle = form.get("toggle")
    if toggle:
        # Conditional like an interstitial — it fires only when this leg's form is not already the
        # one on screen — but it belongs in the program, because a sequence that starts at the fills
        # describes a leg that types into whichever form the tenant happened to default to.
        steps.append({"intent": "click", "params": {"field": toggle[0]}})
    for field, source in form.get("fields", ()):
        params = {"field": field}
        ref = _VALUE_REF.get(source)
        if ref:
            params["value_ref"] = ref
        else:
            params["value_ref"] = source          # an answer-store key (first_name, country, ...)
        steps.append({"intent": "set_text", "params": params})
    for field, source in form.get("selects", ()):
        steps.append({"intent": "select_option", "params": {"field": field, "value_ref": source}})
    for field in form.get("refusals", ()):
        # An opt-in that ARRIVES checked. `check_group` with an empty value set is the refusal, and
        # it re-reads the DOM to confirm — a refusal we cannot verify is not a refusal.
        steps.append({"intent": "check_group", "params": {"field": field}})
    for opener, commit, _proof in form.get("confirms", ()):
        # Two steps, because it is two acts against two different widgets — and a program that
        # showed one would replay one and consent to nothing.
        steps.append({"intent": "click", "params": {"field": opener}})
        steps.append({"intent": "click", "params": {"field": commit}})
    steps.append({"intent": "click", "params": {"field": form["submit"]}})
    for control, _state in form.get("interstitials", ()):
        # Conditional by nature — it fires only if the gate actually appears — but it belongs in
        # the program, because a sequence that stops at the submit describes a leg that ends one
        # dialog short of an authenticated session.
        steps.append({"intent": "click", "params": {"field": control}})
    return steps


def program_fields(ats: str, leg: str) -> list[str]:
    """Every field this leg touches, in order — the program's `guard_fields`. A field that appears
    here and not in `apply_fields` is a stale recipe, and `resolve()` will say so loudly."""
    return [s["params"]["field"] for s in program_steps(ats, leg)]


def unresolved_fields(ats: str, leg: str) -> list[str]:
    """Fields this leg names that `apply_fields` cannot address. Empty is the invariant; anything
    else means the two tables have drifted and the drive would fail mid-form, having already typed
    half a credential into a page."""
    bad = []
    for field in program_fields(ats, leg):
        try:
            apply_fields.resolve(ats, field)
        except apply_fields.FieldNotFound:
            bad.append(field)
    return bad
