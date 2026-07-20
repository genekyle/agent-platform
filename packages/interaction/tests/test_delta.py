"""Tests for StateDelta — the supervisor's cheap always-on sense.

The cases that matter are the ones the old 3-tuple `progress_signature` got wrong: a page whose
url/state/unanswered-set are identical but whose CONTROLS changed (a modal, an error banner, a
button going disabled), and the mirror case of incidental churn that must NOT read as movement.
"""

from __future__ import annotations

from interaction.delta import (
    FIRST_OBSERVATION,
    PROMPT_IDENTITY_CAP,
    StateDelta,
    compute,
    delta_to_prompt,
    identities_from_ax,
    identities_from_scan,
)


# --- identities: one normalization, shared with the fingerprint ----------------------
def test_identities_from_ax_uses_the_fingerprint_normalization():
    """Volatile tokens are stripped, so a badge count ticking up is NOT a changed control."""
    before = identities_from_ax([{"role": "link", "name": "Messages (3)"}])
    after = identities_from_ax([{"role": "link", "name": "Messages (7)"}])
    assert before == after == ("link|messages",)


def test_identities_from_ax_drops_purely_volatile_labels():
    """A bare count/price carries no stable identity — it must not appear as a control."""
    assert identities_from_ax([{"role": "generic", "name": "$1,299.00"}]) == ()


def test_identities_from_ax_is_sorted_and_deduplicated():
    ids = identities_from_ax([
        {"role": "button", "name": "Continue"},
        {"role": "button", "name": "Continue"},
        {"role": "button", "name": "Back"},
    ])
    assert ids == ("button|back", "button|continue")


def test_identities_from_scan_encodes_answeredness():
    """A field flipping answered shows up as one disappeared + one appeared — the only movement a
    scan-only delta can see."""
    before = identities_from_scan([{"field": "Work authorization", "answered": False}])
    after = identities_from_scan([{"field": "Work authorization", "answered": True}])
    assert before != after
    d = compute(before=before, after=after)
    assert d.appeared == ("field|work authorization|answered=True",)
    assert d.disappeared == ("field|work authorization|answered=False",)


def test_identities_from_scan_ignores_junk_rows():
    assert identities_from_scan([{"field": ""}, "not a dict", None, {}]) == ()


# --- the first turn is not a stall ---------------------------------------------------
def test_first_observation_is_moved_not_stalled():
    """There is no prior to have failed to move from. Calling step one a stall would escalate
    every single run immediately."""
    d = compute(before=None, after=("button|continue",))
    assert d is FIRST_OBSERVATION
    assert d.first_observation is True
    assert d.moved is True


def test_empty_before_is_not_the_same_as_no_before():
    """A caller that synthesises `[]` for the first turn gets a real (empty) comparison — which is
    why `compute` demands None, and why this distinction is tested rather than assumed."""
    d = compute(before=(), after=("button|continue",))
    assert d.first_observation is False
    assert d.appeared == ("button|continue",)


# --- the treadmill: what the old 3-tuple could not see -------------------------------
def test_identical_page_does_not_move():
    """The 2026-07-19 treadmill: a verified click that leaves the page byte-identical."""
    ids = ("button|continue", "textbox|why do you want this role")
    d = compute(before=ids, after=ids,
                url_before="https://smartapply.indeed.com/questions/1",
                url_after="https://smartapply.indeed.com/questions/1",
                state_before="indeed_apply_questions", state_after="indeed_apply_questions",
                unanswered_before=2, unanswered_after=2)
    assert d.moved is False
    assert d.churn == 0


def test_a_modal_opening_moves_even_when_url_state_and_fields_are_identical():
    """The exact blind spot of `progress_signature`: same url, same state, same unanswered set —
    but an overlay appeared. This must read as movement, and as churn worth diagnosing."""
    d = compute(
        before=("button|continue", "textbox|salary"),
        after=("button|continue", "textbox|salary", "dialog|verify your identity", "button|close"),
        url_before="https://x.test/apply", url_after="https://x.test/apply",
        state_before="indeed_apply_questions", state_after="indeed_apply_questions",
        unanswered_before=1, unanswered_after=1,
    )
    assert d.moved is True
    assert d.appeared == ("button|close", "dialog|verify your identity")
    assert d.disappeared == ()
    assert d.churn == 2


def test_an_error_banner_appearing_is_movement():
    d = compute(before=("button|continue",),
                after=("button|continue", "alert|choose an option to continue"))
    assert d.moved is True
    assert d.appeared == ("alert|choose an option to continue",)


# --- routes are compared TEMPLATED ---------------------------------------------------
def test_route_change_ignores_id_segments_and_query():
    """Paginating within one templated route is not a route change — otherwise a treadmill on a
    form whose url carries a step id would look like progress forever.

    These are the REAL urls from the 2026-07-18 Lactalis drive: all 29 journalled rows across
    `questions/1`, `questions/2` and `questions/3` share one route,
    `…/questions-module/questions/{id}`.
    """
    base = "https://smartapply.indeed.com/beta/indeedapply/form/questions-module/questions"
    d = compute(before=("button|continue",), after=("button|continue",),
                url_before=f"{base}/1?src=a", url_after=f"{base}/2?src=b")
    assert d.route_changed is False
    assert d.moved is False


def test_real_question_page_progress_is_visible_ONLY_in_the_control_churn():
    """Why the delta had to exist. Advancing questions/1 -> questions/2 changes NEITHER the
    templated route NOR the state (both are `indeed_apply_questions`) — the sole evidence that the
    page moved is that its controls turned over. A signature keyed on url+state is blind here, and
    so cannot tell this apart from the treadmill in the test above."""
    base = "https://smartapply.indeed.com/beta/indeedapply/form/questions-module/questions"
    d = compute(
        before=("button|continue", "radio|are you authorized to work in the us"),
        after=("button|continue", "textbox|why do you want this role"),
        url_before=f"{base}/1", url_after=f"{base}/2",
        state_before="indeed_apply_questions", state_after="indeed_apply_questions",
    )
    assert d.route_changed is False and d.state_changed is False
    assert d.moved is True and d.churn == 2


def test_a_non_id_path_segment_is_still_a_route_change():
    """The honest limit of `route_template`: it collapses digit/uuid/long-hex segments, not every
    opaque slug. A short alphanumeric id reads as a distinct route."""
    d = compute(before=(), after=(),
                url_before="https://x.test/questions/abc123",
                url_after="https://x.test/questions/def456")
    assert d.route_changed is True


def test_a_real_route_change_moves():
    d = compute(before=("button|continue",), after=("button|continue",),
                url_before="https://x.test/questions/1", url_after="https://x.test/review")
    assert d.route_changed is True
    assert d.moved is True


def test_missing_urls_do_not_invent_a_route_change():
    d = compute(before=("button|continue",), after=("button|continue",))
    assert d.route_changed is False
    assert d.moved is False


# --- state and unanswered ------------------------------------------------------------
def test_state_change_moves():
    d = compute(before=("button|continue",), after=("button|continue",),
                state_before="indeed_apply_questions", state_after="indeed_apply_review")
    assert d.state_changed is True
    assert d.moved is True


def test_an_unknown_state_on_either_side_is_not_a_state_change():
    """An unrecognised page is its own failure class, not movement — a None state must not be
    laundered into "we got somewhere"."""
    assert compute(before=(), after=(), state_before="indeed_apply_questions",
                   state_after=None).state_changed is False
    assert compute(before=(), after=(), state_before=None,
                   state_after="indeed_apply_questions").state_changed is False


def test_unanswered_delta_is_signed():
    """Negative = fields got answered (progress); positive = a new step's fields appeared."""
    assert compute(before=(), after=(), unanswered_before=3,
                   unanswered_after=1).unanswered_delta == -2
    assert compute(before=(), after=(), unanswered_before=1,
                   unanswered_after=4).unanswered_delta == +3


def test_answering_a_field_counts_as_movement_even_with_no_control_churn():
    """A field fill legitimately leaves the control set alone — it is still progress."""
    d = compute(before=("textbox|salary",), after=("textbox|salary",),
                unanswered_before=2, unanswered_after=1)
    assert d.moved is True


def test_missing_unanswered_counts_do_not_invent_movement():
    d = compute(before=("textbox|salary",), after=("textbox|salary",))
    assert d.unanswered_delta == 0
    assert d.moved is False


# --- the frozen serialization --------------------------------------------------------
def test_delta_to_prompt_is_stable_and_names_the_stall_loudly():
    ids = ("button|continue",)
    text = delta_to_prompt(compute(before=ids, after=ids, state_before="s", state_after="s"))
    assert text == "\n".join([
        "# DELTA",
        "moved: NO — the page is unchanged",
        "route_changed: no",
        "state_changed: no",
        "unanswered_delta: +0",
        "appeared: (none)",
        "disappeared: (none)",
    ])


def test_delta_to_prompt_caps_the_identity_list():
    """The delta is a prompt surface: an unbounded list of appeared controls is the raw-AX dump
    this architecture keeps out of prompts."""
    after = tuple(f"button|b{i}" for i in range(PROMPT_IDENTITY_CAP + 5))
    text = delta_to_prompt(compute(before=(), after=after))
    assert "(+5 more)" in text
    assert text.count("button|b") == PROMPT_IDENTITY_CAP


def test_delta_to_prompt_marks_the_first_observation():
    assert "first observation" in delta_to_prompt(FIRST_OBSERVATION)


def test_state_delta_is_frozen():
    import dataclasses
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        StateDelta().appeared = ("x",)  # type: ignore[misc]
