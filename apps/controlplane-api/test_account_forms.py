"""The account form as data, and the invariants that make it safe to STEP rather than to run.

A program that has drifted from the driver is worse than no program at all: rung 0 replays it
without asking anyone. So the sequence is asserted against the driver's own source here, not
maintained by hand in two places and hoped about.
"""

from __future__ import annotations

import inspect

import account_forms
import apply_fields
import pytest


def test_every_field_every_leg_names_resolves_to_a_real_address():
    """The invariant that keeps a half-typed credential off a page. A field named here and missing
    from apply_fields fails MID-FORM — after the email and the first password box have already
    been filled — and the account is left half-made."""
    for leg, by_ats in account_forms.ACCOUNT_FORMS.items():
        for ats in by_ats:
            assert account_forms.unresolved_fields(ats, leg) == [], f"{ats}/{leg}"


def test_the_successfactors_create_leg_steps_in_the_order_the_form_demands():
    steps = account_forms.program_steps("successfactors", "create_account")
    kinds = [s["intent"] for s in steps]
    fields = [s["params"]["field"] for s in steps]

    # Order is recipe, not implementation detail: SAP will not take the form until the country is
    # chosen, and a refusal after the submit refuses nothing.
    assert kinds.index("select_option") < kinds.index("check_group")
    assert kinds.index("check_group") < len(kinds) - 1
    assert fields[-1] == "create_account_submit"
    assert kinds[-1] == "click"
    # Both marketing opt-ins are refused BY NAME. They arrive checked; omission is not refusal.
    assert "opt_in_job_notifications" in fields and "opt_in_career_news" in fields


def test_a_step_carries_no_value_and_no_selector():
    """The PII/addressing guard, asserted at the source as well as at the store. The marketing
    refusals are the interesting case: apply_fields addresses them by SELECTOR (#fbclc_...), and
    the step must still travel as a field name — otherwise a stored program pins a DOM id and dies
    the first time SAP re-renders."""
    from interaction.decision import looks_like_selector

    for leg, by_ats in account_forms.ACCOUNT_FORMS.items():
        for ats in by_ats:
            for step in account_forms.program_steps(ats, leg):
                params = step["params"]
                assert "value" not in params, f"{ats}/{leg}: a step carries a literal value"
                for v in params.values():
                    assert not looks_like_selector(v), f"{ats}/{leg}: {v!r} is a selector"


def test_the_credential_refs_do_not_point_at_the_answer_store():
    """A credential is not an application answer. These resolve from the encrypted vault, and
    saying so in the ref is what keeps a committed program free of anything that reads like the
    secret."""
    steps = account_forms.program_steps("successfactors", "create_account")
    refs = {s["params"].get("value_ref") for s in steps}
    assert "account.username" in refs and "account.password" in refs
    assert set(account_forms.VAULT_REFS) <= refs


def test_a_program_step_survives_the_stores_own_sanitiser_unchanged():
    """Defence in depth is only defence if the two agree. If `save_program` would strip a param we
    emit, the stored program is quietly shorter than the one we tested."""
    from controller.programs import _sanitize_step

    for step in account_forms.program_steps("successfactors", "create_account"):
        assert _sanitize_step(step) == step


def test_an_unmapped_ats_is_none_rather_than_someone_elses_field_names():
    assert account_forms.form_for("greenhouse", "create_account") is None
    assert account_forms.program_steps("greenhouse", "create_account") == []
    # iCIMS has no sign-in leg — a leg we have not walked reads as unknown, not as covered.
    assert account_forms.form_for("icims", "sign_in") is None
    assert "successfactors" in account_forms.known_ats("sign_in")


@pytest.mark.parametrize("leg", ["create_account", "sign_in"])
def test_the_program_matches_what_the_driver_actually_does(leg):
    """THE anti-drift test, and the reason the sequence is allowed to be data.

    `_drive_account_form` reads the same table, but it reads it in control flow — four loops in a
    fixed order. If someone reorders those loops, or adds a fifth, the program silently describes
    a drive that no longer happens. So this reads the driver's SOURCE and asserts the loops appear
    in the order `program_steps` claims.
    """
    from routers import session_control

    src = inspect.getsource(session_control._drive_account_form)
    # The order these keys are consumed in the driver's body. `fields` and `submit` are read as
    # required (`form["fields"]`) and the optional stages through `.get`, so accept either spelling
    # rather than pinning the test to one — this asserts ORDER, not style.
    order = []
    for key in ("fields", "selects", "refusals", "confirms", "submit"):
        at = min((i for i in (src.find(f'form.get("{key}"'), src.find(f'form["{key}"]')) if i >= 0),
                 default=-1)
        assert at >= 0, f"the driver no longer consumes {key!r}"
        order.append((at, key))
    driver_order = [k for _i, k in sorted(order)]
    assert driver_order == ["fields", "selects", "refusals", "confirms", "submit"]

    # And the rendered program follows that same shape for a leg that uses every stage.
    if leg == "create_account":
        kinds = [s["intent"] for s in account_forms.program_steps("successfactors", leg)]
        assert kinds == (["set_text"] * 6 + ["select_option", "check_group", "check_group",
                                             "click", "click"])
