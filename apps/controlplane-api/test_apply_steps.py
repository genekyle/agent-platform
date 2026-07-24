"""Tests for apply steps — N picks become N steps, and the page waits for all of them.

The properties worth paying for, in order:

  1. Choosing does not finish a page. `page:N` stays blocked while any application is unfinished.
  2. A step ends ONLY at a terminal flag, and `submitted` is the only one that means success.
  3. `unknown` is a first-class outcome that HALTS. An unrecognised ATS must never be guessed at.
  4. Parked and abandoned are different things and must not collapse into each other.
"""

import apply_steps as aps

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
