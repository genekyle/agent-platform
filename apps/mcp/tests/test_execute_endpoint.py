"""Pin /execute's OUTCOME gate at the endpoint, not the driver.

The upload-failure gate shipped dead (2026-08-11 → 2026-08-20): it read `result.action_id` —
which always echoes the request's literal `"upload"` — for a verdict that only ever appears in
`result.extra["mode"]`. Every upload returned OK, including rejected and unstaged ones, which is
the exact "required upload read as done over a page still demanding the file" incident the gate
was written for. The driver-level test asserted on `_element_act`'s return value and passed the
whole time; ONLY a test that calls the endpoint and reads the response could have caught it.
These do that, with the driver faked at the seam `execute_action` actually uses.
"""
import asyncio

import pytest

from app.executor.driver import ExecResult


@pytest.fixture()
def journal_dir(tmp_path, monkeypatch):
    # The @journaled wrapper writes the intent journal; keep it out of the real corpus.
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    return tmp_path


class _FakeDriver:
    name = "humanized"

    def __init__(self, mode: str):
        self._mode = mode

    async def move_and_act(self, **_kw) -> ExecResult:
        # ok=True is the trap under test: the driver's `ok` means "mechanism completed",
        # and a not-staged upload completes mechanically. The verdict is the mode.
        return ExecResult(ok=True, driver=self.name, action_id="upload",
                          css_point=(10.0, 10.0), extra={"mode": self._mode})


def _execute_upload(monkeypatch, mode: str) -> dict:
    from app.executor import driver as driver_mod
    from app import main_server

    monkeypatch.setattr(driver_mod, "get_driver", lambda name=None: _FakeDriver(mode))
    body = main_server.ExecuteRequest(
        action_id="upload", target_bbox={"x": 0, "y": 0, "width": 10, "height": 10},
        backend_node_id=42, files=["/abs/resume.pdf"],
        tab_url="https://example.myworkdayjobs.com/apply", driver="humanized")
    return asyncio.run(main_server.execute_action(body))


def test_an_unstaged_upload_is_not_staged_at_the_endpoint(monkeypatch, journal_dir):
    out = _execute_upload(monkeypatch, "upload:not_staged:files=0 rendered=no")
    assert out["outcome"] == "not_staged"
    assert out["ok"] is False
    # The reason must survive into the journaled detail — a failure reason that dies in a
    # local variable cannot be learned from.
    assert "upload:not_staged" in out["detail"]
    assert out["mode"].startswith("upload:not_staged")


def test_a_rejected_upload_is_not_staged_at_the_endpoint(monkeypatch, journal_dir):
    # `upload:rejected:<why>` is the uploader refusing the file (size/type) — the node did
    # not accept it, so it is NOT_STAGED exactly like the silent case, with the why kept.
    out = _execute_upload(monkeypatch, "upload:rejected:too large")
    assert out["outcome"] == "not_staged"
    assert out["ok"] is False
    assert "too large" in out["detail"]


def test_a_landed_upload_is_still_ok(monkeypatch, journal_dir):
    out = _execute_upload(monkeypatch, "upload")
    assert out["outcome"] == "ok"
    assert out["ok"] is True
