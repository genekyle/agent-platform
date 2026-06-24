"""Tests for the distillation promotion gate (verifier-confirmed Haiku pick → label).

Covers the pure decision policy `_promotion_decision`, which is the heart of the
auto-promotion pass: it decides whether a teacher (Haiku) pick becomes a machine
golden label ('auto'), a staged one-click-confirm ('suggested'), or is left to a
human. Behavioral confirmation (verify_ok) is MANDATORY for both promotion tiers.
"""

import main


def test_auto_band_high_confidence_verified():
    # >= AUTO threshold + verified + mappable candidate → write golden now.
    assert main._promotion_decision(
        confidence=0.97, needs_human=False, verify_ok=True, has_candidate=True
    ) == ("auto", None)


def test_suggested_band_mid_confidence_verified():
    # In [GATE_MIN, AUTO) + verified → stage for a one-click human confirm.
    assert main._promotion_decision(
        confidence=0.88, needs_human=False, verify_ok=True, has_candidate=True
    ) == ("suggested", None)


def test_boundary_at_auto_threshold_is_auto():
    assert main._promotion_decision(
        confidence=main._PROMOTE_AUTO_CONFIDENCE, needs_human=False,
        verify_ok=True, has_candidate=True
    )[0] == "auto"


def test_boundary_at_gate_min_is_suggested():
    assert main._promotion_decision(
        confidence=main._GATE_MIN_CONFIDENCE, needs_human=False,
        verify_ok=True, has_candidate=True
    )[0] == "suggested"


def test_unverified_never_promotes():
    # No replay verdict yet → behavioral confirmation missing → leave to a human.
    assert main._promotion_decision(
        confidence=0.99, needs_human=False, verify_ok=None, has_candidate=True
    ) == (None, "unverified")


def test_verify_failed_never_promotes():
    assert main._promotion_decision(
        confidence=0.99, needs_human=False, verify_ok=False, has_candidate=True
    ) == (None, "verify_failed")


def test_low_confidence_left_to_human():
    assert main._promotion_decision(
        confidence=0.70, needs_human=False, verify_ok=True, has_candidate=True
    ) == (None, "low_confidence")


def test_escalated_pick_never_promotes():
    # The teacher itself punted → never a positive label.
    assert main._promotion_decision(
        confidence=0.99, needs_human=True, verify_ok=True, has_candidate=True
    ) == (None, "escalated")


def test_unmappable_candidate_skipped():
    # Picked backend node isn't in this capture's AX set → nothing to label.
    assert main._promotion_decision(
        confidence=0.99, needs_human=False, verify_ok=True, has_candidate=False
    ) == (None, "no_candidate_mapping")
