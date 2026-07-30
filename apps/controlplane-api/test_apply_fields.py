"""The resolver's invariants.

These are cheap tests over pure data, which is exactly why the recipe should have been data
all along: not one of these could have been written against the prose it replaces.
"""

from __future__ import annotations

import pytest

from interaction.contract import VALUE_READ_AT, WidgetType

import apply_fields
from apply_fields import FieldNotFound, addressing_for, known_ats, known_fields, resolve


def test_resolve_returns_addressing_and_shape():
    e = resolve("workday", "phone_device_type")
    assert e["addressed_by"] == "selector"
    assert e["widget_type"] == WidgetType.ARIA_LISTBOX.value
    assert e["vocabulary"] == {"Mobile": "Mobile"}


def test_resolve_is_case_and_whitespace_insensitive():
    assert resolve("  Workday ", " Phone_Device_Type ")["name"] is None


def test_unknown_field_raises_loudly_and_says_what_exists():
    # A stale recipe must be Outcome.NOT_FOUND, not a None that fails three layers down as
    # something else.
    with pytest.raises(FieldNotFound) as exc:
        resolve("greenhouse", "nope")
    assert "first_name" in str(exc.value)      # tells you what IS there
    assert "do not hardcode" in str(exc.value)  # ...and why not to route around it


def test_unknown_ats_raises_loudly():
    with pytest.raises(FieldNotFound) as exc:
        resolve("taleo", "email")
    assert "unknown ats" in str(exc.value)


def test_every_field_is_addressed_exactly_one_way():
    # The six shapes collapsed to two. This is the invariant that made resolution writable.
    for ats in known_ats():
        for name in known_fields(ats):
            e = resolve(ats, name)
            has_sel, has_rn = bool(e["selector"]), bool(e["name"])
            assert has_sel != has_rn, f"{ats}.{name} is addressed both ways or neither"
            assert e["addressed_by"] == ("selector" if has_sel else "role_name")


def test_addressed_by_is_derived_not_hand_written():
    # A hand-written discriminator can disagree with the data it discriminates.
    with pytest.raises(ValueError, match="addressed one way"):
        apply_fields._f(ats="x", selector="#a", role="button", name="B",
                        widget_type=WidgetType.TEXT)


def test_a_field_must_be_addressable_at_all():
    with pytest.raises(ValueError, match="needs a selector or an accessible name"):
        apply_fields._f(ats="x", widget_type=WidgetType.TEXT)


def test_every_widget_type_used_is_a_real_member_with_a_known_truth_location():
    for ats in known_ats():
        for name in known_fields(ats):
            wt = WidgetType(resolve(ats, name)["widget_type"])   # raises if not a member
            assert wt in VALUE_READ_AT


def test_greenhouse_country_is_named_for_what_it_is_not_what_its_id_says():
    """#country is the PHONE country code, not the address country.

    The id lies. The field name is where we get to tell the truth once, instead of every
    caller re-learning it — this single entry is the argument for a resolver.
    """
    assert "phone_country" in known_fields("greenhouse")
    assert resolve("greenhouse", "phone_country")["selector"] == "#country"
    assert "location_city" in known_fields("greenhouse")   # the ADDRESS lives here


def test_the_workday_honeypot_is_data_so_it_can_be_refused_by_name():
    e = resolve("workday", "honeypot_do_not_fill")
    assert "never fill" in e["note"].lower()


def test_staged_commit_fields_declare_their_footer_button():
    # Indeed's distance pill: selecting only STAGES; Update commits. A field that needs a
    # commit and doesn't say so is the bug where "it selected but nothing applied".
    assert resolve("indeed", "distance")["commit"] == "Update"
    # Workday's listbox applies on select — no footer.
    assert resolve("workday", "phone_device_type")["commit"] is None


def test_vocabulary_carries_the_known_aliases_from_the_lessons():
    # Each of these was paid for live on 2026-07-15 and lived only in prose until now.
    assert resolve("greenhouse", "degree")["vocabulary"]["Bachelor of Science"] == "Bachelor's Degree"
    assert resolve("greenhouse", "discipline")["vocabulary"]["Sports Science"] == "Kinesiology"
    assert resolve("greenhouse", "school")["vocabulary"]["University of Santo Tomas"] == "Other"


def test_addressing_for_returns_what_a_tier2_endpoint_needs():
    a = addressing_for("greenhouse", "school")
    assert set(a) == {"addressed_by", "selector", "role", "name", "widget_type", "commit"}
    # No answer_key/vocabulary: those are the INTENT tier's business, not the protocol's.
    assert "vocabulary" not in a


# --- password policies -----------------------------------------------------------------
def test_sap_password_rules_are_checked_as_data_not_prose():
    """SAP states five rules on its create-account form. They lived as prose in three files and
    nothing read any of them — so a derived password that violated one was discovered by SAP, at
    submit, after an account was half-made."""
    ok = apply_fields.check_password("successfactors", "Tabcd12!")
    assert ok == []
    assert apply_fields.check_password("successfactors", "Tabc1!") == [
        "shorter than the 8-character minimum (it is 6)"]
    assert apply_fields.check_password("successfactors", "T" + "abcdefg1" * 3) == [
        "longer than the 18-character maximum (it is 25)"]
    assert "no uppercase letter" in apply_fields.check_password("successfactors", "abcdefg1")
    assert "no lowercase letter" in apply_fields.check_password("successfactors", "ABCDEFG1")
    assert "no number or punctuation character" in apply_fields.check_password(
        "successfactors", "Abcdefgh")
    assert "contains whitespace" in apply_fields.check_password("successfactors", "Ab cdef1")
    assert "contains non-ASCII characters" in apply_fields.check_password(
        "successfactors", "Abcdefg1é")


def test_every_violation_is_reported_not_just_the_first():
    # The caller's job is to tell a human what to change about the derivation. One at a time
    # turns that into a guessing game.
    bad = apply_fields.check_password("successfactors", "abc")
    assert len(bad) >= 3


def test_a_violation_message_never_quotes_the_password():
    # These strings reach an operator-facing detail and a mini-step. §4 has no "but it was
    # rejected" exemption.
    secret = "hunter2hunter2hunter2hunter2"
    for msg in apply_fields.check_password("successfactors", secret):
        assert secret not in msg


def test_teradyne_is_the_boundary_case_the_check_exists_for():
    # The password is INITIALS + a shared suffix, so its length is a property of the COMPANY
    # NAME. "Teradyne" yields one initial, so the whole credential is suffix + 1 — and SAP's
    # floor is 8. One character shorter anywhere and this account cannot be made.
    assert apply_fields.check_password("successfactors", "T" + "abcde1!") == []
    assert apply_fields.check_password("successfactors", "T" + "abcd1!") != []


def test_an_unread_policy_is_not_a_clean_bill_of_health():
    # Absent means "we have not read this form's rules", not "anything goes" — and the two must
    # be distinguishable, or an unchecked ATS looks exactly like a checked one.
    assert apply_fields.check_password("greenhouse", "x") == []
    assert apply_fields.has_policy("greenhouse") is False
    assert apply_fields.has_policy("successfactors") is True


def test_every_policy_names_an_ats_we_actually_have_fields_for():
    assert set(apply_fields.PASSWORD_POLICIES) <= set(known_ats())


# ---------------------------------------------------------------------------------------------
# SAP CANDIDATE PROFILE — the three traps measured live on 2026-07-30. Every one of them is
# invisible to a human reading the page, and every one resolves to SOMETHING rather than failing.
# ---------------------------------------------------------------------------------------------

def _resolve_like_the_driver(candidates, role, name):
    """`_resolve_ax_node`'s matching, verbatim (main_server.py:183-194): strip+lower both sides,
    role-gate, exact wins, substring falls back. Duplicated on purpose — the point is to catch a
    field table that stops resolving, and importing the MCP app here would couple the suites."""
    want_role, want = (role or "").strip().lower(), (name or "").strip().lower()
    def role_ok(c):
        return not want_role or (c.get("role") or "").strip().lower() == want_role
    def nm(c):
        return (c.get("name") or "").strip().lower()
    exact = [c for c in candidates if role_ok(c) and nm(c) == want]
    if exact:
        return exact
    return [c for c in candidates if role_ok(c) and want and want in nm(c)]


#: Verbatim from the live AX scan of career41.sapsf.com/portalcareer with Profile Information
#: expanded (2026-07-30). The leading \xa0 are REAL and are NON-BREAKING SPACES, not ASCII
#: ones — an aria-hidden required-marker span leaves its whitespace in the accessible name.
#: Written as escapes so they survive an editor, and they must not be "tidied" out: a fixture
#: with a plain space would pass while testing a condition the live page never produces.
_LIVE_PROFILE_AX = [
    {"role": "button", "name": "Expand all sections", "backend_node_id": 19898},
    {"role": "button", "name": "Profile Information", "backend_node_id": 20006},
    {"role": "button", "name": "Jobs Applied (2)", "backend_node_id": 20186},
    {"role": "textbox", "name": "\xa0First Name", "backend_node_id": 19222},
    {"role": "textbox", "name": "Middle Name", "backend_node_id": 19223},
    {"role": "textbox", "name": "\xa0Postal Code", "backend_node_id": 19227},
    {"role": "combobox", "name": "Country", "backend_node_id": 19217},
    {"role": "button", "name": "Country", "backend_node_id": 20071},
    {"role": "combobox", "name": "State / Province", "backend_node_id": 19219},
    {"role": "button", "name": "State / Province", "backend_node_id": 20086},
]


def test_required_marker_whitespace_does_not_break_resolution():
    # AX reports " First Name" because <span class=requiredField aria-hidden> keeps its spaces
    # while losing its asterisk. The table stores the clean name; the driver strips. If either
    # side stops stripping, every REQUIRED field on this form goes unreachable at once — and
    # only the required ones, which is the worst possible half-failure.
    hits = _resolve_like_the_driver(_LIVE_PROFILE_AX, "textbox", resolve("successfactors", "profile_first_name")["name"])
    assert [h["backend_node_id"] for h in hits] == [19222]
    hits = _resolve_like_the_driver(_LIVE_PROFILE_AX, "textbox", resolve("successfactors", "profile_zip")["name"])
    assert [h["backend_node_id"] for h in hits] == [19227]


@pytest.mark.parametrize("field,node", [("profile_country", 19217), ("profile_state", 19219)])
def test_country_and_state_need_their_role_to_be_unambiguous(field, node):
    # Each name belongs to TWO controls: the combobox holding the value and the button opening
    # the picker. With the role, exactly one survives. Without it, `exact[0]` is document order —
    # a coin flip between typing the value and opening a dropdown.
    entry = resolve("successfactors", field)
    assert entry["role"] == "combobox", "dropping the role here re-opens the ambiguity below"
    assert [h["backend_node_id"] for h in _resolve_like_the_driver(_LIVE_PROFILE_AX, entry["role"], entry["name"])] == [node]
    assert len(_resolve_like_the_driver(_LIVE_PROFILE_AX, None, entry["name"])) == 2


def test_jobs_applied_is_matched_by_prefix_because_its_name_carries_a_count():
    # "Jobs Applied (2)" becomes "(3)" the next time we apply. Storing today's count would make
    # the bar unreachable on the very run that changes it.
    entry = resolve("successfactors", "profile_section_jobs_applied")
    assert "(" not in entry["name"], "the count must not be baked into the name"
    assert [h["backend_node_id"] for h in _resolve_like_the_driver(_LIVE_PROFILE_AX, "button", entry["name"])] == [20186]


def test_a_collapsed_section_hides_its_fields_so_the_bar_is_a_precondition():
    # The collapsed page offered 25 AX candidates and NOT ONE textbox; opening one bar took it
    # to 41. So a field lookup that succeeds in the table can still be NOT_FOUND on the page,
    # and that is openness, not staleness. Every profile field therefore has a section bar.
    collapsed = [c for c in _LIVE_PROFILE_AX if c["role"] == "button"]
    assert _resolve_like_the_driver(collapsed, "textbox", "First Name") == []
    assert {"profile_expand_all", "profile_section_profile_information"} <= set(known_fields("successfactors"))
