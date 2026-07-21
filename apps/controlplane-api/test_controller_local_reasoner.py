"""Offline tests for the local student's seat — no model, no network.

The `transport` seam makes every one of these run against a fake, so the contract is proven
before a single token is generated. What matters here is not that Gemma is smart; it is that a
weak local model fails LOUD and SAFE, and that its prompt surface is byte-identical to Haiku's.
"""

from __future__ import annotations

import json

import pytest

from controller.local_reasoner import DEFAULT_MODEL, LocalReasoner, _flatten
from controller.reason import DECISION_JSON_SCHEMA, build_messages
from interaction.decision import RUNGS, Bundle


def _bundle(**over) -> Bundle:
    base = dict(
        task="indeed_apply", goal_text="apply", done=False,
        url="https://smartapply.indeed.com/questions/2", route="smartapply.indeed.com/questions/{id}",
        state="indeed_apply_questions", is_branch=False, human_required=False,
        ats="indeed_quick_apply", expected_next=("indeed_apply_review",),
        unanswered=({"field": "Salary", "kind": "text", "answered": False, "valid": True},),
    )
    return Bundle(**{**base, **over})


class FakeServer:
    """Records the request and returns a canned chat completion."""

    def __init__(self, content="", *, raise_exc=None):
        self.calls = []
        self._content = content
        self._raise = raise_exc

    def __call__(self, path, payload):
        self.calls.append((path, payload))
        if self._raise:
            raise self._raise
        return {"message": {"content": self._content}}

    @property
    def payload(self):
        return self.calls[0][1]


_GOOD = json.dumps({
    "intent": "set_text", "params": {"field": "Salary", "value": "65000"},
    "confidence": 0.82, "rationale": "the salary field is the first unanswered required field",
    "evidence": ["unanswered[0].field"], "expected_next": ["indeed_apply_questions"],
})


# --- the prompt surface must not fork ------------------------------------------------
def test_the_student_reads_the_exact_same_prompt_as_haiku():
    """The frozen serialization is the feature contract. If this rung 'improved' the prompt, a
    shadow comparison against Haiku would be measuring two different questions, and rows from the
    two rungs could never train one policy."""
    fake = FakeServer(_GOOD)
    bundle = _bundle()
    LocalReasoner(transport=fake)(bundle)

    system, messages = build_messages(bundle)
    sent = fake.payload["messages"]
    assert sent[0] == {"role": "system", "content": system}
    assert sent[1:] == _flatten(messages)
    # and the user turn really is bundle_to_prompt, not a paraphrase
    from interaction.decision import bundle_to_prompt
    assert bundle_to_prompt(bundle) in sent[1]["content"]


def test_flatten_turns_content_blocks_into_plain_strings():
    """`build_messages` returns Anthropic's nested form because that is what the Haiku rung sends.
    Flattening here — rather than forking build_messages — is what keeps the surfaces identical."""
    assert _flatten([{"role": "user", "content": [{"type": "text", "text": "a"},
                                                  {"type": "text", "text": "b"}]}]) == \
        [{"role": "user", "content": "ab"}]
    assert _flatten([{"role": "user", "content": "plain"}]) == \
        [{"role": "user", "content": "plain"}]


# --- constrained decoding is the whole quality lever ---------------------------------
def test_the_decision_grammar_is_sent_as_the_decoding_format():
    """On a 2B model this is the single largest quality lever: the closed Intent enum is enforced
    during DECODING, so an off-vocabulary verb cannot be generated at all."""
    fake = FakeServer(_GOOD)
    LocalReasoner(transport=fake)(_bundle())
    assert fake.payload["format"] == DECISION_JSON_SCHEMA
    # the enum really is in there — this is what makes it a grammar and not a suggestion
    assert "set_text" in fake.payload["format"]["properties"]["intent"]["enum"]
    assert fake.payload["format"]["properties"]["params"]["additionalProperties"] is False


def test_decoding_is_deterministic():
    """A policy, not a writer. Determinism is also what makes a shadow disagreement a real
    disagreement rather than a sample."""
    fake = FakeServer(_GOOD)
    LocalReasoner(transport=fake)(_bundle())
    assert fake.payload["options"]["temperature"] == 0
    assert fake.payload["stream"] is False


# --- the good path -------------------------------------------------------------------
def test_a_well_formed_answer_parses_into_a_student_decision():
    d = LocalReasoner(transport=FakeServer(_GOOD))(_bundle())
    assert d.intent == "set_text"
    assert d.params == {"field": "Salary", "value": "65000"}
    assert d.confidence == 0.82
    assert d.escalate is False
    assert d.evidence == ("unanswered[0].field",)


def test_the_student_is_journaled_as_its_own_rung():
    """Not `model`. Shadow agreement compares the student against the backstop — if both wrote
    `model`, it would be comparing a rung against itself."""
    d = LocalReasoner(transport=FakeServer(_GOOD))(_bundle())
    assert d.rung == "student"
    assert "student" in RUNGS


def test_the_student_costs_nothing_and_says_so():
    r = LocalReasoner(transport=FakeServer(_GOOD))
    r(_bundle())
    assert r.last_cost_usd == 0.0


def test_the_student_must_be_reviewed_before_acting():
    """An untrained local policy is the LEAST trusted thing in the system. If `student` were
    missing from PROPOSE_RUNGS, a 2B model would act unreviewed on a real job application."""
    from controller.teach import PROPOSE_RUNGS
    assert "student" in PROPOSE_RUNGS


# --- failing loud and safe -----------------------------------------------------------
def test_a_dead_server_becomes_an_escalation_not_a_crash():
    d = LocalReasoner(transport=FakeServer(raise_exc=ConnectionError("connection refused")))(_bundle())
    assert d.escalate is True
    assert "unreachable" in d.rationale and "connection refused" in d.rationale


def test_an_empty_message_escalates():
    d = LocalReasoner(transport=FakeServer(""))(_bundle())
    assert d.escalate is True and "empty" in d.rationale


def test_non_json_output_names_the_grammar_as_the_suspect():
    """With `format` applied this should be unreachable — so if it fires, the runtime ignored the
    grammar. Saying that plainly beats a generic parse error, because the fix is a runtime
    upgrade, not a prompt tweak."""
    d = LocalReasoner(transport=FakeServer("I think you should click Continue!"))(_bundle())
    assert d.escalate is True
    assert "constrained decoding" in d.rationale


def test_the_parser_still_runs_even_though_decoding_was_constrained():
    """A grammar constrains SHAPE, not sense. It cannot stop a selector-shaped VALUE — invariant
    #10 is enforced by `parse_decision`, which runs regardless."""
    smuggled = json.dumps({
        "intent": "click", "params": {"control": "#submit-button"},
        "confidence": 0.9, "rationale": "clicking submit",
    })
    d = LocalReasoner(transport=FakeServer(smuggled))(_bundle())
    assert d.escalate is True
    assert "selector-shaped" in d.rationale


def test_an_out_of_range_confidence_is_rejected():
    bad = json.dumps({"intent": "click", "params": {"control": "Continue"},
                      "confidence": 4.2, "rationale": "very sure"})
    d = LocalReasoner(transport=FakeServer(bad))(_bundle())
    assert d.escalate is True and "out of range" in d.rationale


def test_an_omitted_expectation_inherits_the_recipes_edges():
    """Same rule as the Haiku rung: a model may NARROW the expectation, never erase it — an empty
    `expected_next` makes the decision unverifiable and silently disarms the escalation trigger."""
    no_exp = json.dumps({"intent": "click", "params": {"control": "Continue"},
                         "confidence": 0.9, "rationale": "every required field is answered"})
    d = LocalReasoner(transport=FakeServer(no_exp))(_bundle())
    assert d.expected_next == ("indeed_apply_review",)


def test_the_model_tag_is_configuration_not_architecture():
    """The occupant of the seat is a config value. Gemma 4 E2B did not fit (7.2GB on an 8GB
    machine) and llama3.2:1b fits but scores 0/4 — the seat outlives both."""
    fake = FakeServer(_GOOD)
    LocalReasoner(transport=fake)(_bundle())
    assert fake.payload["model"] == DEFAULT_MODEL

    other = FakeServer(_GOOD)
    LocalReasoner(model="some-future-adapter", transport=other)(_bundle())
    assert other.payload["model"] == "some-future-adapter"
