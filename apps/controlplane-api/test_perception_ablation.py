"""The ablation's own arithmetic.

These are the numbers a build decision rests on, so the maths gets tested the same way the
models do. Cheap, offline, no corpus — the harness functions take plain dicts.
"""

from __future__ import annotations

import pytest

from perception.ablation import (_rate_at_budget, q1_error_prediction, q2_novelty_operating_point,
                                 q3_where_vision_wins, q4_two_stage)


def _pred(gold, dom_pred, vis_pred, *, margin=0.3, clarity=0.8, tokens=100,
          dom_novelty=0.1, vis_novelty=0.1):
    return {
        "filename": f"{gold}-{dom_pred}-{vis_pred}-{tokens}",
        "gold": gold, "gold_platform": gold.split("_")[0], "gold_phase": "x",
        "dom_tokens": tokens, "class_size": 3,
        "dom": {"pred": dom_pred, "margin": margin, "clarity": clarity,
                "novelty": dom_novelty, "right": dom_pred == gold},
        "visual:stub": {"pred": vis_pred, "margin": margin, "clarity": clarity,
                        "novelty": vis_novelty, "right": vis_pred == gold},
    }


# --- the operating-point maths --------------------------------------------------------
def test_recall_at_a_false_flag_budget_is_read_off_the_known_distribution():
    """The threshold is set by what we can AFFORD on known pages, then recall is whatever it
    is — not the other way round. AUROC cannot express this and that is why it is not enough."""
    known = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    unseen = [0.85, 0.95, 0.99, 0.5]
    out = _rate_at_budget(known, unseen, 0.10)
    assert out["threshold"] == pytest.approx(0.9)
    assert out["recall"] == pytest.approx(0.5)       # only 0.95 and 0.99 clear it
    assert out["false_flag_rate"] <= 0.11


def test_a_perfect_novelty_score_catches_everything_within_budget():
    out = _rate_at_budget([0.0] * 20, [1.0] * 5, 0.10)
    assert out["recall"] == 1.0


def test_empty_inputs_return_none_rather_than_a_number():
    assert _rate_at_budget([], [1.0], 0.1)["recall"] is None


# --- Q1: incremental value ------------------------------------------------------------
def test_agreement_that_only_repeats_the_margin_shows_no_conditional_lift():
    """The test that actually decides whether a second witness earns its place: if inside a
    clarity band agreement no longer separates right from wrong, it was the margin talking."""
    preds = []
    for i in range(30):
        right = i % 2 == 0
        preds.append(_pred("a", "a" if right else "b", "a" if right else "b",
                           clarity=0.9 if right else 0.1))
    out = q1_error_prediction(preds, "visual:stub")
    assert out["alone"]["dom_clarity"] is not None
    assert set(out["conditional_on_dom_clarity"]) == {"low_clarity", "mid_clarity", "high_clarity"}


def test_q1_reports_each_signal_separately_and_never_averages_them():
    preds = [_pred("a", "a", "a"), _pred("a", "b", "a"), _pred("b", "b", "b")]
    out = q1_error_prediction(preds, "visual:stub")
    assert set(out["alone"]) == {"dom_margin", "dom_clarity", "agreement", "visual_margin"}


# --- Q2: fusion rules -----------------------------------------------------------------
def test_and_fusion_is_the_min_and_or_fusion_is_the_max():
    """AND = both must flag (fewer false alarms); OR = either may (fewer misses). Computed per
    row so the pairing survives — averaging the two distributions would lose it."""
    novelty = [{"unseen": False, "dom": 0.1, "visual:stub": 0.9},
               {"unseen": False, "dom": 0.2, "visual:stub": 0.8},
               {"unseen": True, "dom": 0.95, "visual:stub": 0.99},
               {"unseen": True, "dom": 0.9, "visual:stub": 0.2}]
    out = q2_novelty_operating_point(novelty, "visual:stub", budget=0.5)
    assert out["n_unseen"] == 2 and out["n_known"] == 2
    # The row where only the DOM fired survives OR and dies under AND.
    assert out["or_either"]["auroc"] >= out["and_both"]["auroc"]


# --- Q3: conditional value ------------------------------------------------------------
def test_vision_rescues_are_counted_where_the_dom_is_starved():
    """Average accuracy hides the case vision was bought for. This counts the rows only the
    visual witness got, bucketed by how little the DOM had to work with."""
    starved = [_pred("a", "b", "a", tokens=t) for t in range(1, 6)]      # vision-only wins
    rich = [_pred("a", "a", "b", tokens=t) for t in range(200, 205)]     # dom-only wins
    out = q3_where_vision_wins(starved + rich, "visual:stub")
    buckets = list(out.values())
    assert buckets[0]["visual_rescues"] > 0
    assert buckets[-1]["visual_rescues"] == 0


# --- Q4: the two-stage ceiling --------------------------------------------------------
def test_the_platform_gate_ceiling_counts_only_cross_platform_errors():
    """A platform gate can only fix errors that crossed platforms. Errors inside the right
    platform are a different problem, and knowing which dominates says whether to build it."""
    platform_of = {"workday_a": "workday", "workday_b": "workday", "indeed_a": "indeed"}
    preds = [
        _pred("workday_a", "workday_b", "workday_b"),   # within-platform error
        _pred("workday_a", "indeed_a", "indeed_a"),     # cross-platform error — fixable
        _pred("indeed_a", "indeed_a", "indeed_a"),      # correct
    ]
    out = q4_two_stage(preds, platform_of)
    assert out["errors"] == 2
    assert out["cross_platform_errors"] == 1
    assert out["within_platform_errors"] == 1
    assert out["ceiling_if_platform_were_free"] == pytest.approx(2 / 3, abs=1e-3)
