"""Tests for the checkpoint ladder — the open-ended alternative to end flags.

The property these exist to pin is narrow and expensive to get wrong: **a consuming rung is
never re-run.** Submitting the Indeed query twice is what makes Indeed collapse results, so the
test that matters most is the one asserting we RECOVER instead of repeating.
"""

import session_checkpoints as cps


def _full_preamble() -> cps.Ledger:
    led = cps.Ledger()
    for cp in cps.PREAMBLE:
        led.mark(cp.id, evidence="test")
    return led


# --- climbing the preamble ---------------------------------------------------------------
def test_empty_ledger_starts_at_the_first_rung():
    nxt = cps.next_step(cps.Ledger(), {})
    assert nxt.kind == cps.ADVANCE and nxt.checkpoint.id == "provisioned"


def test_rungs_are_worked_in_order():
    led = cps.Ledger()
    led.mark("provisioned")
    assert cps.next_step(led, {}).checkpoint.id == "authenticated"
    led.mark("authenticated")
    assert cps.next_step(led, {}).checkpoint.id == "query_entered"
    led.mark("query_entered")
    assert cps.next_step(led, {}).checkpoint.id == "radius_set"


def test_topped_out_preamble_becomes_open_ended_review():
    nxt = cps.next_step(_full_preamble(), {}, page=3)
    assert nxt.kind == cps.REVIEW
    assert nxt.checkpoint.id == "page:3" and nxt.page == 3


# --- THE property: consuming rungs are never re-run --------------------------------------
def test_lapsed_consuming_rung_recovers_and_never_repeats():
    """The results page is gone (we navigated away). The query was already spent, so the answer
    is 'get back to it', NOT 'search again' — the whole reason this module exists."""
    led = _full_preamble()
    nxt = cps.next_step(led, {"query_entered": False})
    assert nxt.kind == cps.RECOVER
    assert nxt.checkpoint.id == "query_entered"
    assert nxt.checkpoint.recovery and "never re-submit" in nxt.checkpoint.recovery
    # and it stays held — a lapse is not an un-reaching
    assert led.holds("query_entered")


def test_lapsed_radius_recovers_too():
    nxt = cps.next_step(_full_preamble(), {"radius_set": False})
    assert nxt.kind == cps.RECOVER and nxt.checkpoint.id == "radius_set"


def test_standing_rung_regression_re_runs_because_it_is_idempotent():
    """Signing back in is safe to repeat, so a lapsed standing rung ADVANCES."""
    nxt = cps.next_step(_full_preamble(), {"authenticated": False})
    assert nxt.kind == cps.ADVANCE and nxt.checkpoint.id == "authenticated"


def test_unknown_observation_is_not_a_regression():
    """None means 'we did not check'. A flaky probe must never send us re-running a rung that
    costs a real query — that is the whole reason the map is tri-state."""
    led = _full_preamble()
    assert cps.next_step(led, {"query_entered": None}).kind == cps.REVIEW
    assert cps.next_step(led, {}).kind == cps.REVIEW


def test_earliest_unsatisfied_rung_wins():
    """Auth lapsing outranks a lapsed query — no point recovering results while logged out."""
    nxt = cps.next_step(_full_preamble(), {"authenticated": False, "query_entered": False})
    assert nxt.checkpoint.id == "authenticated"


# --- the ledger ---------------------------------------------------------------------------
def test_marking_twice_keeps_the_first_reaching():
    """The first reaching is the one that paid the cost; overwriting it would erase the record
    that we already spent the query."""
    led = cps.Ledger()
    first = led.mark("query_entered", evidence="original", initiator="operator")
    again = led.mark("query_entered", evidence="clobber", initiator="auto")
    assert again is first
    assert led.reached["query_entered"].evidence == "original"
    assert led.reached["query_entered"].initiator == "operator"


def test_ledger_round_trips_through_json_shape():
    led = cps.Ledger()
    led.mark("provisioned", evidence="4 tabs", initiator="auto")
    back = cps.Ledger.from_dict(led.as_dict())
    assert back.holds("provisioned")
    assert back.reached["provisioned"].initiator == "auto"
    assert back.reached["provisioned"].at == led.reached["provisioned"].at


def test_from_dict_ignores_malformed_rows():
    back = cps.Ledger.from_dict({"provisioned": {"at": "2026-07-23T00:00:00Z"},
                                 "junk": {"no_at": True}, "worse": "not a dict"})
    assert back.holds("provisioned") and not back.holds("junk") and not back.holds("worse")


# --- the open-ended tail -------------------------------------------------------------------
def test_page_rungs_accumulate_and_are_ordered():
    led = _full_preamble()
    for p in (1, 2, 3):
        led.mark(cps.page_rung(p).id, evidence=f"page {p}")
    assert led.pages_reviewed() == [1, 2, 3]
    assert cps.next_step(led, {}, page=4).checkpoint.id == "page:4"


def test_page_of_parses_only_page_rungs():
    assert cps.page_of("page:12") == 12
    assert cps.page_of("query_entered") is None
    assert cps.page_of("page:not-a-number") is None


def test_the_ladder_never_reports_done():
    """There is no terminal rung by construction — REVIEW is always the answer at the top, no
    matter how many pages have been walked. 'Done' has to come from the world (no next page)
    or the operator, never from the ladder."""
    led = _full_preamble()
    for p in range(1, 40):
        led.mark(cps.page_rung(p).id)
    assert cps.next_step(led, {}, page=40).kind == cps.REVIEW


# --- the panel view ------------------------------------------------------------------------
def test_status_rows_label_each_rung():
    led = cps.Ledger()
    led.mark("provisioned")
    rows = {r["id"]: r for r in cps.status_rows(led, {})}
    assert rows["provisioned"]["status"] == cps.HELD
    assert rows["authenticated"]["status"] == cps.NEXT
    assert rows["query_entered"]["status"] == cps.PENDING
    assert rows["provisioned"]["reached"]["initiator"] == "operator"


def test_status_rows_distinguish_regressed_from_lapsed():
    led = _full_preamble()
    regressed = {r["id"]: r for r in cps.status_rows(led, {"authenticated": False})}
    assert regressed["authenticated"]["status"] == cps.REGRESSED
    lapsed = {r["id"]: r for r in cps.status_rows(led, {"query_entered": False})}
    assert lapsed["query_entered"]["status"] == cps.LAPSED


def test_status_rows_include_walked_pages_and_the_next_one():
    led = _full_preamble()
    led.mark(cps.page_rung(1).id)
    rows = {r["id"]: r for r in cps.status_rows(led, {}, page=2)}
    assert rows["page:1"]["status"] == cps.HELD
    assert rows["page:2"]["status"] == cps.NEXT


def test_progress_reports_the_phase_boundary():
    climbing = cps.progress(cps.Ledger(), {})
    assert climbing["phase"] == "climbing" and climbing["at_start_line"] is False
    at_line = cps.progress(_full_preamble(), {}, page=2)
    assert at_line["phase"] == "start_line" and at_line["at_start_line"] is True
    assert at_line["preamble_held"] == at_line["preamble_total"] == len(cps.PREAMBLE)


def test_every_consuming_rung_carries_a_recovery_that_is_not_a_repeat():
    """A consuming rung with no recovery instruction would leave the executor with nothing to do
    but re-run it — the failure this design exists to prevent."""
    for cp in cps.PREAMBLE:
        if cp.kind == cps.CONSUMING:
            assert cp.recovery, f"{cp.id} has no recovery"
    assert cps.page_rung(1).recovery
