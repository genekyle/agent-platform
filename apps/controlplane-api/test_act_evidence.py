"""An act carries its evidence (SESSION 20) — the verify half.

Pinned in the order it costs to get wrong:
  1. `expected_next` is VERIFIED, not filed as "we could not judge". The decision named its
     acceptable destinations in advance, which is the strongest evidence available — and until
     2026-08-27 those rows fell through to UNOBSERVED (flagged 2026-08-22).
  2. The two meanings of `mismatch` are separable: "the world did not move" (verify) and "the
     supervisor judged this non-nominal" (the actuator). One word for both is why the teacher's
     queue ranked two different problems as one class.
  3. The kind is written only on a mismatch, and historical rows that carry none stay readable.
"""
from __future__ import annotations

import json

import pytest

import step_runner as sr


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "_transitions_dir", lambda: tmp_path)
    return tmp_path


def _obs(url="https://a.test/1"):
    return sr.Observation(ts="t", ok=True, url=url)


# --- expected_next: the branch that did not exist ------------------------------------------------
def test_a_landing_the_decision_named_in_advance_is_confirmed():
    expect = sr.Expectation(kind="expected_next", value=["workday_my_information", "workday_review"])
    verdict, why = sr.verify(expect, {"landed_state": "workday_review"}, _obs())
    assert verdict == sr.CONFIRMED and "named in advance" in why


def test_a_landing_the_decision_did_not_name_is_a_mismatch():
    expect = sr.Expectation(kind="expected_next", value=["workday_my_information"])
    verdict, why = sr.verify(expect, {"landed_state": "workday_error_retry"}, _obs())
    assert verdict == sr.MISMATCH
    assert "workday_error_retry" in why and "chose another" in why


def test_a_family_name_is_satisfied_by_the_render_beneath_it():
    """The recipe names families, not renders: a declared `workday_my_information` is satisfied by
    a landed `workday_my_information_edit`. Containment both ways, deliberately."""
    expect = sr.Expectation(kind="expected_next", value=["workday_my_information"])
    verdict, _ = sr.verify(expect, {"landed_state": "workday_my_information_edit"}, _obs())
    assert verdict == sr.CONFIRMED


def test_an_unnamed_landing_leaves_the_claim_standing_rather_than_failing_it():
    """A verifier that cannot see must not block — the `unobserved` rule this module was built
    on. Nothing landed a state name, so there is nothing to compare and nothing to demote."""
    expect = sr.Expectation(kind="expected_next", value=["workday_review"])
    assert sr.verify(expect, {"landed_state": ""}, _obs())[0] == sr.UNOBSERVED
    assert sr.verify(sr.Expectation(kind="expected_next", value=[]),
                     {"landed_state": "anything"}, _obs())[0] == sr.UNOBSERVED


# --- the split ------------------------------------------------------------------------------------
def test_a_mismatch_records_which_kind_it_was(corpus):
    before, after = _obs(), _obs()
    sr.record_transition(session_id=1, rung_id="advance", action={}, before=before, after=after,
                         expect=sr.Expectation(kind="content_changed"), changes={},
                         verdict=sr.MISMATCH, evidence="nothing moved", claimed="ok")
    sr.record_transition(session_id=1, rung_id="advance", action={}, before=before, after=after,
                         expect=sr.Expectation(kind="content_changed"), changes={},
                         verdict=sr.MISMATCH, evidence="judged", claimed="ok",
                         mismatch_kind=sr.MISMATCH_JUDGED)
    rows = [json.loads(ln) for ln in (corpus / "session_1.jsonl").read_text().splitlines()]
    assert rows[0]["mismatch_kind"] == sr.MISMATCH_WORLD, \
        "the verifier's own meaning is the default, because it is this function's oldest caller"
    assert rows[1]["mismatch_kind"] == sr.MISMATCH_JUDGED


def test_only_a_mismatch_carries_a_kind(corpus):
    """A confirmed row has no mismatch to classify, and inventing a field for it would put a
    value where there is no fact."""
    sr.record_transition(session_id=2, rung_id="open", action={}, before=_obs(), after=_obs(),
                         expect=sr.Expectation(kind="content_changed"), changes={},
                         verdict=sr.CONFIRMED, evidence="url moved", claimed="ok")
    row = json.loads((corpus / "session_2.jsonl").read_text().splitlines()[0])
    assert "mismatch_kind" not in row
