"""The contract's invariants — the ones a future edit could quietly break."""

from __future__ import annotations

import pytest

from interaction.contract import (
    DISCOVERY_INTENTS,
    INTENT_SCHEMA_VERSION,
    READ_ONLY_INTENTS,
    VALUE_READ_AT,
    Intent,
    Outcome,
    WidgetType,
    intent_expands_to,
    redact,
)


def test_schema_version_is_pinned():
    # Not a style check: journal rows carry this, and a silent bump makes the corpus
    # before it unjoinable to the corpus after it.
    assert INTENT_SCHEMA_VERSION == "v1"


def test_every_widget_type_declares_where_its_truth_lives():
    # The whole point of /describe_widget: `.value` lies on a react-select. A new
    # WidgetType with no VALUE_READ_AT entry would silently verify against nothing.
    for wt in WidgetType:
        assert wt in VALUE_READ_AT, f"{wt} has no value_read_at — where does its truth live?"
    assert VALUE_READ_AT[WidgetType.REACT_SELECT] != ".value"  # the bug that bought this rule


def test_ok_is_the_only_success_outcome():
    # The anti-silent-success contract: anything that isn't OK must name which step broke.
    assert Outcome.OK.value == "ok"
    assert len([o for o in Outcome if o.value == "ok"]) == 1


def test_error_is_distinct_from_protocol_outcomes():
    # An unexpected exception must not masquerade as a stale recipe — folding a websocket
    # drop into not_found would send us re-mapping selectors that were fine.
    assert Outcome.ERROR not in {Outcome.NOT_FOUND, Outcome.AMBIGUOUS, Outcome.BLOCKED}


def test_probe_is_the_only_discovery_intent():
    assert DISCOVERY_INTENTS == frozenset({Intent.PROBE})


def test_read_only_intents_expand_to_no_actions():
    for intent in READ_ONLY_INTENTS:
        assert intent_expands_to(intent) == (), f"{intent} is read-only but claims to act"


def test_intents_expand_to_actionid_strings_not_new_verbs():
    """The bridge to the frozen vocabulary.

    Intent sits ABOVE select_stage.schema.ActionId; every primitive an intent expands into
    must be a verb the executor's driver actually implements. If this fails, we have minted
    a rival vocabulary — the exact failure this layering exists to avoid.
    """
    # driver.py's dispatch table, which is the superset that actually runs.
    driver_actions = {"click", "type", "select", "scroll", "submit", "clear", "upload"}
    for intent in Intent:
        for action in intent_expands_to(intent):
            assert action in driver_actions, f"{intent} expands to unknown primitive {action!r}"


@pytest.mark.parametrize("field_name", [
    "password", "Password", "user_password", "passwd", "passphrase", "pwd",
    "api_key", "api-key", "apikey", "secret", "auth_token",
    "ssn", "social_security_number", "credit_card", "card_number", "cvv", "cvc",
    "otp", "one_time_code", "2fa_code", "mfa", "verification_code", "pin",
])
def test_sensitive_fields_are_redacted(field_name):
    out = redact("hunter2!", field=field_name)
    assert "hunter2" not in out
    assert out == "[redacted:8]"   # length survives, the secret does not


@pytest.mark.parametrize("field_name", [
    "phone_device_type", "first_name", "email", "school", "degree", "discipline",
    "how_did_you_hear", "gender", "veteran_status",
])
def test_ordinary_fields_are_not_redacted(field_name):
    assert redact("Mobile", field=field_name) == "Mobile"


def test_explicit_sensitive_flag_overrides_field_name_inference():
    # A field whose NAME looks secret but whose value isn't.
    assert redact("Public Trust", field="clearance_pin_level", sensitive=False) == "Public Trust"
    # ...and the reverse: a value the caller knows is secret in an innocuous-looking field.
    assert redact("s3cret", field="answer", sensitive=True) == "[redacted:6]"


def test_redact_truncates_long_values_but_keeps_them_readable():
    out = redact("x" * 500, field="cover_letter")
    assert out is not None and len(out) < 200 and out.endswith("…")


def test_redact_passes_none_through():
    assert redact(None, field="password") is None


# --- the intent/param shape gate (2026-07-20) ----------------------------------------
def test_check_intent_params_accepts_the_documented_shapes():
    from interaction.contract import check_intent_params
    assert check_intent_params("click", {"control": "Continue"}) == ""
    assert check_intent_params("set_text", {"field": "Salary", "value": "65000"}) == ""
    assert check_intent_params("set_date", {"field": "Start", "month": 3, "year": 2026}) == ""
    assert check_intent_params("check_group", {"field": "Terms", "values": ["Accept"]}) == ""
    assert check_intent_params("observe", {}) == ""


def test_click_may_not_be_addressed_by_field():
    """The exact decision a 1B model emitted on 2026-07-20: `click {field, value}`. Every part is
    well-formed — click is a real verb, both keys are in the closed param set, and a JSON grammar
    passes it. `LiveActuator` resolves a click as `control or name or VALUE`, so this would have
    clicked a control named "0" on a live job application."""
    from interaction.contract import check_intent_params
    why = check_intent_params("click", {"field": "salary", "value": "0"})
    assert why and "control" in why          # names the addressing it actually needs
    # and the foreign-key branch fires too, when `control` IS present alongside junk
    assert "does not take" in check_intent_params(
        "click", {"control": "Continue", "value": "0"})


def test_a_field_intent_without_a_field_is_rejected():
    from interaction.contract import check_intent_params
    why = check_intent_params("set_text", {"value": "65000"})
    assert "requires" in why and "field" in why


def test_none_valued_params_do_not_count_as_present():
    """A model that emits `{"control": null}` has not addressed anything."""
    from interaction.contract import check_intent_params
    assert check_intent_params("click", {"control": None})


def test_an_unknown_verb_is_left_to_the_vocabulary_check():
    """One gate per concern: `parse_decision` already rejects off-vocabulary intents, and this
    function must not shadow that with a confusing second message."""
    from interaction.contract import check_intent_params
    assert check_intent_params("teleport", {"anything": 1}) == ""


def test_every_intent_has_a_documented_param_shape():
    """The guard against the vocabulary growing past the table: a new Intent member with no
    INTENT_PARAMS entry would silently accept anything."""
    from interaction.contract import INTENT_PARAMS, Intent
    assert {i.value for i in Intent} == set(INTENT_PARAMS)
    for intent, (required, allowed) in INTENT_PARAMS.items():
        assert required <= allowed, f"{intent}: required keys must be allowed"
