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


# ---------------------------------------------------------------------------------------------
# ACCORDION FORMS. The bug these guard against produces no error and no empty result — it
# produces a CONFIDENT, ACCURATE summary of a page nobody opened.
# ---------------------------------------------------------------------------------------------

#: Verbatim shape of an /ax_scan candidate for a SAP section bar. `expanded` is tri-state.
def _bar(name, expanded=None, role="button"):
    return {"role": role, "name": name, "expanded": expanded}


_ALL_SHUT = [
    _bar("My Documents", False), _bar("Profile Information", False),
    _bar("Search Options and Privacy", False), _bar("Jobs Applied (2)", False),
    _bar("Saved Applications", False), _bar("Employment History", False),
    _bar("Formal Education", False), _bar("Language Skills", False),
    _bar("Geographic Mobility", False),
]


def test_a_flat_ats_reports_none_rather_than_asserting_flatness():
    # Absent from SECTION_BARS means "flat, or nobody checked". Those must not look alike, and a
    # None here is what keeps apply_fill from claiming a form has no sections when it has never
    # been looked at.
    assert ff.section_status("greenhouse", _ALL_SHUT) is None


def test_every_bar_shut_is_reported_as_shut_not_as_an_empty_form():
    s = ff.section_status("successfactors", _ALL_SHUT)
    assert len(s["closed"]) == 9 and s["open"] == [] and s["all_open"] is False


def test_the_caveat_distinguishes_zero_fields_from_zero_because_shut():
    # The whole point: "0 fields" and "0 fields, nine sections closed" must not read alike.
    shut = ff.section_status("successfactors", _ALL_SHUT)
    assert "9 sections still closed" in ff.sections_caveat(shut, 0)
    assert "Nothing is open yet" in ff.sections_caveat(shut, 0)
    # ...and once everything is open there is nothing to warn about, so the card stays quiet.
    open_all = ff.section_status("successfactors", [_bar(b["name"], True) for b in _ALL_SHUT])
    assert open_all["all_open"] is True
    assert ff.sections_caveat(open_all, 13) == ""


def test_a_bar_we_cannot_read_is_not_a_bar_we_know_is_shut():
    # `expanded=None` means the node never claimed to be expandable; a MISSING bar means we are
    # probably not even on the profile. Folding either into "closed" invents knowledge, and would
    # make "Open all sections" offer to click things that are not there.
    s = ff.section_status("successfactors", [_bar("Profile Information", None)])
    assert s["closed"] == [] and len(s["unknown"]) == 9
    assert ff.sections_caveat(s, 0) == ""


def test_the_bar_whose_name_carries_a_count_still_matches():
    # "Jobs Applied (2)" becomes "(3)" the next time we apply. Exact match cannot hold it, so the
    # substring fallback is load-bearing — and the LIVE label is carried through so the operator
    # sees the real count rather than our generic name.
    s = ff.section_status("successfactors", [_bar("Jobs Applied (7)", True)])
    row = next(r for r in s["sections"] if r["field"] == "profile_section_jobs_applied")
    assert row["state"] == "open" and row["label"] == "Jobs Applied (7)"
