"""Observation profiles (SESSION 18) — what to look at here, and what we could not see.

Pinned in the order it costs to get wrong:
  1. The DIALOG is read before the form beneath it, on every kind — a modal over the Apply button
     makes every fact under it irrelevant, and that is the whole 2026-08-19 Paylocity miss.
  2. Platform entries SHARPEN, never replace: an unknown ATS keeps the full generic sweep (TAM).
  3. A truncated option list is REPORTED, with its true count — 52 states read as 24 with New
     Hampshire missing (2026-08-21).
  4. The structural blindnesses are stated on every page: a census confirms answeredness and never
     correctness, and ax_scan carries no checked state.
  5. The page's own wizard position is READ, not just used as a yes/no (2026-08-19: a six-step
     wizard read as one screen from Submit).
"""
from __future__ import annotations

import apply_landing as al
import observation_profiles as op


def test_the_dialog_is_read_before_the_form_beneath_it_on_every_kind():
    for kind in al.KINDS:
        order = op.reading_order(kind)
        assert order[0] == op.DIALOGS, f"{kind} reads something before checking for a dialog"


def test_a_platform_may_sharpen_an_order_and_an_unknown_one_keeps_the_generic_sweep():
    """2026-08-19: Paylocity's form opens BEHIND an upload modal, and the résumé parse then
    multiplies the form — so uploads outrank the census there. TAM had no entry at all and went
    posting→submitted in one pass, which is the reason the generic tier must carry a stranger."""
    generic = op.reading_order(al.APPLICATION_FORM)
    paylocity = op.reading_order(al.APPLICATION_FORM, "paylocity")
    assert paylocity.index(op.UPLOADS) < paylocity.index(op.REQUIRED)
    assert paylocity != generic
    unknown = op.reading_order(al.APPLICATION_FORM, "applicantmanager")
    assert unknown == generic, "an unmeasured platform must keep the full generic sweep"
    assert set(paylocity) == set(generic), "a platform entry may reorder, never drop a reader"


def test_an_unnamed_kind_looks_at_everything_not_at_nothing():
    order = op.reading_order("some_screen_nobody_has_named")
    assert order == op._FALLBACK_ORDER and op.REQUIRED in order


def test_a_truncated_option_list_is_reported_with_its_true_count():
    """THE REAL CENSUS SHAPE: rows live under `unanswered`/`answered`/`optional` (never `fields`)
    and a row's name key is `field`. The first draft read `census["fields"]` and `row["label"]`,
    so it would have found nothing at all — and a ~250-entry Country select is just as likely to
    sit in `answered` as in `unanswered`."""
    census = {"answered": [{"field": "State", "options": ["AL"] * 24, "option_count": 52,
                            "options_truncated": True}]}
    gaps = op.read_gaps(census=census)
    truncations = [g for g in gaps if g["what"] == "option list truncated"]
    assert truncations, "an answered field's truncated list is still a gap"
    assert "24 of 52" in truncations[0]["detail"] and "State" in truncations[0]["detail"]


def test_the_censuss_own_cap_flags_are_read_by_their_real_names():
    """These three were computed by the scanner on 2026-08-21 and dropped twice — at the mcp
    handler and again at the census projection — so no consumer could tell a complete reading
    from a capped one. Carried end to end as of 2026-08-27."""
    gaps = op.read_gaps(census={"optional_truncated": True, "page_errors_truncated": True,
                                "field_errors_truncated": True})
    whats = {g["what"] for g in gaps}
    assert {"optional_truncated", "page_errors_truncated", "field_errors_truncated"} <= whats


def test_a_visible_dialog_is_the_headline_and_an_unchecked_page_says_so():
    """The tri-state that matters most: a modal FOUND, a page checked and clear, and a page
    NOBODY CHECKED — the last two must never render alike (2026-08-19, Paylocity)."""
    found = op.read_gaps(census={"unanswered": [], "dialogs": [
        {"role": "alertdialog", "text": "We Care About Your Privacy", "visible": True,
         "modal": True, "area": 400000}]})
    hit = [g for g in found if g["what"] == "a dialog is on top of this page"]
    assert hit and "Privacy" in hit[0]["detail"]

    clear = op.read_gaps(census={"unanswered": [], "dialogs": [
        {"role": "dialog", "text": "hidden furniture", "visible": False, "area": 0}]})
    assert not [g for g in clear if g["reader"] == op.DIALOGS], \
        "a hidden dialog node is furniture every SPA carries — reporting it would cry wolf"

    unasked = op.read_gaps(candidates=[{"role": "button", "name": "Apply"}])
    assert any(g["what"] == "nobody checked for a dialog" for g in unasked)


def test_the_structural_blindnesses_are_stated_every_time():
    gaps = op.read_gaps(census={"fields": []}, candidates=[{"role": "button", "name": "Apply"}])
    whats = {g["what"] for g in gaps}
    assert "correctness" in whats, "a census that never says it cannot judge correctness invites a retry"
    assert "checked state" in whats


def test_a_control_with_no_accessible_name_is_named_as_a_gap():
    gaps = op.read_gaps(candidates=[{"role": "radio", "name": ""},
                                    {"role": "radio", "name": "  "},
                                    {"role": "button", "name": "Submit"}])
    nameless = [g for g in gaps if g["what"] == "controls with no accessible name"]
    assert nameless and "2 candidate(s)" in nameless[0]["detail"]


def test_an_unread_frame_carrying_real_text_is_a_gap():
    """THE REAL FRAME SHAPE, as `pick_content` reads it: `id`/`name` for identity and `text` for
    content. The first draft matched `url`/`text_len`, neither of which is ever set, so it could
    not have fired once."""
    frames = [{"id": "inner", "text": "x" * 4000, "readable": True}]
    gaps = op.read_gaps(frames=frames, content_source="top")
    assert any(g["what"] == "an unread frame with real text" for g in gaps)
    # the frame we DID classify is not a gap
    quiet = op.read_gaps(frames=frames, content_source="inner")
    assert not any(g["what"] == "an unread frame with real text" for g in quiet)


def test_the_page_states_its_own_position_and_the_report_carries_it():
    """The regex has parsed this since the confirmation guard was written and the numbers were
    discarded every time — which is why 'at most 1 screen from Submit' survived a six-step wizard."""
    report = op.describe(kind=al.APPLICATION_FORM, platform="paylocity",
                         page_text="Step 1 of 6 — Information")
    assert report["wizard"] == {"step": 1, "of": 6}
    assert report["profile_source"] == "platform"
    assert report["looked_at"][0] == op.DIALOGS


def test_a_page_with_no_position_says_none_rather_than_guessing_one():
    report = op.describe(kind=al.JOB_POSTING, page_text="Apply for this job")
    assert report["wizard"] is None
    assert report["profile_source"] == "generic"
