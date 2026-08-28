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


# --- frames: the top document is not the page ---------------------------------------------
def test_the_selector_resolver_searches_same_origin_frames():
    """iCIMS renders its ENTIRE apply flow inside `icims_content_iframe`, so a top-document query
    finds nothing and the failure reads exactly like a stale recipe — "could not fill 'first_name'
    (not_found)" over a form plainly on screen (live 2026-08-12, Odyssey Consulting). The driver
    already translated frame coordinates; the RESOLVER never looked in.

    Asserted against the JS we actually ship (jsdom has no frames worth testing, and the live drive
    is where the behaviour is proven — it resolved `#email` in iframe#icims_content_iframe)."""
    js = ms._RESOLVE_SCOPED_JS
    assert "contentDocument" in js, "the resolver must descend into frames"
    assert "ownerDocument" in js, "a path is minted and verified in the node's OWN document"
    # Cross-origin frames throw on contentDocument — that is a real boundary, caught and skipped.
    assert "catch" in js


def test_the_captcha_rail_sees_a_challenge_nested_in_a_frame():
    """THE SAFETY RAIL, and it was blind on exactly the platforms that need it. The hCaptcha guarding
    iCIMS's Candidate Profile is an iframe INSIDE the same-origin content frame, so a top-document
    query answered `hcaptcha_count: 0, blocking: false, solved: true` — a green light — over a form
    carrying two live challenges (measured 2026-08-12; after the fix, hcaptcha_count: 2).

    A rail that reports "clear" when it cannot see is worse than no rail."""
    js = ms._CHALLENGE_VISIBILITY_JS
    assert "contentDocument" in js, "the rail must walk frames"
    assert "hcaptcha" in js and "recaptcha" in js
    # Judged in the element's own view: a rect from one frame against another frame's viewport is
    # wrong in both directions.
    assert "defaultView" in js
    # Bounded descent — a cycle or a deep tree must not hang the check.
    assert "depth" in js


def test_the_captcha_rail_does_not_mistake_a_badge_for_a_checkbox():
    """The invisible reCAPTCHA's corner badge is served from the SAME anchor URL as the v2
    checkbox; the only tell is `size=invisible` in its src. Counting the badge as an unsolved
    checkbox reported blocking:true over a form nobody was challenging — and the apply ladder
    refused every crank on it (measured live 2026-08-28, Greenhouse/Bottomline: the queue's
    closest-to-done application stalled on a false positive of its own safety rail)."""
    js = ms._CHALLENGE_VISIBILITY_JS
    assert "size=invisible" in js, "anchors must exclude the passive scorer's badge"
    # The image-challenge rail stays whole — an invisible flow that DOES challenge raises a
    # bframe, and that half must not be filtered.
    assert "bframe" in js


# --- ambiguity: several controls, or one control drawn several times? --------------------------
#
# The refusal these exercise was written on 2026-08-13, when "Country" named both the country field
# and the phone-code field and the first match silently won — changing the operator's country on a
# live application. It is right about that. It is wrong about page furniture: a job posting repeats
# its Apply block top and bottom, and on 2026-08-14 that stopped a drive dead on Boston Children's
# (two `link` nodes named exactly "Apply", identical role/name/x/width/height, y 395 and 2137).
#
# The distinction the count cannot make, the page can: a link's identity is where it GOES.

import asyncio

import app.main_server as ms


def _resolve(monkeypatch, candidates, destinations, *, role=None, name="Apply"):
    """Run the resolver against a fixed candidate list and a fixed answer from the page."""
    async def fake_propose(**_kw):
        return list(candidates)
    # `_resolve_ax_node` imports the proposer inside the call, so the module it imports FROM is
    # the one to patch.
    monkeypatch.setattr("app.observer.ax_proposer.propose_ax_candidates", fake_propose,
                        raising=False)

    async def fake_same(_url, _tab, _turl, found):
        got = {destinations.get(c["backend_node_id"]) for c in found}
        return len(got) == 1 and "" not in got and None not in got
    monkeypatch.setattr(ms, "_same_destination", fake_same)
    return asyncio.run(ms._resolve_ax_node("http://x", None, None, role, name))


def test_one_apply_rendered_twice_resolves_to_the_first(monkeypatch):
    """Same destination = one action, however many times it is drawn. This is the live Boston
    Children's posting: the block appears above the description and again below it."""
    cands = [{"role": "link", "name": "Apply", "backend_node_id": 338},
             {"role": "link", "name": "Apply", "backend_node_id": 458}]
    got = _resolve(monkeypatch, cands,
                   {338: "https://jobs.bostonchildrens.org/apply/85104BR",
                    458: "https://jobs.bostonchildrens.org/apply/85104BR"})
    assert got == 338


def test_two_applies_going_different_places_still_refuse(monkeypatch):
    """The employee door and the candidate door both say "Apply" on plenty of careers sites. When
    the destinations disagree the name does not identify a control, and the refusal stands."""
    cands = [{"role": "link", "name": "Apply", "backend_node_id": 1},
             {"role": "link", "name": "Apply", "backend_node_id": 2}]
    got = _resolve(monkeypatch, cands,
                   {1: "https://careers.example.com/apply/123",
                    2: "https://internal.example.com/employee/apply/123"})
    assert got is None


def test_a_destination_that_cannot_be_read_refuses(monkeypatch):
    """Buttons carry their behaviour in a listener, not a URL. An unreadable destination is not
    evidence of sameness — any doubt keeps the refusal."""
    cands = [{"role": "button", "name": "Submit", "backend_node_id": 1},
             {"role": "button", "name": "Submit", "backend_node_id": 2}]
    got = _resolve(monkeypatch, cands, {1: "", 2: ""}, role="button", name="Submit")
    assert got is None


def test_an_unambiguous_name_never_asks_the_page(monkeypatch):
    """The common path must not pay for this. One candidate resolves without a CDP round trip."""
    called = {"n": 0}

    async def boom(*_a, **_k):
        called["n"] += 1
        return False
    monkeypatch.setattr(ms, "_same_destination", boom)

    async def fake_propose(**_kw):
        return [{"role": "link", "name": "Apply", "backend_node_id": 7}]
    monkeypatch.setattr("app.observer.ax_proposer.propose_ax_candidates", fake_propose,
                        raising=False)
    got = asyncio.run(ms._resolve_ax_node("http://x", None, None, None, "Apply"))
    assert got == 7 and called["n"] == 0


def test_a_role_the_caller_asked_for_breaks_an_ax_collapsed_tie(monkeypatch):
    """AX ROLE IS NOT DOM TAG, and an `<a>` styled as a button reports `button` exactly like a real
    one. MAPFRE's posting carries `<a class="…apply…">Apply now »</a>` beside
    `<button class="btn…">Apply now</button>`, so both collapse to the same candidate and a caller
    who asked for a LINK had their distinction discarded before the tier was built.

    This is not choosing between them — it is honouring a discrimination the caller made and AX
    erased.
    """
    cands = [{"role": "button", "name": "Apply now", "backend_node_id": 1598},
             {"role": "button", "name": "Apply now", "backend_node_id": 1869}]

    async def fake_propose(**_kw):
        return list(cands)
    monkeypatch.setattr("app.observer.ax_proposer.propose_ax_candidates", fake_propose,
                        raising=False)

    async def no_same(*_a, **_k):
        return False
    monkeypatch.setattr(ms, "_same_destination", no_same)

    tags = {1598: "A", 1869: "BUTTON"}

    async def fake_tag(_u, _t, _tu, found, want_role):
        want = ms._ROLE_TAGS.get(want_role)
        hits = [c["backend_node_id"] for c in found if tags[c["backend_node_id"]] == want]
        return hits[0] if len(hits) == 1 else None
    monkeypatch.setattr(ms, "_by_dom_tag", fake_tag)

    # The caller says "link" and gets the anchor; says "button" and gets the button.
    assert asyncio.run(ms._resolve_ax_node("http://x", None, None, "link", "Apply now")) == 1598
    assert asyncio.run(ms._resolve_ax_node("http://x", None, None, "button", "Apply now")) == 1869


def test_two_anchors_of_the_same_name_are_still_ambiguous(monkeypatch):
    """The tiebreak narrows the refusal on positive evidence and never replaces it. A role that
    cannot separate the candidates leaves them separated by nothing."""
    cands = [{"role": "link", "name": "Apply now", "backend_node_id": 1},
             {"role": "link", "name": "Apply now", "backend_node_id": 2}]

    async def fake_propose(**_kw):
        return list(cands)
    monkeypatch.setattr("app.observer.ax_proposer.propose_ax_candidates", fake_propose,
                        raising=False)

    async def no_same(*_a, **_k):
        return False
    monkeypatch.setattr(ms, "_same_destination", no_same)

    async def both_anchors(_u, _t, _tu, found, want_role):
        return None          # two A's — the tag cannot separate them either
    monkeypatch.setattr(ms, "_by_dom_tag", both_anchors)

    assert asyncio.run(ms._resolve_ax_node("http://x", None, None, "link", "Apply now")) is None


def test_the_upload_witness_tells_a_dropzone_from_a_plain_input():
    """Files sitting on a raw input a dropzone ignores read as success while the page shows an
    empty zone ("1 of 1 confirmed" over nothing, Workday/Cadence 2026-08-28). The witness now
    names an ingesting zone by the page's own words, and the driver only trusts `rendered`
    there — with the chooser-interception path as the zone's own door."""
    from app.executor import driver as dr

    assert "ingesting" in dr._UPLOAD_WITNESS_JS
    assert "drop files" in dr._UPLOAD_WITNESS_JS.lower()
    src = open(dr.__file__).read()
    assert "setInterceptFileChooserDialog" in src, "the chooser path is the dropzone's own flow"
    assert "fileChooserOpened" in src
