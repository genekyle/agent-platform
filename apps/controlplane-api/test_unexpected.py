"""Tests for the shared unexpected-state policy — the one rule both the controller loop and the
login drive must decide identically: re-observe ONCE, then escalate. Never twice, never a guess."""

from __future__ import annotations

from controller import unexpected as ux
from interaction.contract import Outcome


def test_verified_always_continues():
    # A verified action continues even if the outcome would otherwise read as stale.
    assert ux.respond(Outcome.OK.value, verified=True) is ux.Response.CONTINUE
    assert ux.respond(Outcome.NOT_FOUND.value, verified=True) is ux.Response.CONTINUE


def test_stale_earns_exactly_one_re_observe():
    for outcome in (Outcome.NOT_FOUND.value, Outcome.NOT_OPENED.value, Outcome.AMBIGUOUS.value,
                    ux.STALE_TAB):
        # first miss -> re-observe
        assert ux.respond(outcome, already_retried=False) is ux.Response.RE_OBSERVE
        # second consecutive miss -> escalate, never a third try
        assert ux.respond(outcome, already_retried=True) is ux.Response.ESCALATE


def test_non_stale_failures_escalate_immediately():
    # BLOCKED is a challenge/session — it must never be retried (the loop hands it to the human
    # before it ever reaches here, and the policy agrees).
    assert ux.respond(Outcome.BLOCKED.value) is ux.Response.ESCALATE
    assert ux.respond(Outcome.ERROR.value) is ux.Response.ESCALATE
    assert ux.respond(Outcome.NOT_COMMITTED.value) is ux.Response.ESCALATE
    assert ux.respond(None) is ux.Response.ESCALATE


def test_is_stale_covers_both_levels():
    assert ux.is_stale(Outcome.NOT_FOUND.value)      # protocol level: the control isn't there
    assert ux.is_stale(ux.STALE_TAB)                 # tab level: the whole target is gone
    assert not ux.is_stale(Outcome.OK.value)
    assert not ux.is_stale(Outcome.BLOCKED.value)
    assert not ux.is_stale(None)


def test_stale_tab_is_not_an_outcome_member():
    """STALE_TAB is deliberately one level ABOVE the protocol outcomes — it must not collide with
    the Outcome vocabulary, or a dead tab would read as a failed control."""
    assert ux.STALE_TAB not in {o.value for o in Outcome}
