"""M3 tests: the strict model-output parser, the shadow-agreement metric, and the controller
HTTP surface (decide_model / decide / summary / programs). The Haiku call itself is not exercised
(operator-present, budget-gated); everything around it is."""

from __future__ import annotations

from controller.metrics import shadow_agreement
from controller.reason import DECISION_JSON_SCHEMA, parse_decision
from interaction.decision import Bundle


def _bundle(state="indeed_apply_questions", expected=("indeed_apply_review",)) -> Bundle:
    return Bundle(task="indeed_apply", goal_text="apply", done=False,
                  url="https://smartapply.indeed.com/x", route="smartapply.indeed.com/x",
                  state=state, is_branch=False, human_required=False,
                  ats="indeed_quick_apply", expected_next=tuple(expected))


# --- the strict parser -------------------------------------------------------
def test_parses_a_clean_decision():
    d = parse_decision({"intent": "set_text", "params": {"field": "Phone", "value": "x"},
                        "confidence": 0.9, "rationale": "fill phone",
                        "expected_next": ["indeed_apply_questions"]}, _bundle())
    assert not d.escalate and d.rung == "model"
    assert d.intent == "set_text" and d.params == {"field": "Phone", "value": "x"}
    assert d.confidence == 0.9 and d.expected_next == ("indeed_apply_questions",)


def test_rejects_intent_outside_vocabulary():
    d = parse_decision({"intent": "navigate", "params": {}, "confidence": 0.9,
                        "rationale": "go"}, _bundle())
    assert d.escalate and "not in the closed vocabulary" in d.rationale


def test_rejects_selector_smuggled_into_params():
    d = parse_decision({"intent": "click", "params": {"control": "#submit-btn"},
                        "confidence": 0.9, "rationale": "click"}, _bundle())
    assert d.escalate and "selector-shaped param" in d.rationale


def test_rejects_selector_key_in_params():
    d = parse_decision({"intent": "click", "params": {"[data-automation-id=x]": "y"},
                        "confidence": 0.9, "rationale": "click"}, _bundle())
    assert d.escalate and "selector-shaped param" in d.rationale


def test_rejects_missing_or_out_of_range_confidence():
    for bad in ({"intent": "click", "params": {}, "rationale": "c"},           # missing
                {"intent": "click", "params": {}, "confidence": 1.7, "rationale": "c"},
                {"intent": "click", "params": {}, "confidence": True, "rationale": "c"}):  # bool != number
        d = parse_decision(bad, _bundle())
        assert d.escalate and "confidence" in d.rationale


def test_rejects_non_object():
    assert parse_decision("not json", _bundle()).escalate
    assert parse_decision({"intent": "click", "params": "x", "confidence": 0.9,
                           "rationale": "c"}, _bundle()).escalate


def test_a_rejection_is_an_escalation_not_a_crash():
    # A rejected parse must be a journalable model-rung escalation, so it's training signal.
    d = parse_decision({"garbage": True}, _bundle())
    assert d.rung == "model" and d.escalate and d.confidence == 0.0
    assert d.expected_next == ("indeed_apply_review",)   # carries the state's expectation


def test_schema_intent_enum_is_the_closed_vocabulary():
    from interaction.contract import Intent
    assert set(DECISION_JSON_SCHEMA["properties"]["intent"]["enum"]) == {i.value for i in Intent}


# --- shadow agreement --------------------------------------------------------
def _pair(intent, field, prop_intent, prop_field, *, ats="indeed_quick_apply",
          state="s", golden=False):
    return {"ats": ats, "state": state, "intent": intent,
            "params": {"field": field} if field else {},
            "proposed_intent": prop_intent,
            "proposed_params": {"field": prop_field} if prop_field else {},
            "golden": golden}


def test_agreement_over_paired_rows():
    rows = [
        _pair("set_text", "Phone", "set_text", "Phone"),       # agree
        _pair("set_text", "Email", "set_text", "Email"),       # agree
        _pair("click", None, "submit", None),                  # disagree: wrong_intent
        _pair("set_text", "Name", "set_text", "Nombre"),       # disagree: wrong_field
    ]
    rep = shadow_agreement(rows)
    assert rep["n"] == 4 and rep["agree"] == 2 and rep["disagree"] == 2
    assert rep["agreement"] == 0.5
    assert rep["by_category"] == {"wrong_intent": 1, "wrong_field": 1}
    assert rep["by_scenario"][0]["scenario"] == "indeed_quick_apply:s"


def test_agreement_exact_vs_loose():
    rows = [_pair("set_text", "Phone", "set_text", "Phone")]
    rows[0]["params"] = {"field": "Phone", "value": "555"}
    rows[0]["proposed_params"] = {"field": "Phone", "value": "999"}
    assert shadow_agreement(rows, match="loose")["agreement"] == 1.0    # same field
    assert shadow_agreement(rows, match="exact")["agreement"] == 0.0    # different value


def test_agreement_empty_is_honest_zero():
    rep = shadow_agreement([])
    assert rep["n"] == 0 and rep["agreement"] == 0.0 and rep["by_scenario"] == []


# --- the HTTP surface --------------------------------------------------------
def test_controller_endpoints_wired(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setenv("CONTROLLER_PROGRAMS_DIR", str(tmp_path / "progs"))
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    # summary + programs + decisions read cleanly on an empty corpus
    s = client.get("/api/controller/summary").json()
    assert s["corpus_size"] == 0 and s["program_count"] == 0
    assert s["agreement"]["n"] == 0
    assert client.get("/api/controller/programs").json()["count"] == 0
    assert client.get("/api/controller/decisions").json()["count"] == 0

    # the free cascade probe: a bundle with no program escalates to the teacher (no spend)
    body = {"bundle": {"task": "indeed_apply", "goal_text": "apply", "done": False,
                       "url": "https://smartapply.indeed.com/x", "route": "smartapply.indeed.com/x",
                       "state": "indeed_apply_questions", "is_branch": False,
                       "human_required": False, "ats": "indeed_quick_apply",
                       "expected_next": ["indeed_apply_review"], "unanswered": [], "recent": []}}
    d = client.post("/api/controller/decide", json=body).json()["decision"]
    assert d["escalate"] is True and d["rung"] == "teacher"


def test_observe_endpoint_manual_mode(monkeypatch, tmp_path):
    """Preview mode with a supplied url+text needs no browser and never acts."""
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setenv("CONTROLLER_PROGRAMS_DIR", str(tmp_path / "progs"))
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    resp = client.post("/api/controller/observe", json={
        "task": "indeed_apply", "goal_text": "apply with indeed",
        "url": "https://smartapply.indeed.com/questions/8839201"}).json()
    assert resp["bundle"]["state"] == "indeed_apply_questions"
    assert resp["bundle"]["ats"] == "indeed_quick_apply"
    assert "# GOAL" in resp["prompt"]
    # no program yet -> escalate to teacher, no spend
    assert resp["decision"]["rung"] == "teacher" and resp["decision"]["escalate"] is True
    assert resp["model_cost_usd"] == 0.0


# --- expected_next is never left empty (measured gap, 2026-07-19) ------------------
def test_omitted_expected_next_inherits_the_recipe_edges():
    """All 48 rows of the first real corpus carried expected_next=[], so `verified` stayed None
    and the loop's "landed somewhere we didn't expect" trigger was inert. A model that omits the
    field must inherit the recipe's edges rather than produce an unverifiable decision."""
    d = parse_decision({"intent": "click", "params": {"control": "Continue"},
                        "confidence": 0.9, "rationale": "advance"}, _bundle())
    assert d.expected_next == ("indeed_apply_review",)      # from the bundle, not empty


def test_the_model_may_narrow_the_expectation_but_not_erase_it():
    bundle = _bundle(expected=("a", "b", "c"))
    narrowed = parse_decision({"intent": "click", "params": {"control": "Continue"},
                               "confidence": 0.9, "expected_next": ["b"]}, bundle)
    assert narrowed.expected_next == ("b",)                 # an explicit narrower set wins

    erased = parse_decision({"intent": "click", "params": {"control": "Continue"},
                             "confidence": 0.9, "expected_next": []}, bundle)
    assert erased.expected_next == ("a", "b", "c")          # an empty list is not an erasure


def test_inheritance_cannot_invent_an_expectation():
    """If the recipe itself has no edges for this state there is nothing to inherit — stay empty
    rather than fabricate a landing state."""
    d = parse_decision({"intent": "click", "params": {"control": "Continue"},
                        "confidence": 0.9}, _bundle(expected=()))
    assert d.expected_next == ()
