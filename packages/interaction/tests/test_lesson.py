"""Tests for the Lesson contract — an escalation is paid for once.

Two properties carry the whole design, and both have a test named after them:

  * `test_ten_tenants_teach_the_same_page_once` — the scope chain is what stops Claude teaching
    the same Workday page at every tenant. If this breaks, teacher-calls-per-application never
    falls and the plan has no cost curve.
  * `test_an_unverified_lesson_is_refused` — a lesson is a PREDICTION until the step it describes
    works. Accepting on the teacher's confidence rather than the page's verdict is how a corpus
    fills with confident teaching that never landed.
"""

from __future__ import annotations

import pytest

from interaction.lesson import (
    SCOPE_UNIVERSAL,
    Lesson,
    LessonKind,
    LessonRejected,
    accept,
    known,
    lookup,
    parse_scope,
    platform_scope,
    read_all,
    scope_chain,
    scope_rank,
    summarize,
    tenant_scope,
    write,
)

WHY = "the field is a react-select whose value only commits on the option click"


def lesson(kind=LessonKind.FIELD_ALIAS.value, scope=SCOPE_UNIVERSAL, subject="sponsorship",
           **over):
    kw = dict(kind=kind, scope=scope, subject=subject, payload={"alias": "requires_sponsorship"},
              rationale=WHY, evidence=("state", "unanswered[0].field"))
    kw.update(over)
    return Lesson(**kw)


# --- scope ---------------------------------------------------------------------------
def test_scope_chain_is_most_specific_first():
    """The ordering IS the generalisation lever — reversed, a universal default would silently
    beat a tenant's own correction."""
    assert scope_chain(platform="workday", tenant="acme") == (
        "tenant:acme", "platform:workday", "universal")


def test_scope_chain_degrades_cleanly():
    assert scope_chain() == ("universal",)
    assert scope_chain(platform="workday") == ("platform:workday", "universal")


def test_parse_and_rank():
    assert parse_scope("platform:Workday") == ("platform", "workday")
    assert parse_scope("universal") == ("universal", "")
    assert scope_rank(tenant_scope("acme")) > scope_rank(platform_scope("workday"))
    assert scope_rank(platform_scope("workday")) > scope_rank(SCOPE_UNIVERSAL)


# --- acceptance ----------------------------------------------------------------------
def test_an_unverified_lesson_is_refused():
    with pytest.raises(LessonRejected, match="did not verify"):
        accept(lesson(), verified=False)


def test_a_verified_lesson_is_stamped():
    got = accept(lesson(), verified=True)
    assert got.accepted and got.verified_at


def test_a_lesson_needs_real_reasoning():
    """§10 — a placeholder 'why' teaches WHAT with no rule to generalise from. That is a label."""
    with pytest.raises(LessonRejected, match="real reasoning"):
        accept(lesson(rationale="operator correction"), verified=True)
    with pytest.raises(LessonRejected, match="real reasoning"):
        accept(lesson(rationale=""), verified=True)


def test_a_lesson_needs_a_kind_and_a_subject():
    with pytest.raises(LessonRejected, match="unknown lesson kind"):
        accept(lesson(kind="vibes"), verified=True)
    with pytest.raises(LessonRejected, match="no delivery address"):
        accept(lesson(subject="  "), verified=True)


def test_capability_gap_is_a_first_class_kind():
    """The kind that turns 'the observer is great until we can't do anything about it' from a
    complaint into a work item: its payload is a reach gap and its resolution is code."""
    got = accept(lesson(kind=LessonKind.CAPABILITY_GAP.value, subject="widget:signature_pad",
                        payload={"gap": "widget:unknown@signature", "endpoint": "/sign_canvas"},
                        rationale="no tier-2 protocol drives a canvas signature pad"),
                 verified=True)
    assert got.accepted


# --- the cache key: the reuse property ----------------------------------------------
def test_the_cache_key_excludes_the_scope():
    """'What is this Workday sponsorship field?' is the SAME question at every tenant. Keying the
    cache by scope would give each tenant its own entry and re-buy the identical lesson."""
    a = lesson(scope=platform_scope("workday"))
    b = lesson(scope=tenant_scope("acme"))
    assert a.cache_key() == b.cache_key()


def test_ten_tenants_teach_the_same_page_once():
    """The property the cost curve depends on: one platform-scoped lesson answers for every
    tenant of that platform, including ones never seen before."""
    taught = [accept(lesson(scope=platform_scope("workday")), verified=True)]
    for tenant in [f"tenant{i}" for i in range(10)]:
        assert known(LessonKind.FIELD_ALIAS.value, "sponsorship",
                     platform="workday", tenant=tenant, lessons=taught)


def test_a_platform_lesson_does_not_leak_to_another_platform():
    taught = [accept(lesson(scope=platform_scope("workday")), verified=True)]
    assert not known(LessonKind.FIELD_ALIAS.value, "sponsorship",
                     platform="greenhouse", lessons=taught)


def test_the_most_specific_scope_wins():
    universal = accept(lesson(payload={"alias": "generic"}), verified=True)
    tenant = accept(lesson(scope=tenant_scope("acme"), payload={"alias": "acme_specific"}),
                    verified=True)
    got = lookup(LessonKind.FIELD_ALIAS.value, "sponsorship", platform="workday", tenant="acme",
                 lessons=[universal, tenant])
    assert got.payload["alias"] == "acme_specific"


def test_lookup_misses_a_different_subject():
    taught = [accept(lesson(), verified=True)]
    assert lookup(LessonKind.FIELD_ALIAS.value, "veteran_status", lessons=taught) is None


def test_lookup_misses_a_different_kind():
    taught = [accept(lesson(), verified=True)]
    assert lookup(LessonKind.STATE_LABEL.value, "sponsorship", lessons=taught) is None


def test_subject_matching_is_case_insensitive():
    taught = [accept(lesson(subject="Sponsorship"), verified=True)]
    assert known(LessonKind.FIELD_ALIAS.value, "sponsorship", lessons=taught)


# --- the store -----------------------------------------------------------------------
def test_write_refuses_an_unaccepted_lesson():
    with pytest.raises(LessonRejected, match="accepted lesson"):
        write(lesson())


def test_write_then_read_round_trips():
    got = write(accept(lesson(subject="round_trip_probe"), verified=True))
    back = [l for l in read_all() if l.subject == "round_trip_probe"]
    assert back and back[0].payload == got.payload and back[0].rationale == WHY
    assert back[0].evidence == got.evidence


def test_summarize_is_the_reuse_scoreboard():
    rows = [accept(lesson(subject="a"), verified=True),
            accept(lesson(subject="b", scope=platform_scope("workday")), verified=True),
            accept(lesson(subject="a", scope=tenant_scope("acme")), verified=True)]
    s = summarize(rows)
    assert s["total"] == 3
    assert s["distinct_subjects"] == 2        # 'a' taught twice is ONE question
    assert s["by_scope"] == {"universal": 1, "platform": 1, "tenant": 1}


def test_every_kind_is_reachable_and_named():
    assert {k.value for k in LessonKind} == {
        "state_label", "field_alias", "recipe_edge", "recovery_rule", "tenant_patch",
        "capability_gap"}
