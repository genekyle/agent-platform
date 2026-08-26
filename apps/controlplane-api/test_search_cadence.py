"""Tests for the search cadence — focused on SEARCH_RECIPE staying in lockstep with the live
tab classifier. If a recipe state isn't a state apply_recipe.map_url_to_state can emit, the
blackboard's search plan can never advance to it, so this guards the integration seam."""

import apply_recipe
import search_cadence as sc


# A representative URL that should classify to each search-phase recipe state.
_URL_FOR_STATE = {
    "indeed_home": "https://www.indeed.com/",
    "indeed_search_results": "https://www.indeed.com/jobs?q=reporting+analyst&l=Nashua%2C+NH",
    "indeed_job_posting": "https://www.indeed.com/viewjob?jk=abc123",
}


def test_recipe_states_match_live_tab_classifier():
    for entry in sc.SEARCH_RECIPE:
        state = entry["state"]
        url = _URL_FOR_STATE.get(state)
        assert url is not None, f"no representative URL for recipe state {state!r}"
        assert apply_recipe.map_url_to_state(url) == state, (
            f"recipe state {state!r} is not what the tab classifier emits for {url!r} "
            f"(got {apply_recipe.map_url_to_state(url)!r}) — the search plan could never advance")


def test_search_phase_tabs_have_search_role():
    # Every search-recipe state must be a search-role tab (not apply/other), so reconcile keeps
    # the search plan active until the apply handoff.
    for entry in sc.SEARCH_RECIPE:
        desc = apply_recipe.describe_tab(_URL_FOR_STATE[entry["state"]])
        assert desc["role"] == "search", f"{entry['state']} classified as {desc['role']}"


def test_recipe_is_ordered_and_unique():
    states = sc.search_recipe_states()
    assert states == ["indeed_home", "indeed_search_results", "indeed_job_posting"]
    assert len(states) == len(set(states))


def test_cadence_spec_exposes_recipe():
    spec = sc.cadence_spec()
    assert spec["search_recipe"] == sc.SEARCH_RECIPE


def test_bounds_enforce_50mi_distance_floor():
    assert sc.BOUNDS["min_radius_miles"] == 50


def test_extraction_sweep_documents_distance_cardwalk_pagination():
    steps = " ".join(sc.CADENCE_MODES["extraction_sweep"]["steps"]).lower()
    assert "distance" in steps           # set the radius filter
    assert "click into" in steps         # walk the cards (in-page pane)
    assert "pagination" in steps         # page forward by clicking
    # and it explicitly refuses sub-floor results
    assert any("min_radius_miles" in d for d in sc.CADENCE_MODES["extraction_sweep"]["does_not"])


def test_submitted_outcome_catches_common_success_phrasings():
    # "...was submitted" was the gap: the regex only allowed "submitted" or
    # "has been submitted", so the very common past-tense confirmation slipped
    # through as 'unknown'. All of these are success confirmations.
    for text in (
        "Your application was submitted",
        "Application was submitted",
        "Your application has been submitted",
        "Application successfully submitted",
        "Application submitted",
        "Your application was sent",
        "Thanks for applying",
        "Application received",
        "You successfully applied",
    ):
        result = sc.classify_apply_outcome(text)
        assert result["outcome"] == "submitted", f"{text!r} -> {result}"
        assert result["human_required"] is False


def test_submitted_regex_does_not_swallow_blocker_branches():
    # The broadened 'submitted' pattern must not false-positive on the
    # ats_unavailable / account_creation pages, which never contain "submitted".
    ats = sc.classify_apply_outcome("The page you are looking for doesn't exist")
    assert ats["outcome"] == "ats_unavailable"
    acct = sc.classify_apply_outcome("Create an account to continue")
    assert acct["outcome"] == "account_creation"


# --- finding the search box by LOOKING, not assuming ----------------------------------------
def _cands(*pairs):
    return [{"role": r, "name": n, "backend_node_id": i} for i, (r, n) in enumerate(pairs, 10)]


#: Exactly what Indeed's logged-in home offered on 2026-07-24 (session 19). The first version of
#: the drive assumed "What" / "Where" / "Find jobs" and matched none of them.
_LIVE_INDEED = _cands(
    ("button", "Skip to main content"),
    ("combobox", "search: Job title, keywords, or company"),
    ("combobox", "Edit location"),
    ("button", "Clear location input"),
    ("button", "Search"),
    ("button", "Account"),
)


def test_finds_the_real_indeed_controls_not_the_assumed_ones():
    got = sc.find_search_controls(_LIVE_INDEED)
    assert got["query"]["name"] == "search: Job title, keywords, or company"
    assert got["location"]["name"] == "Edit location"
    assert got["submit"]["name"] == "Search"


def test_the_assumed_names_are_absent_from_the_real_page():
    """The regression, stated as the fact that caused it: nothing on the live page is called
    What, Where or Find jobs, so a hard-coded matcher types into nothing and clicks nothing."""
    names = {c["name"].lower() for c in _LIVE_INDEED}
    assert not ({"what", "where", "find jobs"} & names)


def test_a_page_naming_things_differently_still_resolves():
    """The point is the matcher looks; these names are as provisional as the last set."""
    got = sc.find_search_controls(_cands(
        ("textbox", "What"), ("textbox", "Where"), ("button", "Find jobs")))
    assert got["query"]["name"] == "What"
    assert got["location"]["name"] == "Where"
    assert got["submit"]["name"] == "Find jobs"


def test_one_box_matching_both_lists_is_the_query_box():
    """'search: Job title, keywords, or company' must never be taken for the location field —
    that types a city into the keyword box."""
    got = sc.find_search_controls(_cands(
        ("combobox", "Job title, keywords, or company"), ("button", "Search")))
    assert got["query"]["name"] == "Job title, keywords, or company"
    assert "location" not in got


def test_missing_controls_are_reported_as_missing():
    got = sc.find_search_controls(_cands(("button", "Account"), ("link", "Help")))
    assert got == {}


def test_the_most_specific_hint_wins():
    """Ordered hints, so a page with several plausible boxes yields the right one."""
    got = sc.find_search_controls(_cands(
        ("combobox", "What are you searching for"),
        ("combobox", "Job title, keywords, or company"),
        ("button", "Search")))
    assert got["query"]["name"] == "Job title, keywords, or company"


def test_the_home_feed_is_an_appending_surface_with_nothing_to_paginate():
    """Indeed's front page is a THIRD traversal shape — not a page of results, not a virtualised
    inner column. Measured live 2026-08-25, session 32: the window scrolls, batches of 15 append at
    the bottom, and there is no page number to click."""
    import search_cadence as sc

    t = sc.home_feed_traversal()
    assert t["appending"] is True and t["virtualised"] is False
    assert t["scroll_container"] == "window"
    assert t["batch_size"] == 15
    # There is nothing to paginate — asking "which page next" is the wrong question on a feed.
    assert t["paginate_by"] is None
    # It is a COPY: a caller that edits the traversal must not edit it for everyone.
    t["batch_size"] = 999
    assert sc.home_feed_traversal()["batch_size"] == 15


def test_the_batch_evidence_is_new_ids_and_never_mere_motion():
    """Three consecutive wheels moved the window 900px each and rendered NOTHING (doc height flat at
    3717); the fourth appended 15 and the height jumped to 6906. A walker that read motion as
    progress would score three empty passes as three reviewed batches."""
    import search_cadence as sc

    t = sc.home_feed_traversal()
    assert "new_ids" in t["batch_evidence"]
    assert "motion alone is not a batch" in t["batch_evidence"]
    assert "exhausted" in t["stop_scrolling_when"]


def test_the_feed_mode_has_no_query_and_bounds_itself():
    """The one mode with no query, no distance pill and no pagination — Indeed already did the
    matching. What replaces them is the batch, and a feed that never ends needs a BOUND rather than
    a termination proof."""
    import search_cadence as sc

    mode = sc.CADENCE_MODES["suggested_feed_apply"]
    joined = " ".join(mode["does_not"]).lower()
    assert "type a query" in joined and "distance filter" in joined
    assert "unbounded" in joined, "a feed with no measured end must bound its own run"
    assert "moved" in joined, "motion must not be mistaken for a batch reviewed"
    # The consequential gate does not relax on this surface either.
    assert any("per-application" in s or "operator confirm" in s for s in mode["steps"])
    assert sc.cadence_spec()["modes"] is sc.CADENCE_MODES or "suggested_feed_apply" in sc.cadence_spec()["modes"]


def test_the_feed_ships_a_row_that_is_not_a_job():
    """`data-jk="cdef0123456789ab"` — a literal hex placeholder — renders at height 0. Anything
    reading [data-jk] directly must drop it, or the cadence opens a card that cannot be clicked."""
    import search_cadence as sc

    t = sc.home_feed_traversal()
    assert "cdef0123456789ab" in t["not_a_card"]
    assert "zero-height" in t["not_a_card"]


def test_the_feed_shows_the_whole_batch_and_filters_nothing():
    """The first draft of this mode rejected sub-floor cards from the card itself. Operator,
    2026-08-26: *"i don't want to set a floor yet, i do want to consider all opportunities."* A feed
    is a surface of things we did not ask for, which is what makes it worth working — pre-filtering
    it is deciding for the operator."""
    import search_cadence as sc

    mode = sc.CADENCE_MODES["suggested_feed_apply"]
    joined = " ".join(mode["does_not"]).lower()
    assert "filter the batch before showing it" in joined
    assert "apply to anything the operator did not pick" in joined
    # EVERY card is recorded, not a surviving subset.
    assert any("every card" in r.lower() for r in mode["records"])
    # And the traversal opens what the operator picked, not what a filter left behind.
    assert sc.home_feed_traversal()["click_into"] == "operator_picks"


def test_the_feed_is_a_process_inside_the_session_not_a_new_one():
    """Operator: it *"shouldn't require a new session since all actions are still being performed
    on indeed — so instead it will be a new process or workflow within a domain."*"""
    import search_cadence as sc

    mode = sc.CADENCE_MODES["suggested_feed_apply"]
    assert any("new session" in d for d in mode["does_not"])
    assert any("ensure_active_feed" in s for s in mode["steps"]), \
        "the mode must name the process it opens, or nothing attributes its sightings"
