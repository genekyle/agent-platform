"""Tests for execution style — HOW an action is spent in time, not WHAT it is.

The property that actually matters here is the one that is easy to lose while making pacing
configurable: **style varies how far above the bot-safety floor we sit, never whether we sit above
it.** A "fast" mode that could undercut the floor would be a safety regression wearing a feature's
clothes.
"""

import random

import execution_style as xs
import search_cadence


def _fixed(value: float) -> random.Random:
    """An rng pinned to one point of each range, so a sample is checkable."""
    class _R(random.Random):
        def uniform(self, a, b):  # noqa: A003 — matching random.Random's API
            return a + (b - a) * value
    return _R()


# --- the floor holds for every style ---------------------------------------------------------
def test_navigation_never_dips_under_the_bot_safety_floor():
    """`fast` is a pace, not permission. The floor is a property of what is safe to do to a real
    site, not of how eager this particular run is."""
    floor = float(search_cadence.BOUNDS["min_seconds_between_navigations"])
    for style in xs.STYLES.values():
        for point in (0.0, 0.5, 1.0):
            got = xs.pause_for(style, xs.NAVIGATION, rng=_fixed(point))
            assert got >= floor, f"{style.name} navigated after {got}s, under the {floor}s floor"


def test_fast_is_the_style_the_floor_actually_binds():
    """Proof the clamp is load-bearing rather than incidentally satisfied: fast's own low end is
    below the floor, and the clamp is what lifts it."""
    assert xs.FAST.navigation[0] < search_cadence.BOUNDS["min_seconds_between_navigations"]
    assert xs.pause_for(xs.FAST, xs.NAVIGATION, rng=_fixed(0.0)) == \
        float(search_cadence.BOUNDS["min_seconds_between_navigations"])


def test_in_page_pauses_are_not_clamped():
    """The floor is about navigation. Clamping in-page pauses to it would make every click take
    three seconds, which is not human either."""
    assert xs.pause_for(xs.FAST, xs.SETTLE, rng=_fixed(0.0)) == xs.FAST.settle[0]


# --- the pace the operator asked for -----------------------------------------------------------
def test_the_default_style_averages_about_a_second_and_a_half():
    """The operator watched a step finish in ~0.5s and asked for ~1.5s. `human` is the default,
    so this is the pace an unpinned run actually gets."""
    mid = (xs.HUMAN.settle[0] + xs.HUMAN.settle[1]) / 2
    assert 1.3 <= mid <= 1.7
    between = (xs.HUMAN.between[0] + xs.HUMAN.between[1]) / 2
    assert 1.3 <= between <= 1.9


def test_fast_preserves_the_old_behaviour_under_a_name():
    """The previous pace is kept rather than deleted — it is useful when the operator is watching
    a single step and wants the answer now."""
    assert xs.FAST.settle[1] < 0.5 and xs.FAST.between[1] < 1.0


def test_styles_are_ordered_slowest_to_fastest_where_it_matters():
    for kind in (xs.SETTLE, xs.BETWEEN):
        f = xs.FAST.range_for(kind)
        h = xs.HUMAN.range_for(kind)
        u = xs.UNHURRIED.range_for(kind)
        assert f[1] <= h[0] and h[1] <= u[0], f"{kind} ranges should not overlap"


# --- choosing ----------------------------------------------------------------------------------
def test_a_named_style_always_wins():
    assert xs.pick_style("fast") is xs.FAST
    assert xs.pick_style("unhurried") is xs.UNHURRIED


def test_an_unknown_style_is_refused_rather_than_silently_defaulted():
    """Falling back to a default on a typo would run at a pace nobody asked for and say nothing."""
    try:
        xs.pick_style("brisk")
    except ValueError as exc:
        assert "brisk" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_unpinned_choice_spreads_across_styles_but_favours_human():
    """The point is a believable distribution, not an even split."""
    rng = random.Random(7)
    picks = [xs.pick_style(rng=rng).name for _ in range(400)]
    assert set(picks) == set(xs.STYLES)                 # all of them appear
    assert picks.count("human") > picks.count("unhurried") > picks.count("fast")


def test_a_sequence_gets_ONE_style_not_one_per_pause():
    """Coherence within a drive: a person is not brisk and dawdling in the same five seconds.
    This is a property of how callers use it — pick once, sample many."""
    style = xs.pick_style("human")
    pauses = [xs.pause_for(style, xs.SETTLE) for _ in range(20)]
    assert all(xs.HUMAN.settle[0] <= p <= xs.HUMAN.settle[1] for p in pauses)
    assert len(set(pauses)) > 1        # sampled, not constant


def test_pauses_vary_because_a_constant_cadence_is_its_own_signature():
    style = xs.pick_style("human")
    assert len({round(xs.pause_for(style, xs.BETWEEN), 4) for _ in range(30)}) > 20


def test_describe_carries_the_pace_for_the_panel_and_journal():
    """'It felt too quick' is unfalsifiable unless the step records what pace it ran at."""
    d = xs.describe(xs.HUMAN)
    assert d["style"] == "human" and d["why"]
    assert d["settle_s"] == list(xs.HUMAN.settle)


def test_an_unknown_pause_kind_falls_back_to_settle_rather_than_zero():
    """A typo'd kind must not silently mean 'no pause at all'."""
    assert xs.pause_for(xs.HUMAN, "nonsense", rng=_fixed(0.0)) == xs.HUMAN.settle[0]
