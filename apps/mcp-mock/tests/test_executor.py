"""Phase 5 tests — executor drivers (coord math, factory, record-only).

DirectDriver's live click needs a browser, so it's not exercised here; the
coordinate conversion it relies on is tested directly.

Run from apps/mcp-mock:  ../../.venv/bin/python -m pytest tests/test_executor.py -q
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
    assert isinstance(get_driver(), DirectDriver)
    assert isinstance(get_driver("direct"), DirectDriver)
    assert isinstance(get_driver("record_only"), RecordOnlyDriver)
    assert isinstance(get_driver("nonsense"), DirectDriver)  # unknown -> safe default


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
    assert get_driver().name == "direct"  # min-jerk is opt-in only
