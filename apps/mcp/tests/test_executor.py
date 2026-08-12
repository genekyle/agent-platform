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
            # The post-set confirmation reads files.length off the node — the upload is only
            # `ok` when the input actually holds the files (Workday accepted the command and
            # kept an empty FileList, 2026-08-11).
            if method == "Runtime.callFunctionOn":
                return {"result": {"value": 2}}
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


def test_upload_that_the_node_did_not_accept_is_not_staged():
    """`DOM.setFileInputFiles` not raising means the COMMAND was accepted, not that the input
    holds a file: Workday's uploader took the call and left files.length at 0 while /execute
    reported a clean ok over a page still demanding the upload (live 2026-08-11)."""
    from app.executor.driver import ActionRequest, DirectDriver

    class FakeCDP:
        async def send(self, method, params=None):
            if method == "DOM.resolveNode":
                return {"object": {"objectId": "obj-1"}}
            if method == "Runtime.callFunctionOn":
                return {"result": {"value": 0}}      # the input stayed empty
            return {"result": {"value": {}}}

    req = ActionRequest(action_id="upload", target_bbox={}, backend_node_id=42,
                        files=["/abs/a.pdf"])
    mode = asyncio.run(DirectDriver()._element_act(FakeCDP(), req))
    assert mode == "upload:not_staged:files=0"
