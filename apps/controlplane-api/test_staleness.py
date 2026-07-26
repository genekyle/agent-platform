"""Staleness prototype — the rules, and the two that are safety rather than heuristics.

The THRESHOLDS are provisional and these tests deliberately do not assert specific seconds as
though they were meaningful. They pin the SHAPE: ordering, unknown-handling, and the two
downgrades that stop a suspicion from destroying work. Recalibrating the table should not break
this file — if it does, the test was asserting a guess.
"""

from perception import staleness as st

NOW = 1_000_000.0


def _ev(**kw):
    return st.Evidence(now=NOW, **kw)


# --- ordering -----------------------------------------------------------------------
def test_worst_takes_the_highest_level_not_an_average():
    """One dead signal makes the observation suspect however fresh the others look."""
    assert st.worst([st.FRESH, st.FRESH, st.RED]) == st.RED
    assert st.worst([st.FRESH, st.YELLOW]) == st.YELLOW
    assert st.worst([]) == st.FRESH


def test_a_quiet_recent_drive_is_fresh_and_operable():
    s = st.assess(_ev(logged_in=True, last_action_at=NOW - 5, last_nav_at=NOW - 10))
    assert s.level == st.FRESH
    assert s.verdict == st.CONTINUE
    assert s.operable is True


def test_levels_rise_with_age():
    y, o, r = st.THRESHOLDS["idle_s"]
    seen = [st.assess(_ev(logged_in=True, last_action_at=NOW - t)).level
            for t in (0, y, o, r)]
    assert seen == [st.FRESH, st.YELLOW, st.ORANGE, st.RED]


def test_cookie_ttl_is_inverted_less_time_left_is_worse():
    y, o, r = st.THRESHOLDS["cookie_ttl_s"]
    lv = lambda ttl: st.assess(_ev(logged_in=True, cookie_expires_at=NOW + ttl)).level
    assert lv(y * 4) == st.FRESH
    assert lv(y) == st.YELLOW
    assert lv(r) == st.RED


# --- unknowns -----------------------------------------------------------------------
def test_an_unmeasured_signal_is_recorded_not_scored_as_fresh():
    """None means NOT MEASURED. It must not quietly read as an all-clear — the same rule the
    checkpoint ladder enforces one layer up — so it is named in `unmeasured` even though it
    cannot raise the level on its own."""
    s = st.assess(_ev(logged_in=True))
    assert set(s.unmeasured) >= {"idle_s", "page_age_s", "cookie_ttl_s"}
    assert "cookie_ttl_s" in s.unmeasured, "inert until a CDP cookie read lands"


# --- the remedies -------------------------------------------------------------------
def test_logged_out_is_renew_never_refresh():
    """Reloading a signed-out view lands on a login wall. The remedy is a fresh state."""
    s = st.assess(_ev(logged_in=False, last_action_at=NOW - 5))
    assert s.level == st.RED and s.verdict == st.RENEW


def test_a_blind_reading_is_handed_off_and_never_remedied():
    """Refreshing a page nobody could observe is guessing with a page load."""
    s = st.assess(_ev(blind_reason="AX scan returned no controls with errors",
                      last_action_at=NOW - 5))
    assert s.verdict == st.HANDOFF
    assert "cannot observe" in s.why
    # and it does not pretend the other signals were read
    assert s.unmeasured and "observation itself failed" in s.unmeasured[0]


def test_blind_outranks_every_other_signal():
    s = st.assess(_ev(blind_reason="auth probe failed", logged_in=True,
                      last_action_at=NOW - 1, last_nav_at=NOW - 1))
    assert s.verdict == st.HANDOFF


# --- SAFETY: work outranks freshness ------------------------------------------------
def test_refresh_is_withheld_when_the_page_holds_unsaved_work():
    """A half-filled application is worth more than a suspicion. Orange means "suspect", and
    reloading would throw away typed answers that cost real effort."""
    o = st.THRESHOLDS["idle_s"][1]
    without = st.assess(_ev(logged_in=True, last_action_at=NOW - o))
    withwork = st.assess(_ev(logged_in=True, last_action_at=NOW - o, holds_unsaved_work=True))
    assert without.verdict == st.REFRESH
    assert withwork.verdict == st.CONTINUE
    assert "withheld" in withwork.why


def test_renew_over_unsaved_work_goes_to_the_operator_not_to_a_reset():
    """A genuinely dead view still must not be reset out from under real work — the operator can
    save it or abandon it knowingly. We never make that call for them."""
    s = st.assess(_ev(logged_in=False, last_action_at=NOW - 5, holds_unsaved_work=True))
    assert s.verdict == st.HANDOFF
    assert "withheld" in s.why


def test_unsaved_work_does_not_upgrade_a_healthy_page():
    s = st.assess(_ev(logged_in=True, last_action_at=NOW - 5, holds_unsaved_work=True))
    assert s.verdict == st.CONTINUE and s.level == st.FRESH


# --- the journaled row --------------------------------------------------------------
def test_as_dict_carries_raw_values_so_thresholds_can_be_fitted_later():
    """The prototype's whole job: journal the EVIDENCE, not just the verdict, so the levels can
    be calibrated from our own history instead of re-instrumented."""
    s = st.assess(_ev(logged_in=True, last_action_at=NOW - 42, last_nav_at=NOW - 300))
    d = s.as_dict()
    assert d["rules_version"] == st.RULES_VERSION, "rows under different rules must not pool"
    by_name = {sig["name"]: sig for sig in d["signals"]}
    assert by_name["idle_s"]["value"] == 42
    assert by_name["page_age_s"]["value"] == 300
    assert all("level" in sig for sig in d["signals"])


def test_the_row_round_trips_through_json():
    import json
    s = st.assess(_ev(logged_in=True, last_action_at=NOW - 1))
    assert json.loads(json.dumps(s.as_dict()))["verdict"] == st.CONTINUE
