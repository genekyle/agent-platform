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
