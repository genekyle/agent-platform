"""Tests for apply steps — N picks become N steps, and the page waits for all of them.

The properties worth paying for, in order:

  1. Choosing does not finish a page. `page:N` stays blocked while any application is unfinished.
  2. A step ends ONLY at a terminal flag, and `submitted` is the only one that means success.
  3. `unknown` is a first-class outcome that HALTS. An unrecognised ATS must never be guessed at.
  4. Parked and abandoned are different things and must not collapse into each other.
"""

import apply_steps as aps
import pytest

PICKS = [
    {"job_id": "indeed:a1", "title": "Compliance Reporting Analyst", "company": "Acme"},
    {"job_id": "indeed:a2", "title": "Healthcare Data Analyst", "company": "BIDMC"},
    {"job_id": "indeed:a3", "title": "Financial & Reporting Analyst", "company": "Globex"},
]


def _queue(picks=None):
    q = aps.Queue(page=1)
    q.enqueue(picks if picks is not None else PICKS)
    return q


# --- N picks, N steps ---------------------------------------------------------------------
def test_each_pick_becomes_its_own_step():
    """The operator's sentence, as a test: 11 checkboxes is 11 steps, not one page action."""
    q = _queue()
    assert len(q.steps) == 3
    assert [s.job_id for s in q.steps] == ["indeed:a1", "indeed:a2", "indeed:a3"]
    assert all(s.status == aps.STATUS_QUEUED for s in q.steps)


def test_enqueue_is_idempotent_and_never_reopens_finished_work():
    q = _queue()
    q.steps[0].finish(aps.SUBMITTED)
    added = q.enqueue(PICKS)          # Choose pressed twice
    assert added == 0 and len(q.steps) == 3
    assert q.steps[0].terminal == aps.SUBMITTED


def test_only_one_step_is_worked_at_a_time():
    """Two half-finished applications in one window is the duplicate-application fault."""
    q = _queue()
    assert q.current().job_id == "indeed:a1"
    q.steps[0].finish(aps.SUBMITTED)
    assert q.current().job_id == "indeed:a2"


# --- the page waits -----------------------------------------------------------------------
def test_the_page_stays_blocked_until_every_step_is_terminal():
    """"i don't continue until i fully apply" — the whole reason the queue exists."""
    q = _queue()
    assert q.blocks_page() is True
    q.steps[0].finish(aps.SUBMITTED)
    q.steps[1].finish(aps.PARKED_ACCOUNT_WALL, "operator must create the account")
    assert q.blocks_page() is True          # one still open
    q.steps[2].finish(aps.ABANDONED_GONE, "requisition 404s")
    assert q.blocks_page() is False


def test_a_parked_step_stops_holding_the_page_but_is_not_success():
    """The escape hatch. Parked means "not now" — the page moves, the application does not
    count, and the record says exactly which wall it hit."""
    q = _queue(PICKS[:1])
    q.steps[0].finish(aps.PARKED_AI_RECRUITER, "video interview gate")
    s = q.summary()
    assert s["blocks_page"] is False
    assert s["done"] == 1 and s["submitted"] == 0
    assert s["by_flag"] == {aps.PARKED_AI_RECRUITER: 1}


def test_summary_counts_submitted_separately_from_merely_done():
    q = _queue()
    q.steps[0].finish(aps.SUBMITTED)
    q.steps[1].finish(aps.PARKED_UNKNOWN_ATS)
    q.steps[2].finish(aps.ABANDONED_OPERATOR, "not actually a fit")
    s = q.summary()
    assert s["done"] == 3 and s["submitted"] == 1 and s["remaining"] == 0


def test_parked_and_abandoned_do_not_collapse_together():
    """Parked is "not now", abandoned is "not ever". Conflating them puts dead requisitions back
    in the queue forever, or quietly drops applications the operator meant to finish."""
    assert aps.PARKED_ACCOUNT_WALL.startswith("parked:")
    assert aps.ABANDONED_GONE.startswith("abandoned:")
    assert aps.PARKED_ACCOUNT_WALL != aps.ABANDONED_GONE


def test_an_invented_terminal_flag_is_refused():
    q = _queue(PICKS[:1])
    try:
        q.steps[0].finish("basically_done")
    except ValueError as exc:
        assert "basically_done" in str(exc)
    else:
        raise AssertionError("expected ValueError")


# --- the known prefix, then discovery -----------------------------------------------------
def test_the_prefix_is_walked_in_order():
    step = aps.ApplyStep(job_id="indeed:a1")
    assert step.next_rung().id == "open_pane"
    step.record("open_pane", aps.OK)
    assert step.next_rung().id == "verify_identity"
    step.record("verify_identity", aps.OK)
    assert step.next_rung().id == "enter_apply"
    step.record("enter_apply", aps.OK)
    assert step.next_rung().id == "classify"


def test_the_prefix_ends_at_discovery_because_the_tail_does_not_exist_yet():
    """A step is a ladder that grows while you are on it — past `classify` the rungs come from
    whatever platform we turned out to be on."""
    step = aps.ApplyStep(job_id="indeed:a1")
    for rung in aps.PREFIX_IDS:
        step.record(rung, aps.OK)
    assert step.next_rung() is None


def test_a_failed_mini_step_does_not_count_as_walked():
    """Only OK advances. A failed open must not let us proceed to clicking Apply on nothing."""
    step = aps.ApplyStep(job_id="indeed:a1")
    step.record("open_pane", aps.FAILED, "card click did not open the pane")
    assert step.next_rung().id == "open_pane"


def test_verify_identity_is_a_rung_of_its_own():
    """The near-miss guard, structural rather than remembered: an application to the wrong job
    cannot be taken back."""
    assert "verify_identity" in aps.PREFIX_IDS
    assert aps.PREFIX_IDS.index("verify_identity") < aps.PREFIX_IDS.index("enter_apply")


def test_submit_is_never_part_of_the_automatic_prefix():
    assert "submit" not in aps.PREFIX_IDS
    assert "operator confirms" in aps.SUBMIT_RUNG.why


# --- flags, and the one that matters ------------------------------------------------------
def test_unknown_halts_the_step():
    """`unknown` is an admission, not a failure — and it must stop us. "Found nothing" and
    "could not look" are different answers and only one is safe to continue from."""
    step = aps.ApplyStep(job_id="indeed:a1")
    step.record("classify", aps.UNKNOWN, "never seen this ATS")
    assert step.needs_operator() is True
    assert aps.UNKNOWN in aps.NEEDS_OPERATOR


def test_blocked_and_human_required_also_halt():
    for flag in (aps.BLOCKED, aps.HUMAN_REQUIRED):
        step = aps.ApplyStep(job_id="x")
        step.record("enter_apply", flag)
        assert step.needs_operator() is True


def test_an_ok_step_does_not_need_the_operator():
    step = aps.ApplyStep(job_id="x")
    step.record("open_pane", aps.OK)
    assert step.needs_operator() is False


def test_a_finished_step_never_asks_for_the_operator_again():
    step = aps.ApplyStep(job_id="x")
    step.record("classify", aps.UNKNOWN)
    step.finish(aps.PARKED_UNKNOWN_ATS)
    assert step.needs_operator() is False


def test_every_mini_step_is_recorded_with_provenance():
    """The flag per mini-step the operator asked for: what we tried, what came of it, who."""
    step = aps.ApplyStep(job_id="x")
    step.record("open_pane", aps.OK, "pane opened", initiator="auto")
    m = step.minis[-1]
    assert m.rung == "open_pane" and m.outcome == aps.OK
    assert m.initiator == "auto" and m.at and m.detail == "pane opened"


def test_recording_opens_a_queued_step():
    step = aps.ApplyStep(job_id="x")
    assert step.status == aps.STATUS_QUEUED
    step.record("open_pane", aps.OK)
    assert step.status == aps.STATUS_OPEN


# --- discovery ------------------------------------------------------------------------------
def test_smartapply_is_recognised_as_indeeds_own_flow():
    d = aps.classify_landing("https://smartapply.indeed.com/beta/indeedapply/form/resume-module")
    assert d.platform == "indeed" and d.known is True and d.outcome == aps.OK


def test_an_unrecognised_host_is_unknown_and_halts():
    d = aps.classify_landing("https://careers.some-startup.io/apply/123")
    assert d.known is False and d.outcome == aps.UNKNOWN
    assert "guessed at" in d.detail


def test_a_named_but_never_driven_ats_is_still_unknown():
    """The distinction that keeps us honest: `ats_registry` recognises far more hosts than we
    have ever driven, and being able to NAME a platform is not being able to finish it."""
    d = aps.classify_landing("https://jobs.lever.co/acme/1234")
    assert d.platform not in ("unknown", "company_site")   # the registry did name it
    assert d.known is False and d.outcome == aps.UNKNOWN
    assert "never been driven" in d.detail


def test_driven_platforms_are_a_deliberately_short_list():
    assert aps.DRIVEN_PLATFORMS == {"indeed", "workday", "greenhouse"}


# --- persistence ----------------------------------------------------------------------------
def test_the_queue_round_trips_through_its_json_shape():
    q = _queue()
    q.steps[0].record("open_pane", aps.OK, "opened")
    q.steps[0].record("classify", aps.UNKNOWN, "new ats")
    q.steps[0].platform = "company_site"
    q.steps[1].finish(aps.SUBMITTED, "confirmed sent")

    back = aps.Queue.from_dict(q.as_dict())
    assert [s.job_id for s in back.steps] == [s.job_id for s in q.steps]
    assert back.steps[0].platform == "company_site"
    assert [m.outcome for m in back.steps[0].minis] == [aps.OK, aps.UNKNOWN]
    assert back.steps[1].terminal == aps.SUBMITTED
    assert back.current().job_id == "indeed:a1"


def test_whether_a_mini_step_staged_input_survives_the_round_trip():
    """The panel's reload remedy is decided from a PERSISTED queue — a marker that lives only in
    the request that wrote it decides nothing. Three states, and the third is the point: None means
    UNSTATED, and it must come back as None rather than collapsing into False, because a reader
    that cannot tell "typed nothing" from "did not say" will un-protect every mini-step written
    before the field existed."""
    q = _queue()
    q.steps[0].record("account", aps.HUMAN_REQUIRED, "handoff", staged=False)
    q.steps[0].record("account", aps.HUMAN_REQUIRED, "filled", staged=True)
    q.steps[0].record("open_pane", aps.OK, "opened")

    back = aps.Queue.from_dict(q.as_dict())
    assert [m.staged for m in back.steps[0].minis] == [False, True, None]


def test_a_mini_step_persisted_before_the_staged_field_still_loads():
    """The rows already in the blackboards. `staged` is absent from every one of them."""
    step = aps.ApplyStep.from_dict({
        "job_id": "indeed:a1",
        "minis": [{"rung": "account", "outcome": aps.HUMAN_REQUIRED, "detail": "d",
                   "at": "2026-07-28T00:00:00+00:00", "initiator": "operator"}]})
    assert step.minis[0].staged is None


def test_from_dict_survives_an_empty_or_missing_queue():
    assert aps.Queue.from_dict(None).steps == []
    assert aps.Queue.from_dict({}).blocks_page() is False


# --- is the action even well-formed? --------------------------------------------------------
def test_a_click_addressed_with_field_is_refused():
    """The live failure, 2026-07-24. `field` is the SET_TEXT key; a click needs `control`. It was
    stored, rendered as a confident proposal, approved, and only then failed at act time."""
    why = aps.validate_action("click", {"field": "Apply now"})
    assert why and "needs control" in why and "field=" in why


def test_a_well_formed_click_passes():
    assert aps.validate_action("click", {"control": "Apply now"}) is None
    assert aps.validate_action("click", {"name": "Apply now"}) is None


def test_set_text_needs_both_field_and_value():
    assert aps.validate_action("set_text", {"field": "Email"}) is not None
    assert aps.validate_action("set_text", {"field": "Email", "value": "a@b.c"}) is None


def test_an_intent_outside_the_vocabulary_is_refused():
    why = aps.validate_action("press_the_button", {"control": "x"})
    assert why and "not in the intent vocabulary" in why


def test_a_missing_intent_is_refused():
    assert aps.validate_action("", {}) == "no intent given"


def test_read_only_intents_need_no_params():
    assert aps.validate_action("observe", {}) is None
    assert aps.validate_action("scan_required", {}) is None


def test_validation_does_not_silently_rewrite_a_wrong_key():
    """Guessing that `field` meant `control` would paper over a teacher with the vocabulary wrong
    — and the vocabulary is the thing the students learn."""
    params = {"field": "Apply now"}
    aps.validate_action("click", params)
    assert params == {"field": "Apply now"}      # untouched


def test_a_parked_step_can_come_back_and_an_abandoned_one_cannot():
    """The parked/abandoned split has to be actionable, not just documented. Parked means "not
    now" — the operator's top pick sat parked under its own "re-queue after the matcher fix" note
    with the fix already shipped, and there was no way back.
    """
    q = aps.Queue()
    q.enqueue([{"job_id": "indeed:a1", "title": "Healthcare Data Analyst", "company": "BILH"},
               {"job_id": "indeed:b2", "title": "Quality Systems Analyst", "company": "Abbott"}])
    parked, abandoned = q.steps[0], q.steps[1]

    parked.record("open_pane", aps.OK, "pane opened")
    parked.record("enter_apply", aps.OK, "clicked the WRONG company's card")
    parked.finish(aps.PARKED_OPERATOR, "re-queue after the matcher fix")
    abandoned.finish(aps.ABANDONED_OPERATOR, "looked and do not want it")
    assert q.current() is None                      # both terminal: the queue is empty of work

    parked.reopen("the enter_apply matcher fix shipped; nothing was ever entered for this job")

    assert parked.done is False
    assert q.current() is parked                    # back at its ORIGINAL place in the pick order
    # The ladder restarts from the top: a rung answered by a click on the wrong card is exactly
    # what must not be carried forward.
    assert parked.next_rung().id == "open_pane"
    assert [m.rung for m in parked.minis] == ["reopened"]
    # ...but the failed attempt is kept, which is what makes the retry legible as a correction.
    assert len(parked.archived_minis) == 1
    assert parked.archived_minis[0]["parked_as"] == aps.PARKED_OPERATOR
    assert [m["rung"] for m in parked.archived_minis[0]["minis"]] == ["open_pane", "enter_apply"]

    # "not ever" stays not ever.
    with pytest.raises(ValueError, match="only a PARKED step"):
        abandoned.reopen("changed my mind")
    # and a live step has nothing to reopen
    with pytest.raises(ValueError, match="not finished"):
        aps.ApplyStep(job_id="indeed:c3").reopen("why")


def test_a_reopened_step_survives_a_round_trip():
    """The archive rides in the queue's dict — a blackboard reload must not lose the first attempt."""
    q = aps.Queue()
    q.enqueue([{"job_id": "indeed:a1", "title": "T", "company": "C"}])
    q.steps[0].record("open_pane", aps.OK, "opened")
    q.steps[0].finish(aps.PARKED_ACCOUNT_WALL, "account wall")
    q.steps[0].reopen("operator made the account")

    back = aps.Queue.from_dict(q.as_dict())
    assert back.current().job_id == "indeed:a1"
    assert len(back.steps[0].archived_minis) == 1
    assert back.steps[0].archived_minis[0]["parked_as"] == aps.PARKED_ACCOUNT_WALL


# --- the careers FRONT: where the page points, not where it is -----------------------------------
# MEASURED live 2026-07-30 driving a LinkedIn apply for Ahold Delhaize's Sr. Reporting Analyst.
_FRONT_URL = "https://aholddelhaizeusa.careerswithus.com/job/Procurement-%26-Logistics/Sr.-Reporting-Analyst/Quincy-MA/ADUSA"
_FRONT_APPLY = "https://aholddelhaizeapply.appvault.com/external/home?jobId=533857&company=ADUSA"
_FRONT_TEXT = ("Join Our Talent Community Search for more jobs Sr. Reporting Analyst Posting Date: "
               "07/22/2026 Quincy, MA APPLY NOW Category/Area of Expertise: Procurement & Logistics "
               "Job Requisition: 533857 Responsibilities Qualifications")


def test_a_careers_front_halts_when_only_its_own_host_is_read():
    """The honest halt, and it is the RIGHT answer given only the URL: the employer's own careers
    domain names no ATS, so we stop rather than guess through a real application."""
    d = aps.classify_landing(_FRONT_URL, _FRONT_TEXT)
    assert d.platform == "company_site"
    assert d.known is False and d.outcome == aps.UNKNOWN
    # …but it still says WHAT it was looking at, which is the point of the content axis
    assert d.kind == "job_posting" and d.state == "company_site_job_posting"


def test_the_apply_link_names_the_ats_the_landing_host_cannot():
    """The signpost. Same page, same text — the only new input is where APPLY NOW points, and that
    turns an UNKNOWN halt into a recognised platform. The registry had described this exact hop in
    AppVault's notes from an Indeed drive; prose does not classify, so it halted anyway."""
    d = aps.classify_landing(_FRONT_URL, _FRONT_TEXT, apply_hrefs=[_FRONT_APPLY])
    assert d.platform == "appvault"
    # recognised is NOT the same as driven: AppVault's recipe is still an empty stub, so the step
    # must still halt for a human rather than claim it can finish.
    assert d.known is False and d.outcome == aps.UNKNOWN
    assert "never been driven" in d.detail


def test_an_apply_link_to_nowhere_known_changes_nothing():
    """The hint may only ever promote a landing to a platform the registry already knows. A random
    href must not invent one."""
    d = aps.classify_landing(_FRONT_URL, _FRONT_TEXT,
                             apply_hrefs=["https://careers.example.com/apply/123"])
    assert d.platform == "company_site"


# --- a correction has to be able to reopen a rung ------------------------------------------------
def test_the_latest_verdict_settles_a_rung_not_the_best_one_ever_recorded():
    """Live 2026-07-30: `account` was recorded ok while the browser was still on a careers-front job
    posting. The guard that re-reads the page then recorded `account unknown` — and the ladder went
    on reporting the prefix as walked, so the panel sat on the wrong step offering a sign-in for a
    wall that was not on screen. `any OK ever` meant a rung could never be corrected."""
    step = aps.ApplyStep(job_id="linkedin:1", title="Sr. Reporting Analyst",
                         company="Ahold Delhaize USA")
    for rung in ("open_pane", "verify_identity", "enter_apply", "classify", "account"):
        step.record(rung, aps.OK, "walked")
    assert step.next_rung() is None                      # prefix walked, as recorded

    step.record("account", aps.UNKNOWN, "not at an account wall — the page is a job posting")
    nxt = step.next_rung()
    assert nxt is not None and nxt.id == "account", "a correction could not reopen the rung"

    # …and re-settling it closes the prefix again, so a corrected step is not stuck open forever.
    step.record("account", aps.OK, "signed in")
    assert step.next_rung() is None


def test_both_sides_of_the_correction_stay_on_the_record():
    """Reopening must not be implemented by deleting history — the wrong answer is evidence too."""
    step = aps.ApplyStep(job_id="linkedin:1", title="T", company="C")
    step.record("account", aps.OK, "claimed")
    step.record("account", aps.UNKNOWN, "corrected")
    outcomes = [m.outcome for m in step.minis if m.rung == "account"]
    assert outcomes == [aps.OK, aps.UNKNOWN]


# ---------------------------------------------------------------------------------------------
# THE DISCOVERY IS ALLOWED TO CHANGE THE LADDER. `classify` documents itself as the point where
# "the rungs after this one do not exist until this is answered" — and the ladder then walked a
# fixed tuple regardless. Live 2026-07-30, session 24.
# ---------------------------------------------------------------------------------------------

def test_a_platform_with_no_account_wall_rules_the_account_rung_out():
    applies, why = aps.rung_applies("account", platform="indeed")
    assert applies is False and "without an account" in why
    assert aps.rung_applies("account", platform="greenhouse")[0] is False


def test_a_platform_that_does_want_an_account_keeps_the_rung():
    assert aps.rung_applies("account", platform="successfactors")[0] is True
    assert aps.rung_applies("account", platform="workday")[0] is True
    # An unclassified landing must NOT lose the rung — "we do not know yet" is not "not needed".
    assert aps.rung_applies("account", platform=None)[0] is True
    assert aps.rung_applies("account", platform="")[0] is True


def test_rungs_other_than_account_are_untouched():
    for rung_id in ("open_pane", "verify_identity", "enter_apply", "classify"):
        assert aps.rung_applies(rung_id, platform="indeed")[0] is True


def test_a_ruled_out_rung_is_named_with_its_reason_rather_than_vanishing():
    # Silently dropping it reads as a ladder that never had the step. The panel greys it instead.
    step = aps.ApplyStep(job_id="indeed:abc", title="t", company="BRISTOL COUNTY SAVINGS BANK")
    step.platform = "indeed"
    out = {r["id"]: r for r in step.inapplicable_rungs()}
    assert "account" in out and "indeed" in out["account"]["why"]
    assert out["account"]["label"] == "Get past the account wall"
    assert step.as_dict()["inapplicable_rungs"] == step.inapplicable_rungs()


def test_an_unclassified_step_rules_nothing_out():
    # Before classify has spoken there is no discovery to act on, so the full prefix stands.
    assert aps.ApplyStep(job_id="x:1", title="t", company="C").inapplicable_rungs() == []


# --- the ladder's tail: the recipe's spine, reached one screen at a time ------------------------
def test_the_tail_walks_the_recipe_spine_and_ends_at_the_operator_s_gate():
    """The hole this closes: `next_rung` returned None past the prefix, so `SUBMIT_RUNG` was
    defined and referenced by nothing and every application dead-ended at "not built yet"."""
    assert aps.tail_rung_for("indeed", "indeed_apply_resume_selection").id == \
        "indeed_apply_resume_selection"
    assert aps.tail_rung_for("indeed", "indeed_apply_questions").id == "indeed_apply_questions"
    # The gate, and it is the SUBMIT rung rather than another advance.
    assert aps.tail_rung_for("indeed", "indeed_apply_review").id == aps.SUBMIT_RUNG.id
    # Past the gate there is nothing left to walk.
    assert aps.tail_rung_for("indeed", "indeed_apply_submitted") is None


def test_an_unrecognised_screen_gets_no_rung_rather_than_a_hopeful_continue():
    assert aps.tail_rung_for("indeed", "a_screen_nobody_has_driven") is None
    assert aps.tail_rung_for("some_new_ats", "indeed_apply_review") is None
    assert aps.tail_rung_for(None, None) is None


def test_the_step_reaches_the_tail_once_the_prefix_is_walked():
    step = aps.ApplyStep(job_id="indeed:1", title="T", company="C", platform="indeed")
    for rung in ("open_pane", "verify_identity", "enter_apply", "classify"):
        step.record(rung, aps.OK)
    # `account` is ruled out on Indeed, so walking must pass it and land on the tail.
    step.landing_state = "indeed_apply_resume_selection"
    rung, passed = step.walk_to_next_rung()
    assert [p[0] for p in passed] == ["account"]
    assert rung.id == "indeed_apply_resume_selection"
    # The live page is the authority: the same call with a later screen moves the ladder on.
    assert step.walk_to_next_rung("indeed_apply_review")[0].id == aps.SUBMIT_RUNG.id


def test_a_tail_rung_is_not_suppressed_by_having_been_walked_before():
    """Indeed serves `questions` across several pages — the recipe's own `expect` says so. A tail
    rung gated on `settled_rungs` would strand the drive on page two."""
    step = aps.ApplyStep(job_id="indeed:1", platform="indeed")
    for rung in ("open_pane", "verify_identity", "enter_apply", "classify"):
        step.record(rung, aps.OK)
    step.record("indeed_apply_questions", aps.OK, "clicked 'Continue'")
    assert step.walk_to_next_rung("indeed_apply_questions")[0].id == "indeed_apply_questions"


def test_the_prefix_still_answers_alone_when_no_state_is_offered():
    """Every pre-tail caller passes no state and must behave exactly as it did before."""
    step = aps.ApplyStep(job_id="indeed:1", platform="indeed")
    assert step.next_rung().id == "open_pane"
    for rung in ("open_pane", "verify_identity", "enter_apply", "classify", "account"):
        step.record(rung, aps.OK)
    assert step.next_rung() is None       # no landing_state -> no tail, as before


# --- the generic cadence meets the ladder --------------------------------------------------------

def test_an_unmapped_ats_gets_a_tail_rung_instead_of_new_territory():
    """The dead end the cadence exists to end: Cornerstone named, page read, and the ladder said
    "genuinely new territory — drive it by hand." The shared spine now serves the rung."""
    rung = aps.tail_rung_for("cornerstone", "cornerstone_job_posting")
    assert rung is not None and rung.id == "cornerstone_job_posting"
    assert "press the page's own apply control" in rung.label.lower()
    assert "shared ats cadence" in rung.why.lower()          # the fuzz is said, not hidden


def test_the_generic_review_gate_is_the_submit_rung():
    assert aps.tail_rung_for("cornerstone", "cornerstone_review") is aps.SUBMIT_RUNG


def test_the_generic_wall_hands_to_the_account_rung():
    """An account_gate screen is the account rung's business — the machinery that already exists
    (legs, operator gating, handoff card), never a second wall surface coined by the cadence."""
    rung = aps.tail_rung_for("cornerstone", "cornerstone_account_gate")
    assert rung is not None and rung.id == "account"


def test_the_account_rung_applies_on_measurement_not_prediction():
    """auth='account' in the registry is a measured posture (Workday, SAP) — the rung stands.
    An unmeasured platform ('unknown') defers until the wall is SEEN on screen."""
    # Measured: the wall is a fixture — the rung applies before it is on screen.
    assert aps.rung_applies("account", platform="successfactors")[0] is True
    assert aps.rung_applies("account", platform="workday")[0] is True
    # Unmeasured: not before the page shows it…
    applies, why = aps.rung_applies("account", platform="cornerstone",
                                state="cornerstone_job_posting")
    assert applies is False and "unmeasured" in why
    # …and the moment it IS the wall, no better measurement exists.
    assert aps.rung_applies("account", platform="cornerstone",
                        state="cornerstone_account_gate")[0] is True
    # The no-account list still outranks everything.
    assert aps.rung_applies("account", platform="greenhouse")[0] is False


def test_the_walk_skips_the_unmeasured_wall_and_serves_the_generic_tail():
    """After classify names an unmapped ATS, the next press is the page's own Apply — not
    account-creation for a wall nobody has seen (the premature offer, live 2026-08-11)."""
    step = aps.ApplyStep(job_id="indeed:x", title="BI Analyst", company="Boston College")
    for r in ("open_pane", "verify_identity", "enter_apply", "classify"):
        step.record(r, aps.OK)
    step.platform = "cornerstone"
    step.landing_state = "cornerstone_job_posting"
    rung, passed = step.walk_to_next_rung()
    assert rung is not None and rung.id == "cornerstone_job_posting"
    assert any(r_id == "account" and "unmeasured" in why for r_id, why in passed)


def test_a_measured_wall_still_waits_for_the_page_to_reach_it():
    """Workday's registry row says auth=account — a measured fact — and the ladder read that as
    "the wall is NOW": on the posting, the cockpit's whole surface became account-creation while
    the Lens correctly showed a landing page (operator, live 2026-08-11). The flow order has
    always said the wall is two screens later. WHETHER is the registry's answer; WHEN is the
    page's."""
    # Before the wall on the platform's own flow: the tail leads.
    applies, why = aps.rung_applies("account", platform="workday",
                                    state="workday_job_posting")
    assert applies is False and "before" in why
    assert aps.rung_applies("account", platform="workday",
                            state="workday_apply_method")[0] is False
    # At the wall: engages.
    assert aps.rung_applies("account", platform="workday",
                            state="workday_apply_auth")[0] is True
    # Past it: still engages (re-auth mid-flow is real).
    assert aps.rung_applies("account", platform="workday",
                            state="workday_my_information")[0] is True
    # No readable position: legacy behaviour — the wall engages at classify, honestly.
    assert aps.rung_applies("account", platform="workday", state=None)[0] is True
    assert aps.rung_applies("account", platform="workday",
                            state="not_a_workday_state")[0] is True
    # The generic cadence gets the same courtesy: an unmeasured platform already defers, and a
    # measured-account generic platform on its posting would too.
    assert aps.rung_applies("account", platform="successfactors",
                            state="successfactors_job_posting")[0] is False


def test_the_grind_counter_counts_the_streak_not_the_history():
    """The retry discipline (2 misses → look; 3 → the operator) was prose-only since 2026-08-14.
    `stall_count` is its number: consecutive non-landing tries on ONE rung, where minis of other
    rungs do not break the streak (an orient between two failed advances is the same grind) and
    an OK resets it."""
    step = aps.ApplyStep(job_id="indeed:g1", title="t", company="c")
    assert step.stall_count("advance") == 0
    step.record("advance", aps.MISMATCH, "pressed, nothing moved")
    step.record("orient", aps.OK, "looked around")               # another rung: streak survives
    step.record("advance", aps.FAILED, "pressed, page refused")
    assert step.stall_count("advance") == 2
    step.record("advance", aps.MISMATCH, "third miss")
    assert step.stall_count("advance") == 3
    step.record("advance", aps.OK, "landed at last")
    assert step.stall_count("advance") == 0, "an OK ends the streak"
    # And a fresh mismatch after the OK starts a NEW streak of one.
    step.record("advance", aps.MISMATCH, "new page, new problem")
    assert step.stall_count("advance") == 1


def test_the_submit_gate_cannot_be_reached_by_omission():
    """THE GUARD THAT PASSED ON A DEFAULT. The submit rung refused any initiator that was not the
    operator — but `initiator` DEFAULTS to "operator", so a bare `apply_step {}` asserted that the
    human pressed send when nobody had. Measured live 2026-08-26: a feed application walked to the
    review screen and "Submit your application" was pressed with no confirmation in the loop.

    A field that defaults to "the human did this" is not evidence the human did anything, so the
    one irreversible rung now needs an affirmative that cannot be reached by leaving it out.
    """
    from routers.session_control import ApplyStepBody

    # The shape that pressed Submit.
    assert ApplyStepBody().confirm_submit is False
    assert ApplyStepBody().initiator == "operator", "the default that could not serve as consent"
    # Only an explicit affirmative opens the gate.
    assert ApplyStepBody(confirm_submit=True).confirm_submit is True
    # And it is not something a stray string can satisfy by truthiness at the boundary.
    assert ApplyStepBody(confirm_submit=False).confirm_submit is False


# --------------------------------------------------------------------------------------------
# Typing and sending are different permissions (live 2026-08-27, BambooHR)
# --------------------------------------------------------------------------------------------

def _block(strength="active", **vis):
    b = {"provider": "recaptcha_checkbox", "strength": strength}
    if vis:
        b["visibility"] = {"ok": True, **vis}
    return b


def test_a_footer_checkbox_gates_submit_not_the_form():
    """The live case: a reCAPTCHA checkbox sits below the fields, unsolved. Refusing to FILL
    there made the operator race the checkbox's ~2-minute expiry — the form came back reading
    "Verification expired. Check the checkbox again." Filling first, ticking last, is the order
    a human uses and the one that wins the race."""
    import escalation_rules as er

    assert er.blocks_typing(_block(blocking=True, challenge_visible=False,
                                   checkbox_visible=True, checkbox_unsolved=True)) is False


def test_a_visible_challenge_still_stops_everything():
    """The image-grid interstitial overlays the page and demands the human right now."""
    import escalation_rules as er

    assert er.blocks_typing(_block(blocking=True, challenge_visible=True,
                                   checkbox_visible=True)) is True


def test_an_unmeasurable_block_stays_conservative():
    """A Facebook checkpoint, or a probe that failed: nothing was measured, so nothing is
    relaxed. This is the rail that keeps us off a challenged page."""
    import escalation_rules as er

    assert er.blocks_typing(_block()) is True                      # no visibility at all
    assert er.blocks_typing({"provider": "x", "strength": "active",
                             "visibility": {"ok": False}}) is True  # probe unreachable


def test_a_passive_block_blocks_nothing():
    import escalation_rules as er

    assert er.blocks_typing(_block(strength="passive")) is False
    assert er.blocks_typing(None) is False


def test_an_active_block_carries_its_probe_so_the_distinction_is_readable():
    """`downgrade_block_if_hidden` used to discard the visibility reading whenever it kept a
    block ACTIVE — throwing it away at the one moment it distinguishes a checkbox from an
    interstitial, so every active block looked maximally blocking."""
    import escalation_rules as er

    vis = {"ok": True, "blocking": True, "challenge_visible": False, "checkbox_visible": True}
    out = er.downgrade_block_if_hidden({"provider": "recaptcha", "strength": "active"}, vis)
    assert out["strength"] == "active" and out["visibility"] == vis
    assert er.blocks_typing(out) is False


# --------------------------------------------------------------------------------------------
# The grind guard (operator's rule, live 2026-08-28)
# --------------------------------------------------------------------------------------------

def _ground(step, times=4):
    """The exact pattern `linkedin:4424504424` wrote: eight minis in 77 seconds, alternating a
    claim that the click landed with the world reporting that nothing moved."""
    for _ in range(times):
        step.record("enter_apply", aps.OK, "clicked 'Apply'; stayed in this tab")
        step.record("enter_apply", aps.MISMATCH,
                    "world disagrees: no tab opened and none navigated")
    return step


def test_a_rung_that_records_the_same_failure_three_times_is_grinding():
    """Live 2026-08-28: the rung was already satisfied — the ATS tab had been open for hours — so
    `new_tab_or_nav` was unsatisfiable by construction and every retry was identical. The tally is
    taken over FAILURES alone: the alternating pattern writes an ok for every mismatch, and a
    global max ties three-all and can hand back the ok half, which reads as 'not grinding'."""
    n, detail = _ground(aps.ApplyStep(job_id="j", title="t")).grinding_on("enter_apply")
    assert n == 3
    assert detail == "world disagrees: no tab opened and none navigated"


def test_repeating_a_SUCCESS_is_not_grinding():
    """`open_pane` on the same card twice is idempotent, not stuck."""
    s = aps.ApplyStep(job_id="j", title="t")
    for _ in range(4):
        s.record("open_pane", aps.OK, "pane switched to 'Analyst'")
    assert s.grinding_on("open_pane") == (0, "")


def test_two_tries_is_not_yet_grinding_and_a_retry_that_WORKS_never_trips_it():
    """The threshold is three, not one, and the reason is measured: Taleo's `taleo_job_posting`
    reported 'nothing observably changed', was pressed again, and landed. A guard that fired on the
    first repeat would have stopped the retry that currently succeeds."""
    s = aps.ApplyStep(job_id="j", title="t")
    s.record("x", aps.MISMATCH, "nope")
    s.record("x", aps.MISMATCH, "nope")
    assert s.grinding_on("x") == (0, "")

    t2 = aps.ApplyStep(job_id="j2", title="t")
    t2.record("taleo_job_posting", aps.MISMATCH, "nothing observably changed")
    t2.record("taleo_job_posting", aps.OK, "Clicked 'Apply Now'")
    assert t2.grinding_on("taleo_job_posting") == (0, "")


def test_the_stop_summons_a_human_and_the_next_press_is_that_human():
    """Without the escape the guard deadlocks: recording its own refusal leaves the three failures
    inside the window, so every later press re-fires and the operator can never retry — not even
    after reconciling the record. It re-arms if the grind resumes."""
    s = _ground(aps.ApplyStep(job_id="j", title="t"))
    assert s.grinding_on("enter_apply")[0] == 3

    s.record("enter_apply", aps.HUMAN_REQUIRED, "stopped after 3 identical results")
    assert s.grinding_on("enter_apply") == (0, ""), "the press after the stop belongs to the human"

    s.record("enter_apply", aps.MISMATCH, "world disagrees: no tab opened and none navigated")
    assert s.grinding_on("enter_apply")[0] == 3, "and it re-arms if the grind resumes"


def test_a_different_rung_is_not_tarred_by_its_neighbour():
    s = _ground(aps.ApplyStep(job_id="j", title="t"))
    assert s.grinding_on("classify") == (0, "")
