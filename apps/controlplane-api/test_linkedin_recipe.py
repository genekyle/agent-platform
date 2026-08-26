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


def test_ready_is_true_now_that_the_box_fills_and_enter_commits():
    """`ready` was False for a reason that turned out to be false. The claim behind it — "the
    humanized type reports ok, blurs the field and inserts nothing" — was retracted by the
    operator's own recording (17 trusted keystrokes landed), and Enter then committed and landed on
    the results page. So `ready` is True, and the retracted claim must not be back in `why`."""
    controls = lr.search_controls(LIVE_HOME)
    assert controls["ready"] is True
    assert "does not fill" not in controls["why"]
    assert lr.SEARCH_SUBMIT_READY is True


def test_a_page_with_no_query_box_is_still_not_ready():
    """`ready` has to keep meaning something. On the results page there is no query box to fill, and
    saying "ready" there would send a caller typing into nothing."""
    controls = lr.search_controls([_c("button", "Location Greater Boston", 9)])
    assert controls.get("query") is None
    assert controls["ready"] is False
    assert "No query box" in controls["why"]


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
def test_the_search_is_staged_title_then_location_then_radius_then_traverse():
    assert [s["stage"] for s in lr.SEARCH_CADENCE] == ["title", "location", "radius", "traverse"]
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


def test_location_comes_first_and_radius_never_comes_at_all():
    """RETRACTED AND REWRITTEN 2026-08-14, and kept under a new name rather than deleted.

    This test used to assert that after location the next stage is `radius` — encoding the belief
    that LinkedIn has a radius slider. It never did. The belief was Indeed's shape written into
    LinkedIn's recipe and asserted by a test, which is how an unmeasured claim earns a green tick
    and survives three weeks: the test was true about the CODE and false about the PAGE.

    Measured live (session #29): no distance pill on the filter row, zero DOM hits for
    distance/radius/mile/within, and the location popup holds only a place-name field. So after
    location the next real work is the traversal.
    """
    on_results = lr.stage_for_state(lr.SEARCH_RESULTS, done=("title",))
    assert on_results["stage"] == lr.STAGE_LOCATION
    after_loc = lr.stage_for_state(lr.SEARCH_RESULTS, done=("title", "location"))
    assert after_loc["stage"] == lr.STAGE_TRAVERSE


def test_a_state_with_no_outstanding_stage_answers_none():
    """The honest answer on a page the cadence does not drive — it keeps a caller from forcing a
    stage onto the wrong screen."""
    assert lr.stage_for_state(lr.JOB_DETAIL) is None
    assert lr.stage_for_state(
        lr.SEARCH_RESULTS, done=("title", "location", "radius", "traverse")) is None


def test_the_title_stage_must_open_the_widget_before_typing():
    """MEASURED via /observe: a bare `type` filled nothing four times; click-then-type filled it on
    the first try, and the recorder caught six trusted keystrokes building R -> Report. The box is a
    staged widget, so opening it is part of the step, not a nicety."""
    title = next(s for s in lr.SEARCH_CADENCE if s["stage"] == "title")
    assert title["open_first"] is True


def test_filling_and_submitting_are_tracked_separately():
    """'We can type' is not 'we can search' — two flags, and they were true at different times,
    which is the only reason the gap between them was ever visible. Both measured now."""
    assert lr.SEARCH_FILL_READY is True
    assert lr.SEARCH_SUBMIT_READY is True


def test_the_commit_is_enter_and_is_confirmable_without_a_navigation():
    """MEASURED from the operator's own /observe recording (2026-07-28): click, 17 keystrokes,
    then `keydown Enter` -> `change` -> `blur`. There is no submit button and no suggestion tile.
    The change+blur pair matters because this is a SPA — there is no load to wait for, so the
    commit has to be confirmable from events."""
    title = next(s for s in lr.SEARCH_CADENCE if s["stage"] == "title")
    assert title["commit_key"] == "Enter"
    assert title["commit_signature"] == ("change", "blur")


def test_the_disproven_mechanisms_are_gone_from_the_record():
    """Four mechanisms were written down as findings about this one control and all four were
    disproven by a single 13-second recording. None may survive in the docstring as current."""
    doc = lr.__doc__
    for dead in ("blurs the element", "the accessible name changes on focus",
                 "THE OPEN BLOCKER"):
        assert dead not in doc, dead


# --- walking the results list ------------------------------------------------------------------
# Operator-directed 2026-07-30, after the list would not scroll: on LinkedIn the traversal IS the
# search — scroll it, open every card, record each — and the scroll has to be a wheel with the
# cursor over the list, like the clicks. These assertions are what keeps that from being optional
# again, because every part of it was previously left to whichever caller got there first.
def test_walking_the_list_is_a_stage_of_the_cadence_not_an_afterthought():
    traverse = next(s for s in lr.SEARCH_CADENCE if s["stage"] == lr.STAGE_TRAVERSE)
    assert traverse["on_state"] == lr.SEARCH_RESULTS
    assert traverse["commits"] is False           # scrolling and reading spend no search
    # it comes AFTER the filters — traversing a list you are about to re-query is wasted motion
    assert lr.stage_for_state(lr.SEARCH_RESULTS,
                              done=("title", "location", "radius"))["stage"] == "traverse"


def test_the_scroll_is_a_wheel_over_the_list_and_never_scrolltop():
    """The measured failure: `pane.scrollTop` on a class-named element that was null on the live
    page, so every scroll fell through to the window and the list never moved. Both halves are
    named here — the wheel, and the pointer being over the list — because either one alone is the
    same no-op wearing a different hat."""
    t = lr.results_traversal()
    assert t["scroll_by"] == "wheel"
    assert "over the results list" in t["scroll_pointer"]
    assert t["scroll_container"] == "inner"
    # The MECHANISM is the assertion, not the absence of a word: `scrollTop` legitimately appears in
    # the evidence ("scrollTop 0 -> 700") because reading the element's own position is how we
    # confirmed the wheel landed. Assigning to it is what is forbidden, and `scroll_by` says so.
    assert t["scroll_by"] != "scrollTop"
    assert t["scroll_endpoint"] == "/scroll_job_list"


def test_the_list_is_located_from_a_card_not_by_class():
    """The classes are build-hashed, which is why the class-named scroller and the class-named card
    reader both came back empty on the same page. Note what this does NOT say any more: 'from an
    anchor'. A card is not a link here — assuming it was is what walked the scroller-finder up from
    the detail pane's title and scrolled the wrong column."""
    t = lr.results_traversal()
    assert t["located_by"].startswith("job card")
    assert "lazy-column" in t["located_by"]
    assert "anchor" not in t["located_by"]
    assert t["virtualised"] is True


def test_a_scroll_needs_evidence_that_it_moved_or_rendered():
    """`ok` from a wheel means CDP dispatched it. Movement OR new cards is what means it landed —
    and both are needed, because at the end of a rendered list the position moves with nothing new,
    while a list that renders in place grows on almost no movement."""
    t = lr.results_traversal()
    assert "MOVED" in t["scroll_evidence"] and "RENDERED" in t["scroll_evidence"]
    assert "exhausted" in t["stop_scrolling_when"]


def test_every_card_is_opened_and_the_pane_switch_is_the_proof():
    t = lr.results_traversal()
    assert t["click_into"] == "every_card"
    assert "humanized" in t["click_by"] and ".click()" in t["click_by"]
    assert "switched" in t["click_evidence"]


def test_the_traversal_separates_what_was_driven_live_from_what_was_not():
    """PRINCIPLES §13: say which it is. The scroll, the reader and the click were driven live
    2026-07-30 and say so with the numbers; the sweep and page 2 were driven live 2026-08-26 and
    now say so too. A recipe that claimed both without dating them would be the same failure as
    claiming neither — and the claim this test used to pin ("paging has not been PRESSED", "the
    sweep stops at /set_distance") was still being asserted here long after it stopped being true,
    which is how a stale blocked_on survives a green suite."""
    t = lr.results_traversal()
    assert "scrollTop 0 -> 700" in t["verified_live"]
    assert "25/25 cards" in t["verified_live"]
    # the second drive, and it has to carry its own date and its own numbers
    assert "2026-08-26" in t["verified_live_2"] and "Page 2" in t["verified_live_2"]
    # and what is open is now about the SURFACE and the result set, not about paging
    assert "result set" in t["still_unverified"].lower()
    assert "f_AL" in t["still_unverified"]
    assert "set_distance" not in lr.spec()["blocked_on"]
    assert "PREFERENCES_LANDING" in lr.spec()["blocked_on"]


def test_the_card_is_addressed_by_componentkey_not_by_an_href_or_an_attribute():
    """MEASURED: the page carries no data-job-id / urn / class hook, and its only /jobs/view/
    anchors belong to the DETAIL PANE — which is exactly how the scroller-walk ended up on the
    wrong column. The card is `[componentkey=job-card-component-ref-<id>]` with role=button."""
    t = lr.results_traversal()
    assert "componentkey" in t["card_selector"] and "role=button" in t["card_selector"]
    assert "componentkey" in t["card_identity"]
    assert "lazy-column" in t["located_by"]


def test_the_pane_is_asked_which_job_it_is_showing():
    """The pane states its own id (?currentJobId= / JobDetails_*_<id>), so 'is this the job I
    clicked' is compared, not inferred from a text diff — and identity arrives BEFORE the body, so
    both are waited on separately."""
    t = lr.results_traversal()
    assert "currentJobId" in t["click_evidence"]
    assert "description" in t["click_settled"]


def test_the_shared_cadence_reads_linkedins_traversal_and_indeeds_default():
    """The cadence is the same everywhere; the LIST is not. Indeed's cards are all in the DOM and
    earn a shortlist; LinkedIn's are virtualised and every one gets opened."""
    assert sc.traversal_for("linkedin")["click_into"] == "every_card"
    assert sc.traversal_for("linkedin")["virtualised"] is True
    assert sc.traversal_for("indeed")["click_into"] == "shortlist"
    assert sc.traversal_for("indeed")["virtualised"] is False
    # an engine we have not mapped gets the conservative default, not an error
    assert sc.traversal_for("dice")["click_into"] == "shortlist"
    # and the shared bounds now say how a scroll is performed at all
    assert "wheel" in sc.BOUNDS["scroll_by"] and "scrollTop" in sc.BOUNDS["scroll_by"]


def test_the_title_stage_asserts_which_search_it_committed():
    """MEASURED: the system drove click/type/Enter, all three reported ok, and it landed on
    /search/results/all/?...origin=GLOBAL_SEARCH_HEADER — LinkedIn's GLOBAL search — while the
    operator's hand-driven run of the same-looking control landed on origin=JOBS_HOME_SEARCH.
    Nothing errored. `origin=` is the cheapest check that we committed the search we meant."""
    title = next(s for s in lr.SEARCH_CADENCE if s["stage"] == "title")
    # The success condition is the PATH: two routes reach it (JOBS_HOME_SEARCH direct, and
    # BLENDED_SEARCH via the global box + the Jobs section's "Show all"). Asserting one origin
    # would have failed a route the operator confirmed works.
    assert title["lands_on_path"] == "/jobs/search-results/"


# --- the state layer: LinkedIn's own states, finally reaching the live classifier ---------------
# `linkedin_recipe.classify`/`map_url_to_state` were measured live on 2026-07-30 and then called by
# nothing: the live classifier is `apply_recipe.map_url_to_state`, which had no LinkedIn patterns at
# all. So every LinkedIn tab in the system read `state="unknown"`, `role="other"`, and every
# consumer downstream fell through to Indeed's default. These pin the delegation.

import apply_recipe as ar


def test_the_live_classifier_knows_linkedins_states():
    """The whole point: `apply_recipe.map_url_to_state` is what the ladder, the blackboard and the
    transition corpus all call. Before this, all four of these returned `unknown`."""
    assert ar.map_url_to_state("https://www.linkedin.com/jobs/") == lr.HOME
    assert ar.map_url_to_state(
        "https://www.linkedin.com/jobs/search-results/?keywords=report+analyst"
    ) == lr.SEARCH_RESULTS
    assert ar.map_url_to_state("https://www.linkedin.com/jobs/view/4123456789/") == lr.JOB_DETAIL
    assert ar.map_url_to_state("https://www.linkedin.com/uas/login") == lr.LOGIN_WALL


def test_an_engines_matcher_may_only_speak_about_its_own_host():
    """LinkedIn's login pattern is `/login|/uas/login|/checkpoint` — correct within linkedin.com and
    a land-grab anywhere else. Un-host-gated it would claim Workday's and smartapply's sign-in
    pages, which is the same over-broad-matcher fault as `/<Tenant>/search/` claiming
    `linkedin.com/jobs/search` in `ats_registry`."""
    assert ar.map_url_to_state("https://tenant.myworkdayjobs.com/en-US/careers/login") != lr.LOGIN_WALL
    assert ar.map_url_to_state("https://secure.icims.com/checkpoint") != lr.LOGIN_WALL


def test_indeeds_states_are_untouched_by_the_delegation():
    """A regression guard on the engine that already worked — the delegation runs FIRST, so a
    mistake in it would silently re-label every Indeed page."""
    assert ar.map_url_to_state("https://www.indeed.com/jobs?q=report+analyst") == "indeed_search_results"
    assert ar.map_url_to_state("https://www.indeed.com/viewjob?jk=abc") == "indeed_job_posting"
    assert ar.map_url_to_state(
        "https://smartapply.indeed.com/beta/indeedapply/form/resume-selection"
    ) == "indeed_apply_resume_selection"


def test_a_linkedin_tab_has_the_search_role_and_a_posting_is_the_handoff():
    """`describe_tab`'s role decided "search" from a literal tuple of Indeed states, so LinkedIn's
    search tab was `other` — which is why `obs["search_tab"]` came back empty on LinkedIn and every
    hardcoded `indeed.com/jobs` fallback fired."""
    assert ar.describe_tab("https://www.linkedin.com/jobs/")["role"] == "search"
    assert ar.describe_tab(
        "https://www.linkedin.com/jobs/search-results/?keywords=x")["role"] == "search"
    assert ar.describe_tab("https://www.linkedin.com/jobs/view/1/")["role"] == "search"
    # Easy Apply is an APPLY surface and is caught by the name rule, not the search set.
    assert lr.EASY_APPLY not in ar.search_states()
    # Both engines' posting pages are the search->apply handoff.
    assert lr.JOB_DETAIL in ar.triage_states()
    assert "indeed_job_posting" in ar.triage_states()


def test_the_search_spine_is_per_engine_and_its_ids_are_the_live_states():
    """The subtask ids ARE the live state ids — that contract is what lets `_advance_plan` mark
    progress with no model, and it is why one Indeed-shaped spine could never advance on LinkedIn."""
    assert sc.search_recipe_states("linkedin") == [
        lr.HOME, lr.BLENDED_SEARCH, lr.SEARCH_RESULTS, lr.JOB_DETAIL]
    # THE CONTRACT: every spine id must be something the live classifier actually EMITS. A spine
    # of invented ids type-checks, reads fine, and silently never advances — which is the failure
    # this whole change is about. So walk a real url per state and prove the classifier agrees.
    reachable = {
        lr.HOME: "https://www.linkedin.com/jobs/",
        lr.BLENDED_SEARCH: "https://www.linkedin.com/search/results/all/?keywords=x",
        lr.SEARCH_RESULTS: "https://www.linkedin.com/jobs/search-results/?keywords=x",
        lr.JOB_DETAIL: "https://www.linkedin.com/jobs/view/4123456789/",
    }
    for state in sc.search_recipe_states("linkedin"):
        assert ar.map_url_to_state(reachable[state]) == state
    # Indeed's spine is unchanged, and an unmapped engine gets it rather than an error.
    assert sc.search_recipe_states("indeed")[0] == "indeed_home"
    assert sc.search_recipe_states("dice") == sc.search_recipe_states("indeed")


def test_an_unclaimed_state_answers_none_rather_than_indeed():
    """The load-bearing None. If `engine_of_state` defaulted, every page no spine names — a login
    wall, a captcha, the blank moment between navigations — would read as Indeed, and a LinkedIn
    session's spine would be torn down and rebuilt as Indeed's on its own sign-in page."""
    assert sc.engine_of_state(lr.SEARCH_RESULTS) == "linkedin"
    assert sc.engine_of_state("indeed_search_results") == "indeed"
    assert sc.engine_of_state(lr.LOGIN_WALL) is None
    assert sc.engine_of_state("unknown") is None
    assert sc.engine_of_state(None) is None


# --- the disambiguation page is a STATE, not a stumble ------------------------------------------
# Operator, 2026-08-14, watching a drive sit on it: "it was basically a page of 'general search'
# results that is just making sure you didn't mean to search a company or a person named 'reporting
# analyst' … I simply pressed the 'show all results' on that in-between state where it shows only 3
# job opportunities." It had lived only as a branch inside `_run_query`, so nothing could name it.

def test_the_blended_search_is_its_own_named_state():
    blended = "https://www.linkedin.com/search/results/all/?keywords=Reporting%20Analyst&origin=GLOBAL_SEARCH_HEADER"
    assert lr.map_url_to_state(blended) == lr.BLENDED_SEARCH
    assert ar.map_url_to_state(blended) == lr.BLENDED_SEARCH   # the LIVE classifier, not just ours
    # It is a search surface, so a tab sitting on it is not role `other` with nothing to reason from.
    assert ar.describe_tab(blended)["role"] == "search"
    assert lr.BLENDED_SEARCH in ar.search_states()


def test_the_blended_page_is_not_confused_with_the_jobs_results():
    """One segment apart: `/search/results/` vs `/jobs/search-results/`. Collapsing them would make
    the disambiguation page report itself as the result set — a page claiming to be its own goal."""
    assert lr.map_url_to_state(
        "https://www.linkedin.com/jobs/search-results/?keywords=x") == lr.SEARCH_RESULTS
    assert lr.map_url_to_state(
        "https://www.linkedin.com/search/results/all/?keywords=x") == lr.BLENDED_SEARCH


def test_the_spine_knows_the_way_off_the_blended_page():
    """Being able to NAME a state is only half of it — the cadence has to say what to do there."""
    spine = sc.search_recipe_for("linkedin")
    step = next(s for s in spine if s["state"] == lr.BLENDED_SEARCH)
    assert step["expect"] == [lr.SEARCH_RESULTS]
    # And the home step must admit that BOTH landings are ordinary, or the common one reads as a fault.
    home = next(s for s in spine if s["state"] == lr.HOME)
    assert set(home["expect"]) == {lr.SEARCH_RESULTS, lr.BLENDED_SEARCH}
    # The way off spends no new search: the query is already committed.
    assert lr.BLENDED_TO_RESULTS["commits"] is False
    assert lr.BLENDED_TO_RESULTS["lands_on"] == lr.SEARCH_RESULTS


def test_the_way_off_excludes_the_job_cards_and_the_filter_pill():
    """MEASURED 2026-08-14: 12 anchors share the /jobs/search-results/ prefix. Ten are job CARDS
    (they carry currentJobId); of the two whole-set links, the vertical pill is covered by a LABEL
    and does not navigate when clicked at its centre. So the selector names the SEE_ALL affordance
    — a destination is not a target."""
    sel = lr.BLENDED_TO_RESULTS["selector"]
    assert "currentJobId" in sel and ":not(" in sel   # cards excluded
    assert "SEE_ALL" in sel                            # and the pill is not merely hoped against


def test_linkedin_has_no_radius_and_the_cadence_stops_offering_one():
    """Retracted from a scaffold that was never measured. Live 2026-08-14: the filter row carries no
    distance pill, a DOM sweep for distance/radius/mile/within returned ZERO, and the location popup
    — the last place it could hide, since LinkedIn's popups do not render until opened — holds only
    a text field with the place name. A stage with no control is not outstanding work."""
    radius = next(s for s in lr.SEARCH_CADENCE if s["stage"] == lr.STAGE_RADIUS)
    assert radius["applies"] is False and radius["control"] is None
    # So it is never handed back as the next thing to do...
    assert lr.stage_for_state(lr.SEARCH_RESULTS, done=(lr.STAGE_LOCATION,))["stage"] == lr.STAGE_TRAVERSE
    # ...and the engine registry agreed all along; the disagreement lived in this recipe.
    import command_center
    assert command_center.has_distance_filter("linkedin_jobs") is False
    assert command_center.has_distance_filter("indeed_jobs") is True
