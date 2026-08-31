"""The precedent embedder's pure parts: layout, hashing, doc composition, and — the one that
matters doctrinally — labels never leak into features (PLAN_inhouse_reasoner_v1 §3)."""
from precedent.embedder import (
    DIM,
    FACET_DIM,
    FACET_SLICE,
    TEXT_SLICE,
    VISION_SLICE,
    FusionEmbedder,
    doc_from_decision,
    doc_from_transition,
    route_template,
)


def test_block_layout_covers_dim_exactly():
    assert TEXT_SLICE.stop == VISION_SLICE.start
    assert VISION_SLICE.stop == FACET_SLICE.start
    assert FACET_SLICE.stop == DIM


def test_route_template_collapses_identifiers():
    a = route_template("https://x.wd5.myworkdayjobs.com/jobs/4424504424/apply?src=a")
    b = route_template("https://x.wd5.myworkdayjobs.com/jobs/999/apply")
    assert a == b and "{n}" in a and "?" not in a


def test_facet_block_is_deterministic_and_normalized():
    e = FusionEmbedder()
    v1 = e.facet_block({"platform": "workday", "state": "workday_review"})
    v2 = e.facet_block({"state": "Workday_Review ", "platform": "WORKDAY"})  # case/space folded
    assert v1 == v2
    assert len(v1) == FACET_DIM
    assert abs(sum(x * x for x in v1) - 1.0) < 1e-9


DECISION_ROW = {
    "ts": "2026-08-28T00:00:00+00:00",
    "bundle_digest": "abcdef123456",
    "intent": "click",
    "params": {"ref": "Submit"},
    "session_id": 34,
    "bundle_snapshot": {
        "goal_text": "submit the application",
        "state": "greenhouse_review",
        "url": "https://job-boards.greenhouse.io/acme/jobs/123",
        "ats": "greenhouse",
        "task": "indeed_apply",
        "unanswered": [],
        "phase": None,
        "expected_next": "confirmation",
    },
}

TRANSITION_ROW = {
    "session_id": 34,
    "ts": "2026-08-28T01:00:00+00:00",
    "rung": "enter_apply",
    "verdict": "confirmed",
    "action": {"intent": "click", "control": "Apply", "ats": "workday"},
    "before": {
        "url": "https://vrtx.wd501.myworkdayjobs.com/jobs/1",
        "title": "Business Systems Analyst",
        "candidates": [["button", "Apply"], ["link", "Sign In"]],
        "belief": {"state": "workday_job_posting", "facets": {"platform": "workday"}},
        "artifact": "a.json",
        "screenshot": "",
    },
    "teacher_correction": None,
}


def test_decision_doc_carries_label_outside_features():
    doc = doc_from_decision(DECISION_ROW)
    assert doc.intent == "click" and doc.ref == "Submit"
    # the decided intent/ref must not appear in any feature field
    assert "click" not in doc.text.lower()
    assert "submit the application" in doc.text  # the goal is a feature; the ACT is not
    assert all("click" not in v.lower() for v in doc.facets.values())
    assert doc.session == "34" and doc.state == "greenhouse_review"


def test_transition_before_doc_features_exclude_the_act():
    doc = doc_from_transition(TRANSITION_ROW, "before")
    assert doc.intent == "click" and doc.ref == "Apply"
    assert doc.phase == "enter_apply"  # the rung is KNOWN before the act — legitimate feature
    # candidates are features (the page offers Apply); the CHOSEN control is not marked in them
    assert "button Apply" in doc.text
    assert doc.facets.get("state") == "workday_job_posting"


def test_after_half_is_a_state_exemplar_without_labels():
    doc = doc_from_transition(TRANSITION_ROW, "after") if TRANSITION_ROW.get("after") else None
    assert doc is None  # no after observation in fixture -> no doc, never a guessed one


def test_embed_doc_is_unit_norm_even_without_macos_frameworks():
    e = FusionEmbedder()
    e._nl_tried = True  # force the no-NLEmbedding path so the test runs anywhere
    e._nl = None
    doc = doc_from_decision(DECISION_ROW)
    vec, has_vision = e.embed_doc(doc)
    assert len(vec) == DIM and has_vision is False
    norm = sum(v * v for v in vec) ** 0.5
    assert abs(norm - 1.0) < 1e-9  # facets alone still normalize
