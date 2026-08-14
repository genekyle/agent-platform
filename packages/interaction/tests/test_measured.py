"""The type that makes "I did not look" unrepresentable.

Every test here is a real incident from `docs/LEARNINGS.md`, rewritten as the thing that would
have refused to compile or refused to answer. The point of the module is that these stop being
lessons and start being types, so the tests are named after what they cost.
"""

from __future__ import annotations

import pytest
from interaction.measured import (MEASURED, PARTIAL, UNMEASURED, Reading, Unmeasured,
                                  all_measured, any_measured, from_capped_list)


# --- the enforcement ---------------------------------------------------------------------------

def test_bool_raises_because_three_states_do_not_fit_in_two():
    """`if reading:` and `not reading` are the two lines that turned every incident into a silent
    wrong answer. They must not compile into working code."""
    for r in (Reading.measured(True, how="w"), Reading.unmeasured("no document"),
              Reading.partial([1], shown=1, total=9)):
        with pytest.raises(TypeError, match="three states"):
            bool(r)
        with pytest.raises(TypeError):
            if r:            # noqa: SIM103 - the point is that this line raises
                pass


def test_the_error_says_which_question_to_ask_instead():
    """A refusal the caller cannot act on is still a dead end — including a refusal to a
    programmer."""
    with pytest.raises(TypeError) as e:
        bool(Reading.unmeasured("cross-origin frame"))
    msg = str(e.value)
    assert "is_true" in msg and "is_false" in msg and "value_or" in msg


# --- could-not-look is not "no" ----------------------------------------------------------------

def test_a_probe_that_found_nothing_has_not_found_no():
    """`/auth_state` answered `logged_in: false, has_sign_in: false, has_account: false` on a
    SIGNED-IN session (2026-08-13). It found no evidence and reported the absence as a negative.

    `is_false()` is the accessor that refuses to make that mistake, and it is why both accessors
    exist: `not is_true()` would fold unmeasured back into "no".
    """
    blind = Reading.unmeasured("no document we can enter")
    assert blind.is_true() is False
    assert blind.is_false() is False          # <- the whole point
    assert blind.is_unmeasured() is True

    looked = Reading.measured(False, how="/auth_state saw the sign-in form")
    assert looked.is_false() is True


def test_a_rail_that_cannot_see_does_not_report_clear():
    """`/challenge_visibility` answered `blocking: false, solved: true` over a form carrying two
    live hCaptchas inside a frame it could not enter. *A rail that reports "clear" when it cannot
    see is worse than no rail.*

    The rail's question is "is ANYTHING blocking?", so it combines with `any_measured`: a gap
    among otherwise-false readings can never settle as "no".
    """
    seen = Reading.measured(False, how="0 hcaptcha frames in the top document")
    unseen = Reading.unmeasured("icims_content_iframe is cross-origin")
    verdict = any_measured(seen, unseen)
    assert verdict.is_unmeasured()
    assert verdict.is_false() is False           # the answer the old rail gave
    assert "cross-origin" in verdict.why


def test_a_blocker_we_did_see_still_blocks_over_a_gap():
    """The mirror half: one definite YES settles an OR whatever else went unread."""
    verdict = any_measured(Reading.unmeasured("could not read the second frame"),
                           Reading.measured(True, how="a live hCaptcha in the top document"))
    assert verdict.is_true()
    assert "hCaptcha" in verdict.how


def test_choosing_the_combinator_is_choosing_which_answer_is_safe():
    """The asymmetry, stated as a test because picking the wrong operator IS the bug class — and
    it caught a flaw in this module's own first draft.

    The SAME two readings settle opposite ways depending on the question, and each operator is
    decisive in exactly one direction: whichever one you would act on, make it the one that has
    to be measured.
    """
    definite_no = Reading.measured(False, how="looked, nothing there")
    gap = Reading.unmeasured("could not look")

    # "is everything clear?" — a definite NO settles it.
    assert all_measured(definite_no, gap).is_false()
    # "is anything blocking?" — the same NO settles nothing, because the gap might hold a yes.
    assert any_measured(definite_no, gap).is_unmeasured()


def test_require_raises_rather_than_defaulting():
    with pytest.raises(Unmeasured, match="signed in"):
        Reading.unmeasured("no cookie jar").require("signed in")
    assert Reading.measured(7, how="counted").require("count") == 7


def test_value_or_puts_the_decision_at_the_call_site():
    """What an absent measurement should MEAN is a decision, and it differs per caller — so it is
    stated where it is made rather than baked into the reader."""
    blind = Reading.unmeasured("tab closed")
    assert blind.value_or([]) == []
    assert blind.value_or("unknown") == "unknown"
    assert Reading.measured(["a"], how="scan").value_or([]) == ["a"]


# --- partial is not complete -------------------------------------------------------------------

def test_absence_from_a_sample_is_not_absence():
    """THE COUNTRY CAP, 2026-08-14. A ~250-entry select censused 24 options whose only "United"
    entry was United Arab Emirates, with nothing saying the list had been cut.

    Precisely: the cockpit rendered those 24 as the complete set of choices and offered its
    free-text box only when there were ZERO options, so the dropdown was a wall. (The fill planner
    was NOT the victim here — it held the right answer and the bunch pass skips dropdowns by
    design. Any future consumer that matches a wanted value against this list is, which is what
    this type is for.)"""
    countries = from_capped_list(["Aruba", "Afghanistan", "United Arab Emirates"],
                                 total=250, how="census option cap")
    assert countries.is_partial()
    assert countries.is_complete() is False

    # Present is always decisive...
    assert countries.contains("Aruba").is_true()
    # ...absent never is, on a sample.
    missing = countries.contains("United States")
    assert missing.is_unmeasured()
    assert missing.is_false() is False
    assert "absence from a sample is not absence" in missing.why


def test_absence_is_decisive_once_the_reading_is_whole():
    whole = from_capped_list(["Yes", "No"], total=2, how="all options read")
    assert whole.is_complete()
    assert whole.contains("Maybe").is_false()


def test_a_sample_that_is_the_whole_thing_collapses_to_measured():
    """So no caller has to special-case "partial but actually complete"."""
    assert Reading.partial([1, 2], shown=2, total=2).status == MEASURED
    assert Reading.partial([1], shown=1, total=9).status == PARTIAL


def test_a_list_with_no_declared_total_is_unmeasured_not_whole():
    """A payload that never said how many there were has not told us it was complete. Assuming it
    did is the same assumption, one layer earlier."""
    r = from_capped_list(["a", "b"], total=None, how="some endpoint")
    assert r.is_unmeasured()
    assert "cannot tell a whole list from a capped one" in r.why
    assert r.contains("a").is_unmeasured()


# --- provenance and the JSON boundary ----------------------------------------------------------

def test_a_reading_says_what_it_looked_at():
    r = Reading.measured(True, how="the widget's own rendered row")
    assert "rendered row" in r.describe()
    gapped = Reading.measured(True, how="idle timer", gaps=("page_age_s", "cookie_ttl_s"))
    assert "did not cover page_age_s, cookie_ttl_s" in gapped.describe()


@pytest.mark.parametrize("original", [
    Reading.measured(True, how="w"),
    Reading.measured(["a", "b"], how="scan", gaps=("frame2",)),
    Reading.unmeasured("cross-origin"),
    Reading.partial([1, 2], shown=2, total=40, how="cap"),
])
def test_readings_round_trip_as_json(original):
    """These cross mcp <-> controlplane as JSON, which is exactly where the type would otherwise
    be erased back into the bare value that caused the incident."""
    import json
    back = Reading.from_dict(json.loads(json.dumps(original.as_dict())))
    assert back.status == original.status
    assert back.value_or("!") == original.value_or("!")
    assert back.why == original.why and back.how == original.how
    assert (back.shown, back.total) == (original.shown, original.total)
    assert back.gaps == original.gaps


def test_a_payload_from_an_unmigrated_reader_parses_as_unmeasured():
    """The boundary rule. An endpoint that has not been migrated has, by definition, not told us
    what it looked at — treating its bare value as measured would re-create the bug at the seam."""
    for payload in ({"logged_in": False}, [], None, "false", 0):
        r = Reading.from_dict(payload)
        assert r.is_unmeasured()
        assert r.is_false() is False
    assert Reading.from_dict({"status": UNMEASURED, "why": "no frame"}).why == "no frame"


def test_from_dict_is_idempotent_on_a_reading():
    r = Reading.measured(1, how="w")
    assert Reading.from_dict(r) is r


def test_an_unmeasured_reading_never_carries_a_value():
    """There is no value to carry, and offering one is how a default gets read as a measurement."""
    r = Reading.unmeasured("nothing to read")
    assert r.as_dict()["value"] is None
    assert r.why
    with pytest.raises(Unmeasured):
        r.require()
