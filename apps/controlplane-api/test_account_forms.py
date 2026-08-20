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
        steps = account_forms.program_steps("successfactors", leg)
        kinds = [s["intent"] for s in steps]
        # The consent is TWO clicks — open the dialog, then Accept inside it — before the submit.
        # A program that showed one would replay one and consent to nothing.
        assert kinds == (["set_text"] * 6 + ["select_option", "check_group", "check_group",
                                             "click", "click", "click"])
        assert [s["params"]["field"] for s in steps][-3:] == ["terms", "terms_accept",
                                                              "create_account_submit"]


# --- PAGED FORMS ---------------------------------------------------------------------------
# PowerSchool's Auth0 login is identifier-first, so `schoolspring` is the first leg whose form
# cannot be described as one screen. These pin the two properties that make a paged spec safe to
# drive: every page must be FINDABLE before it is filled, and no page may imply an account exists.

def test_a_flat_form_is_a_one_page_form_so_readers_need_not_branch():
    """`pages_of` is the unification. If a flat form did not answer it, every caller would carry
    an `if "pages" in form` and the ones that forgot would silently skip paged ATSs."""
    flat = account_forms.pages_of("workday", "create_account")
    assert len(flat) == 1
    assert flat[0] is account_forms.form_for("workday", "create_account")
    assert [p["id"] for p in account_forms.pages_of("schoolspring", "create_account")] \
        == ["identifier"]


def test_every_page_of_every_paged_leg_can_be_recognised_before_it_is_filled():
    """`present` is how the driver decides WHICH page it is looking at, and it resolves it through
    `apply_fields` at act time. A page missing it — or naming a field nobody mapped — raises
    mid-drive, after the leg has already been entered."""
    for leg, by_ats in account_forms.ACCOUNT_FORMS.items():
        for ats, form in by_ats.items():
            if not form.get("pages"):
                continue
            for page in form["pages"]:
                assert page.get("present"), f"{ats}/{leg}: a page with no `present`"
                apply_fields.resolve(ats, page["present"])      # raises if unmapped
                assert page.get("id"), f"{ats}/{leg}: a page with no id is unnameable in a report"
                _assert_locatable(ats, page["present"], f"{ats}/{leg} page {page['id']}")
                if page.get("toggle"):
                    # The toggle's PROOF is probed the same way, so it carries the same constraint.
                    _assert_locatable(ats, page["toggle"][1], f"{ats}/{leg} toggle proof")


def _assert_locatable(ats: str, field: str, where: str) -> None:
    """A field probed through `/locate` must be findable BY ITS OWN TEXT.

    `/locate` matches visible text, not the accessibility tree. A text input's accessible name
    comes from a separate <label>, so the input itself carries no text and `/locate` returns
    found:false while the box is plainly on screen — measured on PowerSchool's identifier page,
    2026-08-20: "Email address" not found, "Continue" found.

    That failure is invisible in every other layer. `apply_fields.resolve` succeeds, the AX scan
    lists the field, `/execute` can drive it — only the `/locate` probe disagrees, and its verdict
    is what decides whether a mapped page is recognised at all. So a `present` naming a text box
    reports every drive as `unmapped_page` and reads like a missing recipe.
    """
    addr = apply_fields.resolve(ats, field)
    if addr.get("selector"):
        return                       # a CSS probe needs no text
    assert addr.get("widget_type") != "text", (
        f"{where}: `{field}` is a text input addressed by accessible name, and /locate cannot see "
        f"it. Probe a control that renders its own text (its submit button), or give it a selector."
    )


def test_an_identifier_screen_never_claims_the_account_was_created():
    """THE safety property of the paged shape, pinned in the data rather than in the driver.

    `apply_account` calls `mark_created` on a submitted form. PowerSchool's Continue takes an email
    address and nothing else — marking it would leave a row claiming a login the ATS has never
    heard of, make the sign-in leg due forever, and turn every later rejection into what reads as a
    bad password. Only a page that says `completes_leg` may end the leg, and no mapped page of
    schoolspring says it, because nobody has seen the screen that would.
    """
    for leg in ("create_account", "sign_in"):
        pages = account_forms.pages_of("schoolspring", leg)
        assert not any(p.get("completes_leg") for p in pages), \
            "a schoolspring page claims to finish the leg; no such screen has been measured"


def test_the_two_schoolspring_legs_cross_toward_opposite_screens():
    """Apply always lands on the LOGIN identifier and the two screens carry identical controls, so
    the leg is told only by its cross-link. If both legs toggled the same way one of them would
    drive the wrong screen — and on the create leg that means asking PowerSchool to sign in an
    account that does not exist yet."""
    create = account_forms.pages_of("schoolspring", "create_account")[0]
    sign_in = account_forms.pages_of("schoolspring", "sign_in")[0]
    assert create["toggle"] == ("signup_link", "login_link")
    assert sign_in["toggle"] == ("login_link", "signup_link")
    # And the proof of arrival is never the control we pressed to get there.
    for page in (create, sign_in):
        assert page["toggle"][0] != page["toggle"][1]
