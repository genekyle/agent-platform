"""Session-snapshot routes — take, list, restore, verify (SESSION 21).

Thin by design: everything real lives in `session_snapshot.py`. What these routes add is REACH —
if the teacher can curl it the operator can click it, and a recovery the operator cannot press is
a recovery that exists only in a session transcript.

**Nothing here returns a payload.** `SnapshotMeta.as_dict()` is the whole public shape; the blobs
never leave the store, and `test_session_snapshot` pins that.

The restore is deliberately a TWO-PRESS flow — restore, then verify — rather than one endpoint
that does both and reports a single cheerful `ok`. A perfect local restore is not evidence the
SERVER still honours the session, and collapsing the two would be the false success the verifier
exists to prevent.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

import session_snapshot as snap

router = APIRouter()


class CaptureBody(BaseModel):
    profile: str
    port: int
    note: str = ""


class RestoreBody(BaseModel):
    snapshot_id: str
    port: int


class PinBody(BaseModel):
    snapshot_id: str
    pinned: bool = True


@router.get("/api/session_snapshots")
def list_snapshots(profile: Optional[str] = None) -> dict[str, Any]:
    """Every snapshot, newest first. Also reports what the store is holding, because an unbounded
    set of bearer credentials is the thing you want visible rather than discovered."""
    rows = snap.list_snapshots(profile)
    return {"ok": True, "count": len(rows), "keep_per_profile": snap.KEEP_PER_PROFILE,
            "snapshots": [r.as_dict() for r in rows],
            "profiles_with_auth_vocabulary": sorted(snap.AUTH_COOKIES)}


@router.get("/api/session_snapshots/live")
def live_profiles() -> dict[str, Any]:
    """Which profiles have a live browser right now, and on which port — the answer a capture
    needs before it can be pressed.

    Reuses `browser_provisioning.find_chromes`, which reads the actual `--user-data-dir` off the
    process table. That is the same primitive the launch guard uses, and it is right for the same
    reason: a DB row does not hold a directory lock, and the recorded port is precisely what has
    been unreliable.
    """
    import browser_provisioning as bp
    from settings import settings
    root = f"{settings.training_chrome_profiles_dir.rstrip('/')}/persistent"
    out = []
    for name in sorted(snap.AUTH_COOKIES):
        procs = bp.find_chromes(user_data_dir=f"{root}/{name}")
        for p in procs:
            out.append({"profile": name, "pid": p.pid, "port": p.debug_port,
                        "user_data_dir": f"{root}/{name}"})
    return {"ok": True, "live": out, "profiles_root": root,
            # The finding that motivated the feature, surfaced rather than filed: /tmp is cleared
            # on reboot, and these logins cost a HUMAN to re-create.
            "durable": not root.startswith("/tmp") and not root.startswith("/private/tmp"),
            "warning": ("the signed-in profiles live under /tmp, which macOS clears on reboot"
                        if root.startswith(("/tmp", "/private/tmp")) else "")}


@router.post("/api/session_snapshots/capture")
async def capture(body: CaptureBody) -> dict[str, Any]:
    """Take a warm identity snapshot off a running browser.

    Warm rather than a file copy on purpose: Chrome keeps Cookies in a WAL-backed SQLite file, so
    copying it under a live browser can read torn state. This costs the session nothing and needs
    no downtime.
    """
    try:
        meta = await snap.capture_warm(port=body.port, profile=body.profile, note=body.note)
    except Exception as exc:  # noqa: BLE001 — a capture that failed must say so, not half-succeed
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "snapshot": meta.as_dict()}


@router.post("/api/session_snapshots/restore")
async def restore(body: RestoreBody) -> dict[str, Any]:
    """Put a snapshot's cookies back. Does NOT verify — see `/verify`, and the module docstring
    for why those are two presses."""
    try:
        return await snap.restore_warm(port=body.port, snapshot_id=body.snapshot_id)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


@router.post("/api/session_snapshots/verify")
def verify(body: RestoreBody) -> dict[str, Any]:
    """Ask `/auth_state` whether the restored session is actually honoured, and record the verdict.

    Three answers, and the third is the one that matters: `restored_unverified` when the probe
    could not judge — it covers indeed and linkedin only. Calling that "authenticated" is the
    false success; calling it "logged out" sends the operator to re-login a session that was fine.
    """
    from settings import settings
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.post(f"{settings.capture_server_url}/auth_state",
                            json={"browser_url": snap.browser_url_for(body.port)})
            r.raise_for_status()
            state = r.json() or {}
    except Exception as exc:  # noqa: BLE001
        state = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    verdict = snap.verify_verdict(state)
    meta = snap.record_verification(body.snapshot_id, verdict)
    ttl, found = snap.auth_ttl_s(state.get("cookies") or [], state.get("platform"))
    return {"ok": True, "verdict": verdict,
            "authenticated": verdict == snap.RESTORED_AUTHENTICATED,
            "probe_ok": bool(state.get("ok")),
            "url": state.get("url"), "platform": state.get("platform"),
            # The signal this feature lit, reported where it can be read against a real session.
            "auth_cookies_found": found,
            "auth_ttl_s": ttl, "auth_ttl_h": None if ttl is None else round(ttl / 3600, 1),
            "checked_at": time.time(),
            "snapshot": meta.as_dict() if meta else None}


@router.post("/api/session_snapshots/pin")
def pin(body: PinBody) -> dict[str, Any]:
    """Pin a snapshot as a recovery FIXTURE so retention never sweeps it.

    "Stale sessions are fixtures" given a mechanism: a snapshot of a BROKEN state is a regression
    test for recovery, and it is worth keeping past the rolling window.
    """
    meta = snap.set_pinned(body.snapshot_id, body.pinned)
    if meta is None:
        return {"ok": False, "detail": f"no snapshot {body.snapshot_id}"}
    return {"ok": True, "snapshot": meta.as_dict()}


@router.post("/api/session_snapshots/delete")
def delete(body: PinBody) -> dict[str, Any]:
    """Permanently remove a snapshot and its encrypted payload."""
    return {"ok": snap.delete_snapshot(body.snapshot_id), "snapshot_id": body.snapshot_id}
