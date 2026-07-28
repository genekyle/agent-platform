"""LinkedIn's own recipe — and the landmines a shared matcher walks into on its pages.

Every assertion here mirrors something read off the live page (session #22, 2026-07-28). The point
of the file is that the next person changing the search cadence cannot silently re-introduce the
faults this drive found.
"""

from __future__ import annotations

import linkedin_recipe as lr
import search_cadence as sc


def _c(role, name, node=1):
    return {"role": role, "name": name, "backend_node_id": node}


#: The jobs home as AX actually serves it — including the skip-links, which are the trap.
LIVE_HOME = [
    _c("button", "Skip to main content", 1),
    _c("button", "Skip to search", 2),
    _c("textbox", "I'm looking for…", 3),
    _c("combobox", "I’m looking for...", 4),
    _c("button", "Me", 5),
    _c("button", "Keyboard shortcuts", 6),
]


def test_the_shared_matcher_picks_a_skip_link_as_the_submit():
    """Not a hypothetical: `_SUBMIT_HINTS` contains "search", LinkedIn ships `Skip to search`, and
    clicking it jumps the caret to a landmark while reporting a submitted query. This test exists
    to keep that fact visible if anyone widens the shared hints again."""
    found = sc.find_search_controls(LIVE_HOME)
    assert found.get("submit", {}).get("name") == "Skip to search"
    assert lr.is_forbidden_submit("Skip to search")


def test_linkedins_own_finder_takes_the_box_and_refuses_the_skip_link():
    controls = lr.search_controls(LIVE_HOME)
    assert "looking for" in controls["query"]["name"].lower()
    assert controls["submit"] is None       # measured: the jobs home has none
    assert controls["location"] is None     # measured: no location box either


def test_it_will_not_claim_it_can_run_a_query_while_the_box_cannot_be_filled():
    """THE HONEST BIT. Finding a query box is not the same as being able to use one: the humanized
    `type` reports ok, blurs the field and inserts nothing. `ready` stays False so no caller reads
    "found" as "can run"."""
    controls = lr.search_controls(LIVE_HOME)
    assert controls["ready"] is False
    assert "does not fill" in controls["why"]
    assert lr.SEARCH_SUBMIT_READY is False


def test_the_placeholder_is_not_an_identifier():
    """It changes to "Describe the job you want" on focus. The accessible name does not — so a
    matcher keyed to the placeholder finds the box once and then loses it."""
    focused = [_c("combobox", "I’m looking for...", 4)]
    assert lr.find_query_box(focused) is not None
    # the visible placeholder is accepted as a hint too, for the pages that expose it as the name
    assert "describe the job" in lr.QUERY_NAME_HINTS


def test_states_come_from_the_url_not_from_hashed_markup():
    assert lr.classify("https://www.linkedin.com/jobs/") == lr.HOME
    assert lr.classify("https://www.linkedin.com/jobs/search/?keywords=x") == lr.SEARCH_RESULTS
    assert lr.classify("https://www.linkedin.com/jobs/view/12345/") == lr.JOB_DETAIL
    assert lr.classify("https://www.linkedin.com/login") == lr.LOGIN_WALL


def test_a_logged_out_wall_is_promotable_by_text():
    assert lr.classify("https://www.linkedin.com/jobs/", "Join now to see jobs") == lr.LOGIN_WALL


def test_the_home_state_id_matches_what_facets_already_maps():
    """The id was registered as a page state and is already mapped to phase `home` in
    perception/facets. Two names for one screen splits the corpus."""
    from perception import facets
    assert facets.phase_for(lr.HOME) == "home"
    assert facets.platform_for(lr.HOME) == "linkedin"


# --- the staged cadence -----------------------------------------------------------------------
# Operator-described 2026-07-28 and the real structural difference from Indeed:
#   INDEED    [what] + [where] together -> submit -> results -> radius
#   LINKEDIN  [title] alone             -> submit -> results -> location -> radius
def test_the_search_is_staged_title_then_location_then_radius():
    assert [s["stage"] for s in lr.SEARCH_CADENCE] == ["title", "location", "radius"]
    assert lr.stage_for_state(lr.HOME)["stage"] == "title"
    assert lr.stage_for_state(lr.HOME)["value_from"] == "query"


def test_only_the_title_stage_spends_the_query():
    """Location and radius refine an existing result set — they re-query but do not spend a NEW
    search, which is why they are separate rungs rather than inputs to the first one."""
    by_stage = {s["stage"]: s for s in lr.SEARCH_CADENCE}
    assert by_stage["title"]["commits"] is True
    assert by_stage["location"]["commits"] is False
    assert by_stage["radius"]["commits"] is False


def test_location_and_radius_only_exist_once_results_are_on_screen():
    """The location box is not missing from the jobs home — it does not exist YET. A cadence that
    tried to fill it there would hunt a control that cannot exist and call the page broken."""
    assert lr.location_box_expected_on(lr.HOME) is False
    assert lr.location_box_expected_on(lr.SEARCH_RESULTS) is True
    for stage in ("location", "radius"):
        step = next(s for s in lr.SEARCH_CADENCE if s["stage"] == stage)
        assert step["on_state"] == lr.SEARCH_RESULTS


def test_radius_follows_location_never_precedes_it():
    """A radius is meaningless before there is a location to be radial about."""
    on_results = lr.stage_for_state(lr.SEARCH_RESULTS, done=("title",))
    assert on_results["stage"] == "location"
    after_loc = lr.stage_for_state(lr.SEARCH_RESULTS, done=("title", "location"))
    assert after_loc["stage"] == "radius"


def test_a_state_with_no_outstanding_stage_answers_none():
    """The honest answer on a page the cadence does not drive — it keeps a caller from forcing a
    stage onto the wrong screen."""
    assert lr.stage_for_state(lr.JOB_DETAIL) is None
    assert lr.stage_for_state(lr.SEARCH_RESULTS, done=("title", "location", "radius")) is None
