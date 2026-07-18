"""Tests for the drive lock — the single-machine "keyboard owned by CDP" latch and its API."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import drive_lock
from routers import drive_lock as drive_lock_router


@pytest.fixture(autouse=True)
def _reset():
    drive_lock.release()          # never let a test leak the global latch into the next
    yield
    drive_lock.release()


def test_engage_release_and_state():
    assert drive_lock.state()["locked"] is False
    st = drive_lock.engage("workday teacher drive", holder="indeed_apply")
    assert st["locked"] is True and st["reason"] == "workday teacher drive"
    assert st["holder"] == "indeed_apply" and st["since"]
    assert drive_lock.state()["locked"] is True
    assert drive_lock.release()["locked"] is False
    assert drive_lock.state()["since"] is None


def test_engage_is_idempotent_and_keeps_since():
    first = drive_lock.engage("a")
    again = drive_lock.engage("b")            # re-engage refreshes reason, keeps the original since
    assert again["since"] == first["since"]
    assert again["reason"] == "b"


def test_driving_context_manager_releases_on_exception():
    with pytest.raises(RuntimeError):
        with drive_lock.driving("live drive", holder="workday_apply"):
            assert drive_lock.state()["locked"] is True
            raise RuntimeError("drive blew up mid-step")
    # the keyboard is handed back even though the drive crashed
    assert drive_lock.state()["locked"] is False


def test_api_get_and_post_roundtrip():
    app = FastAPI()
    app.include_router(drive_lock_router.router)
    client = TestClient(app)

    assert client.get("/api/drive_lock").json()["locked"] is False
    engaged = client.post("/api/drive_lock", json={"locked": True, "reason": "teacher drive",
                                                   "holder": "workday_apply"}).json()
    assert engaged["locked"] is True and engaged["holder"] == "workday_apply"
    assert client.get("/api/drive_lock").json()["locked"] is True
    released = client.post("/api/drive_lock", json={"locked": False}).json()
    assert released["locked"] is False
