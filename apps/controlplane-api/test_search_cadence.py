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
