"""Which question does the target answer? — the correlation check, and its refusals.

Operator, 2026-08-12, after a resume was uploaded into the wrong Workday box three times:
"we need to do some sort of confirmation of target versus what is required ... that idea of not
being able to correlate what question correlates with what (regardless of whether it's required
or not) is pivotal in making a system not become lost."

An address is a PREDICTION about which question a control answers. These tests pin the two ways
that prediction is allowed to fail — ambiguously, or wrongly — and the one thing that must never
happen again: acting anyway and reporting `ok`.

As elsewhere in this suite, the PYTHON half is tested by faking the page's answers; the page-side
JS is validated on the live drive (jsdom's offsetParent is always null, so a fake-DOM test would
validate a fiction).

Run from apps/mcp:  ../../.venv/bin/python -m pytest tests/test_target_correlation.py -q
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

import app.main_server as ms


@pytest.fixture(autouse=True)
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_OUTPUT_DIR", str(tmp_path))
    return tmp_path


def wire(monkeypatch, resolve_value, *, acted=None):
    """Fake the resolver's page-side answer. `acted` records whether the driver ever ran."""
    class _Session:
        def __init__(self, ws):
            pass

        async def send(self, method, params=None):
            if method == "Runtime.evaluate":
                return {"result": {"value": resolve_value}}
            if method == "DOM.getDocument":
                return {"root": {"nodeId": 1}}
            if method == "DOM.querySelector":
                return {"nodeId": 7}
            if method == "DOM.describeNode":
                return {"node": {"backendNodeId": 4242}}
            return {}

    @asynccontextmanager
    async def _connect(url, **kw):
        yield object()

    async def _discover(browser_url, tab_id=None, tab_url=None):
        return {"webSocketDebuggerUrl": "ws://x"}

    import app.observer.ax_proposer as axp
    monkeypatch.setattr(axp, "_CDPSession", _Session, raising=False)
    monkeypatch.setattr(axp, "_discover_target", _discover, raising=False)
    monkeypatch.setattr("websockets.connect", _connect, raising=False)

    if acted is not None:
        from app.executor import driver as drv

        class _NeverDriver(drv.TrajectoryDriver):
            name = "never"

            async def move_and_act(self, *, browser_url, request, tab_id=None, tab_url=None):
                acted.append(request.action_id)
                return drv.ExecResult(ok=True, driver="never", action_id=request.action_id)

        # /execute imports get_driver INSIDE the handler, so the patch has to land on the source
        # module, not on main_server's namespace.
        monkeypatch.setattr(drv, "get_driver", lambda *_a, **_k: _NeverDriver())


def test_two_identical_controls_refuse_rather_than_pick_the_first():
    """THE bug, pinned. Workday's applyManually renders two file inputs alike in every attribute;
    `input[type=file]` matched both, took the first, and put the resume in the optional Attachments
    box three times while the required Resume/CV uploader stayed empty — each attempt reported ok."""
    def check(monkeypatch):
        wire(monkeypatch, {"matched": 2,
                           "candidates": [{"question": "Attachments", "source": "section-only"},
                                          {"question": "Resume/CV", "source": "section-only"}]})
        node, why, tgt = asyncio.run(ms._resolve_node_by_selector(
            "http://b", None, None, "input[type=file]"))
        assert node is None                      # never a coin flip
        assert "ambiguous" in why
        # The refusal names what it saw, so the caller can scope the next attempt instead of retrying.
        assert "Attachments" in why and "Resume/CV" in why
    with pytest.MonkeyPatch.context() as mp:
        check(mp)


def test_scoping_by_section_resolves_the_one_that_answers_the_question():
    with pytest.MonkeyPatch.context() as mp:
        wire(mp, {"matched": 1, "path": "body > div:nth-child(2) > input:nth-child(1)",
                  "section": "Resume/CV",
                  "target": {"question": "Upload a file (5MB max)", "source": "proximity",
                             "section": "Resume/CV"}})
        node, why, tgt = asyncio.run(ms._resolve_node_by_selector(
            "http://b", None, None, "input[type=file]", within="Resume/CV"))
        assert node == 4242
        assert tgt["question"] == "Upload a file (5MB max)"
        assert "Upload a file" in why            # the act says which question it is answering


def test_a_mismatched_target_refuses_and_never_acts():
    """The confirmation half. The page says this control asks X, the caller means Y — so nothing
    is typed, clicked or uploaded. An act that answers the wrong question is worse than no act."""
    acted: list[str] = []
    with pytest.MonkeyPatch.context() as mp:
        wire(mp, {"matched": 1, "mismatch": True, "expected": "Resume/CV",
                  "target": {"question": "Attachments", "source": "section-only",
                             "section": "Attachments"}}, acted=acted)
        out = asyncio.run(ms.execute_action(ms.ExecuteRequest(
            action_id="upload", target_bbox={}, selector="input[type=file]",
            expect_question="Resume/CV", files=["/tmp/GM_Resume.pdf"])))
    assert out["outcome"] == ms.Outcome.NOT_FOUND
    assert "TARGET MISMATCH" in out["detail"]
    assert out["target_question"]["question"] == "Attachments"
    assert acted == [], "a mismatched target must not reach the driver at all"


def test_an_ambiguous_selector_does_not_fall_back_to_a_passed_node_id():
    """The coin flip wearing a different hat: refusing the selector but acting on whatever
    backend_node_id the caller happened to carry would reintroduce the same wrong-target act."""
    acted: list[str] = []
    with pytest.MonkeyPatch.context() as mp:
        wire(mp, {"matched": 3, "candidates": []}, acted=acted)
        out = asyncio.run(ms.execute_action(ms.ExecuteRequest(
            action_id="click", target_bbox={}, selector="input[type=file]",
            backend_node_id=99)))
    assert out["outcome"] == ms.Outcome.NOT_FOUND
    assert acted == []


def test_an_act_with_no_expectation_still_reports_what_it_answered():
    """No assertion is not the same as no correlation: the question rides out on every act, so the
    journal can learn which control answers what even when nobody stated an expectation."""
    acted: list[str] = []
    with pytest.MonkeyPatch.context() as mp:
        wire(mp, {"matched": 1, "path": "#resume", "section": "Resume/CV",
                  "target": {"question": "Upload a file (5MB max)", "source": "proximity",
                             "section": "Resume/CV"}}, acted=acted)
        out = asyncio.run(ms.execute_action(ms.ExecuteRequest(
            action_id="upload", target_bbox={}, selector="#resume",
            files=["/tmp/GM_Resume.pdf"])))
    assert out["outcome"] == ms.Outcome.OK
    assert out["target_question"]["question"] == "Upload a file (5MB max)"
    assert acted == ["upload"]


def test_the_shared_tells_are_the_same_definition_in_census_and_resolver():
    """One definition, or the address book drifts from the action. Both blobs must carry
    __questionOf — and the placeholder must have actually substituted (an unsubstituted blob is a
    page-side SyntaxError, which fails as 'no node matching' and looks like a stale recipe)."""
    from app.js_common import WIDGET_TELLS_JS
    from app.protocols import SCAN_REQUIRED_JS
    assert "__questionOf" in WIDGET_TELLS_JS and "__sameQuestion" in WIDGET_TELLS_JS
    for blob in (ms._RESOLVE_SCOPED_JS, SCAN_REQUIRED_JS):
        assert "__questionOf" in blob
        assert "__WIDGET_TELLS__" not in blob


def test_upload_is_confirmed_by_the_widget_when_the_input_was_reset():
    """The false negative that produced last night's wrong diagnosis. An INGESTING uploader POSTs
    the file and clears the input, so `files.length` returns to 0 ON SUCCESS. The rendered filename
    is the witness; a files-only check calls a completed upload `not_staged`."""
    from app.executor import driver as drv

    class _CDP:
        def __init__(self, witness):
            self.witness = witness
            self.sent: list[str] = []

        async def send(self, method, params=None):
            self.sent.append(method)
            if method == "DOM.resolveNode":
                return {"object": {"objectId": "obj-1"}}
            if method == "Runtime.callFunctionOn":
                fn = (params or {}).get("functionDeclaration", "")
                if "dispatchEvent" in fn:
                    return {"result": {"value": 1}}
                return {"result": {"value": self.witness}}
            return {}

    req = ActionRequest = drv.ActionRequest(
        action_id="upload", target_bbox={}, backend_node_id=5,
        files=["/Users/x/assets/documents/GM_Resume.pdf"])

    cdp = _CDP({"files": 0, "rendered": True, "error": "",
                "scope": "GM_Resume.pdf 111.32 KB Successfully Uploaded!"})
    assert asyncio.run(drv.DirectDriver()._element_act(cdp, req)) == "upload"
    assert "DOM.setFileInputFiles" in cdp.sent

    # …and a genuine refusal from the uploader is reported as one, not polled over ten times.
    cdp = _CDP({"files": 0, "rendered": False, "error": "too large", "scope": "File too large"})
    assert asyncio.run(drv.DirectDriver()._element_act(cdp, req)).startswith("upload:rejected")
