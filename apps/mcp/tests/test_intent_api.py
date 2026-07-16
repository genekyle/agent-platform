"""The @journaled decorator's contract.

The property under test is "an endpoint cannot forget", so most of these assert on what
happens when an endpoint misbehaves — raises, returns no outcome, or reports success while
journaling a failure.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel

from interaction.contract import Intent, Outcome

from app.intent_api import journaled


@pytest.fixture(autouse=True)
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    return tmp_path


def rows(tmp_path):
    p = tmp_path / "cache" / "intent_journal.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def run(coro):
    """Drive one coroutine to completion.

    Plain asyncio.run rather than pytest-asyncio: these are the repo's first async tests,
    and a new dev dependency for both apps is not worth one decorator.
    """
    return asyncio.run(coro)


class Body(BaseModel):
    tab_url: str | None = "https://boards.greenhouse.io/kkr/jobs/123"
    ats: str | None = "greenhouse"
    field: str | None = "country"
    value: str | None = "United States"
    action_id: str = "click"


def test_a_successful_endpoint_journals_one_row(corpus):
    @journaled(Intent.SELECT_OPTION)
    async def ep(body):
        return {"outcome": Outcome.OK, "detail": "picked", "steps": [{"step": "open"}]}

    out = run(ep(Body()))
    assert out["ok"] is True and out["outcome"] == "ok"
    (row,) = rows(corpus)
    assert row["intent"] == "select_option"
    assert row["ats"] == "greenhouse" and row["field"] == "country"
    assert row["route"] == "boards.greenhouse.io/kkr/jobs/{id}"   # templated, joins to captures
    assert row["steps"] == [{"step": "open"}]


def test_an_endpoint_that_raises_is_journaled_as_error_not_lost(corpus):
    @journaled(Intent.SELECT_OPTION)
    async def ep(body):
        raise RuntimeError("websocket closed")

    out = run(ep(Body()))
    assert out["ok"] is False and out["outcome"] == "error"
    (row,) = rows(corpus)
    # ERROR, not NOT_FOUND: a mechanism failure must not read as a stale recipe.
    assert row["outcome"] == "error"
    assert "websocket closed" in row["detail"]
    # Nothing verifiably reached the page. Marking a connection-refused `executed` would be
    # the same rehearsal/performance confusion the event log has.
    assert row["executed"] is False


def test_a_raising_probe_keeps_the_question_it_was_asking(corpus):
    """For /probe the `note` IS the training signal; the expression is its artifact.

    Losing it exactly when the probe fails would keep the most interesting rows mute.
    """
    class ProbeBody(Body):
        note: str = "what shape is Greenhouse's attestation widget?"

    @journaled(Intent.PROBE)
    async def ep(body):
        raise RuntimeError("target closed")

    run(ep(ProbeBody()))
    assert "attestation widget" in rows(corpus)[0]["detail"]


def test_an_endpoint_that_declares_no_outcome_is_journaled_as_error_loudly(corpus):
    # The anti-silent-success contract: an un-declared outcome is an un-audited path, and
    # the corpus must show that rather than assume `ok`.
    @journaled(Intent.CLICK)
    async def ep(body):
        return {"detail": "did a thing"}

    out = run(ep(Body()))
    assert out["ok"] is False and out["outcome"] == "error"
    assert "declared no outcome" in rows(corpus)[0]["detail"]


def test_ok_is_derived_from_the_outcome_not_asserted_by_the_endpoint(corpus):
    # An endpoint cannot report success while journaling a failure — the response is built
    # from the record.
    @journaled(Intent.SELECT_OPTION)
    async def ep(body):
        return {"ok": True, "outcome": Outcome.NO_OPTION, "detail": "vocabulary miss"}

    out = run(ep(Body()))
    assert out["ok"] is False, "the endpoint's own ok:True must not override the outcome"
    assert out["outcome"] == "no_option"


def test_committed_unconfirmed_does_not_read_as_success(corpus):
    # The staged-commit popup case: the commit navigates and destroys its own observer.
    @journaled(Intent.SELECT_OPTION)
    async def ep(body):
        return {"outcome": Outcome.COMMITTED_UNCONFIRMED, "detail": "commit clicked"}

    out = run(ep(Body()))
    assert out["ok"] is False
    assert rows(corpus)[0]["outcome"] == "committed_unconfirmed"


def test_a_polymorphic_endpoint_resolves_its_intent_per_call(corpus):
    from interaction.contract import intent_for_action

    @journaled(lambda body: intent_for_action(body.action_id))
    async def ep(body):
        return {"outcome": Outcome.OK, "actions": [body.action_id]}

    run(ep(Body(action_id="type")))
    run(ep(Body(action_id="scroll")))
    run(ep(Body(action_id="upload")))
    got = [(r["intent"], r["actions"]) for r in rows(corpus)]
    # `clear`/`type` both fold into set_text; the primitive survives in `actions`.
    assert got == [("set_text", ["type"]), ("scroll", ["scroll"]), ("upload", ["upload"])]


def test_values_are_redacted_by_field_name_without_the_endpoint_asking(corpus):
    @journaled(Intent.SET_TEXT)
    async def ep(body):
        return {"outcome": Outcome.OK}

    run(ep(Body(field="password", value="hunter2")))
    assert "hunter2" not in json.dumps(rows(corpus))


def test_sensitive_override_redacts_when_the_field_name_is_uninformative(corpus):
    # /execute type has no semantic field name — it's how a credential flow is driven.
    @journaled(Intent.SET_TEXT, sensitive=True)
    async def ep(body):
        return {"outcome": Outcome.OK}

    run(ep(Body(field=None, value="s3cret-token")))
    assert "s3cret" not in json.dumps(rows(corpus))


def test_unjournaled_keys_pass_through_to_the_caller(corpus):
    # An endpoint can return rich debug payloads without polluting the corpus's columns.
    # NB `value` is an unfortunate name collision: /probe RETURNS `value` (the JS result),
    # while the journal HAS a `value` column (the intent's argument, off the body). They are
    # different things. The returned one reaches the caller; the column keeps the argument.
    @journaled(Intent.PROBE)
    async def ep(body):
        return {"outcome": Outcome.OK, "value": {"count": 63}, "exception": None}

    out = run(ep(Body(value="United States")))
    assert out["value"] == {"count": 63}            # the probe's result reached the caller
    assert rows(corpus)[0]["value"] == "United States"   # the column still holds the argument
