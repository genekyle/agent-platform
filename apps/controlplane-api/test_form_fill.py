"""Tests for form-fill planning — map a form's fields to values, in a bunch, honestly."""

from datetime import date

import form_fill as ff

_FIELDS = [
    {"role": "textbox", "name": "First Name"},
    {"role": "textbox", "name": "Last Name"},
    {"role": "textbox", "name": "Address Line 1"},
    {"role": "textbox", "name": "City"},
    {"role": "combobox", "name": "State Select One Required"},
    {"role": "textbox", "name": "Postal Code"},
    {"role": "textbox", "name": "Phone Number"},
    {"role": "textbox", "name": "How Did You Hear About Us?"},
    {"role": "textbox", "name": "Enter website. This input is for robots only"},   # honeypot
    {"role": "button", "name": "Save and Continue"},                                # not a field
]
_IDENTITY = {"first_name": "Gene", "last_name": "Magsipoc",
             "email": "genomags@gmail.com", "how_did_you_hear": "Indeed"}


def test_identity_fields_fill_from_the_account():
    rows = {r["field"]: r for r in ff.plan(_FIELDS, answers={}, identity=_IDENTITY)}
    assert rows["First Name"]["value"] == "Gene" and rows["First Name"]["source"] == ff.SRC_IDENTITY
    assert rows["Last Name"]["value"] == "Magsipoc"
    # "How Did You Hear" is a prompt, not a text fill — it is not planned here at all
    assert "How Did You Hear About Us?" not in rows


def test_missing_data_is_flagged_never_guessed():
    """The honest core: an address we do not hold is a blank to ask about, not an invented street."""
    rows = {r["field"]: r for r in ff.plan(_FIELDS, answers={}, identity=_IDENTITY)}
    assert rows["Address Line 1"]["fillable"] is False
    assert rows["Address Line 1"]["value"] is None
    assert rows["Address Line 1"]["source"] == ff.SRC_MISSING
    assert rows["City"]["fillable"] is False and rows["Postal Code"]["fillable"] is False


def test_a_stored_answer_beats_no_data():
    rows = {r["field"]: r for r in ff.plan(_FIELDS,
            answers={"city": "Nashua", "postal_code": "03060"}, identity=_IDENTITY)}
    assert rows["City"]["value"] == "Nashua" and rows["City"]["source"] == ff.SRC_STORED
    assert rows["Postal Code"]["value"] == "03060"


def test_a_working_variable_beats_a_stale_stored_string():
    fields = [{"role": "textbox", "name": "Today's Date"}]
    rows = ff.plan(fields, answers={"todays_date": "01/01/2020"}, identity={},
                   today=date(2026, 7, 24))
    assert rows[0]["value"] == "07/24/2026" and rows[0]["source"] == ff.SRC_WORKING


def test_the_honeypot_maps_to_nothing_and_is_skipped():
    rows = {r["field"]: r for r in ff.plan(_FIELDS, answers={}, identity=_IDENTITY)}
    assert not any("robots only" in f for f in rows)


def test_buttons_and_unmapped_fields_are_left_out():
    rows = {r["field"] for r in ff.plan(_FIELDS, answers={}, identity=_IDENTITY)}
    assert "Save and Continue" not in rows          # a button is not a field


def test_dropdowns_are_planned_as_select_not_text():
    rows = {r["field"]: r for r in ff.plan(_FIELDS, answers={"state": "New Hampshire"},
                                           identity=_IDENTITY)}
    assert rows["State Select One Required"]["widget"] == "select"


def test_phone_device_type_wins_over_phone():
    """Ordering: the more specific field name must not be swallowed by the shorter one."""
    fields = [{"role": "combobox", "name": "Phone Device Type Select One Required"}]
    rows = ff.plan(fields, answers={}, identity={})
    assert rows[0]["answer_key"] == "phone_device_type"


def test_summary_counts_and_lists_the_missing():
    rows = ff.plan(_FIELDS, answers={}, identity=_IDENTITY)
    s = ff.summarise(rows)
    assert s["fillable"] == 2                        # first, last (how-did-you-hear is a prompt now)
    assert "Address Line 1" in s["missing"] and "City" in s["missing"]
    assert s["by_source"][ff.SRC_IDENTITY] == 2
