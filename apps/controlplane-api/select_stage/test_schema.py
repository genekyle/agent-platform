"""Phase 0 tests — the frozen SELECT contract.

Run from apps/controlplane-api:  ../../.venv/bin/python -m pytest select_stage/test_schema.py -q
"""

from select_stage import schema as s


def test_schema_version_frozen():
    assert s.SELECTOR_SCHEMA_VERSION == "v1"


def test_haiku_output_schema_shape():
    sch = s.HAIKU_OUTPUT_SCHEMA
    assert sch["type"] == "object"
    assert sch["additionalProperties"] is False
    assert set(sch["required"]) == {"action_id", "mark", "confidence", "needs_human", "reason_code"}
    # enums are derived from the Enums — they can't silently drift
    assert sch["properties"]["action_id"]["enum"] == [a.value for a in s.ActionId]
    assert sch["properties"]["reason_code"]["enum"] == list(s._MODEL_REASON_CODES)
    assert sch["properties"]["mark"]["type"] == "integer"


def test_model_reason_codes_are_a_subset():
    all_reasons = {r.value for r in s.ReasonCode}
    assert set(s._MODEL_REASON_CODES).issubset(all_reasons)
    # system-only reasons must NOT be emittable by the model
    for system_only in ("cache_hit", "budget_exceeded", "stop_state", "verifier_failed"):
        assert system_only not in s._MODEL_REASON_CODES


def _ax(bid, role, name):
    return {"backend_node_id": bid, "role": role, "caption": name,
            "bbox": {"x": 1, "y": 2, "width": 3, "height": 4}}


def test_candidates_from_ax_numbers_marks():
    cands = s.candidates_from_ax([_ax(101, "textbox", "Email"), _ax(202, "button", "Log In")])
    assert [c.mark for c in cands] == [1, 2]
    assert cands[0].backend_node_id == 101
    assert cands[1].name == "Log In"


def test_candidates_from_ax_backcompat_debug_and_skip_missing():
    ax = [
        {"role": "link", "caption": "x", "bbox": {}, "_debug": {"backend_node_id": 9}},  # back-compat
        {"role": "link", "caption": "no-id", "bbox": {}},                                 # skipped
        _ax(5, "button", "Go"),
    ]
    cands = s.candidates_from_ax(ax)
    assert [c.backend_node_id for c in cands] == [9, 5]
    assert [c.mark for c in cands] == [1, 2]  # marks are contiguous after skips


def test_resolve_mark():
    cands = s.candidates_from_ax([_ax(101, "textbox", "Email"), _ax(202, "button", "Log In")])
    assert s.resolve_mark(2, cands).backend_node_id == 202
    assert s.resolve_mark(0, cands) is None    # 0 = none match
    assert s.resolve_mark(99, cands) is None    # out of range


def test_selection_result_status():
    resolved = s.SelectionResult(s.ActionId.CLICK, 101, 0.9, False, s.ReasonCode.SOM_PICK)
    escalate = s.SelectionResult(s.ActionId.NONE, None, 0.0, True, s.ReasonCode.BUDGET_EXCEEDED)
    assert resolved.status == "resolved"
    assert escalate.status == "escalate"
