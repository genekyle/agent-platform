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


# --- the url a tab_id caller never supplied -----------------------------------------------
def test_a_tab_id_caller_still_journals_where_it_happened(monkeypatch):
    """`url` is not decoration — `route` derives from it, and route+state is the key an intent
    program is compiled and replayed under. A row without it journals the action perfectly and
    teaches nothing. Every action of the SuccessFactors account drive landed that way on
    2026-07-28: correct, complete, unusable.

    And the caller was right to use tab_id: a url goes stale the moment the page navigates, which
    is why the executor addresses by id. So the resolution belongs in one place, not in a rule
    every call site has to remember."""
    import asyncio

    from app import intent_api

    class _Body:
        browser_url = "http://127.0.0.1:9322"
        tab_id = "TAB-1"
        tab_url = None

    class _Resp:
        @staticmethod
        def json():
            return [{"id": "OTHER", "url": "https://example.com/other"},
                    {"id": "TAB-1", "url": "https://career41.sapsf.com/career?company=teradynein"}]

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, _url): return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: _Client())

    got = asyncio.run(intent_api._resolve_url_for_journal(_Body(), {}))
    assert "career41.sapsf.com" in got


def test_the_endpoints_own_answer_is_preferred_over_asking_the_browser(monkeypatch):
    # Cheapest source first: if the endpoint already resolved a target it knows the url, and
    # asking the browser again is a round trip for an answer we hold.
    import asyncio

    from app import intent_api

    class _Body:
        browser_url = "http://127.0.0.1:9322"
        tab_id = "TAB-1"
        tab_url = None

    def _boom(**_kw):
        raise AssertionError("must not ask the browser when the result carries the url")

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _boom)
    got = asyncio.run(intent_api._resolve_url_for_journal(
        _Body(), {"url": "https://career41.sapsf.com/career"}))
    assert got == "https://career41.sapsf.com/career"


def test_journaling_never_fails_the_action_it_is_describing():
    """A request that raised while enriching a log line is worth nothing at all."""
    import asyncio

    from app import intent_api

    class _Body:
        browser_url = "http://127.0.0.1:9322"
        tab_id = "TAB-1"
        tab_url = None

    # No monkeypatching: the browser at that port is not answering in a test run.
    assert asyncio.run(intent_api._resolve_url_for_journal(_Body(), {})) == ""

    class _Bare:
        pass

    assert asyncio.run(intent_api._resolve_url_for_journal(_Bare(), {})) == ""


# --- the url a caller with NO tab address never supplied ------------------------------------
def test_a_backend_node_id_caller_still_journals_where_it_happened(monkeypatch):
    """The main line, not an edge case. `backend_node_id` addressing survives the navigation a
    url does not, so recipes drive /execute with `browser_url` + `backend_node_id` and no tab
    address at all — and this resolver used to require a `tab_id` and hand back "" for exactly
    those calls. The Odyssey iCIMS drive of 2026-08-12 journaled 400 rows that way: every click
    that filled and submitted a federal self-identification form, `route:""`, nothing for
    `compile_from_journal` to file under and nothing for rung 0 to replay.

    Resolution asks the SAME target resolver the endpoint used, so the row names the tab the
    action actually reached rather than the first plausible page."""
    import asyncio

    from app import intent_api
    from app.observer import ax_proposer

    class _Body:
        browser_url = "http://127.0.0.1:9322"
        # no tab_id, no tab_url — the way the executor is actually driven

    seen = {}

    async def _fake_discover(browser_url, tab_id=None, tab_url=None):
        seen.update(browser_url=browser_url, tab_id=tab_id, tab_url=tab_url)
        return {"url": "https://careers-odysseyconsult.icims.com/jobs/8308/data-business-analyst/form",
                "webSocketDebuggerUrl": "ws://x"}

    monkeypatch.setattr(ax_proposer, "_discover_target", _fake_discover)

    got = asyncio.run(intent_api._resolve_url_for_journal(_Body(), {}))
    assert "careers-odysseyconsult.icims.com" in got
    # it asked the browser the caller named, not the 9222 default
    assert seen["browser_url"] == "http://127.0.0.1:9322"


def test_no_browser_url_resolves_to_empty_rather_than_guessing():
    """Without a browser to ask there is no honest answer, and inventing one files the row
    under a page the action never touched."""
    import asyncio

    from app import intent_api

    class _Body:
        browser_url = None

    assert asyncio.run(intent_api._resolve_url_for_journal(_Body(), {})) == ""


def test_a_route_is_derived_once_the_url_is_recovered():
    """The point of the url is the route: route+state is the key a program is compiled under."""
    from interaction.fingerprint import route_template

    got = route_template(
        "https://careers-odysseyconsult.icims.com/jobs/8308/data-business-analyst/form")
    assert got, "a recovered url must template to a non-empty route"
    assert "8308" not in got, "the job id is the part that must be templated away"
