"""The promotion gate's two bars (CONTROLLER_PROMOTION.md, ruled 2026-08-22).

`loose` is INTENT-ONLY for every click decision by construction — a click's params carry
`control`, not `field`, so `_field()` returns None on both sides. A scenario could clear a
loose-only gate while reaching for the wrong button every time, and `open_pane`/`enter_apply`
are exactly those click-shaped turns. `exact` is the second bar; both are required.

The pins that matter are the ones that would let a wrong promotion through: a different control
must not score exact, and a scenario passing loose while failing exact must not be eligible.
"""

from __future__ import annotations

from controller import metrics


def _pair(intent, params, proposed_intent, proposed_params, **over):
    row = {"intent": intent, "params": params,
           "proposed_intent": proposed_intent, "proposed_params": proposed_params,
           "ats": "indeed", "state": "indeed_apply_review"}
    row.update(over)
    return row


# --- the finding this gate exists for ------------------------------------------------
def test_loose_cannot_see_which_control_was_proposed():
    """The measured blind spot, pinned so nobody 'simplifies' the gate back onto loose alone."""
    row = _pair("click", {"control": "A"}, "click", {"control": "TOTALLY DIFFERENT"})
    assert metrics._matches(row, "loose") is True        # <- the blind spot, by construction
    assert metrics._matches(row, "exact") is False       # <- what the second bar is for


def test_a_different_control_never_scores_exact():
    """The coach's required pin. Case-folding must not soften a genuinely different control —
    these are real pairs from the 2026-08-22 journal."""
    for teacher, shadow in (
        ("Submit your application", "review your application"),
        ("Apply Now", "save job"),
        ("Apply", "saved jobs"),
    ):
        row = _pair("click", {"control": teacher}, "click", {"control": shadow})
        assert metrics._matches(row, "exact") is False, (teacher, shadow)


def test_exact_folds_case_and_whitespace_because_they_are_two_renderings_of_one_name():
    """The controller reads normalised AX identities (lowercased); the teacher records the label
    as rendered. 72 of 87 value-differences on 2026-08-22 were case-only. Comparing raw strings
    would make `exact` a test of string formatting and the bar unpassable for no actionable
    reason."""
    row = _pair("click", {"control": "Save and Continue"}, "click", {"control": "save and continue"})
    assert metrics._matches(row, "exact") is True
    spaced = _pair("click", {"control": "Save  and\tContinue"}, "click", {"control": "save and continue"})
    assert metrics._matches(spaced, "exact") is True


def test_a_row_the_teacher_left_no_params_on_cannot_testify_about_control_choice():
    """The crank synthesises a teacher Decision from `_RUNG_INTENT` with empty params on most
    rungs (131 of 294 pairs on 2026-08-22). Scoring those as exact MISSES would blame the
    controller for a journaling gap; scoring them as hits would flatter it. They leave the
    denominator and are reported instead — `step_runner.verify`'s 'BLIND IS NOT WRONG' rule."""
    rows = [_pair("click", {}, "click", {"control": "Continue"})] * 4
    rep = metrics.shadow_agreement(rows)
    assert rep["n"] == 4
    assert rep["exact_n"] == 0 and rep["exact_unscoreable"] == 4
    assert rep["loose_agreement"] == 1.0        # loose still scores them — intent+field agree
    # An observe/observe pair has empty params on BOTH sides: a real agreement, not a no-claim.
    obs = metrics.shadow_agreement([_pair("observe", {}, "observe", {})])
    assert obs["exact_n"] == 1 and obs["exact_agreement"] == 1.0


# --- the gate itself -----------------------------------------------------------------
def test_passing_loose_while_failing_exact_is_NOT_eligible():
    """The coach's second required pin, and the whole reason there are two bars."""
    assert metrics.is_promotable(n=30, loose=0.95, exact=0.50, exact_n=30) is False
    assert metrics.is_promotable(n=30, loose=0.95, exact=0.90, exact_n=30) is True


def test_both_windows_are_required_so_one_lucky_row_cannot_promote():
    """Measured 2026-08-22: indeed_job_posting had 67 pairs and exact_n=1. Without its own
    window, a single matching control observation reads as exact=1.000 and carries a scenario
    through a gate it has no evidence for."""
    assert metrics.is_promotable(n=67, loose=0.95, exact=1.0, exact_n=1) is False
    assert metrics.is_promotable(n=10, loose=1.0, exact=1.0, exact_n=25) is False   # loose window
    assert metrics.is_promotable(n=30, loose=1.0, exact=1.0, exact_n=25) is True


def test_omitting_the_exact_window_can_never_promote():
    """`exact_n` defaults to 0 on purpose: a caller that forgets it gets a refusal, never a
    promotion. The gate's default answer is no."""
    assert metrics.is_promotable(n=99, loose=1.0, exact=1.0) is False


def test_the_bars_are_named_constants_the_operator_can_retune():
    assert metrics.PROMOTION_LOOSE_BAR == 0.90
    assert metrics.PROMOTION_EXACT_BAR == 0.85
    assert metrics.PROMOTION_MIN_N == 25 and metrics.PROMOTION_MIN_EXACT_N == 25
    rep = metrics.shadow_agreement([_pair("click", {"control": "A"}, "click", {"control": "A"})])
    assert rep["bars"] == {"loose": 0.90, "exact": 0.85, "min_n": 25, "min_exact_n": 25}


# --- the additive shape the cockpit reads --------------------------------------------
def test_the_per_scenario_shape_stays_additive():
    """The UI's promotion panel reads `by_scenario`'s existing keys. They must keep their meaning
    and place; the two-bar keys sit beside them."""
    rep = metrics.shadow_agreement([
        _pair("click", {"control": "Continue"}, "click", {"control": "continue"}),
        _pair("click", {"control": "Continue"}, "observe", {}),
    ])
    s = rep["by_scenario"][0]
    assert s["scenario"] == "indeed:indeed_apply_review" and s["n"] == 2
    assert s["agree"] == 1 and s["agreement"] == 0.5          # unchanged meaning: loose
    for key in ("loose_agreement", "exact_agreement", "exact_n", "exact_unscoreable", "eligible"):
        assert key in s, key
    assert rep["eligible"] == []                               # nothing promotable here


def test_an_empty_corpus_reports_an_honest_zero_not_a_pass():
    rep = metrics.shadow_agreement([])
    assert rep["n"] == 0 and rep["eligible"] == []
    assert rep["loose_agreement"] == 0.0 and rep["exact_agreement"] == 0.0
