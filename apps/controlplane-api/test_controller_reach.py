"""Tests for the actuation reach probe — can the local executor operate this page?

The probe's job is to separate two failures that look identical from the outside and need
opposite remedies: "I don't know what this page means" (teach me — ORANGE) and "I cannot touch
this page" (drive it, and the gap becomes an endpoint — RED).

The bias throughout is asymmetric on purpose, and the tests pin the direction: a false "in reach"
costs one honest `not_found` outcome the supervisor already names; a false "out of reach" sends a
perfectly workable page to the teacher, which is the expensive mistake this whole plan exists to
stop making.
"""

from __future__ import annotations

import pytest

from interaction.contract import Intent, WidgetType
from interaction.decision import Bundle, Decision

from controller import reach as reach_mod

IDENTITIES = ("button|Continue", "textbox|Phone number", "combobox|Country", "link|Back")


def bundle(*, ats="greenhouse", identities=IDENTITIES, unanswered=()):
    return Bundle(task="apply", goal_text="", done=False, url="https://x/y", route="/y",
                  state="greenhouse_apply", is_branch=False, human_required=False, ats=ats,
                  ax_identities=tuple(identities), unanswered=tuple(unanswered))


def decision(intent, **params):
    return Decision(intent=intent, params=params, confidence=1.0, rung="recipe", rationale="t")


def table(widget=WidgetType.TEXT.value, known=("phone",)):
    """A stand-in for `apply_fields.resolve`, injected so these tests never touch the real one."""
    def _resolve(ats, field):
        if ats != "greenhouse" or field not in known:
            raise KeyError(f"no field {field!r}")
        return {"widget_type": widget}
    return _resolve


# --- page level ---------------------------------------------------------------------
def test_a_page_with_no_addressable_controls_is_out_of_reach():
    """Also the signature of a DEAD TAB, whose failure mode is a successful EMPTY result rather
    than an error (2026-07-19). Both mean 'do not act'; the supervisor separates them after."""
    r = reach_mod.probe(bundle(identities=()), decision(Intent.CLICK.value, control="Continue"),
                        resolve=table())
    assert not r.can_operate
    assert r.gaps == ("page:no-addressable-controls",)


def test_no_decision_asks_only_the_page_level_question():
    assert reach_mod.probe(bundle(), None, resolve=table()).can_operate
    assert not reach_mod.probe(bundle(identities=()), None, resolve=table()).can_operate


# --- clicks address a control by name, and need no recipe table ----------------------
def test_a_click_on_a_present_control_is_in_reach():
    r = reach_mod.probe(bundle(), decision(Intent.CLICK.value, control="Continue"),
                        resolve=table())
    assert r.can_operate and r.gaps == ()


def test_a_click_on_a_control_that_is_not_there_is_out_of_reach():
    r = reach_mod.probe(bundle(), decision(Intent.CLICK.value, control="Submit application"),
                        resolve=table())
    assert not r.can_operate
    assert r.gaps == ("control:Submit application",)


@pytest.mark.parametrize("control", ["continue", "Continue to next step", "CONTINUE"])
def test_control_matching_is_forgiving_in_both_directions(control):
    """`ax_summary` normalizes names and a recipe's control text is routinely a prefix of the
    rendered label. Being strict here would route working pages to the teacher."""
    assert reach_mod.probe(bundle(), decision(Intent.CLICK.value, control=control),
                           resolve=table()).can_operate


def test_an_unknown_ats_does_not_block_a_click():
    """The distinction that makes ORANGE useful: with no recipe table we cannot fill a form, but
    we can still navigate — so an unfamiliar ATS is not automatically a teacher-drives page."""
    r = reach_mod.probe(bundle(ats=""), decision(Intent.CLICK.value, control="Continue"),
                        resolve=table())
    assert r.can_operate


# --- field intents need the recipe table --------------------------------------------
def test_a_resolvable_field_with_a_known_widget_is_in_reach():
    r = reach_mod.probe(bundle(), decision(Intent.SET_TEXT.value, field="phone"),
                        resolve=table())
    assert r.can_operate and r.gaps == ()


def test_an_unresolvable_field_is_out_of_reach_and_names_itself():
    r = reach_mod.probe(bundle(), decision(Intent.SET_TEXT.value, field="veteran_status"),
                        resolve=table())
    assert not r.can_operate
    assert r.gaps == ("field:greenhouse/veteran_status",)


def test_an_unknown_ats_blocks_a_field_intent():
    r = reach_mod.probe(bundle(ats=""), decision(Intent.SELECT_OPTION.value, field="phone"),
                        resolve=table())
    assert not r.can_operate and r.gaps == ("ats:unknown",)


def test_an_unknown_widget_is_a_capability_gap_not_a_failure():
    """`WidgetType.UNKNOWN` is the enum's own 'route to PROBE' marker. The right move is discovery
    whose output is a new member plus a protocol — not an attempt in the dark."""
    r = reach_mod.probe(bundle(), decision(Intent.SET_TEXT.value, field="phone"),
                        resolve=table(widget=WidgetType.UNKNOWN.value))
    assert not r.can_operate
    assert r.gaps == ("widget:unknown@phone",)


def test_operable_widgets_are_every_type_except_unknown():
    assert reach_mod.OPERABLE_WIDGETS == {w.value for w in WidgetType} - {WidgetType.UNKNOWN.value}


# --- verbs with no target ------------------------------------------------------------
@pytest.mark.parametrize("intent", [
    Intent.OBSERVE.value, Intent.SCAN_REQUIRED.value, Intent.PROBE.value, Intent.SCROLL.value,
])
def test_targetless_intents_are_always_in_reach(intent):
    assert reach_mod.probe(bundle(), decision(intent), resolve=table()).can_operate


def test_an_unrecognised_intent_fails_closed():
    """An unknown verb is exactly where a silent 'sure, go ahead' would cost the most."""
    r = reach_mod.probe(bundle(), decision("teleport"), resolve=table())
    assert not r.can_operate and r.gaps == ("intent:teleport",)


# --- the survey: the capability-gap inventory ---------------------------------------
def test_survey_inventories_every_unoperable_field_not_just_the_next_one():
    """What an escalation carries, so the teacher's answer becomes an endpoint rather than a
    one-off fix for the field that happened to come first."""
    b = bundle(unanswered=({"field": "phone"}, {"field": "veteran_status"},
                           {"field": "disability_status"}))
    gaps = reach_mod.survey(b, resolve=table())
    assert gaps == ("field:greenhouse/veteran_status", "field:greenhouse/disability_status")


def test_survey_is_clean_when_everything_resolves():
    b = bundle(unanswered=({"field": "phone"},))
    assert reach_mod.survey(b, resolve=table()) == ()


def test_survey_deduplicates():
    b = bundle(unanswered=({"field": "veteran_status"}, {"field": "veteran_status"}))
    assert len(reach_mod.survey(b, resolve=table())) == 1


def test_survey_reports_an_unknown_ats_per_field():
    b = bundle(ats="", unanswered=({"field": "phone"},))
    assert reach_mod.survey(b, resolve=table()) == ("field:unknown-ats/phone",)


# --- it really is free ---------------------------------------------------------------
def test_the_probe_makes_no_network_call_and_reads_only_the_bundle():
    """A probe that cost a round trip would get skipped on exactly the turns it matters."""
    calls: list[tuple] = []

    def counting_resolve(ats, field):
        calls.append((ats, field))
        return {"widget_type": WidgetType.TEXT.value}

    b = bundle(unanswered=({"field": "phone"}, {"field": "email"}))
    reach_mod.probe(b, decision(Intent.SET_TEXT.value, field="phone"), resolve=counting_resolve)
    assert calls == [("greenhouse", "phone")], "one static table lookup, and nothing else"
