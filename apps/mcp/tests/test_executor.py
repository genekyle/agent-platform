"""Phase 5 tests — executor drivers (coord math, factory, record-only).

DirectDriver's live click needs a browser, so it's not exercised here; the
coordinate conversion it relies on is tested directly.

Run from apps/mcp:  ../../.venv/bin/python -m pytest tests/test_executor.py -q
"""

import asyncio

from app.executor import driver as drv
from app.executor.driver import ActionRequest, DirectDriver, get_driver, target_css_point
from app.executor.record_only import RecordOnlyDriver


def test_target_css_point_divides_by_dpr():
    # screenshot-px bbox center is (150, 220); at DPR 2 the CSS point is half that.
    bbox = {"x": 100, "y": 200, "width": 100, "height": 40}
    assert target_css_point(bbox, 2.0) == (75.0, 110.0)
    assert target_css_point(bbox, 1.0) == (150.0, 220.0)
    assert target_css_point(bbox, 0) == (150.0, 220.0)  # dpr 0 guarded -> treated as 1


def test_factory_defaults_and_choices():
    import os
    os.environ.pop("EXECUTOR_DRIVER", None)
    from app.executor.humanized import HumanizedDriver
    # humanized (trusted clicks + wiggle + human scroll) is the system-wide default for real driving
    assert isinstance(get_driver(), HumanizedDriver)
    assert get_driver().name == "humanized"
    assert isinstance(get_driver("direct"), DirectDriver)     # the robotic baseline, explicit only
    assert isinstance(get_driver("record_only"), RecordOnlyDriver)
    assert isinstance(get_driver("nonsense"), HumanizedDriver)  # unknown -> the humanized default


def test_record_only_logs_and_does_not_execute(tmp_path, monkeypatch):
    monkeypatch.setattr("app.executor.record_only.ARTIFACTS_DIR", tmp_path)
    req = ActionRequest(action_id="click", target_bbox={"x": 0, "y": 0, "width": 20, "height": 20},
                        backend_node_id=42, device_scale_factor=2.0)
    res = asyncio.run(RecordOnlyDriver().move_and_act(browser_url="http://x", request=req, tab_url="http://t"))
    assert res.ok and res.driver == "record_only" and res.extra["executed"] is False
    log = tmp_path / "cache" / "executor_intents.jsonl"
    assert log.exists()
    row = log.read_text().strip()
    assert '"executed": false' in row and '"backend_node_id": 42' in row


def test_direct_driver_is_a_trajectory_driver():
    # contract: same interface, swappable without changing the selector
    assert isinstance(DirectDriver(), drv.TrajectoryDriver)
    assert isinstance(RecordOnlyDriver(), drv.TrajectoryDriver)


def test_minimum_jerk_path():
    from app.executor.minimum_jerk import MinimumJerkDriver, min_jerk_points
    pts = min_jerk_points((0, 0), (100, 50), steps=20)
    assert len(pts) == 21
    assert pts[0] == (0, 0)
    assert pts[-1] == (100, 50)            # ends exactly on target
    xs = [p[0] for p in pts]
    assert xs == sorted(xs)                 # monotonic progress (no backtracking)
    # subclasses DirectDriver -> swappable; factory returns it on request
    assert isinstance(MinimumJerkDriver(), DirectDriver)
    assert isinstance(get_driver("minimum_jerk"), MinimumJerkDriver)


def test_minimum_jerk_off_by_default():
    import os
    os.environ.pop("EXECUTOR_DRIVER", None)
    assert get_driver().name == "humanized"  # default is humanized; min-jerk is opt-in only


# --- humanized body: path is randomized, masks the robotic signature, lands on target ---
def test_humanized_path_starts_and_ends_on_target():
    import random
    from app.executor.humanized import humanized_path
    start, end = (0.0, 0.0), (400.0, 120.0)
    pts = humanized_path(start, end, random.Random(7))
    assert len(pts) > 12
    # settles onto the true target at the end (within a px)
    assert abs(pts[-1][0] - end[0]) < 1.0 and abs(pts[-1][1] - end[1]) < 1.0


def test_humanized_path_is_not_a_straight_line():
    import random
    from app.executor.humanized import humanized_path
    pts = humanized_path((0.0, 0.0), (300.0, 0.0), random.Random(3))
    # a straight horizontal move would keep y==0; wiggle must push some points off the axis
    assert any(abs(y) > 0.5 for _, y in pts)


def test_humanized_path_varies_run_to_run():
    import random
    from app.executor.humanized import humanized_path
    a = humanized_path((0.0, 0.0), (250.0, 90.0), random.Random(1))
    b = humanized_path((0.0, 0.0), (250.0, 90.0), random.Random(2))
    assert a != b  # randomized proportions → different traces


def test_factory_returns_humanized():
    from app.executor.humanized import HumanizedDriver
    assert isinstance(get_driver("humanized"), HumanizedDriver)


# --- upload action: sets files on a <input type=file> via DOM.setFileInputFiles (no click) ---
def test_element_act_upload_sets_files():
    from app.executor.driver import ActionRequest, DirectDriver

    sent = []

    class FakeCDP:
        async def send(self, method, params=None):
            sent.append((method, params or {}))
            if method == "DOM.resolveNode":
                return {"object": {"objectId": "obj-1"}}
            # The post-set confirmation asks the UPLOADER, not just the FileList. A plain input
            # keeps what you set (`at_node`); an ingesting uploader POSTs and resets it, and
            # answers with a rendered filename instead. Either witness is a pass and the driver
            # says which one — a files.length-only rule called a COMPLETED Workday upload
            # `not_staged` (live 2026-08-12).
            if method == "Runtime.callFunctionOn":
                fn = (params or {}).get("functionDeclaration", "")
                if "dispatchEvent" in fn:
                    return {"result": {"value": 2}}
                return {"result": {"value": {"files": 2, "at_node": True, "rendered": False,
                                             "error": ""}}}
            return {"result": {"value": {}}}

    req = ActionRequest(action_id="upload", target_bbox={}, backend_node_id=42,
                        files=["/abs/a.jpg", "/abs/b.jpg"])
    mode = asyncio.run(DirectDriver()._element_act(FakeCDP(), req))
    assert mode == "upload"
    up = [(m, p) for m, p in sent if m == "DOM.setFileInputFiles"]
    assert len(up) == 1
    assert up[0][1]["backendNodeId"] == 42
    assert up[0][1]["files"] == ["/abs/a.jpg", "/abs/b.jpg"]
    # upload must NOT click or move the mouse (file dialog can't be driven)
    assert not any(m == "Input.dispatchMouseEvent" for m, _ in sent)


# --- the dropdown matcher, tested as the string we actually ship ------------------------
def _native_select_js():
    """The EXACT functionDeclaration `_select_option` sends to the page for a native <select>.

    Captured from the driver rather than retyped: a test that re-implements the JS proves the
    re-implementation works. This one runs the shipped string.
    """
    import asyncio

    from app.executor.humanized import HumanizedDriver

    sent = {}

    class _FakeCDP:
        async def send(self, method, params=None):
            sent["fn"] = (params or {}).get("functionDeclaration")
            return {"result": {"value": "native"}}   # stop after the first call

    asyncio.run(HumanizedDriver()._select_option(_FakeCDP(), "obj-1", "United States"))
    return sent["fn"]


def _run_native_select(option_texts, value):
    """Run that JS in node against a stub <select>, and report which option it chose."""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:                                     # pragma: no cover - environment-dependent
        import pytest
        pytest.skip("node not available")

    script = (
        "const fn = " + _native_select_js() + ";\n"
        "const opts = " + json.dumps(option_texts) + ".map(t => ({text: t, value: t}));\n"
        "const sel = {tagName: 'SELECT', options: opts, value: null,\n"
        "             dispatchEvent(){ return true; }};\n"
        "const verdict = fn.call(sel, " + json.dumps(value) + ");\n"
        "console.log(JSON.stringify({verdict, chosen: sel.value}));\n"
    )
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


def test_a_dropdown_prefers_the_exact_option_over_one_that_merely_contains_it():
    """SAP's country list really does carry both of these. Substring-only matching picked the
    right one purely because the exact option sorts first — order is not a guarantee we have, and
    the failure is a job application silently filed against the wrong country."""
    countries = ["United Arab Emirates", "United Kingdom", "United States",
                 "United States Minor Outlying Islands"]
    got = _run_native_select(countries, "United States")
    assert got["chosen"] == "United States"
    assert got["verdict"] == "native"

    # The order that used to decide it. Exact match must still win when it sorts LAST.
    got = _run_native_select(list(reversed(countries)), "United States")
    assert got["chosen"] == "United States"


def test_a_substring_match_still_works_but_says_so():
    # Not a failure — the stored answer is legitimately shorter than the option's own label. But
    # "the value we were given is not what the option is called" is worth seeing in the verdict.
    got = _run_native_select(["United States of America", "Canada"], "United States")
    assert got["chosen"] == "United States of America"
    assert got["verdict"] == "native_contains"


def test_a_dropdown_that_cannot_find_its_option_says_so_rather_than_setting_nothing():
    got = _run_native_select(["Canada", "Mexico"], "United States")
    assert got["verdict"] == "native_notfound"
    assert got["chosen"] is None


def test_upload_that_no_witness_confirms_is_not_staged():
    """`DOM.setFileInputFiles` not raising means the COMMAND was accepted, nothing more.

    The rule this pins has survived a correction. It used to read "files.length is the verdict",
    which was HALF right: an ingesting uploader resets the input on SUCCESS, so files.length==0
    also describes a completed upload, and the rule produced a false `not_staged` over three
    Workday uploads the page had visibly accepted (live 2026-08-12). What survives is the spirit:
    an upload NEITHER witness confirms — the node does not hold it and the widget does not show
    it — must never report ok."""
    from app.executor.driver import ActionRequest, DirectDriver

    class FakeCDP:
        async def send(self, method, params=None):
            if method == "DOM.resolveNode":
                return {"object": {"objectId": "obj-1"}}
            if method == "Runtime.callFunctionOn":
                fn = (params or {}).get("functionDeclaration", "")
                if "dispatchEvent" in fn:
                    return {"result": {"value": 0}}
                # empty input AND nothing rendered: the file went nowhere
                return {"result": {"value": {"files": 0, "at_node": False, "rendered": False,
                                             "error": ""}}}
            return {"result": {"value": {}}}

    req = ActionRequest(action_id="upload", target_bbox={}, backend_node_id=42,
                        files=["/abs/a.pdf"])
    mode = asyncio.run(DirectDriver()._element_act(FakeCDP(), req))
    assert mode.startswith("upload:not_staged")
    assert "rendered=no" in mode


# --- cross-origin frames: the centre comes from CDP, never from the in-page frameElement walk ---
#
# Regression for the iCIMS VEVRAA form (live 2026-08-12). Inside a cross-origin frame
# `window.frameElement` is NULL rather than an error, so the in-page walk returned FRAME-LOCAL
# coordinates and reported `framed:false`. The trusted click went 182px wide of the radio, hit
# nothing, and /execute answered ok. The page's own arithmetic cannot cross that boundary;
# `DOM.getBoxModel` can, because the browser does the translation.
def _framed_cdp(box_quad, record):
    class FakeCDP:
        async def send(self, method, params=None):
            record.append((method, params or {}))
            if method == "DOM.resolveNode":
                return {"object": {"objectId": "obj-1"}}
            if method == "DOM.getBoxModel":
                if box_quad is None:
                    raise RuntimeError("DOM.getBoxModel: Could not compute box model")
                return {"model": {"content": box_quad}}
            return {"result": {"value": {"framesScrolled": 0}}}
    return FakeCDP()


def test_element_act_measures_centre_with_box_model_not_page_arithmetic():
    from app.executor.driver import ActionRequest, DirectDriver

    sent = []
    # The live numbers: frame-local centre (25.5, 574) vs the true page centre (45.5, 392).
    quad = [39.0, 385.5, 52.0, 385.5, 52.0, 398.5, 39.0, 398.5]
    req = ActionRequest(action_id="click", target_bbox={"x": 0, "y": 0, "width": 0, "height": 0},
                        backend_node_id=3782)
    asyncio.run(DirectDriver()._element_act(_framed_cdp(quad, sent), req))

    # The scroll walk must no longer be asked for coordinates — only for scrolling.
    walks = [p for m, p in sent if m == "Runtime.callFunctionOn"
             and "scrollIntoView" in p.get("functionDeclaration", "")]
    assert walks, "the element must still be scrolled into view"
    assert "getBoundingClientRect" not in walks[0]["functionDeclaration"], \
        "measuring in-page is exactly what breaks across a cross-origin frame"

    # And the centre used is the browser's, in page space.
    assert ("DOM.getBoxModel", {"backendNodeId": 3782}) in sent


def test_node_centre_is_the_box_model_quad_centre():
    from app.executor.driver import DirectDriver

    quad = [39.0, 385.5, 52.0, 385.5, 52.0, 398.5, 39.0, 398.5]
    pt = asyncio.run(DirectDriver()._node_centre(_framed_cdp(quad, []), 3782))
    assert pt == {"x": 45.5, "y": 392.0}


def test_node_centre_returns_empty_when_the_node_has_no_box():
    """No box → no point. The caller falls back to the native click, which is honest;
    inventing a coordinate is how a click lands on nothing and still reports ok."""
    from app.executor.driver import DirectDriver

    assert asyncio.run(DirectDriver()._node_centre(_framed_cdp(None, []), 99)) == {}
    assert asyncio.run(DirectDriver()._node_centre(_framed_cdp([1.0, 2.0], []), 99)) == {}
    assert asyncio.run(DirectDriver()._node_centre(_framed_cdp([1.0], []), None)) == {}


# --- the set_text ceiling: cadence is bounded, correctness is not (2026-08-20) ---
def test_long_text_types_a_prefix_and_always_lands_whole():
    """A 640-char answer used to spend ~90s in the per-char loop and the HTTP timeout killed the
    coroutine BEFORE the authoritative write — the one statement that guarantees the field holds
    exactly `text` was the one the timeout skipped, leaving partial text (live 2026-08-19, the
    ~350-char practical ceiling). The cadence now comes from a bounded prefix; the native-setter
    write always runs, in constant time, with the FULL string."""
    import asyncio as aio
    from unittest.mock import patch

    from app.executor.humanized import HumanizedDriver

    long_text = "x" * 640
    calls = {"keys": 0, "set_value": None}

    class FakeCDP:
        async def send(self, method, params=None):
            p = params or {}
            if method == "Input.dispatchKeyEvent" and p.get("type") == "keyDown":
                calls["keys"] += 1
            if method == "Runtime.callFunctionOn":
                calls["set_value"] = (p.get("arguments") or [{}])[0].get("value")
                return {"result": {"value": calls["set_value"]}}
            return {"result": {"value": None}}

    async def _nosleep(_secs):
        return None

    drv = HumanizedDriver(seed=7)
    with patch("app.executor.humanized.asyncio.sleep", _nosleep):
        asyncio.run(drv._human_type(FakeCDP(), long_text, object_id="obj-1"))

    assert calls["keys"] <= HumanizedDriver._TYPE_CADENCE_MAX_CHARS
    assert calls["set_value"] == long_text, "the authoritative write must carry the whole text"


def test_key_fields_carry_code_and_keycode_so_a_handler_can_hear_them():
    """A keyDown with only `text`/`key` has `code == ""` and `keyCode == 0`, and a widget that
    switches on either never fires. Measured live 2026-08-25 on Workday's segmented date, whose
    wrapper exposes onKeyDown and NO onChange/onInput — `clear` and `submit` moved it because they
    always sent the full set, and `type` did not because it never had."""
    from app.executor.humanized import HumanizedDriver as H
    assert H._key_fields("8") == {"code": "Digit8", "windowsVirtualKeyCode": 56}
    assert H._key_fields("g") == {"code": "KeyG", "windowsVirtualKeyCode": 71}
    assert H._key_fields("G") == {"code": "KeyG", "windowsVirtualKeyCode": 71}
    assert H._key_fields("/") == {"code": "Slash", "windowsVirtualKeyCode": 191}
    # Unmapped degrades to the text-only event rather than guessing: a WRONG code is worse than an
    # absent one, because a handler filtering on it acts on the wrong key instead of ignoring it.
    assert H._key_fields("é") == {}
    assert H._key_fields("→") == {}


def test_keys_only_is_off_by_default_and_reaches_the_request():
    """`keys_only` skips the authoritative value write — right for a widget that composes from key
    handling, wrong for every ordinary field, where a dropped keystroke is the likelier failure."""
    req = ActionRequest(action_id="type", target_bbox={}, value="08252026")
    assert req.keys_only is False, "the write stays the default safety net"
    assert ActionRequest(action_id="type", target_bbox={}, value="x", keys_only=True).keys_only
