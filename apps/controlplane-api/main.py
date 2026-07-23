import json
import os
import socket
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import asyncio
import re
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.orm import Session, selectinload

from db import Base, engine, get_db
from models import (
    ActionRegistry,
    ApplicationAnswer,
    DomainRegistry,
    ObservedJob,
    GoalRegistry,
    ModelEvalRun,
    ModelRegistry,
    PageStateRegistry,
    Run,
    Step,
    TaskRegistry,
    ScenarioRegistry,
    TrainingCapture,
    TrainingSession,
    Worker,
)
from model_lib import eval as model_eval, registry as model_registry
from schemas import (
    ModelEvalRunDetail,
    ModelEvalRunRead,
    ModelEvalRunSummary,
    ModelRead,
    DomainRead,
    DomainUpdate,
    DomainWrite,
    GoalRead,
    GoalUpdate,
    GoalWrite,
    RunRead,
    RunCreateResponse,
    ScenarioUpdate,
    ScenarioRead,
    ScenarioWrite,
    StepLeaseResponse,
    StepResultIn,
    TaskRead,
    TaskUpdate,
    TaskWrite,
    TrainingCaptureRead,
    TrainingSessionCreate,
    TrainingSessionRead,
    WorkerHeartbeatIn,
    WorkerHeartbeatResponse,
)
from settings import settings
from deps import _artifacts_dir, _session_browser_url, _slugify, utcnow
from observed_jobs import upsert_observed_jobs
from migrations import migrate_schema
from seed import (
    backfill_goal_stages,
    backfill_page_state_stages,
    seed_actions,
    seed_application_answers,
    seed_facebook_extras,
    seed_gmail_domain,
    seed_page_states,
    seed_training_registry,
)
from training import (
    build_grounding_dataset,
    build_vision_dataset,
    compare_training_targets,
    merge_training_annotation,
    read_meta,
    train_grounding_model,
    write_meta,
)

# Assets store (listing photos) — imported here so create_app() can mount it. Stub for cloud (S3).
import assets as _assets  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

# --- Routers (see docs/PLAN_main-split.md) -----------------------------------
# Domain routers extracted from main.py; the module-level `router` below holds the CORE routes not
# yet extracted into a domain module. create_app() (bottom of file) wires all of them into the app.
from routers import accounts as accounts_router  # noqa: E402
from routers import activity as activity_router  # noqa: E402
from routers import application_answers as application_answers_router  # noqa: E402
from routers import career_search as career_search_router  # noqa: E402
from routers import controller as controller_router  # noqa: E402
from routers import drive_lock as drive_lock_router  # noqa: E402
from routers import events as events_router  # noqa: E402
from routers import facebook as facebook_router  # noqa: E402
from routers import inventory as inventory_router  # noqa: E402
from routers import providers as providers_router  # noqa: E402
from routers import session_control as session_control_router  # noqa: E402
from routers import sessions as sessions_router  # noqa: E402
from routers import workspace as workspace_router  # noqa: E402

router = APIRouter()


def _resolve_chrome_binary() -> str:
    candidates = [
        settings.chrome_binary_path,
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise HTTPException(status_code=500, detail="Chrome binary not found for training session startup")


def _training_profiles_root() -> Path:
    root = Path(settings.training_chrome_profiles_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _persistent_profiles_root() -> Path:
    """Root for SHARED, surviving Chrome profiles (one dir per named profile). These are
    NOT deleted between sessions, so a supervised login persists (cookies/session stay)."""
    root = _training_profiles_root() / "persistent"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _profile_dir_for(session: TrainingSession) -> Path:
    """Where this session's Chrome keeps its user-data-dir: a shared persistent profile when
    attached (login survives), else a throwaway per-session dir (fresh every time)."""
    if session.persistent_profile:
        return _persistent_profiles_root() / _slugify(session.persistent_profile)
    return _training_profiles_root() / f"training-session-{session.id}"


def _next_training_port(db: Session) -> int:
    """Pick a free debugging port. We skip ports claimed by active/starting DB rows AND any port
    that answers CDP *right now* — because a row can be stale (API restarted, status never
    updated) while its Chrome is still very much alive. Trusting DB status alone is exactly how a
    fresh launch could seize a live session's port and disturb it (e.g. the persistent Facebook
    browser that drifted to 9327). The live probe is the honest, collision-proof check."""
    import channel_browser

    active_port_set = {
        value
        for value in db.scalars(
            select(TrainingSession.chrome_debug_port).where(
                TrainingSession.chrome_debug_port.is_not(None),
                TrainingSession.status.in_(["active", "starting"]),
            )
        ).all()
        if value is not None
    }
    port = settings.training_chrome_port_start
    # Bound the walk so a pathological host can't spin forever; far more headroom than we'd ever
    # run concurrently. A refused connection returns immediately, so probing dead ports is cheap.
    for _ in range(200):
        if port not in active_port_set and not channel_browser.cdp_reachable(port, timeout=0.5):
            return port
        port += 1
    return port


def _session_action_hint(goal: GoalRegistry) -> str:
    return str((goal.action_type_hints or ["any"])[0])


# Chrome launch flags that suppress browser-chrome popovers (save password,
# translate, notifications, sync, autofill, infobars). These prompts get rendered
# above the page surface and are missed by Page.captureScreenshot — so leaving
# them on means a model can't see what the user sees. For v0 (vision_element_grounding,
# which only needs page content), blocking them is the right tradeoff. The OS-level
# capture path stays open for the day we need to train on browser-chrome interactions.
_TRAINING_CHROME_FLAGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-features=Translate,PasswordManagerOnboarding,AutofillEnableAccountWalletStorage,AutofillServerCommunication",
    "--disable-save-password-bubble",
    "--disable-translate",
    "--disable-notifications",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-sync",
    "--disable-infobars",
    "--disable-prompt-on-repost",
    "--no-pings",
    # Indeed (and most ATS) open the apply flow via window.open in a new tab; Chrome's
    # popup blocker stops it when the click isn't a trusted gesture. Allow it so apply
    # tabs open. (We still reach apply pages via on-page clicks, never URL-jumps.)
    "--disable-popup-blocking",
    "--password-store=basic",  # don't talk to macOS Keychain
]


def _deep_merge_dict(base: dict, overlay: dict) -> dict:
    """Recursive dict merge — overlay keys win, nested dicts merge instead of replace."""
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge_dict(base[key], value)
        else:
            base[key] = value
    return base


def _seed_training_chrome_prefs(profile_dir: Path) -> None:
    """Write the prefs that disable the same interruptions as the launch flags.

    Launch flags + Preferences are redundant on purpose — some interruptions
    respect one path but not the other across Chrome versions. Belt and suspenders.

    Chrome reads Preferences once at launch, so this must run before subprocess.Popen.
    """
    default_dir = profile_dir / "Default"
    default_dir.mkdir(parents=True, exist_ok=True)
    prefs_path = default_dir / "Preferences"

    prefs_overlay = {
        "credentials_enable_service": False,
        "credentials_enable_autosignin": False,
        "profile": {
            "password_manager_enabled": False,
            "default_content_setting_values": {
                "notifications": 2,  # 2 = block
                "geolocation": 2,
            },
        },
        "translate": {"enabled": False},
        "translate_blocked_languages": ["*"],
        "autofill": {
            "profile_enabled": False,
            "credit_card_enabled": False,
        },
        "browser": {
            "show_update_promotion_info_bar": False,
        },
    }

    existing: dict = {}
    if prefs_path.exists():
        try:
            existing = json.loads(prefs_path.read_text())
        except Exception:
            existing = {}
    merged = _deep_merge_dict(existing, prefs_overlay)
    prefs_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")


def _launch_training_chrome(db: Session, session: TrainingSession) -> TrainingSession:
    import channel_browser

    # ATTACH if the browser is genuinely alive (CDP answers) — not merely if we once stored a pid.
    # A stale "active" row pointing at a dead port is what made connecting feel impossible.
    if channel_browser.cdp_reachable(session.chrome_debug_port):
        return session

    # A persistent profile dir can back only ONE live Chrome at a time (Chrome locks it). Reap any
    # sibling sessions on the same profile whose browser is actually DEAD (so a stale row can't
    # falsely block us); only a genuinely-alive sibling is a real conflict.
    if session.persistent_profile:
        siblings = db.scalars(
            select(TrainingSession).where(
                TrainingSession.persistent_profile == session.persistent_profile,
                TrainingSession.status.in_(["active", "starting"]),
                TrainingSession.id != session.id,
            )
        ).all()
        reaped = False
        for other in siblings:
            if channel_browser.cdp_reachable(other.chrome_debug_port):
                raise HTTPException(
                    status_code=409,
                    detail=f"Persistent profile '{session.persistent_profile}' is already in use by "
                           f"a live session #{other.id}. Stop it first.",
                )
            if other.protected:
                # Human-owned rows are never auto-reaped, even when their browser looks dead — we
                # leave the record intact and let the operator decide. (A dead profile isn't locked,
                # so this doesn't block the relaunch.)
                continue
            other.status = "stopped"
            other.chrome_stopped_at = utcnow()
            reaped = True
        if reaped:
            db.commit()

    port = _next_training_port(db)
    profile_dir = _profile_dir_for(session)
    profile_dir.mkdir(parents=True, exist_ok=True)
    _seed_training_chrome_prefs(profile_dir)
    process = subprocess.Popen(
        [
            _resolve_chrome_binary(),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            *_TRAINING_CHROME_FLAGS,
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    now = utcnow()

    session.status = "active"
    session.browser_session_id = f"training-session-{session.id}"
    session.chrome_debug_port = port
    session.chrome_user_data_dir = str(profile_dir)
    session.chrome_process_pid = process.pid
    session.chrome_started_at = now
    session.chrome_stopped_at = None
    session.started_at = session.started_at or now
    session.completed_at = None
    db.commit()
    db.refresh(session)

    # Wait until the CDP endpoint actually answers before returning, so callers get a browser
    # that's truly ready to drive (not one still starting up).
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline and not channel_browser.cdp_reachable(port):
        time.sleep(0.4)
    return session


def _stop_training_chrome(session: TrainingSession) -> None:
    if not session.chrome_process_pid:
        return
    try:
        os.kill(session.chrome_process_pid, 15)
    except ProcessLookupError:
        pass


def _training_annotation_from_capture(capture: TrainingCapture) -> dict:
    return {
        "version": "grounding_v1",
        "review_status": capture.review_status,
        "domain_id": capture.domain_id,
        "goal_id": capture.goal_id,
        "task_id": capture.task_id,
        "action_type_hint": capture.action_type_hint,
        "capture_profile": capture.capture_profile,
        "notes": capture.notes,
        "positive_candidate_id": capture.positive_candidate_id,
        "label_source": capture.label_source,
        "label_confidence": capture.label_confidence,
        "verified_at": capture.verified_at.isoformat() if capture.verified_at else None,
        "rejected_candidate_ids": capture.rejected_candidate_ids or [],
        "candidate_labels": capture.candidate_labels or {},
        "approved_bbox": capture.approved_bbox,
        "browser_session_id": capture.browser_session_id,
        # Vision-grounding context — surfaced so the labeler UI can show the prompt
        "element_query": capture.element_query,
        "scenario_id": capture.scenario_id,
        "observed_page_state": capture.observed_page_state,
        "post_action_state": capture.post_action_state,
        # Annotator-created candidates (rendered in Candidates tab alongside observer ones).
        "manual_candidates": capture.manual_candidates or [],
        # Interaction-layer payload — paired with action_type_hint and approved_bbox.
        "action_text": capture.action_text,
    }


def _capture_metadata_from_artifact(
    *,
    artifact: dict,
    session: TrainingSession,
    goal: Optional[GoalRegistry],
    scenario: Optional[ScenarioRegistry],
    tab_id: str,
) -> dict:
    acquisition = artifact.get("acquisition") or {}
    training_metadata = acquisition.get("training_metadata") or {}
    page_identity = acquisition.get("page_identity") or {}
    screenshots = acquisition.get("screenshots") or []
    captured_at = training_metadata.get("captured_at") or artifact.get("metadata", {}).get("timestamp") or utcnow().isoformat()
    return {
        "captured_at": datetime.fromisoformat(captured_at),
        "url": training_metadata.get("url") or page_identity.get("url") or "",
        "title": training_metadata.get("title") or page_identity.get("title") or "",
        "viewport_width": training_metadata.get("viewport_width"),
        "viewport_height": training_metadata.get("viewport_height"),
        "device_scale_factor": training_metadata.get("device_scale_factor"),
        "scroll_x": training_metadata.get("scroll_x"),
        "scroll_y": training_metadata.get("scroll_y"),
        "tab_id": training_metadata.get("tab_id") or tab_id,
        "browser_session_id": training_metadata.get("browser_session_id") or session.browser_session_id,
        "domain_id": training_metadata.get("domain_id") or session.domain_id,
        "goal_id": training_metadata.get("goal_id") or session.goal_id,
        "task_id": training_metadata.get("task_id") or session.task_id,
        "action_type_hint": training_metadata.get("action_type_hint") or (_session_action_hint(goal) if goal else "any"),
        "notes": training_metadata.get("notes") or session.notes,
        "capture_profile": training_metadata.get("capture_profile") or session.capture_profile,
        "screenshot_refs": screenshots,
        # Vision training — propagated from the session's scenario at capture time
        "scenario_id": session.scenario_id,
        "element_query": scenario.element_query if scenario else None,
    }


def _mark_zombie_eval_runs(db: Session) -> None:
    """Any eval run with status in (pending, running) at process startup is a
    zombie — its worker thread died when the previous uvicorn worker exited
    (auto-reload, manual restart, crash, sleep). Mark them failed so the UI
    doesn't spin forever waiting for them, and so they're visible as resume
    candidates. Per-capture predictions on disk are preserved.
    """
    stuck = db.scalars(
        select(ModelEvalRun).where(ModelEvalRun.status.in_(["pending", "running"]))
    ).all()
    for run in stuck:
        run.status = "failed"
        run.error = "worker exited mid-run (uvicorn reload, process restart, or sleep)"
        run.finished_at = datetime.now(timezone.utc)
        run.cancel_requested = False
    if stuck:
        db.commit()


def backfill_label_sources(db: Session) -> None:
    """Stamp pre-existing golden labels (created before provenance tracking) as 'human'.
    They were all hand-confirmed in the labeler, so the auto-promotion pass must treat
    them as human-owned (never overwrite) and the scorecard should count them as human."""
    stmt = (update(TrainingCapture)
            .where(TrainingCapture.label_source.is_(None))
            .where(or_(TrainingCapture.review_status.in_(["reviewed", "approved"]),
                       TrainingCapture.positive_candidate_id.isnot(None)))
            .values(label_source="human"))
    res = db.execute(stmt)
    # Existing page-state labels were all hand-set before provenance tracking → 'human',
    # so the Haiku auto pass treats them as owned and never overwrites them.
    state_res = db.execute(
        update(TrainingCapture)
        .where(TrainingCapture.state_label_source.is_(None))
        .where(TrainingCapture.observed_page_state.isnot(None))
        .values(state_label_source="human")
    )
    if res.rowcount or state_res.rowcount:
        db.commit()


def on_startup():
    migrate_schema()
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        seed_training_registry(db)
        seed_facebook_extras(db)
        seed_gmail_domain(db)
        seed_page_states(db)
        seed_actions(db)
        backfill_goal_stages(db)
        backfill_page_state_stages(db)
        backfill_label_sources(db)
        seed_application_answers(db)
        _mark_zombie_eval_runs(db)


@router.get("/health")
def health():
    return {"ok": True, "service": "control-plane-api"}


def _service_status(
    *,
    service_id: str,
    label: str,
    kind: str,
    status: str,
    reachable: bool,
    required_for_training: bool,
    endpoint_or_target: str,
    message: str,
    details: Optional[dict] = None,
    latency_ms: Optional[float] = None,
):
    payload = {
        "id": service_id,
        "label": label,
        "kind": kind,
        "status": status,
        "reachable": reachable,
        "required_for_training": required_for_training,
        "endpoint_or_target": endpoint_or_target,
        "message": message,
        "details": details or {},
    }
    if latency_ms is not None:
        payload["latency_ms"] = round(latency_ms, 2)
    return payload


def check_controlplane_api_status():
    return _service_status(
        service_id="controlplane_api",
        label="Control Plane API",
        kind="api",
        status="healthy",
        reachable=True,
        required_for_training=True,
        endpoint_or_target="self",
        message="Primary API is serving requests.",
        details={"service": "control-plane-api"},
        latency_ms=0.0,
    )


def _check_http_service(*, service_id: str, label: str, target: str, required_for_training: bool):
    started_at = time.perf_counter()
    try:
        with httpx.Client(timeout=3.0) as client:
            response = client.get(target)
            response.raise_for_status()
            payload = response.json()
        return _service_status(
            service_id=service_id,
            label=label,
            kind="api",
            status="healthy",
            reachable=True,
            required_for_training=required_for_training,
            endpoint_or_target=target,
            message=payload.get("service", "HTTP service reachable"),
            details={"http_status": response.status_code, "payload": payload},
            latency_ms=(time.perf_counter() - started_at) * 1000,
        )
    except Exception as exc:
        return _service_status(
            service_id=service_id,
            label=label,
            kind="api",
            status="down",
            reachable=False,
            required_for_training=required_for_training,
            endpoint_or_target=target,
            message=str(exc),
            details={"error_type": type(exc).__name__},
            latency_ms=(time.perf_counter() - started_at) * 1000,
        )


def check_capture_server_status():
    return _check_http_service(
        service_id="capture_server",
        label="Capture Server",
        target=f"{settings.capture_server_url}/health",
        required_for_training=True,
    )


def check_chrome_cdp_status():
    with Session(engine) as db:
        session = db.scalar(
            select(TrainingSession)
            .where(TrainingSession.status == "active", TrainingSession.chrome_debug_port.is_not(None))
            .order_by(TrainingSession.started_at.desc())
        )
    if session is None:
        return _service_status(
            service_id="chrome_cdp",
            label="Training Chrome Session",
            kind="browser",
            status="healthy",
            reachable=True,
            required_for_training=False,
            endpoint_or_target="session-managed",
            message="Chrome is started on demand per training session.",
            details={"mode": "session_scoped", "active_sessions": 0},
            latency_ms=0.0,
        )

    target = f"http://127.0.0.1:{session.chrome_debug_port}/json/version"
    started_at = time.perf_counter()
    try:
        with httpx.Client(timeout=3.0) as client:
            response = client.get(target)
            response.raise_for_status()
            payload = response.json()
        return _service_status(
            service_id="chrome_cdp",
            label="Training Chrome Session",
            kind="browser",
            status="healthy",
            reachable=True,
            required_for_training=False,
            endpoint_or_target=target,
            message=f"Training session {session.id} Chrome endpoint reachable.",
            details={
                "session_id": session.id,
                "browser": payload.get("Browser"),
                "protocol_version": payload.get("Protocol-Version"),
                "web_socket_debugger_url": payload.get("webSocketDebuggerUrl"),
            },
            latency_ms=(time.perf_counter() - started_at) * 1000,
        )
    except Exception as exc:
        return _service_status(
            service_id="chrome_cdp",
            label="Training Chrome Session",
            kind="browser",
            status="down",
            reachable=False,
            required_for_training=False,
            endpoint_or_target=target,
            message=str(exc),
            details={"error_type": type(exc).__name__, "session_id": session.id},
            latency_ms=(time.perf_counter() - started_at) * 1000,
        )


def check_database_status():
    started_at = time.perf_counter()
    parsed = urlparse(settings.database_url.replace("+psycopg", ""))
    target = f"{parsed.hostname or 'unknown'}:{parsed.port or 'default'}"
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return _service_status(
            service_id="postgres",
            label="Postgres",
            kind="database",
            status="healthy",
            reachable=True,
            required_for_training=True,
            endpoint_or_target=target,
            message="Database connection successful.",
            details={"database": parsed.path.lstrip("/"), "driver": engine.dialect.name},
            latency_ms=(time.perf_counter() - started_at) * 1000,
        )
    except Exception as exc:
        return _service_status(
            service_id="postgres",
            label="Postgres",
            kind="database",
            status="down",
            reachable=False,
            required_for_training=True,
            endpoint_or_target=target,
            message=str(exc),
            details={"database": parsed.path.lstrip("/"), "error_type": type(exc).__name__},
            latency_ms=(time.perf_counter() - started_at) * 1000,
        )


def check_redis_status():
    started_at = time.perf_counter()
    parsed = urlparse(settings.redis_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    target = f"{host}:{port}"
    try:
        with socket.create_connection((host, port), timeout=2.0) as sock:
            sock.sendall(b"*1\r\n$4\r\nPING\r\n")
            data = sock.recv(16)
        if not data.startswith(b"+PONG"):
            raise RuntimeError("Redis did not return PONG")
        return _service_status(
            service_id="redis",
            label="Redis",
            kind="cache",
            status="healthy",
            reachable=True,
            required_for_training=False,
            endpoint_or_target=target,
            message="Redis ping successful.",
            details={"database": parsed.path.lstrip("/") or "0"},
            latency_ms=(time.perf_counter() - started_at) * 1000,
        )
    except Exception as exc:
        return _service_status(
            service_id="redis",
            label="Redis",
            kind="cache",
            status="down",
            reachable=False,
            required_for_training=False,
            endpoint_or_target=target,
            message=str(exc),
            details={"error_type": type(exc).__name__},
            latency_ms=(time.perf_counter() - started_at) * 1000,
        )


def check_artifacts_dir_status():
    path = _artifacts_dir()
    details = {
        "path": str(path.resolve()) if path.exists() else str(path),
        "exists": path.exists(),
        "is_dir": path.is_dir(),
    }
    if not path.exists():
        return _service_status(
            service_id="artifacts_dir",
            label="Artifacts Directory",
            kind="storage",
            status="down",
            reachable=False,
            required_for_training=True,
            endpoint_or_target=str(path),
            message="Artifacts directory does not exist.",
            details=details,
        )
    if not path.is_dir():
        return _service_status(
            service_id="artifacts_dir",
            label="Artifacts Directory",
            kind="storage",
            status="down",
            reachable=False,
            required_for_training=True,
            endpoint_or_target=str(path),
            message="Artifacts path is not a directory.",
            details=details,
        )

    try:
        with tempfile.NamedTemporaryFile(dir=path, prefix=".system-check-", delete=True):
            pass
        return _service_status(
            service_id="artifacts_dir",
            label="Artifacts Directory",
            kind="storage",
            status="healthy",
            reachable=True,
            required_for_training=True,
            endpoint_or_target=str(path.resolve()),
            message="Artifacts directory is readable and writable.",
            details=details,
        )
    except Exception as exc:
        return _service_status(
            service_id="artifacts_dir",
            label="Artifacts Directory",
            kind="storage",
            status="degraded",
            reachable=True,
            required_for_training=True,
            endpoint_or_target=str(path.resolve()),
            message=f"Artifacts directory exists but write test failed: {exc}",
            details={**details, "error_type": type(exc).__name__},
        )


def collect_system_services():
    return [
        check_controlplane_api_status(),
        check_capture_server_status(),
        check_chrome_cdp_status(),
        check_database_status(),
        check_redis_status(),
        check_artifacts_dir_status(),
    ]


def _overall_status_for_services(services: list[dict]) -> str:
    if not services:
        return "unknown"
    if all(service["status"] == "unknown" for service in services):
        return "unknown"
    if any(service["required_for_training"] and service["status"] == "down" for service in services):
        return "down"
    if any(service["status"] in {"down", "degraded"} for service in services):
        return "degraded"
    if any(service["status"] == "unknown" for service in services):
        return "unknown"
    return "healthy"


@router.get("/api/system/status")
def get_system_status():
    services = collect_system_services()
    return {
        "generated_at": utcnow().isoformat(),
        "overall_status": _overall_status_for_services(services),
        "services": services,
    }


@router.get("/api/usage/anthropic")
def get_anthropic_usage():
    """Self-logged Claude API usage + cost (token counts and dollar cost per
    call). Authoritative org-wide numbers live in the Anthropic Console; this is
    our context-tagged view (cost per purpose/day/model) for the flywheel."""
    import anthropic_usage

    return {
        "generated_at": utcnow().isoformat(),
        "key_configured": bool(anthropic_usage.settings_anthropic_key()),
        "pricing": anthropic_usage.PRICING,
        "budget": anthropic_usage.budget_status(),
        **anthropic_usage.summarize(),
    }


@router.get("/api/training/domains", response_model=list[DomainRead])
def list_training_domains(include_inactive: bool = False, db: Session = Depends(get_db)):
    stmt = select(DomainRegistry)
    if not include_inactive:
        stmt = stmt.where(DomainRegistry.status == "active")
    return db.scalars(stmt.order_by(DomainRegistry.display_name.asc())).all()


@router.post("/api/training/domains", response_model=DomainRead)
def create_training_domain(body: DomainWrite, db: Session = Depends(get_db)):
    if db.get(DomainRegistry, body.domain_id):
        raise HTTPException(status_code=409, detail="Domain already exists")
    domain = DomainRegistry(**body.model_dump())
    db.add(domain)
    db.commit()
    db.refresh(domain)
    return domain


@router.patch("/api/training/domains/{domain_id}", response_model=DomainRead)
def update_training_domain(domain_id: str, body: DomainUpdate, db: Session = Depends(get_db)):
    domain = db.get(DomainRegistry, domain_id)
    if domain is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(domain, key, value)
    db.commit()
    db.refresh(domain)
    return domain


@router.delete("/api/training/domains/{domain_id}")
def archive_training_domain(domain_id: str, db: Session = Depends(get_db)):
    domain = db.get(DomainRegistry, domain_id)
    if domain is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    domain.status = "archived"
    for goal in db.scalars(select(GoalRegistry).where(GoalRegistry.domain_id == domain_id)).all():
        goal.status = "archived"
    for task in db.scalars(select(TaskRegistry).where(TaskRegistry.domain_id == domain_id)).all():
        task.status = "archived"
    for scenario in db.scalars(select(ScenarioRegistry).where(ScenarioRegistry.domain_id == domain_id)).all():
        scenario.status = "archived"
    db.commit()
    return {"ok": True}


@router.get("/api/training/goals", response_model=list[GoalRead])
def list_training_goals(domain_id: Optional[str] = None, db: Session = Depends(get_db)):
    stmt = select(GoalRegistry).where(GoalRegistry.status == "active")
    if domain_id:
        stmt = stmt.where((GoalRegistry.domain_id == domain_id) | (GoalRegistry.domain_id.is_(None)))
    return db.scalars(stmt.order_by(GoalRegistry.display_name.asc())).all()


@router.post("/api/training/goals", response_model=GoalRead)
def create_training_goal(body: GoalWrite, db: Session = Depends(get_db)):
    if db.get(GoalRegistry, body.goal_id):
        raise HTTPException(status_code=409, detail="Goal already exists")
    if body.domain_id and db.get(DomainRegistry, body.domain_id) is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    goal = GoalRegistry(**body.model_dump())
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal


@router.patch("/api/training/goals/{goal_id}", response_model=GoalRead)
def update_training_goal(goal_id: str, body: GoalUpdate, db: Session = Depends(get_db)):
    goal = db.get(GoalRegistry, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    patch = body.model_dump(exclude_unset=True)
    if patch.get("domain_id") and db.get(DomainRegistry, patch["domain_id"]) is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    for key, value in patch.items():
        setattr(goal, key, value)
    db.commit()
    db.refresh(goal)
    return goal


@router.delete("/api/training/goals/{goal_id}")
def archive_training_goal(goal_id: str, db: Session = Depends(get_db)):
    goal = db.get(GoalRegistry, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    goal.status = "archived"
    for scenario in db.scalars(select(ScenarioRegistry).where(ScenarioRegistry.goal_id == goal_id)).all():
        scenario.status = "archived"
    db.commit()
    return {"ok": True}


@router.get("/api/training/tasks", response_model=list[TaskRead])
def list_training_tasks(
    scope_level: Optional[str] = None,
    domain_id: Optional[str] = None,
    goal_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    stmt = select(TaskRegistry).where(TaskRegistry.status == "active")
    if scope_level:
        stmt = stmt.where(TaskRegistry.scope_level == scope_level)
    if domain_id:
        stmt = stmt.where((TaskRegistry.domain_id == domain_id) | (TaskRegistry.domain_id.is_(None)))
    if goal_id:
        stmt = stmt.where((TaskRegistry.goal_id == goal_id) | (TaskRegistry.goal_id.is_(None)))
    return db.scalars(stmt.order_by(TaskRegistry.display_name.asc())).all()


@router.post("/api/training/tasks", response_model=TaskRead)
def create_training_task(body: TaskWrite, db: Session = Depends(get_db)):
    if db.get(TaskRegistry, body.task_id):
        raise HTTPException(status_code=409, detail="Task already exists")
    _validate_registry_refs(db, domain_id=body.domain_id, goal_id=body.goal_id)
    task = TaskRegistry(**body.model_dump())
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.patch("/api/training/tasks/{task_id}", response_model=TaskRead)
def update_training_task(task_id: str, body: TaskUpdate, db: Session = Depends(get_db)):
    task = db.get(TaskRegistry, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    patch = body.model_dump(exclude_unset=True)
    _validate_registry_refs(db, domain_id=patch.get("domain_id"), goal_id=patch.get("goal_id"))
    for key, value in patch.items():
        setattr(task, key, value)
    db.commit()
    db.refresh(task)
    return task


@router.delete("/api/training/tasks/{task_id}")
def archive_training_task(task_id: str, db: Session = Depends(get_db)):
    task = db.get(TaskRegistry, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = "archived"
    for scenario in db.scalars(select(ScenarioRegistry).where(ScenarioRegistry.task_id == task_id)).all():
        scenario.task_id = None
    db.commit()
    return {"ok": True}


@router.get("/api/training/scenarios", response_model=list[ScenarioRead])
def list_training_scenarios(domain_id: Optional[str] = None, db: Session = Depends(get_db)):
    stmt = select(ScenarioRegistry).where(ScenarioRegistry.status == "active")
    if domain_id:
        stmt = stmt.where(ScenarioRegistry.domain_id == domain_id)
    return db.scalars(stmt.order_by(ScenarioRegistry.display_name.asc())).all()


def _validate_registry_refs(
    db: Session,
    *,
    domain_id: Optional[str] = None,
    goal_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> None:
    if domain_id and db.get(DomainRegistry, domain_id) is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    if goal_id and db.get(GoalRegistry, goal_id) is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    if task_id and db.get(TaskRegistry, task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found")


@router.post("/api/training/scenarios", response_model=ScenarioRead)
def create_training_scenario(body: ScenarioWrite, db: Session = Depends(get_db)):
    if db.get(ScenarioRegistry, body.scenario_id):
        raise HTTPException(status_code=409, detail="Scenario already exists")
    _validate_registry_refs(db, domain_id=body.domain_id, goal_id=body.goal_id, task_id=body.task_id)
    scenario = ScenarioRegistry(**body.model_dump())
    db.add(scenario)
    db.commit()
    db.refresh(scenario)
    return scenario


@router.patch("/api/training/scenarios/{scenario_id}", response_model=ScenarioRead)
def update_training_scenario(scenario_id: str, body: ScenarioUpdate, db: Session = Depends(get_db)):
    scenario = db.get(ScenarioRegistry, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    patch = body.model_dump(exclude_unset=True)
    _validate_registry_refs(db, domain_id=patch.get("domain_id"), goal_id=patch.get("goal_id"), task_id=patch.get("task_id"))
    for key, value in patch.items():
        setattr(scenario, key, value)
    db.commit()
    db.refresh(scenario)
    return scenario


@router.delete("/api/training/scenarios/{scenario_id}")
def archive_training_scenario(scenario_id: str, db: Session = Depends(get_db)):
    scenario = db.get(ScenarioRegistry, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    scenario.status = "archived"
    db.commit()
    return {"ok": True}


# ===== Page state registry (scoped: global / domain / scenario, + category) =====

def _page_state_dict(s: PageStateRegistry) -> dict:
    return {
        "state_id": s.state_id,
        "display_name": s.display_name,
        "scope": s.scope,
        "domain_id": s.domain_id,
        "goal_id": s.goal_id,
        "scenario_id": s.scenario_id,
        "category": s.category or "general",
        "stage": s.stage,
        "description": s.description,
        "status": s.status,
    }


class PageStateWrite(BaseModel):
    display_name: str
    scope: str = "global"  # global | domain | goal | scenario
    domain_id: Optional[str] = None
    goal_id: Optional[str] = None
    scenario_id: Optional[str] = None
    category: str = "general"
    stage: Optional[str] = None  # unauthenticated | authenticated | neutral
    description: Optional[str] = None
    state_id: Optional[str] = None  # optional explicit slug; else derived from display_name


class PageStateUpdate(BaseModel):
    display_name: Optional[str] = None
    scope: Optional[str] = None
    domain_id: Optional[str] = None
    goal_id: Optional[str] = None
    scenario_id: Optional[str] = None
    category: Optional[str] = None
    stage: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None


@router.get("/api/training/coverage")
def training_coverage(
    domain_id: Optional[str] = None,
    goal_id: Optional[str] = None,
    scenario_id: Optional[str] = None,
    target_per_state: int = 5,
    db: Session = Depends(get_db),
):
    """Capture-coverage matrix for a campaign: for every page-state RELEVANT to the
    given domain/goal/scenario, how many captures are tagged with it. This is the
    navigation aid for data collection — it turns "what should I capture next?" into
    "drive to the ⚠️ gap rows", and surfaces the per-class cap so you don't
    over-collect one state.

    `target_per_state` is the soft cap per class (default 5): below it = gap,
    at/above = covered, well above = over-collected (diversity better spent on a new
    state/domain). Counts come from `TrainingCapture.observed_page_state`, so a
    capture only counts once it's been page-state tagged (Review/Label or PATCH)."""
    states = db.scalars(
        select(PageStateRegistry).where(PageStateRegistry.status == "active")
    ).all()
    context = any((domain_id, goal_id, scenario_id))

    def relevant(s: PageStateRegistry) -> bool:
        if not context:
            return True
        if s.scope == "global":
            return True
        if s.scope == "domain":
            return domain_id is not None and s.domain_id == domain_id
        if s.scope == "goal":
            if goal_id is None or s.goal_id != goal_id:
                return False
            # Optional domain pin: a goal-scoped state may be restricted to ONE domain
            # (e.g. FB's password_entered). domain_id=None = generic across all domains
            # with this goal (e.g. logged_in). Prevents FB login states leaking into Indeed.
            return s.domain_id is None or domain_id is None or s.domain_id == domain_id
        if s.scope == "scenario":
            return scenario_id is not None and s.scenario_id == scenario_id
        return False

    # One grouped count over all captures, then map onto the relevant states.
    counts = dict(
        db.execute(
            select(TrainingCapture.observed_page_state, func.count())
            .group_by(TrainingCapture.observed_page_state)
        ).all()
    )

    rows = []
    for s in states:
        if not relevant(s):
            continue
        n = int(counts.get(s.state_id, 0) or 0)
        status = "gap" if n == 0 else ("thin" if n < target_per_state else
                                       ("over" if n > target_per_state * 3 else "covered"))
        rows.append({
            "state_id": s.state_id, "display_name": s.display_name,
            "scope": s.scope, "category": s.category, "stage": s.stage,
            "count": n, "target": target_per_state, "status": status,
        })
    rows.sort(key=lambda r: (r["status"] != "gap", r["scope"], r["category"], r["display_name"]))

    tagged = sum(r["count"] for r in rows)
    untagged = int(counts.get(None, 0) or 0)
    covered_classes = sum(1 for r in rows if r["count"] >= 1)

    # Faucet health: of all captures, how many came out DRY (0 AX candidates)? A dry capture
    # exists on disk but taught Select nothing — the drive's effort was wasted. Surfaced here so
    # the operator sees the faucet's real yield, not just that captures happened.
    total_captures = int(db.scalar(select(func.count()).select_from(TrainingCapture)) or 0)
    dry_captures = int(
        db.scalar(
            select(func.count()).select_from(TrainingCapture)
            .where(TrainingCapture.ax_candidate_count == 0)
        ) or 0
    )
    return {
        "generated_at": utcnow().isoformat(),
        "target_per_state": target_per_state,
        "filters": {"domain_id": domain_id, "goal_id": goal_id, "scenario_id": scenario_id},
        "totals": {
            "relevant_states": len(rows),
            "covered_states": covered_classes,
            "gap_states": sum(1 for r in rows if r["status"] == "gap"),
            "tagged_captures": tagged,
            "untagged_captures": untagged,
            # Faucet yield across ALL captures (not just this filter's relevant states).
            "total_captures": total_captures,
            "dry_captures": dry_captures,
        },
        "states": rows,
    }


@router.get("/api/training/page-states")
def list_page_states(
    scope: Optional[str] = None,
    domain_id: Optional[str] = None,
    goal_id: Optional[str] = None,
    scenario_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List page states. With no filters → all active states (for the manager).
    With context filters → the states RELEVANT to a capture: all global + that
    domain's + that objective(goal)'s + that scenario's states."""
    stmt = select(PageStateRegistry).where(PageStateRegistry.status == "active")
    rows = db.scalars(stmt).all()

    context = domain_id is not None or goal_id is not None or scenario_id is not None

    def relevant(s: PageStateRegistry) -> bool:
        if scope and s.scope != scope:
            return False
        if not context:
            return True
        if s.scope == "global":
            return True
        if s.scope == "domain":
            return domain_id is not None and s.domain_id == domain_id
        if s.scope == "goal":
            if goal_id is None or s.goal_id != goal_id:
                return False
            # Optional domain pin: a goal-scoped state may be restricted to ONE domain
            # (e.g. FB's password_entered). domain_id=None = generic across all domains
            # with this goal (e.g. logged_in). Prevents FB login states leaking into Indeed.
            return s.domain_id is None or domain_id is None or s.domain_id == domain_id
        if s.scope == "scenario":
            return scenario_id is not None and s.scenario_id == scenario_id
        return False

    result = [_page_state_dict(s) for s in rows if relevant(s)]
    result.sort(key=lambda d: (d["scope"], d["category"], d["display_name"]))
    return result


class JobExtractRequest(BaseModel):
    training_session_id: int
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    search_query: Optional[str] = None
    platform: str = "indeed"


class JobStatusUpdate(BaseModel):
    application_status: Optional[str] = None  # seen|viewed|applied|skipped|rejected
    notes: Optional[str] = None


@router.post("/api/jobs/extract")
async def extract_jobs(body: JobExtractRequest, db: Session = Depends(get_db)):
    """Scrape the live results page for job postings and UPSERT them into observed_jobs,
    deduped by job_id = '{platform}:{external_id}'. A re-seen job bumps seen_count +
    last_seen_at (and records the search/capture) instead of duplicating. Returns how many
    were new vs. duplicates — the dedup signal the operator manages the corpus by."""
    session = db.get(TrainingSession, body.training_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    browser_url = _session_browser_url(session)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{settings.capture_server_url}/extract_jobs",
                                  json={"tab_id": body.tab_id, "tab_url": body.tab_url,
                                        "browser_url": browser_url})
            r.raise_for_status()
            raw = r.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"extractor unreachable: {exc}")

    new_count, dup_count = upsert_observed_jobs(db, raw.get("jobs", []),
                                                body.platform, body.search_query)
    db.commit()
    return {"ok": True, "scraped": raw.get("count", 0), "new": new_count,
            "duplicates": dup_count, "search_query": body.search_query}


_SENIORITY = {"senior", "sr", "junior", "jr", "lead", "principal", "staff", "associate",
              "i", "ii", "iii", "iv", "v", "1", "2", "3"}
_CO_SUFFIX = {"inc", "llc", "corp", "co", "ltd", "lp", "company", "incorporated", "the"}


def _norm_company(s: str) -> str:
    toks = [t for t in re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split() if t not in _CO_SUFFIX]
    return " ".join(toks).strip()


def _norm_title(s: str) -> str:
    toks = [t for t in re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split() if t not in _SENIORITY]
    return " ".join(toks).strip()


def _applied_key(company: str, title: str) -> str:
    """Cross-PLATFORM application identity: same company + same core title = same job,
    whether seen on Indeed, Workday, or applied externally by hand. Lets 'already applied'
    suppress a job we (or the user) applied to anywhere — not just by Indeed jk."""
    return f"{_norm_company(company)}|{_norm_title(title)}"


def _shortlist_jobs(jobs: list[dict], query: str, applied_keys: Optional[set] = None) -> list[dict]:
    """Pick which scraped cards are worth clicking into for a full description — the cheap,
    deterministic 'shortlist only' filter (no model; resource-efficiency). A card is shortlisted
    when its normalized title shares a token with the query AND it isn't already applied (directly
    or cross-platform). An empty query keeps everything that isn't already applied (no signal to
    filter on). Returns the shortlisted card dicts in input order."""
    q_tokens = set(_norm_title(query).split())
    out = []
    for j in jobs:
        title = j.get("title") or ""
        company = j.get("company") or ""
        if applied_keys is not None and _applied_key(company, title) in applied_keys:
            continue
        if q_tokens and not (q_tokens & set(_norm_title(title).split())):
            continue
        out.append(j)
    return out


def _job_dict(j: ObservedJob, applied_keys: Optional[set] = None) -> dict[str, Any]:
    # already_applied = applied directly OR matches the (company,title) of any applied job
    # (cross-platform dedup — the user may have applied off-Indeed).
    already = j.application_status == "applied"
    if not already and applied_keys is not None:
        already = _applied_key(j.company, j.title) in applied_keys
    return {
        "job_id": j.job_id, "platform": j.platform, "external_id": j.external_id,
        "title": j.title, "company": j.company, "location": j.location, "url": j.url,
        "application_status": j.application_status, "already_applied": already,
        "seen_count": j.seen_count, "search_queries": j.search_queries or [],
        "apply_type": j.apply_type, "application_platform": j.application_platform,
        "first_seen_at": j.first_seen_at.isoformat() if j.first_seen_at else None,
        "last_seen_at": j.last_seen_at.isoformat() if j.last_seen_at else None,
        "applied_at": j.applied_at.isoformat() if j.applied_at else None,
    }


@router.post("/api/jobs/autofill_form")
async def autofill_form(training_session_id: int, db: Session = Depends(get_db)):
    """Fill the current Indeed apply form from the Application Profile using the
    type-generalizing interaction layer — match each question to an answer, dispatch by the
    element type present (radio/select/text/number/checkbox/combobox). Returns what filled vs
    what's unmatched (the operator handles unmatched). Same answers, any element shape."""
    session = db.get(TrainingSession, training_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    rows = db.scalars(select(ApplicationAnswer).where(ApplicationAnswer.status == "active")).all()
    answers = [{"key": a.answer_key, "value": a.value, "options": a.options or [],
                "patterns": a.question_patterns or [], "category": a.category} for a in rows]
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(f"{settings.capture_server_url}/autofill_form",
                                  json={"answers": answers, "browser_url": _session_browser_url(session),
                                        "tab_url": "smartapply"})
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"autofill unreachable: {exc}")


async def _scan_required_fields(browser_url: str, tab_url: str = "smartapply") -> Optional[list[dict]]:
    """The live apply form's UNSATISFIED required fields, in `build_form_state`'s shape.

    Feeds `form_complete_gate` — the invariant that makes the model structurally unable to
    mark a form done with an empty required field. ONE function for both callers
    (apply_state + session_state) on purpose: they were byte-identical copies, and a fix
    landing in one call-site and not its twin has bitten this repo three times.

    ON THE NARROWING. `/scan_form` returned EVERY field; `/scan_required` returns only the
    required ones that are unsatisfied. The gate's verdict is unchanged:
    `form_complete_gate` computes `ok = not unsatisfied` and every row here is
    `required=True`, so an empty list means "nothing blocks" exactly as before. What is lost
    is the `satisfied` list (informational only). What is GAINED is that the verdict is now
    correct — see below.

    WHY THE SWAP (measured live on KKR's Greenhouse form, 2026-07-16, both scanners against
    the same tab; ground truth: 30 required, 2 disabled, 1 genuinely unanswered):
      - /scan_form reported 21 "fields" / 18 "required and unfilled". It labels every control
        with its CONTAINER's text, so 14 language checkboxes each became a separate required
        field named "Please indicate any languages…" — 13 of them "empty" even though the
        GROUP is answered. It would have made this gate permanently un-passable.
      - It also found only 5 fieldsets, so it never saw Country, School, Degree, Discipline,
        the 7 screening questions, or the attestation: ~16 real required fields, invisible.
      - /scan_required reports exactly 1: the AI attestation. It excludes the two `disabled`
        End-date fields that keep their '*' and aria-required (the KKR trap), and omits both
        answered checkbox groups.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            fr = await client.post(f"{settings.capture_server_url}/scan_required",
                                   json={"browser_url": browser_url, "tab_url": tab_url})
            fr.raise_for_status()
            body = fr.json()
    except httpx.HTTPError:
        return None   # scan unreachable → leave prior form_state; don't crash the readout
    if not body.get("ok"):
        return None
    return [{
        "field_id": u.get("selector") or u.get("field") or "",
        "label": u.get("field") or "",
        "kind": u.get("kind") or "unknown",
        "required": True,                       # the endpoint returns ONLY required fields
        # NOT always False: a FILLED-but-INVALID required field is reported too, and the gate
        # needs it (satisfied = filled AND valid), which is why `valid` is carried through
        # rather than defaulted.
        "filled": bool(u.get("answered")),
        "valid": bool(u.get("valid", True)),
        "value_preview": u.get("value_preview") or "",
    } for u in body.get("unanswered", [])]


@router.get("/api/runtime/apply_state")
async def apply_state(training_session_id: int, scan_form: bool = True,
                      db: Session = Depends(get_db)):
    """Live state manager for the apply task. Reads every tab in the session's Chrome, folds
    the observation into the PERSISTENT per-session blackboard (apply_state_store), and runs
    the invariant gates — so the readout carries not just "which tab is what" but the live
    plan progress, per-field form_state, the code-enforced completion gate, and the blockers
    (human branches + unsatisfied required fields). This is the store that stops us holding
    tab/step/field state in our heads; it persists between calls instead of recomputing blind.

    `scan_form=true` (default) additionally reads the active apply form's unsatisfied
    required fields from the capture server; set false to skip the form read. (The param
    keeps its name for compatibility even though the `/scan_form` endpoint it was named after
    is gone — see _scan_required_fields. Renaming it would be tidier and silently wrong:
    FastAPI ignores unknown query params, so an existing `?scan_form=false` would quietly
    start scanning instead of erroring.)"""
    import apply_recipe
    import apply_state_store as store
    import escalation_rules
    session = db.get(TrainingSession, training_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    browser_url = _session_browser_url(session)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{browser_url}/json")
            r.raise_for_status()
            all_targets = r.json()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"session Chrome not reachable: {exc}")
    targets = [t for t in all_targets if t.get("type") == "page"]
    # Block detection scans EVERY frame/target (incl. iframes), where an in-page captcha
    # actually lives — the gap that left a reCAPTCHA'd page reading needs_human=false.
    block = escalation_rules.detect_block_frames([t.get("url", "") for t in all_targets])
    # ...but a preloaded-yet-hidden reCAPTCHA must NOT hard-stop the apply flow either; confirm it's
    # actually shown before treating it as human-required (same refinement as session_state).
    block = await _refine_block_visibility(browser_url, block)
    tabs = [apply_recipe.describe_tab(t.get("url", "")) for t in targets
            if "localhost:5173" not in (t.get("url", "") or "")]
    apply_tabs = [t for t in tabs if t["role"] == "apply"]

    # Read the live form (read-only) only when we're actually on an apply tab — the gate has
    # nothing to check otherwise, and we avoid a wasted CDP round-trip on search pages.
    form_fields = None
    if scan_form and apply_tabs:
        form_fields = await _scan_required_fields(browser_url)

    bb = store.load_or_create(training_session_id)
    store.reconcile(bb, tabs=tabs, form_fields=form_fields, block=block)
    store.save(bb)
    bb_dict = bb.to_dict()

    return {
        "training_session_id": training_session_id,
        "tab_count": len(tabs),
        "tabs": tabs,
        "active_apply_state": apply_tabs[0] if apply_tabs else None,
        "needs_human": bb_dict["needs_human"],
        # the blackboard: plan progress, form_state, code-enforced gate, blockers, event log
        "blackboard": bb_dict,
        # Layer-3 made active: "is it safe to proceed/submit from here?" off the live gate
        "proceed_decision": store.proceed_decision(bb),
    }


async def _captcha_gate_for(browser_url: str) -> dict[str, Any]:
    """Probe every session tab for a LIVE, BLOCKING captcha and aggregate. Shared by the captcha_gate
    check and the await_captcha handoff. Uses the token-scoped `blocking` flag (a visible v2 checkbox
    whose OWN wrapper token is empty, or an open image challenge) — NOT fooled by Indeed's invisible
    Enterprise scorer's passed token. Best-effort per tab."""
    targets = await _list_session_tabs(browser_url)
    pages = [t for t in targets if t.get("type") == "page"
             and "localhost:5173" not in (t.get("url", "") or "")]
    per_tab = []
    for t in pages:
        vis = await _capture_post("/challenge_visibility",
                                  {"browser_url": browser_url, "tab_id": t.get("id")}, timeout=8.0)
        if vis.get("ok"):
            per_tab.append({"url": (t.get("url", "") or "")[:90],
                            "blocking": bool(vis.get("blocking")), "solved": bool(vis.get("solved")),
                            "checkbox_visible": bool(vis.get("checkbox_visible")),
                            "challenge_visible": bool(vis.get("challenge_visible"))})
    gated = [p for p in per_tab if p["blocking"]]
    return {"blocking": bool(gated), "needs_human": bool(gated), "gated_tabs": gated, "per_tab": per_tab}


@router.get("/api/runtime/captcha_gate")
async def captcha_gate(training_session_id: int, db: Session = Depends(get_db)):
    """CAPTCHA-FIRST CHECK — the very first thing to consult when an action is blocked/disabled/no-ops.
    Because our only eyes are CDP-AX (which can't SEE a reCAPTCHA in its iframe), this probes each of
    the session's tabs for a LIVE, BLOCKING challenge: a visible v2 checkbox whose OWN token is still
    empty, or an open image challenge. It is NOT fooled by Indeed's invisible Enterprise scorer (which
    holds a passed token on every page) — the token is scoped to the visible widget's wrapper. Returns
    `blocking` + the gated tab(s). When blocking: STOP and hand to the human; poll this (or use
    /await_captcha) until `blocking` flips false (human checked the box → token filled), then resume."""
    session = db.get(TrainingSession, training_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    gate = await _captcha_gate_for(_session_browser_url(session))
    gate["training_session_id"] = training_session_id
    gate["guidance"] = ("STOP — a human must solve the captcha (check the box). Poll until "
                        "blocking=false, then resume. Never auto-solve." if gate["blocking"]
                        else "clear — no live captcha gate.")
    return gate


class AwaitCaptchaRequest(BaseModel):
    training_session_id: int
    timeout_s: int = 240        # how long to wait for the human to solve it
    interval_s: float = 3.0     # poll cadence


@router.post("/api/runtime/await_captcha")
async def await_captcha(body: AwaitCaptchaRequest, db: Session = Depends(get_db)):
    """HANDOFF + RESUME primitive: poll the captcha gate until the human has solved it (blocking flips
    false because the visible widget's token filled) or we time out. This is what the apply loop calls
    after it detects a blocking captcha — it parks, the human checks the box, and this returns `cleared`
    so the loop can resume the blocked action. Never auto-solves; it only waits for the human."""
    session = db.get(TrainingSession, body.training_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    browser_url = _session_browser_url(session)
    import time as _time
    deadline = _time.monotonic() + max(5, min(body.timeout_s, 600))
    polls = 0
    last = await _captcha_gate_for(browser_url)
    while last.get("blocking") and _time.monotonic() < deadline:
        await asyncio.sleep(max(1.0, min(body.interval_s, 10.0)))
        polls += 1
        last = await _captcha_gate_for(browser_url)
    return {"training_session_id": body.training_session_id,
            "cleared": not last.get("blocking"),
            "timed_out": bool(last.get("blocking")),
            "polls": polls, "gate": last}


def _search_page_from_url(url: str) -> Optional[int]:
    """Indeed paginates results with ?start=0/10/20… — map that to a 1-based page number for the
    search_state readout. Returns None when the URL has no start offset (treat as page 1)."""
    try:
        from urllib.parse import parse_qs, urlparse
        start = parse_qs(urlparse(url or "").query).get("start", [None])[0]
        return (int(start) // 10) + 1 if start is not None else None
    except Exception:
        return None


_VISIBILITY_PROBE_PROVIDERS = {"recaptcha", "hcaptcha"}


async def _refine_block_visibility(browser_url: str, block: Optional[dict]) -> Optional[dict]:
    """Confirm an ACTIVE iframe-captcha is actually SHOWN before we hard-stop on it. detect_block_frames
    is URL-only ($0) and flags a reCAPTCHA bframe as active on mere presence — but Indeed preloads
    reCAPTCHA Enterprise invisibly on every page, so that over-triggers a human stop. This probes the
    captcha iframe elements' real visibility (capture server /challenge_visibility) and downgrades the
    block to PASSIVE (advisory) when nothing is visibly challenging. Only the iframe-based providers we
    can introspect are probed; others (turnstile/datadome/...) pass through unchanged. Fail-safe: if the
    probe is unreachable or errors, keep the conservative active stop."""
    if not block or block.get("strength") != "active":
        return block
    if block.get("provider") not in _VISIBILITY_PROBE_PROVIDERS:
        return block
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(f"{settings.capture_server_url}/challenge_visibility",
                                  json={"browser_url": browser_url})
            r.raise_for_status()
            vis = r.json()
    except httpx.HTTPError:
        return block  # probe unreachable → keep the conservative active stop
    import escalation_rules
    return escalation_rules.downgrade_block_if_hidden(block, vis)


@router.get("/api/runtime/session_state")
async def session_state(training_session_id: int, scan_form: bool = True,
                        db: Session = Depends(get_db)):
    """Session-spanning state manager: the generalized sibling of /api/runtime/apply_state that
    tracks the WHOLE flow, not just apply. Same read→detect→describe→reconcile→save flow, but the
    persistent blackboard carries the PHASE (search/triage/apply), the search target + progress
    (query/location/page/observed), the phase-aware plan, and the blockers. Captcha detection runs
    over every iframe in every phase, so a challenge during search halts proceed just like one at
    submit. Seeds the search target from job_search_targets on first creation only."""
    import apply_recipe
    import apply_state_store as store
    import escalation_rules
    import job_search_targets as jst
    session = db.get(TrainingSession, training_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    browser_url = _session_browser_url(session)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{browser_url}/json")
            r.raise_for_status()
            all_targets = r.json()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"session Chrome not reachable: {exc}")
    targets = [t for t in all_targets if t.get("type") == "page"]
    block = escalation_rules.detect_block_frames([t.get("url", "") for t in all_targets])
    # Visibility refinement: an "active" iframe-captcha is only a real human stop if it's actually
    # shown (Indeed preloads reCAPTCHA invisibly). Downgrade preloaded-but-hidden hits to passive.
    block = await _refine_block_visibility(browser_url, block)
    tabs = [apply_recipe.describe_tab(t.get("url", "")) for t in targets
            if "localhost:5173" not in (t.get("url", "") or "")]
    apply_tabs = [t for t in tabs if t["role"] == "apply"]
    search_tabs = [t for t in tabs if t["role"] == "search"]

    # Read the live apply form only when we're actually on an apply tab — search pages have no
    # form gate to check, and we avoid a wasted CDP round-trip.
    form_fields = None
    if scan_form and apply_tabs:
        form_fields = await _scan_required_fields(browser_url)

    # Fold the results-page number off the active search tab's ?start= offset (best-effort).
    search_update = None
    if search_tabs and not apply_tabs:
        page = _search_page_from_url(next((t["url"] for t in search_tabs
                                           if t["state"] == "indeed_search_results"), ""))
        if page is not None:
            search_update = {"page": page}

    # Login gate: probe whether the session is authenticated so the blackboard can refuse
    # task automation on a logged-out session (search/triage/apply require login). None = probe
    # failed/unknown → don't gate (avoids false-blocking).
    authed: Optional[bool] = None
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            ar = await client.post(f"{settings.capture_server_url}/auth_state",
                                   json={"browser_url": browser_url})
            ar.raise_for_status()
            abody = ar.json()
            if abody.get("ok"):
                authed = bool(abody.get("logged_in"))
    except httpx.HTTPError:
        authed = None

    target = jst.active_target() or {}
    bb = store.load_or_create(training_session_id,
                              query=target.get("query", ""), location=target.get("location", ""))
    store.reconcile(bb, tabs=tabs, form_fields=form_fields, block=block,
                    search_update=search_update, authed=authed)
    store.save(bb)
    bb_dict = bb.to_dict()

    return {
        "training_session_id": training_session_id,
        "phase": bb_dict["phase"],
        "tab_count": len(tabs),
        "tabs": tabs,
        "active_search_state": search_tabs[0] if search_tabs else None,
        "active_apply_state": apply_tabs[0] if apply_tabs else None,
        "logged_in": authed,
        "needs_human": bb_dict["needs_human"],
        "search_state": bb_dict["search_state"],
        # context-bound validity: is the current search/triage data valid to act on (approve/apply)?
        "search_actionable": store.search_data_actionable(bb),
        # the blackboard: phase, plan progress, form_state, code-enforced gate, blockers, event log
        "blackboard": bb_dict,
        "proceed_decision": store.proceed_decision(bb),
    }


@router.post("/api/runtime/session/{training_session_id}/start_run")
async def start_cadence_run_endpoint(training_session_id: int, db: Session = Depends(get_db)):
    """Open a NEW authenticated search-cadence run on the session's blackboard and stamp its
    provenance (run id, started_at, gathered_authenticated). This is the context that makes data
    gathered AFTER it actionable — call it right before extracting, so the extraction is attributable
    to a current, authenticated run. Refuses to mark gathered_authenticated unless the live auth
    probe says we're logged in (provenance must be honest)."""
    import apply_state_store as store
    import job_search_targets as jst
    session = db.get(TrainingSession, training_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    browser_url = _session_browser_url(session)
    authed = False
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            ar = await client.post(f"{settings.capture_server_url}/auth_state",
                                   json={"browser_url": browser_url})
            ar.raise_for_status()
            ab = ar.json()
            authed = bool(ab.get("ok") and ab.get("logged_in"))
    except httpx.HTTPError:
        authed = False
    target = jst.active_target() or {}
    bb = store.load_or_create(training_session_id,
                              query=target.get("query", ""), location=target.get("location", ""))
    store.start_cadence_run(bb, query=target.get("query", ""),
                            location=target.get("location", ""), authed=authed)
    # fold the live auth into world so search_data_actionable sees authed_now immediately
    bb.world["authed"] = authed
    store.save(bb)
    return {"training_session_id": training_session_id, "authed": authed,
            "search_state": bb.search_state.__dict__,
            "search_actionable": store.search_data_actionable(bb)}


@router.get("/api/runtime/apply_recipe")
def apply_recipe_spec():
    """The Indeed apply recipe (expected state machine + branches). Teachable: states are
    page_state_registry ids; transitions are what the state_transition model learns."""
    import apply_recipe
    return apply_recipe.recipe_spec()


class SearchTargetCreate(BaseModel):
    query: str
    location: str = ""
    status: str = "active"  # active | paused
    radius_miles: int = 50  # floored at 50 by the sweep regardless


class SearchOutcome(BaseModel):
    query: str
    location: str = ""
    status: Optional[str] = None    # e.g. 'searched' to close a query out
    outcome: Optional[str] = None   # the human/teacher decision note
    radius_miles: Optional[int] = None


@router.get("/api/search/targets")
def list_search_targets():
    """The persisted (query, location) targets the search cadence runs against — the written-down
    'what/where' so it isn't held in a Claude/Haiku context. Seeded on first use."""
    import job_search_targets as jst
    targets = jst.load_targets()
    return {"targets": targets, "active": jst.active_target()}


@router.post("/api/search/targets")
def add_search_target(body: SearchTargetCreate):
    """Add a search target (e.g. 'reporting analyst' / 'Nashua, NH'). Idempotent — a
    case-insensitive duplicate returns the existing row rather than adding a second."""
    import job_search_targets as jst
    try:
        row = jst.add_target(body.query, body.location, body.status, body.radius_miles)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return row


@router.post("/api/search/targets/outcome")
def record_search_outcome(body: SearchOutcome):
    """Record the RESULT of a search run on its (query, location) target — the durable decision log
    (e.g. status='searched', outcome='human override: no good matches found, committed to searching').
    Lets us close a query out with WHY, so the planner doesn't blindly re-run it and the human's call
    is remembered across sessions."""
    import job_search_targets as jst
    return jst.record_outcome(body.query, body.location, status=body.status,
                              outcome=body.outcome, radius_miles=body.radius_miles)


@router.get("/api/search/cadence")
def search_cadence_spec():
    """The bounded job-search cadence: the two task modes (extraction_sweep vs apply_triage),
    their recipes + safety bounds, and the cross-site apply-platform list. The seed of the
    job-search planner — the logic that keeps search safe + consistent instead of ad-hoc."""
    import search_cadence
    return search_cadence.cadence_spec()


@router.get("/api/dashboards/indeed_jobs")
def jobs_dashboard(platform: str = "indeed", db: Session = Depends(get_db)):
    """The Jobs Dashboard data: headline counts + the Jobs Seen and Jobs Applied tables.
    Duplicates are surfaced explicitly (jobs with seen_count>1) so the corpus stays manageable."""
    jobs = db.scalars(select(ObservedJob).where(ObservedJob.platform == platform)
                      .order_by(ObservedJob.last_seen_at.desc())).all()
    by_status: dict[str, int] = {}
    for j in jobs:
        by_status[j.application_status] = by_status.get(j.application_status, 0) + 1
    applied = [j for j in jobs if j.application_status == "applied"]
    # Cross-platform applied signatures (company+core-title of everything applied anywhere).
    applied_keys = {_applied_key(j.company, j.title) for j in applied}
    searches = sorted({q for j in jobs for q in (j.search_queries or [])})
    already_applied = [j for j in jobs if _job_dict(j, applied_keys)["already_applied"]]
    with_desc = [j for j in jobs if (j.description or "").strip()]

    # Per-query rollup: how each search is doing (found / descriptions captured / applied) — the
    # table that answers "did the multi-page sweep actually work for this query".
    by_query: dict[str, dict[str, int]] = {}
    for j in jobs:
        for q in (j.search_queries or []):
            row = by_query.setdefault(q, {"found": 0, "with_description": 0, "applied": 0})
            row["found"] += 1
            if (j.description or "").strip():
                row["with_description"] += 1
            if j.application_status == "applied":
                row["applied"] += 1

    return {
        "platform": platform,
        "totals": {
            "jobs_found": len(jobs),
            "searches_performed": len(searches),
            "duplicates_collapsed": sum((j.seen_count or 1) - 1 for j in jobs),
            "distinct_companies": len({j.company for j in jobs if j.company}),
            "applied": len(applied),
            "with_description": len(with_desc),
            "already_applied_incl_cross_platform": len(already_applied),
            "by_status": by_status,
        },
        "searches": searches,
        "by_query": [{"query": q, **counts} for q, counts in sorted(by_query.items())],
        "jobs_seen": [_job_dict(j, applied_keys) for j in jobs[:100]],
        "jobs_applied": [_job_dict(j, applied_keys) for j in applied],
        "descriptions": [
            {**_job_dict(j, applied_keys), "salary": j.salary,
             "desc_chars": len(j.description or ""), "description": (j.description or "")[:6000]}
            for j in with_desc[:60]
        ],
        "most_seen": [_job_dict(j, applied_keys) for j in sorted(jobs, key=lambda x: x.seen_count or 1, reverse=True)[:10]],
    }


class FetchDescriptionsRequest(BaseModel):
    training_session_id: int
    job_ids: list[str] = []   # specific jobs; if empty, fetch the N most-seen unfetched
    limit: int = 8


@router.post("/api/jobs/fetch_descriptions")
async def fetch_job_descriptions(body: FetchDescriptionsRequest, db: Session = Depends(get_db)):
    """Click INTO postings to collect full job descriptions (+ salary, apply_type) — the
    richer signal that powers matching + resume tailoring. Targets specific job_ids, or the
    most-seen jobs that don't have a description yet. One viewjob navigation per job."""
    session = db.get(TrainingSession, body.training_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    browser_url = _session_browser_url(session)

    if body.job_ids:
        jobs = [db.get(ObservedJob, jid) for jid in body.job_ids]
        jobs = [j for j in jobs if j is not None]
    else:
        jobs = db.scalars(
            select(ObservedJob).where(ObservedJob.platform == "indeed",
                                      ObservedJob.description.is_(None))
            .order_by(ObservedJob.seen_count.desc()).limit(body.limit)).all()

    fetched = 0
    results = []
    async with httpx.AsyncClient(timeout=40.0) as client:
        for j in jobs[:body.limit]:
            try:
                r = await client.post(f"{settings.capture_server_url}/fetch_job_description",
                                      json={"external_id": j.external_id, "browser_url": browser_url})
                d = r.json()
            except httpx.HTTPError:
                continue
            if d.get("ok"):
                j.description = (d.get("description") or "")[:20000]
                j.salary = j.salary or (d.get("salary") or "")[:200] or None
                j.apply_type = d.get("apply_type") or j.apply_type
                fetched += 1
                results.append({"job_id": j.job_id, "title": j.title, "apply_type": j.apply_type,
                                "salary": j.salary, "desc_chars": len(j.description or "")})
            await asyncio.sleep(0.6)
    db.commit()
    return {"ok": True, "fetched": fetched, "results": results}


async def _list_session_tabs(browser_url: str) -> list[dict]:
    """List a session Chrome's open targets (GET /json). Raises 503 if unreachable — the sweep's
    pre-gate needs a live look at the tabs to detect a captcha frame. A seam for tests."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{browser_url}/json")
            r.raise_for_status()
            return r.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"session Chrome not reachable: {exc}")


async def _capture_post(path: str, payload: dict, timeout: float = 40.0) -> dict:
    """POST to the capture server and return parsed JSON (or {ok:False, detail} on transport error).
    The single seam the sweep loop goes through, so the whole multi-page orchestration is unit-testable
    by monkeypatching this one function (no browser needed)."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{settings.capture_server_url}{path}", json=payload)
            return r.json()
    except httpx.HTTPError as exc:
        return {"ok": False, "detail": str(exc)}


class SearchSweepRequest(BaseModel):
    training_session_id: int
    query: Optional[str] = None          # defaults to the active job-search target
    location: Optional[str] = None
    max_pages: Optional[int] = None      # clamped to BOUNDS["max_pages_per_query"]
    min_miles: int = 50                  # floored at BOUNDS["min_radius_miles"]
    max_details_per_page: int = 8        # cap on click-into-card detail fetches per page
    min_pause_seconds: Optional[float] = None  # base human pace between actions (slower runs)


def _sweep_stop(reason: str, **extra) -> dict:
    base = {"ok": False, "stopped_reason": reason, "pages_swept": 0, "jobs_found": 0,
            "new": 0, "shortlisted": 0, "descriptions_captured": 0}
    base.update(extra)
    return base


@router.post("/api/search/sweep")
async def search_sweep(body: SearchSweepRequest, db: Session = Depends(get_db)):
    """The bounded auto-sweep — the 'multi-page' Indeed task, end to end and human-paced:
    force the radius to >= min_miles by CLICKING the distance filter, then per results page extract
    every card, shortlist the ones matching the query (cheap/deterministic), CLICK INTO each
    shortlisted card to read its in-page detail pane (no viewjob URL-jump), and CLICK pagination to
    the next page — stopping at BOUNDS / a live captcha / logout. Stamps a fresh authenticated cadence
    run for provenance and persists incrementally, so a mid-sweep stop keeps the data already gathered.
    Returns the run summary; the dashboard tables read the persisted results."""
    import random
    import apply_state_store as store
    import escalation_rules
    import job_search_targets as jst
    import search_cadence

    session = db.get(TrainingSession, body.training_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    browser_url = _session_browser_url(session)

    target = jst.active_target() or {}
    query = (body.query or target.get("query") or "").strip()
    location = (body.location or target.get("location") or "").strip()
    min_miles = max(int(body.min_miles or 0), search_cadence.BOUNDS["min_radius_miles"])
    max_pages = min(int(body.max_pages or search_cadence.BOUNDS["max_pages_per_query"]),
                    search_cadence.BOUNDS["max_pages_per_query"])

    # --- PRE-GATE: never sweep under a live captcha or logged-out (bot-safety) -------------
    all_targets = await _list_session_tabs(browser_url)
    block = escalation_rules.detect_block_frames([t.get("url", "") for t in all_targets])
    block = await _refine_block_visibility(browser_url, block)
    if block and block.get("strength") == "active":
        return _sweep_stop("captcha", blocker=block)
    ab = await _capture_post("/auth_state", {"browser_url": browser_url}, timeout=8.0)
    if not (ab.get("ok") and ab.get("logged_in")):
        return _sweep_stop("not_authenticated")

    # provenance: a fresh authenticated run makes this sweep's data actionable downstream
    bb = store.load_or_create(body.training_session_id, query=query, location=location)
    store.start_cadence_run(bb, query=query, location=location, authed=True)
    bb.world["authed"] = True

    # --- DISTANCE: force >= min_miles by clicking the filter. If it can't be set, STOP — we never
    # gather sub-floor results (honors the always->=50mi rule). ---------------------------------
    dist = await _capture_post("/set_distance",
                               {"browser_url": browser_url, "tab_url": "indeed.com/jobs",
                                "min_miles": min_miles})
    if not dist.get("applied"):
        store.save(bb)
        return _sweep_stop("distance_filter_failed", distance=dist)
    await asyncio.sleep(1.0)

    # Human pace between actions. Defaults to the cadence bound; min_pause_seconds lets a run go
    # SLOWER (longer, more human-like pauses) on request — floored at the bound, never faster.
    pace_base = max(float(body.min_pause_seconds or 0),
                    float(search_cadence.BOUNDS["min_seconds_between_navigations"]))

    def _jitter(extra: float) -> float:
        return random.uniform(pace_base, pace_base + extra)

    pages_swept = total_found = total_new = total_short = total_desc = 0
    shortlist_refs: list[str] = list(bb.search_state.shortlist or [])
    stopped_reason = "max_pages"
    for _ in range(max_pages):
        ex = await _capture_post("/extract_jobs",
                                 {"browser_url": browser_url, "tab_url": "indeed.com/jobs"})
        cards = ex.get("jobs", []) if ex.get("ok") else []
        new_c, _dup = upsert_observed_jobs(db, cards, "indeed", query)
        db.commit()
        pages_swept += 1
        total_found += len(cards)
        total_new += new_c

        applied = db.scalars(
            select(ObservedJob).where(ObservedJob.application_status == "applied")).all()
        applied_keys = {_applied_key(j.company, j.title) for j in applied}
        shortlisted = _shortlist_jobs(cards, query, applied_keys)
        total_short += len(shortlisted)

        # CLICK INTO each shortlisted card (in-page pane) to grab the full description.
        for card in shortlisted[:body.max_details_per_page]:
            jid = f"indeed:{card.get('external_id')}"
            row = db.get(ObservedJob, jid)
            if row is None or (row.description or "").strip():
                continue  # missing or already captured — don't re-click
            d = await _capture_post("/open_job_card",
                                    {"browser_url": browser_url, "external_id": card.get("external_id")})
            if d.get("ok"):
                row.description = (d.get("description") or "")[:20000]
                row.salary = row.salary or (d.get("salary") or "")[:200] or None
                row.apply_type = d.get("apply_type") or row.apply_type
                total_desc += 1
                if jid not in shortlist_refs:
                    shortlist_refs.append(jid)
                db.commit()
            await asyncio.sleep(_jitter(2.0))

        nxt = await _capture_post("/next_page",
                                  {"browser_url": browser_url, "tab_url": "indeed.com/jobs"})
        if not nxt.get("has_next"):
            stopped_reason = "no_next_page"
            break
        await asyncio.sleep(_jitter(2.5))

    # fold sweep progress onto the blackboard's search_state (written-down, not re-derived)
    bb.search_state.page = pages_swept
    bb.search_state.observed_count = total_found
    bb.search_state.shortlist = shortlist_refs
    bb.log("sweep", f"{query!r} @ {min_miles}mi: {pages_swept}p, {total_found} found, "
                    f"{total_desc} descriptions ({stopped_reason})")
    store.save(bb)
    return {"ok": True, "stopped_reason": stopped_reason, "pages_swept": pages_swept,
            "jobs_found": total_found, "new": total_new, "shortlisted": total_short,
            "descriptions_captured": total_desc, "min_miles": min_miles,
            "distance_selected": dist.get("selected_miles"), "query": query, "location": location}


@router.patch("/api/jobs/{job_id:path}")
def update_job(job_id: str, body: JobStatusUpdate, db: Session = Depends(get_db)):
    """Update a job's application status (e.g. mark 'applied' after the apply flow)."""
    row = db.get(ObservedJob, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if body.application_status:
        row.application_status = body.application_status
        if body.application_status == "applied" and row.applied_at is None:
            row.applied_at = utcnow()
    if body.notes is not None:
        row.notes = body.notes
    db.commit()
    return _job_dict(row)


@router.post("/api/training/page-states")
def create_page_state(body: PageStateWrite, db: Session = Depends(get_db)):
    name = (body.display_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="display_name is required")
    state_id = (body.state_id or _slugify(name)).strip()
    if not state_id:
        raise HTTPException(status_code=400, detail="Could not derive a state id from the name")
    if body.scope not in ("global", "domain", "goal", "scenario"):
        raise HTTPException(status_code=400, detail="scope must be global|domain|goal|scenario")
    if body.scope == "domain" and not body.domain_id:
        raise HTTPException(status_code=400, detail="domain scope requires domain_id")
    if body.scope == "goal" and not body.goal_id:
        raise HTTPException(status_code=400, detail="goal scope requires goal_id")
    if body.scope == "scenario" and not body.scenario_id:
        raise HTTPException(status_code=400, detail="scenario scope requires scenario_id")

    existing = db.get(PageStateRegistry, state_id)
    if existing is not None:
        # Idempotent-ish: return the existing row rather than erroring (the labeler
        # may try to create one that already exists).
        return _page_state_dict(existing)

    row = PageStateRegistry(
        state_id=state_id,
        display_name=name,
        scope=body.scope,
        domain_id=body.domain_id if body.scope in ("domain", "goal", "scenario") else None,
        goal_id=body.goal_id if body.scope in ("goal", "scenario") else None,
        scenario_id=body.scenario_id if body.scope == "scenario" else None,
        category=(body.category or "general").strip() or "general",
        stage=(body.stage or None),
        description=body.description,
        status="active",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _page_state_dict(row)


@router.patch("/api/training/page-states/{state_id}")
def update_page_state(state_id: str, body: PageStateUpdate, db: Session = Depends(get_db)):
    row = db.get(PageStateRegistry, state_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Page state not found")
    for field in ("display_name", "scope", "domain_id", "goal_id", "scenario_id", "category", "stage", "description", "status"):
        val = getattr(body, field)
        if val is not None:
            setattr(row, field, val)
    db.commit()
    db.refresh(row)
    return _page_state_dict(row)


@router.get("/api/training/page-states/candidates")
def list_page_state_candidates(limit: int = 100, db: Session = Depends(get_db)):
    """The promotion queue: page states the agent OBSERVED but that nobody has blessed yet.

    Deliberately NOT returned by `GET /api/training/page-states` (which filters
    `status == "active"`) — an unapproved guess must never reach a labeler menu or become a
    training label. Approve one with `PATCH /api/training/page-states/{state_id}
    {"status": "active"}`; that single flip is the whole promotion step."""
    import page_state_candidates
    return {"candidates": page_state_candidates.list_candidates(db, limit=limit)}


@router.delete("/api/training/page-states/{state_id}")
def archive_page_state(state_id: str, db: Session = Depends(get_db)):
    row = db.get(PageStateRegistry, state_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Page state not found")
    row.status = "archived"
    db.commit()
    return {"ok": True}


# ===== Action registry (the action vocabulary, user-extensible) =====

def _action_dict(a: ActionRegistry) -> dict:
    return {
        "action_id": a.action_id,
        "label": a.label,
        "value_label": a.value_label,
        "is_builtin": a.is_builtin,
        "sort_order": a.sort_order,
    }


class ActionWrite(BaseModel):
    label: str
    value_label: Optional[str] = None
    action_id: Optional[str] = None  # optional explicit slug; else from label


@router.get("/api/training/actions")
def list_actions(db: Session = Depends(get_db)):
    rows = db.scalars(
        select(ActionRegistry).where(ActionRegistry.status == "active")
    ).all()
    rows.sort(key=lambda a: (a.sort_order, a.label))
    return [_action_dict(a) for a in rows]


@router.post("/api/training/actions")
def create_action(body: ActionWrite, db: Session = Depends(get_db)):
    label = (body.label or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="label is required")
    action_id = (body.action_id or _slugify(label)).strip()
    if not action_id:
        raise HTTPException(status_code=400, detail="Could not derive an action id from the label")
    existing = db.get(ActionRegistry, action_id)
    if existing is not None:
        if existing.status != "active":
            existing.status = "active"
            db.commit()
        return _action_dict(existing)
    row = ActionRegistry(
        action_id=action_id,
        label=label,
        value_label=(body.value_label or "Optional Payload"),
        is_builtin=False,
        sort_order=500,  # custom actions sort after built-ins
        status="active",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _action_dict(row)


@router.delete("/api/training/actions/{action_id}")
def archive_action(action_id: str, db: Session = Depends(get_db)):
    row = db.get(ActionRegistry, action_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Action not found")
    if row.is_builtin:
        raise HTTPException(status_code=400, detail="Built-in actions can't be removed")
    row.status = "archived"
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Session manager — one honest, live view of every browser session the platform owns, folded with
# a real CDP liveness probe (not the DB status, which goes stale on restart), the account each runs
# as, and a human-owned "protect" flag. This is the "be cognizant of my sessions" surface so a new
# run can never blindside a live one.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Accounts — configure multiple accounts per domain, in-app. Metadata only; the secret stays in
# .env (or a stronger local backend later) and is NEVER returned by these endpoints.
# ---------------------------------------------------------------------------
@router.get("/api/training/sessions", response_model=list[TrainingSessionRead])
def list_training_sessions(db: Session = Depends(get_db)):
    stmt = select(TrainingSession).order_by(TrainingSession.created_at.desc())
    return db.scalars(stmt).all()


@router.post("/api/training/sessions", response_model=TrainingSessionRead)
def create_training_session(body: TrainingSessionCreate, db: Session = Depends(get_db)):
    domain = db.get(DomainRegistry, body.domain_id)
    scenario = db.get(ScenarioRegistry, body.scenario_id)
    if domain is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    if scenario.domain_id != body.domain_id:
        raise HTTPException(status_code=400, detail="Scenario is not allowed for the selected domain")

    purpose = body.purpose if body.purpose in {"data_collection", "production"} else "data_collection"

    # Bind an account -> its persistent profile is what isolates this session's Chrome (cookies,
    # login) from every other account's. An explicit persistent_profile still wins if given.
    account_id = body.account_id or None
    persistent_profile = body.persistent_profile or None
    if account_id:
        import accounts as accounts_mod
        acct = accounts_mod.get_account(account_id)
        if acct is None:
            raise HTTPException(status_code=404, detail=f"Account '{account_id}' not found")
        persistent_profile = persistent_profile or acct["profile"]

    session = TrainingSession(
        domain_id=body.domain_id,
        scenario_id=body.scenario_id,
        goal_id=scenario.goal_id,
        task_id=scenario.task_id,
        capture_profile=scenario.capture_profile_override or (domain.capture_defaults or {}).get("profile", "viewport"),
        purpose=purpose,
        account_id=account_id,
        persistent_profile=persistent_profile,
        notes=body.notes,
        status="draft",
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.post("/api/training/sessions/{session_id}/start", response_model=TrainingSessionRead)
def start_training_session(session_id: int, db: Session = Depends(get_db)):
    session = db.get(TrainingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    return _launch_training_chrome(db, session)


@router.post("/api/training/sessions/{session_id}/stop", response_model=TrainingSessionRead)
def stop_training_session(session_id: int, force: bool = False, db: Session = Depends(get_db)):
    import session_manager
    session = db.get(TrainingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    allowed, reason = session_manager.may_touch(protected=session.protected, action="stop", force=force)
    if not allowed:
        raise HTTPException(status_code=409, detail=reason)
    _stop_training_chrome(session)
    now = utcnow()
    session.status = "stopped"
    session.chrome_stopped_at = now
    session.completed_at = now
    db.commit()
    db.refresh(session)
    return session


@router.delete("/api/training/sessions/{session_id}")
def delete_training_session(session_id: int, force: bool = False, db: Session = Depends(get_db)):
    """Wipe one training session and everything it owns.

    Cascades to: captures (via SQLAlchemy relationship), artifact JSONs,
    screenshot PNGs, .meta.json sidecars, .vision.json sidecars. Stops any
    active Chrome process for this session first.

    Registry rows (domains, goals, tasks, scenarios) are NOT touched —
    those are configuration, not session data.

    A protected (human-owned) session refuses deletion unless force=true, so a stray
    click can't destroy a live session's captures.
    """
    import session_manager
    session = db.get(TrainingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    allowed, reason = session_manager.may_touch(protected=session.protected, action="delete", force=force)
    if not allowed:
        raise HTTPException(status_code=409, detail=reason)

    # Stop Chrome before tearing down the DB row that holds its PID
    _stop_training_chrome(session)

    # Snapshot artifact filenames before the cascade-delete removes the rows
    artifact_filenames = [capture.artifact_filename for capture in session.captures]
    capture_count = len(artifact_filenames)

    db.delete(session)
    db.commit()

    deleted_files = 0
    for filename in artifact_filenames:
        if _delete_observation_files(filename):
            deleted_files += 1

    return {
        "ok": True,
        "deleted_session_id": session_id,
        "deleted_captures": capture_count,
        "deleted_files": deleted_files,
    }


@router.post("/api/training/reset")
def reset_training_data(db: Session = Depends(get_db)):
    """Clean-slate operation: wipe ALL training sessions and ALL capture artifacts.

    Preserves: registry (domains, goals, tasks, scenarios). Chrome profile
    directories on disk are left in place — they're cheap and re-used by id.

    Stops any active Chrome processes first. Also sweeps any orphaned
    .vision.json sidecars whose parent artifact is already gone.
    """
    sessions = db.scalars(select(TrainingSession)).all()

    artifact_filenames: list[str] = []
    for session in sessions:
        _stop_training_chrome(session)
        artifact_filenames.extend(capture.artifact_filename for capture in session.captures)

    session_count = len(sessions)
    for session in sessions:
        db.delete(session)
    db.commit()

    deleted_files = 0
    for filename in artifact_filenames:
        if _delete_observation_files(filename):
            deleted_files += 1

    # Sweep orphans — any leftover trace/sidecar files in the dir that aren't
    # tied to a tracked artifact (e.g. from crashed captures pre-this-cleanup).
    traces_dir = _artifacts_dir() / "observer-traces"
    screenshots_dir = _artifacts_dir() / "observer-screenshots"
    orphan_files = 0
    if traces_dir.exists():
        for path in traces_dir.iterdir():
            try:
                path.unlink()
                orphan_files += 1
            except Exception:
                pass
    if screenshots_dir.exists():
        for path in screenshots_dir.iterdir():
            try:
                path.unlink()
                orphan_files += 1
            except Exception:
                pass

    return {
        "ok": True,
        "deleted_sessions": session_count,
        "deleted_captures": len(artifact_filenames),
        "deleted_files": deleted_files,
        "swept_orphans": orphan_files,
    }


@router.get("/api/training/sessions/{session_id}/tabs")
async def list_training_session_tabs(session_id: int, db: Session = Depends(get_db)):
    session = db.get(TrainingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    if session.status != "active":
        raise HTTPException(status_code=400, detail="Training session is not active")
    browser_url = _session_browser_url(session)
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{browser_url}/json")
            r.raise_for_status()
            targets = r.json()
            return [
                {
                    "id": t["id"],
                    "title": t.get("title", ""),
                    "url": t.get("url", ""),
                    "faviconUrl": t.get("faviconUrl", ""),
                }
                for t in targets
                if t.get("type") == "page" and not any(pat in t.get("url", "") for pat in _SELF_URL_PATTERNS)
            ]
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Training session Chrome not reachable: {exc}")


@router.get("/api/training/sessions/{session_id}/captures", response_model=list[TrainingCaptureRead])
def list_training_session_captures(session_id: int, db: Session = Depends(get_db)):
    stmt = (
        select(TrainingCapture)
        .where(TrainingCapture.training_session_id == session_id)
        .order_by(TrainingCapture.captured_at.desc())
    )
    return db.scalars(stmt).all()


@router.get("/api/runs", response_model=list[RunRead])
def list_runs(db: Session = Depends(get_db)):
    stmt = select(Run).options(selectinload(Run.steps)).order_by(Run.id.desc())
    runs = db.scalars(stmt).all()
    return runs


@router.post("/api/runs", response_model=RunCreateResponse)
def create_run(db: Session = Depends(get_db)):
    run = Run(status="PENDING")

    run.steps = [
        Step(order_index=1, type="OBSERVE", status="PENDING", payload="initial observation"),
        Step(order_index=2, type="WAIT", status="PENDING", payload="wait 1 second"),
        Step(order_index=3, type="OBSERVE", status="PENDING", payload="post-wait observation"),
    ]

    db.add(run)
    db.commit()
    db.refresh(run)

    return run


@router.post("/api/workers/{worker_id}/heartbeat", response_model=WorkerHeartbeatResponse)
def worker_heartbeat(worker_id: str, body: WorkerHeartbeatIn, db: Session = Depends(get_db)):
    worker = db.get(Worker, worker_id)

    if worker is None:
        worker = Worker(id=worker_id, status=body.status, last_seen_at=utcnow())
        db.add(worker)
    else:
        worker.status = body.status
        worker.last_seen_at = utcnow()

    db.commit()
    db.refresh(worker)
    return worker


@router.get("/api/workers/{worker_id}/next-step", response_model=Optional[StepLeaseResponse])
def get_next_step(worker_id: str, db: Session = Depends(get_db)):
    worker = db.get(Worker, worker_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found")

    now = utcnow()

    stmt = (
        select(Step)
        .join(Run)
        .where(Step.status == "PENDING")
        .where(Run.status.in_(["PENDING", "RUNNING"]))
        .order_by(Run.id.asc(), Step.order_index.asc())
    )

    step = db.scalars(stmt).first()
    if step is None:
        return None

    step.status = "LEASED"
    step.assigned_worker_id = worker_id
    step.lease_expires_at = now + timedelta(seconds=60)
    step.started_at = now

    run = db.get(Run, step.run_id)
    if run and run.status == "PENDING":
        run.status = "RUNNING"
        run.started_at = now

    worker.current_run_id = step.run_id
    worker.current_step_id = step.id
    worker.last_seen_at = now

    db.commit()
    db.refresh(step)

    return StepLeaseResponse(
        id=step.id,
        run_id=step.run_id,
        order_index=step.order_index,
        type=step.type,
        status=step.status,
        payload=step.payload,
    )


@router.post("/api/steps/{step_id}/result")
def post_step_result(step_id: int, body: StepResultIn, db: Session = Depends(get_db)):
    step = db.get(Step, step_id)
    if step is None:
        raise HTTPException(status_code=404, detail="Step not found")

    if step.status != "LEASED":
        raise HTTPException(status_code=400, detail="Step is not currently leased")

    step.status = body.status
    step.completed_at = utcnow()
    step.payload = body.result_payload or step.payload

    run = db.get(Run, step.run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    all_steps = db.scalars(
        select(Step).where(Step.run_id == run.id).order_by(Step.order_index.asc())
    ).all()

    if all(s.status == "SUCCESS" for s in all_steps):
        run.status = "SUCCESS"
        run.completed_at = utcnow()
    elif any(s.status == "FAILED" for s in all_steps):
        run.status = "FAILED"
        run.completed_at = utcnow()
    else:
        run.status = "RUNNING"

    if step.assigned_worker_id:
        worker = db.get(Worker, step.assigned_worker_id)
        if worker:
            worker.current_step_id = None
            if run.status in ["SUCCESS", "FAILED"]:
                worker.current_run_id = None
            worker.last_seen_at = utcnow()

    db.commit()

    return {"ok": True, "run_status": run.status}


# URLs that belong to the control panel itself — never offer these as capture targets
_SELF_URL_PATTERNS = ("localhost:5173", "localhost:3000", "127.0.0.1:5173", "127.0.0.1:3000")


class CaptureRequest(BaseModel):
    training_session_id: int
    # tab_url is the REAL handle: chrome-devtools-mcp's list_pages exposes only an index + URL, no
    # CDP targetId, so a tab_id cannot address a page (see the 2026-07-15 capture entry). Requiring
    # tab_id forced callers to pass an id the capture then couldn't honour. Pass a tab_url that
    # matches exactly ONE page; the capture now refuses rather than grab the wrong tab.
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    scenario: str = "training_capture"


@router.get("/api/tabs")
async def list_tabs():
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{settings.chrome_cdp_url}/json")
            r.raise_for_status()
            targets = r.json()
            return [
                {
                    "id": t["id"],
                    "title": t.get("title", ""),
                    "url": t.get("url", ""),
                    "faviconUrl": t.get("faviconUrl", ""),
                }
                for t in targets
                if t.get("type") == "page"
                and not any(pat in t.get("url", "") for pat in _SELF_URL_PATTERNS)
            ]
    except Exception as exc:
        return {"tabs": [], "warning": f"Chrome not reachable: {exc}"}


@router.post("/api/capture")
async def trigger_capture(body: CaptureRequest, db: Session = Depends(get_db)):
    session = db.get(TrainingSession, body.training_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    if session.status != "active":
        raise HTTPException(status_code=400, detail="Training session is not active")
    goal = db.get(GoalRegistry, session.goal_id)
    if goal is None:
        raise HTTPException(status_code=400, detail="Training session goal is missing")
    scenario = db.get(ScenarioRegistry, session.scenario_id) if session.scenario_id else None

    training_metadata = {
        "captured_at": utcnow().isoformat(),
        "browser_session_id": session.browser_session_id,
        "domain_id": session.domain_id,
        "scenario_id": session.scenario_id,
        "goal_id": session.goal_id,
        "task_id": session.task_id,
        "action_type_hint": _session_action_hint(goal),
        "notes": session.notes,
        "capture_profile": session.capture_profile,
        "tab_id": body.tab_id,
        # Vision training context — sent to capture server so it can embed the query
        "element_query": scenario.element_query if scenario else None,
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{settings.capture_server_url}/capture",
                json={
                    "tab_id": body.tab_id,
                    "tab_url": body.tab_url,
                    "scenario": body.scenario,
                    "browser_url": _session_browser_url(session),
                    "task_context": {
                        "goal": goal.display_name,
                        "action_type_hint": _session_action_hint(goal),
                    },
                    "training_metadata": training_metadata,
                },
            )
            r.raise_for_status()
            payload = r.json()
            if payload.get("filename"):
                trace_path = _artifacts_dir() / "observer-traces" / payload["filename"]
                artifact = json.loads(trace_path.read_text())
                capture_record = TrainingCapture(
                    training_session_id=session.id,
                    artifact_filename=payload["filename"],
                    candidate_count=payload.get("candidate_count", 0),
                    # The faucet's yield: how many CDP-AX candidates this drive produced. 0 means
                    # the sidecar came back empty (unreachable tab / stale node-ids) — the capture
                    # exists but taught Select nothing. Recorded so it's queryable, not file-stat'd.
                    ax_candidate_count=payload.get("ax_candidate_count", 0),
                    **_capture_metadata_from_artifact(
                        artifact=artifact,
                        session=session,
                        goal=goal,
                        scenario=scenario,
                        tab_id=body.tab_id,
                    ),
                )
                db.add(capture_record)
                db.commit()
                db.refresh(capture_record)
                payload["training_capture_id"] = capture_record.id
            return payload
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail=f"mcp capture server not reachable at {settings.capture_server_url}",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class ExecuteActionRequest(BaseModel):
    """Drive one action against a session's live tab via the interim CDP executor.
    Provide a target either explicitly (target_bbox in screenshot px) or by resolving
    it from a prior capture (filename + candidate_id, looked up in the .ax.json sidecar)."""
    training_session_id: int
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    action_id: str = "click"
    value: Optional[str] = None
    target_bbox: Optional[dict] = None
    filename: Optional[str] = None        # resolve bbox from this capture's AX sidecar
    candidate_id: Optional[str] = None    # ...for this candidate
    driver: Optional[str] = None          # 'direct' (default) | 'record_only' (dry-run)


@router.post("/api/runtime/execute")
async def runtime_execute(body: ExecuteActionRequest, db: Session = Depends(get_db)):
    """INTERIM EXECUTOR proxy — the v2-bypass that lets us advance flows during burst
    training. Resolves the session's Chrome port and the target bbox, then proxies to the
    capture server's CDP DirectDriver. Either pass target_bbox directly, or (filename +
    candidate_id) to pull the bbox + device_scale_factor from that capture's AX sidecar."""
    session = db.get(TrainingSession, body.training_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    browser_url = _session_browser_url(session)

    bbox = body.target_bbox
    dsf = 1.0
    backend_node_id = None
    if bbox is None:
        if not (body.filename and body.candidate_id):
            raise HTTPException(status_code=400, detail="Provide target_bbox, or filename + candidate_id")
        sidecar = _artifacts_dir() / "observer-traces" / f"{body.filename}.ax.json"
        if not sidecar.exists():
            raise HTTPException(status_code=404, detail="AX sidecar not found for that capture")
        proposals = json.loads(sidecar.read_text()).get("proposals", [])
        cand = next((c for c in proposals if c.get("candidate_id") == body.candidate_id), None)
        if cand is None:
            raise HTTPException(status_code=404, detail="candidate_id not found in capture")
        bbox = cand.get("bbox")
        dsf = float((cand.get("_debug") or {}).get("dpr", 1.0) or 1.0)
        backend_node_id = cand.get("backend_node_id")  # enables robust element-based action

    payload = {
        "action_id": body.action_id, "target_bbox": bbox, "value": body.value,
        "backend_node_id": backend_node_id,
        "device_scale_factor": dsf, "tab_id": body.tab_id, "tab_url": body.tab_url,
        "browser_url": browser_url, "driver": body.driver,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{settings.capture_server_url}/execute", json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"executor unreachable: {exc}")


# ---------------------------------------------------------------------------
# Observation artifact endpoints
# ---------------------------------------------------------------------------

@router.get("/api/observations/screenshots/{filename}")
def get_observation_screenshot(filename: str):
    path = _artifacts_dir() / "observer-screenshots" / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(str(path))


@router.get("/api/observations/{filename}")
def get_observation(filename: str, db: Session = Depends(get_db)):
    traces_dir = _artifacts_dir() / "observer-traces"
    path = traces_dir / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Observation not found")
    data = json.loads(path.read_text())
    capture = db.scalar(select(TrainingCapture).where(TrainingCapture.artifact_filename == filename))
    meta = read_meta(traces_dir, filename)
    if capture is not None:
        meta["training_annotation"] = _training_annotation_from_capture(capture)
    data["meta"] = meta

    # CDP-AX candidates (PRIMARY proposer) — written at capture-time while the
    # browser was live. This is the main candidate source going forward.
    ax_sidecar_path = traces_dir / f"{filename}.ax.json"
    if ax_sidecar_path.exists():
        try:
            ax_sidecar = json.loads(ax_sidecar_path.read_text())
            data["ax_candidates"] = ax_sidecar.get("proposals", [])
            data["ax_candidates_meta"] = {
                "version": ax_sidecar.get("version"),
                "generated_at": ax_sidecar.get("generated_at"),
                "proposal_count": ax_sidecar.get("proposal_count", 0),
                "stats": ax_sidecar.get("stats", {}),
            }
        except Exception:
            data["ax_candidates"] = []
    else:
        data["ax_candidates"] = []

    # Super-fallback slot (was OmniParser) — REMOVED from the runtime/labeler path
    # 2026-06-14. OmniParser was the wrong fit; a better vision-native grounder will
    # fill this slot ONLY when AX yields zero candidates (canvas/icon-only). Choice
    # is TBD and parked until we actually hit that case. Returned empty so the labeler
    # renders nothing for this source. (OmniParser code still backs the dropped
    # two-stage EVAL baseline via mcp /proposer/predict — that's separate.)
    data["vision_candidates"] = []

    return data


@router.post("/api/observations/{filename}/select")
def select_element(filename: str, element_query: str, cache_only: bool = False,
                   db: Session = Depends(get_db)):
    """SELECT stage: run the inner-loop cascade (cache → Haiku SoM → escalate) to
    ground `element_query` against this capture's CDP-AX candidates. Returns the
    chosen candidate + which layer answered + cost, or an escalate result. Haiku
    is budget-gated; over budget escalates to a human. Lets you try the select
    stage against any captured page.

    `cache_only=true` runs ONLY the free local layers (no paid Haiku) — hand a
    decision to "the kids" and see if the flywheel already knows it."""
    from select_stage import selector
    from select_stage.schema import candidates_from_ax

    traces_dir = _artifacts_dir() / "observer-traces"
    artifact_path = traces_dir / filename
    if not artifact_path.exists():
        raise HTTPException(status_code=404, detail="Observation not found")
    artifact = json.loads(artifact_path.read_text())

    ax_sidecar = traces_dir / f"{filename}.ax.json"
    ax_candidates = []
    if ax_sidecar.exists():
        ax_candidates = json.loads(ax_sidecar.read_text()).get("proposals", [])

    shots = (artifact.get("acquisition", {}) or {}).get("screenshots") or []
    if not shots:
        raise HTTPException(status_code=400, detail="Capture has no screenshot")
    screenshot_path = shots[0].get("path") or (
        _artifacts_dir() / "observer-screenshots" / shots[0].get("filename", "")
    )
    acq = artifact.get("acquisition", {}) or {}
    url = (acq.get("page_identity", {}) or {}).get("url", "")
    page_text = (acq.get("js_state", {}) or {}).get("body_text_preview", "") or ""
    viewport = acq.get("viewport_state", {}) or acq.get("training_metadata", {}) or {}
    dom_clickables = acq.get("actionable_elements", []) or []

    result = selector.select(
        url=url,
        task_goal=element_query,
        ax_candidates=ax_candidates,
        screenshot_path=screenshot_path,
        viewport=viewport,
        page_text=page_text,
        dom_clickables=dom_clickables,
        meta={"filename": filename},
        cache_only=cache_only,
    )
    # Frozen 5-field contract + the resolved candidate (for convenience) + status.
    resolved = next((c for c in candidates_from_ax(ax_candidates)
                     if c.backend_node_id == result.target_backend_node_id), None)
    return {
        "status": result.status,
        "action_id": result.action_id.value,
        "target_backend_node_id": result.target_backend_node_id,
        "confidence": result.confidence,
        "needs_human": result.needs_human,
        "reason_code": result.reason_code.value,
        "layer": result.layer,
        "cost_usd": result.cost_usd,
        "fingerprint": result.fingerprint,
        "candidate": ({"role": resolved.role, "name": resolved.name} if resolved else None),
    }


# Canonical INTENT strings — the item-agnostic goal for each create-listing decision. The fingerprint
# bakes in the goal, so the cache only generalizes across items when the goal is canonical (not the
# per-item element_query we labelled with). Maps (state, golden role, golden name) -> canonical goal.
def _canonical_goal(state: str, role: str, name: str) -> Optional[str]:
    state, role, name = (state or ""), (role or "").lower(), (name or "")
    nl = name.lower()
    if nl == "marketplace":                         return "go to marketplace"
    if "create new listing" in nl:                  return "start a new listing"
    if nl.startswith("item for sale"):              return "choose the item-for-sale listing type"
    if role == "textbox" and nl == "title":         return "enter the item title"
    if role == "textbox" and nl == "price":         return "enter the item price"
    if role == "textbox" and nl == "description":   return "enter the item description"
    if "clothing & shoes" in nl:                    return "choose the item category"
    if "condition" in state:                        return "choose the item condition"
    if nl == "next":                                return "advance to the next step"
    if nl == "publish":                             return "publish the listing"
    return None


@router.post("/api/training/seed_cache")
def seed_selection_cache(domain: Optional[str] = None, db: Session = Depends(get_db)):
    """Seed the SELECT cache (the free Layer-2 inner loop) from our GOLDEN labels: for every reviewed
    capture that has a positive_candidate_id, store (fingerprint, element_query) -> chosen role+name+
    action. That lets the cache REPRODUCE a taught pick for $0 (no Haiku) on any future state whose
    fingerprint matches — i.e. a labeled correction literally trains the cheapest 'kid'. Idempotent
    (overwrites by key). Returns {seeded, skipped, total_golden}."""
    from select_stage import cache as sel_cache
    from select_stage import fingerprint as fp_mod
    from select_stage.schema import ActionId, candidates_from_ax

    traces_dir = _artifacts_dir() / "observer-traces"
    q = select(TrainingCapture).where(TrainingCapture.positive_candidate_id.isnot(None))
    if domain:
        q = q.where(TrainingCapture.domain_id == domain)
    caps = db.scalars(q).all()
    seeded, skipped = 0, 0
    debug_reasons = []
    for cap in caps:
        sidecar = traces_dir / f"{cap.artifact_filename}.ax.json"
        art_path = traces_dir / cap.artifact_filename
        task_goal = (cap.element_query or "").strip()
        if not (sidecar.exists() and art_path.exists() and task_goal):
            skipped += 1
            if len(debug_reasons) < 5:
                debug_reasons.append("guard: sc=%s art=%s goal=%s" % (sidecar.exists(), art_path.exists(), bool(task_goal)))
            continue
        try:
            ax = json.loads(sidecar.read_text()).get("proposals", [])
            gold = next((c for c in ax if c.get("candidate_id") == cap.positive_candidate_id), None)
            chosen = next((c for c in candidates_from_ax(ax)
                           if gold and c.backend_node_id == gold.get("backend_node_id")), None)
            if chosen is None:
                skipped += 1
                continue
            acq = (json.loads(art_path.read_text()).get("acquisition", {}) or {})
            url = (acq.get("page_identity", {}) or {}).get("url", "")
            # Use the SAME viewport fallback as the /select endpoint so fingerprints match exactly.
            viewport = acq.get("viewport_state", {}) or acq.get("training_metadata", {}) or {}
            fp = fp_mod.compute(url=url, viewport=viewport, candidates=ax,
                                task_goal=task_goal, dom_clickables=acq.get("actionable_elements", []) or [])
            # Derive the action from the chosen element's role (no action_type column on the model):
            # a textbox is typed into, everything else is clicked.
            action = ActionId.TYPE if (chosen.role or "").lower() == "textbox" else ActionId.CLICK
            sel_cache.store(fingerprint=fp, task_goal=task_goal, chosen=chosen,
                            action_id=action, source="golden_label")
            seeded += 1
            # ALSO store under the canonical INTENT so the pick generalizes across items (the goal is
            # part of the fingerprint, so we must recompute the fp with the canonical goal).
            canon = _canonical_goal(cap.observed_page_state, chosen.role, chosen.name)
            if canon:
                fp_c = fp_mod.compute(url=url, viewport=viewport, candidates=ax,
                                      task_goal=canon, dom_clickables=acq.get("actionable_elements", []) or [])
                sel_cache.store(fingerprint=fp_c, task_goal=canon, chosen=chosen,
                                action_id=action, source="golden_label_canonical")
        except Exception as exc:  # noqa: BLE001 — best-effort per capture; skip the malformed ones
            skipped += 1
            if len(debug_reasons) < 5:
                debug_reasons.append("exc: %s" % repr(exc)[:120])
    return {"seeded": seeded, "skipped": skipped, "total_golden": len(caps), "debug": debug_reasons}


@router.post("/api/observations/verify")
def verify_action(before: str, after: str, action_id: str = "click",
                  target_backend_node_id: Optional[int] = None, expected_value: Optional[str] = None):
    """ActionVerifierV1: did the action produce the predicted change between the
    BEFORE and AFTER capture? Returns ok + observed delta + next_step (ok/retry/
    escalate). Lets you test the verifier against any two captures."""
    from select_stage import verifier

    traces_dir = _artifacts_dir() / "observer-traces"

    def _snap(fn: str):
        ap = traces_dir / fn
        if not ap.exists():
            raise HTTPException(status_code=404, detail=f"Observation not found: {fn}")
        art = json.loads(ap.read_text())
        sc = traces_dir / f"{fn}.ax.json"
        ax = json.loads(sc.read_text()).get("proposals", []) if sc.exists() else []
        return verifier.snapshot_from_artifact(art, ax)

    res = verifier.verify(action_id=action_id, before=_snap(before), after=_snap(after),
                          target_backend_node_id=target_backend_node_id, expected_value=expected_value)
    return {"ok": res.ok, "predicted": res.predicted, "observed": res.observed,
            "reason": res.reason, "next_step": verifier.next_step(res, 0)}


def _observation_from_capture(filename: str):
    """Build a runtime `Observation` from a stored capture + its CDP-AX sidecar.

    Returns `(observation, capture_goal)`. `capture_goal` is the goal recorded at
    capture time (`task_context.goal`), used as the batch default. Raises 404 if the
    capture file is missing. A capture with no `.ax.json` sidecar yields an empty
    candidate list — the select stage will escalate (no_match), which is itself a
    valid corpus row (it tells us the propose stage produced nothing to pick).

    The artifact→Observation build is shared with the LiveProposer (runtime.live)."""
    from runtime import observation_from_trace

    traces_dir = _artifacts_dir() / "observer-traces"
    try:
        return observation_from_trace(traces_dir, filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Observation not found")


@router.post("/api/runtime/run")
def runtime_run(filename: str, task_goal: str, max_steps: int = 1):
    """Run the per-step runtime loop (classify → propose → select → act → verify)
    against a captured page, RECORD-ONLY. The default RecordOnlyActor logs the
    decided intent and executes nothing — no cursor moves, no clicks — so this is
    safe to run against any capture. Propose is backed by the capture's CDP-AX
    sidecar (a single static observation), so a record-only run resolves one step:
    classify the page, select a target, record the intent, then stop for a human.

    This is the safe first wiring of the loop end-to-end. The live multi-step driver
    (repeated captures + a real executor driver) is the next increment and is gated
    behind explicit go-ahead — see PROJECT_STATUS."""
    from dataclasses import asdict

    from runtime import run_loop

    observation, _ = _observation_from_capture(filename)

    # Single static observation → record-only resolves exactly one step and stops.
    result = run_loop(task_goal=task_goal, proposer=lambda: observation, max_steps=max_steps)
    return {
        "status": result.status.value,
        "reason": result.reason,
        "escalation_reason": result.escalation_reason,
        "total_cost_usd": result.total_cost_usd,
        "steps": [asdict(s) for s in result.steps],
    }


class RunLiveRequest(BaseModel):
    """Drive the runtime loop against a session's LIVE tab, re-observing between steps.

    Unlike `/api/runtime/run` (a single static capture, record-only), this observes the
    real page, executes each decided action via the CDP driver, checks the captcha gate
    before every action, and repeats until the task is done, the budget/steps run out, or
    it escalates to a human (which writes a handoff record + fires a notification).

    Execution is ON by default (humanized driver). Set `record_only=True` for a dry run
    that logs intents without firing input. `tab_url`/`tab_id` pick which existing tab to
    drive — continue from the tab that's already open (never churn tabs)."""
    training_session_id: int
    task_goal: str
    task: Optional[str] = None          # explicit TaskSpec name; else inferred from task_goal
    max_steps: int = 12
    max_retries: int = 2                # attempts per step before giving up (initial + N retries)
    tab_id: Optional[str] = None
    tab_url: Optional[str] = None
    driver: str = "humanized"           # humanized (default) | direct | record_only
    record_only: bool = False           # dry run: decide + log intents, fire nothing
    captcha_diagnostic: bool = True     # after a step's attempts fail, probe for a captcha last
    allow_submit: bool = False          # allow irreversible submit/final-apply without a handoff
    listing_draft_id: Optional[str] = None   # create-listing runs fill form fields from this draft


@router.post("/api/runtime/run_live")
def runtime_run_live(body: RunLiveRequest, db: Session = Depends(get_db)):
    """Run the loop live: observe → select (cache/practiced → Haiku) → gate → act → verify,
    repeating until done/escalation/budget. Practiced states resolve FREE from the selection
    cache (no Claude); the loop only pings a human at a gate. On escalation it emits a durable
    handoff (why + what it tried) and a macOS notification."""
    from dataclasses import asdict

    import auth_gate
    import task_spec
    from runtime import LiveActor, LiveProposer, LoopResult, LoopStatus, PrimedProposer, run_loop
    from runtime import gate as gate_mod
    from runtime import handoff as handoff_mod
    from runtime.live import observation_from_trace

    session = db.get(TrainingSession, body.training_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    browser_url = _session_browser_url(session)
    traces_dir = _artifacts_dir() / "observer-traces"
    server = settings.capture_server_url

    # Turn every live capture into LABELABLE training exhaust: persist each NON-EMPTY capture as a
    # TrainingCapture row, deduped WITHIN this run by state fingerprint so a revisited screen doesn't
    # flood the corpus (downstream run_batch already dedups cross-run). Empty (0-candidate) sidecars
    # are skipped — they carry no Select data. Best-effort; a failure here never breaks the drive.
    from select_stage import fingerprint as _fp
    _live_goal = db.get(GoalRegistry, session.goal_id) if session.goal_id else None
    _live_scenario = db.get(ScenarioRegistry, session.scenario_id) if session.scenario_id else None
    _seen_fps: set[str] = set()
    recorded_captures = {"captures": 0, "skipped_empty": 0, "skipped_duplicate": 0}

    def _persist_live_capture(filename: str, ax_count: int) -> None:
        if ax_count <= 0:
            recorded_captures["skipped_empty"] += 1
            return
        try:
            artifact = json.loads((traces_dir / filename).read_text())
            obs, _ = observation_from_trace(traces_dir, filename)
            fp = _fp.compute(url=obs.url, viewport=obs.viewport,
                             candidates=obs.ax_candidates, task_goal=body.task_goal or "")
            if fp in _seen_fps:
                recorded_captures["skipped_duplicate"] += 1
                return
            _seen_fps.add(fp)
            if db.scalar(select(TrainingCapture.id).where(TrainingCapture.artifact_filename == filename)):
                return  # already a DB capture (e.g. also taken via /api/capture)
            db.add(TrainingCapture(
                training_session_id=session.id, artifact_filename=filename,
                candidate_count=len(obs.ax_candidates), ax_candidate_count=ax_count,
                review_status="draft",
                **_capture_metadata_from_artifact(artifact=artifact, session=session,
                                                  goal=_live_goal, scenario=_live_scenario,
                                                  tab_id=body.tab_id or ""),
            ))
            db.commit()
            recorded_captures["captures"] += 1
        except Exception:
            db.rollback()
            raise  # LiveProposer's on_capture wrapper logs it; the drive continues

    proposer = LiveProposer(
        capture_server_url=server, browser_url=browser_url, traces_dir=traces_dir,
        tab_id=body.tab_id, tab_url=body.tab_url, goal=body.task_goal,
        on_capture=_persist_live_capture,
    )

    # --- AUTH PRE-FLIGHT: observe once; if the domain plainly isn't signed in, hand off
    # ("log in first") instead of driving the loop into a login wall. Unknown/authed → proceed,
    # and PRIME the loop with this observation so it doesn't re-capture the same page.
    first_obs = proposer()
    authed = auth_gate.is_authenticated(session.domain_id, first_obs.url, first_obs.page_text)
    if authed is False:
        result = LoopResult(LoopStatus.ESCALATED, [],
                            reason="not authenticated — sign in to the site first",
                            escalation_reason="not_authenticated")
        handoff = handoff_mod.emit(result, task_goal=body.task_goal, training_session_id=session.id,
                                   last_observation=first_obs, tab_url=body.tab_url)
        return {
            "status": result.status.value, "completed": False, "authenticated": False,
            "task": (task_spec.spec_for(task=body.task, task_goal=body.task_goal) or None)
                    and task_spec.spec_for(task=body.task, task_goal=body.task_goal).name,
            "reason": result.reason, "escalation_reason": "not_authenticated",
            "total_cost_usd": 0.0, "executed_steps": 0, "steps": [],
            "handoff": asdict(handoff),
            "recorded_captures": recorded_captures,
        }
    proposer_for_loop = PrimedProposer(first_obs, proposer)
    actor = LiveActor(
        capture_server_url=server, browser_url=browser_url,
        tab_id=body.tab_id, tab_url=body.tab_url,
        driver=body.driver, record_only=body.record_only,
    )
    # Only the submit-approval gate runs pre-act now (the structural stop that keeps
    # execute-by-default safe). The captcha check is no longer a per-step proactive gate —
    # it's a cheaper LAST-CALL diagnostic run after a step's attempts fail (below), since a
    # stall could be a captcha OR a different problem.
    gate = gate_mod.consequential_gate(allow=body.allow_submit)

    # Terminal-state spec: lets the loop return COMPLETED on success instead of running to
    # max_steps. A MAX_STEPS result then means "did not reach the goal — review", not "done".
    spec = task_spec.spec_for(task=body.task, task_goal=body.task_goal)
    is_done = task_spec.is_done_for(spec)

    # value_for: for a create-listing run, each text/select field's value comes from the
    # operator's ListingDraft, matched by the target field's accessible name. None → the loop
    # escalates that field rather than typing a guess.
    value_for = None
    if body.listing_draft_id:
        import listing_draft as ld
        draft = ld.get_draft(body.listing_draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="listing_draft_id not found")

        def value_for(result, observation, _draft=draft):
            bid = result.target_backend_node_id
            name = ""
            for c in observation.ax_candidates:
                cid = c.get("backend_node_id") or (c.get("_debug") or {}).get("backend_node_id")
                if cid is not None and bid is not None and int(cid) == int(bid):
                    name = (c.get("caption") or c.get("name") or "").strip()
                    break
            return ld.value_for_field(_draft, name)

    result = run_loop(task_goal=body.task_goal, proposer=proposer_for_loop, actor=actor,
                      gate=gate, is_done=is_done, value_for=value_for,
                      max_steps=body.max_steps, max_retries=body.max_retries)

    def _last_obs():
        if proposer.last_filename:
            try:
                return observation_from_trace(traces_dir, proposer.last_filename)[0]
            except Exception:
                return None
        return None

    # Captcha diagnostic — the LAST CALL before a human. Only when a stall could plausibly be
    # a hidden captcha (action fired but nothing changed / page went unreadable / no match),
    # not for a low-confidence pick or an approval gate. Runs one cheap probe.
    _CAPTCHA_SUSPECT = {"verifier_failed", "no_match", "unknown_state", "stage_error"}
    diagnostic = None
    if (result.status is LoopStatus.ESCALATED and body.captcha_diagnostic
            and result.escalation_reason in _CAPTCHA_SUSPECT):
        diagnostic = gate_mod.probe_captcha(capture_server_url=server, browser_url=browser_url,
                                            tab_id=body.tab_id, tab_url=body.tab_url)

    # Hand off on a hard escalation, and also when we ran out of steps WITHOUT completing the
    # task (an incomplete run should surface for review, not vanish). A COMPLETED run is silent.
    handoff = None
    incomplete = result.status is LoopStatus.MAX_STEPS
    if result.status is LoopStatus.ESCALATED or incomplete:
        handoff = handoff_mod.emit(
            result, task_goal=body.task_goal, training_session_id=session.id,
            last_observation=_last_obs(), tab_url=body.tab_url, diagnostic=diagnostic,
        )

    return {
        "status": result.status.value,
        "completed": result.status is LoopStatus.COMPLETED,
        "task": spec.name if spec else None,
        "reason": result.reason,
        "escalation_reason": result.escalation_reason,
        "captcha_diagnostic": diagnostic,
        "total_cost_usd": result.total_cost_usd,
        "executed_steps": sum(1 for s in result.steps if s.executed),
        "steps": [asdict(s) for s in result.steps],
        "handoff": asdict(handoff) if handoff else None,
        # How much labelable training exhaust this drive produced (the flywheel signal).
        "recorded_captures": recorded_captures,
    }


@router.get("/api/runtime/handoffs")
def runtime_handoffs(open_only: bool = False, limit: int = 50):
    """List handoff records (newest first) — the operator's 'what needs me' queue. Each row
    carries WHY the agent stopped and WHAT it tried before giving up."""
    from runtime import handoff as handoff_mod
    rows = handoff_mod.list_handoffs(open_only=open_only, limit=limit)
    return {"handoffs": rows, "open_count": sum(1 for r in rows if r.get("status") != "resolved")}


@router.post("/api/runtime/handoffs/{handoff_id}/resolve")
def runtime_resolve_handoff(handoff_id: str):
    """Mark a handoff resolved once the operator has unblocked/finished the step."""
    from runtime import handoff as handoff_mod
    if not handoff_mod.resolve(handoff_id):
        raise HTTPException(status_code=404, detail="Handoff not found")
    return {"ok": True, "id": handoff_id, "status": "resolved"}


# ---------------------------------------------------------------------------
# Command Center — the cross-domain cockpit rollup + per-domain automation posture.
# ---------------------------------------------------------------------------
@router.get("/api/domains/{domain_id}/training_readiness")
def domain_training_readiness(domain_id: str, db: Session = Depends(get_db)):
    """The money-saving flywheel for ONE domain: how close its cheap local models are to
    displacing the Haiku catchall. Composes the per-domain capture coverage (L3 page-state
    classifier fuel) with the system-wide SELECT-stage telemetry (L4 selector fuel + the
    live cache-hit / escalation / cost / Haiku-share the local layers are driving down).

    Claude/Haiku stays the teacher — every paid pick also emits a page-state label and a
    selection row the students distill from. This endpoint makes that progress legible and
    points at the next gap to capture."""
    import training
    from select_stage import telemetry

    cov = training_coverage(domain_id=domain_id, db=db)
    states = cov["states"]

    L3_MIN = training._L3_MIN_PER_STATE
    L3_MIN_DEEP = training._L3_MIN_DEEP_STATES
    deep = [s for s in states if s["count"] >= L3_MIN]
    thin = [s for s in states if 0 < s["count"] < L3_MIN]
    gaps = [s for s in states if s["count"] == 0]

    # Next gap to capture: prefer the domain's OWN states (its create-listing / inbox
    # screens), thinnest first; fall back to any gap/thin state. Skip nothing — even a
    # global stop-state that's thin is fair game, just deprioritised.
    capturable = [s for s in states if s["count"] < L3_MIN]
    capturable.sort(key=lambda s: (s["scope"] == "global", s["count"]))
    next_gap = capturable[0] if capturable else None

    tele = telemetry.summarize()
    totals = tele.get("totals", {})
    by_layer = {row["layer"]: row["count"] for row in tele.get("by_layer", [])}
    selections = totals.get("selections", 0) or 0
    haiku_ct = by_layer.get("som_haiku", 0)
    haiku_share = round(haiku_ct / selections, 4) if selections else 0.0

    # Distillation counter — the teacher's CONFIRMED reps: captures in this domain that carry a
    # golden positive_candidate_id (the correct element to act on). This is the supervised L4
    # ground truth (label_source='human'), the label historically never written. It's what makes
    # the student able to replace Haiku, so we surface it as the headline distillation metric.
    state_ids = [s["state_id"] for s in states]
    golden_reps = db.scalar(
        select(func.count()).select_from(TrainingCapture).where(
            TrainingCapture.positive_candidate_id.isnot(None),
            TrainingCapture.observed_page_state.in_(state_ids),
        )
    ) or 0

    return {
        "domain_id": domain_id,
        "distillation": {
            "golden_reps": int(golden_reps),         # teacher-confirmed (state → action) labels
            "labeled_captures": cov["totals"]["tagged_captures"],
        },
        "coverage": {
            **cov["totals"],
            "target_per_state": cov["target_per_state"],
            "deep_states": len(deep),
            "thin_states": len(thin),
        },
        "states": states,
        "next_gap": next_gap,
        "l3": {
            # page-state classifier — "which Marketplace screen am I on?"
            "min_per_state": L3_MIN,
            "min_deep_states": L3_MIN_DEEP,
            "deep_states": len(deep),
            "relevant_states": len(states),
            "enough_to_train": len(deep) >= L3_MIN_DEEP,
        },
        "l4": {
            # selection model — "which element, given state + goal" — distilled from the
            # selection-telemetry corpus (system-wide today; not yet domain-sharded).
            "corpus_size": tele.get("corpus_size", 0),
            "cache_hit_rate": tele.get("rates", {}).get("cache_hit", 0.0),
            "escalation_rate": tele.get("rates", {}).get("escalation", 0.0),
            "avg_cost_usd": tele.get("rates", {}).get("avg_cost_usd", 0.0),
            "haiku_share": haiku_share,
            "by_layer": tele.get("by_layer", []),
        },
    }


def _observe_once(session: TrainingSession, tab_url: Optional[str] = None):
    """One live capture of the session's browser → Observation (best-effort)."""
    from runtime import LiveProposer
    p = LiveProposer(capture_server_url=settings.capture_server_url,
                     browser_url=_session_browser_url(session),
                     traces_dir=_artifacts_dir() / "observer-traces", tab_url=tab_url)
    return p()


@router.get("/api/runtime/auth_status")
def runtime_auth_status(training_session_id: int, tab_url: Optional[str] = None,
                        db: Session = Depends(get_db)):
    """Domain-aware "is this session signed in?" — observes the live page and reads the
    auth signal for the session's domain (the gate run_live applies before a real run)."""
    import auth_gate
    session = db.get(TrainingSession, training_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    obs = _observe_once(session, tab_url)
    status = auth_gate.auth_status(session.domain_id, obs.url, obs.page_text)
    status["training_session_id"] = training_session_id
    status["domain_id"] = session.domain_id
    return status


# ---------------------------------------------------------------------------
# Facebook Marketplace — recipe + listing drafts (the create-listing inputs)
# ---------------------------------------------------------------------------
@router.get("/api/assets")
def list_assets(prefix: str = "marketplace"):
    """Available listing-photo assets (keys + thumbnail URLs) for the UI picker. Local folder now,
    cloud (S3) later — the swap lives in assets.py. Items store the returned `key`s in item.photos."""
    import assets
    return {"assets": assets.list_assets(prefix), "prefix": prefix}


@router.get("/api/assets/documents")
def list_document_assets(prefix: str = "documents"):
    """Document assets (resumes, cover letters) + which one is the canonical resume. Domain-agnostic:
    the apply flow uploads `resume_key` into any ATS file input (Indeed, Workday, company sites)."""
    import assets
    return {
        "documents": assets.list_documents(prefix),
        "resume_key": assets.resume_key(),
        "resume_available": assets.resume_path() is not None,
    }


# ---------------------------------------------------------------------------
# Channel browser — one persistent, health-checked, auto-healing browser per sales
# channel that agents ATTACH to. Login is a supervised task through it (observe → drive
# → verify → escalate at a gate), not a bespoke one-shot.
# ---------------------------------------------------------------------------
def _channel_profile(channel_cfg: dict, account_id: Optional[str]) -> str:
    """Which persistent Chrome profile to isolate this channel session in. With an account, the
    ACCOUNT's profile (so two accounts never share cookies); otherwise the channel default."""
    if account_id:
        import accounts as accounts_mod
        acct = accounts_mod.get_account(account_id)
        if acct is None:
            raise HTTPException(status_code=404, detail=f"Account '{account_id}' not found")
        if acct["domain_id"] != channel_cfg["domain_id"]:
            raise HTTPException(status_code=400,
                                detail=f"Account '{account_id}' is not a {channel_cfg['domain_id']} account")
        return acct["profile"]
    return channel_cfg["profile"]


def _ensure_channel_browser(db: Session, channel: str, account_id: Optional[str] = None):
    """Find-or-create the persistent session for a channel (optionally scoped to a specific
    account) and make sure its browser is live. Returns (session, cfg). With account_id the session
    is isolated in the account's OWN profile, so it can run alongside other accounts' browsers."""
    import channel_browser
    cfg = channel_browser.channel_config(channel)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Unknown channel '{channel}'")
    profile = _channel_profile(cfg, account_id)
    session = db.scalar(
        select(TrainingSession).where(
            TrainingSession.persistent_profile == profile
        ).order_by(TrainingSession.id.desc()).limit(1)
    )
    if session is None:
        scenario = db.scalar(
            select(ScenarioRegistry).where(ScenarioRegistry.domain_id == cfg["domain_id"]).limit(1)
        )
        if scenario is None:
            raise HTTPException(status_code=400, detail=f"No scenario for channel {cfg['domain_id']}")
        session = TrainingSession(
            domain_id=cfg["domain_id"], scenario_id=scenario.scenario_id,
            goal_id=scenario.goal_id, task_id=scenario.task_id, capture_profile="viewport",
            purpose="production", persistent_profile=profile, account_id=account_id, status="draft",
        )
        db.add(session)
        db.commit()
        db.refresh(session)
    elif account_id and not session.account_id:
        session.account_id = account_id   # bind the account to a pre-existing profile session
        db.commit()
    _launch_training_chrome(db, session)   # attaches if alive, else relaunches + waits for readiness
    return session, cfg


def _channel_creds(channel: str, account_id: Optional[str] = None):
    """(username, password) for a channel/account, or None. Never logged. With an account the creds
    come from its secret backend (vault or env); otherwise the legacy single-account .env keys."""
    if account_id:
        import accounts as accounts_mod
        return accounts_mod.resolve_creds(account_id)
    if channel == "facebook_marketplace" and settings.fb_username and settings.fb_password:
        return settings.fb_username, settings.fb_password
    return None


def _blocking_challenge(browser_url: str):
    """The live captcha probe (mcp /challenge_visibility) → the block dict if a challenge is
    actually blocking, else None."""
    try:
        with httpx.Client(timeout=8.0) as client:
            cv = client.post(f"{settings.capture_server_url}/challenge_visibility",
                             json={"browser_url": browser_url}).json()
        return cv if (cv.get("ok") and cv.get("blocking")) else None
    except Exception:  # noqa: BLE001
        return None


def _login_field_matcher(domain_id: str):
    """The per-domain 'find the login controls in a CDP-AX scan' matcher. Domain quirks (Facebook
    shipping Log In as a <div role=button>, the field accessible-names) live in the domain recipe —
    not here — so login stays on the generic AX/node interaction layer. Returns a callable or None."""
    if domain_id == "facebook_marketplace":
        import facebook_recipe
        return facebook_recipe.match_login_fields
    return None


def _drive_login_form(session: TrainingSession, cfg: dict, creds: tuple[str, str]) -> dict:
    """Drive a channel's login FORM through the CDP-AX interaction layer — the system's one robust way
    to act on a page. Scan the live accessibility tree (/ax_scan), find email/password/submit by ROLE
    + ACCESSIBLE-NAME via the domain recipe's matcher, then drive each BY backend_node_id through
    /execute (focus+insertText for the fields, native .click() for submit). No hardcoded CSS
    selectors, no coordinate clicks, no screenshots of the credential flow. Never raises; returns
    {ok, reason?}. 'controls not found' is normal when we're already past the wall (a checkpoint / 2FA
    screen) — the caller re-observes and escalates to the human gate."""
    browser_url = _session_browser_url(session)
    matcher = _login_field_matcher(cfg["domain_id"])
    if matcher is None:
        return {"ok": False, "reason": f"no login matcher for {cfg['domain_id']}"}
    try:
        with httpx.Client(timeout=30.0) as client:
            scan = client.post(f"{settings.capture_server_url}/ax_scan",
                               json={"browser_url": browser_url, "tab_url": cfg["tab_url"]}).json()
            fields = matcher(scan.get("candidates", []))
            if not all(k in fields for k in ("email", "password", "submit")):
                return {"ok": False, "reason": f"login controls not found (matched {sorted(fields)})"}

            def _exec(action_id: str, node_id: int, value: Optional[str] = None) -> None:
                # driver="humanized": approach each field/button with a wiggly mouse path + type with
                # cadence (bot-safety on a hostile site), driving the node by backend_node_id (robust).
                client.post(f"{settings.capture_server_url}/execute", json={
                    "action_id": action_id, "backend_node_id": node_id, "target_bbox": {},
                    "value": value, "browser_url": browser_url, "tab_url": cfg["tab_url"],
                    "driver": "humanized"})

            _exec("type", fields["email"], creds[0])
            time.sleep(0.4)
            _exec("type", fields["password"], creds[1])
            time.sleep(0.4)
            _exec("click", fields["submit"])
        time.sleep(4.0)  # let the submit navigate / a challenge render
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": str(exc)}


@router.get("/api/channels/{channel}/status")
def channel_status(channel: str, account_id: Optional[str] = None, db: Session = Depends(get_db)):
    """Cheap, honest connection status (a CDP probe — no capture). Poll this for the UI badge.
    With account_id, reports the status of THAT account's isolated session."""
    import channel_browser
    cfg = channel_browser.channel_config(channel)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Unknown channel '{channel}'")
    profile = _channel_profile(cfg, account_id)
    session = db.scalar(
        select(TrainingSession).where(
            TrainingSession.persistent_profile == profile
        ).order_by(TrainingSession.id.desc()).limit(1)
    )
    connected = bool(session) and channel_browser.cdp_reachable(session.chrome_debug_port)
    return {"channel": channel, "label": cfg["label"], "account_id": account_id, "connected": connected,
            "session_id": session.id if session else None,
            "port": session.chrome_debug_port if session else None}


@router.post("/api/channels/{channel}/connect")
def channel_connect(channel: str, account_id: Optional[str] = None, db: Session = Depends(get_db)):
    """Attach to (or heal) the channel browser and sit it on the channel home. Returns honest
    connected + authed so the UI can show real state instead of hoping. With account_id, opens
    (or heals) THAT account's own isolated browser."""
    import auth_gate
    import channel_browser
    session, cfg = _ensure_channel_browser(db, channel, account_id)
    browser_url = _session_browser_url(session)
    try:
        with httpx.Client(timeout=25.0) as client:
            client.post(f"{settings.capture_server_url}/navigate",
                        json={"url": cfg["home"], "browser_url": browser_url, "settle_seconds": 2.0})
    except Exception:  # noqa: BLE001
        pass  # navigation is a convenience; attachment is what matters
    connected = channel_browser.cdp_reachable(session.chrome_debug_port)
    authed = None
    if connected:
        obs = _observe_once(session, tab_url=cfg["tab_url"])
        authed = auth_gate.is_authenticated(cfg["domain_id"], obs.url, obs.page_text)
    return {"channel": channel, "account_id": account_id, "connected": connected, "authed": authed,
            "session_id": session.id, "port": session.chrome_debug_port}


@router.post("/api/channels/{channel}/login")
def channel_login(channel: str, account_id: Optional[str] = None, db: Session = Depends(get_db)):
    """Supervised login: ensure a healthy browser, observe, drive the login form, re-verify, and
    escalate ONLY at a real gate (captcha / 2FA / checkpoint) — never auto-solving it. The loop
    doing the work and stopping only when it needs a human. With account_id, logs in THAT account
    (creds from its vault/env backend) in its own isolated browser."""
    from dataclasses import asdict

    import auth_gate
    import channel_browser
    from runtime import handoff as handoff_mod
    from runtime.loop import LoopResult, LoopStatus

    session, cfg = _ensure_channel_browser(db, channel, account_id)
    browser_url = _session_browser_url(session)
    creds = _channel_creds(channel, account_id)
    if creds is None:
        raise HTTPException(status_code=400, detail=(
            f"No credentials for account '{account_id}'" if account_id
            else "Channel credentials are not set in .env"))

    obs = _observe_once(session, tab_url=cfg["tab_url"])
    if auth_gate.is_authenticated(cfg["domain_id"], obs.url, obs.page_text) is True:
        return {"logged_in": True, "connected": True, "message": "Already signed in."}

    drive_reason = None
    challenge = _blocking_challenge(browser_url)
    if not challenge:
        drive = _drive_login_form(session, cfg, creds)
        drive_reason = None if drive["ok"] else drive.get("reason")
        obs = _observe_once(session, tab_url=cfg["tab_url"])
        if auth_gate.is_authenticated(cfg["domain_id"], obs.url, obs.page_text) is True:
            return {"logged_in": True, "connected": True, "message": "Signed in."}
        challenge = _blocking_challenge(browser_url)

    gate = "captcha" if challenge else "checkpoint_or_2fa"
    result = LoopResult(LoopStatus.ESCALATED, [], reason="login needs a human gate cleared",
                        escalation_reason="not_authenticated")
    handoff = handoff_mod.emit(
        result, task_goal=f"log in to {channel}" + (f" as {account_id}" if account_id else ""),
        training_session_id=session.id,
        last_observation=obs, tab_url=cfg["tab_url"],
        diagnostic={"reason": gate, "drive_reason": drive_reason, "guidance":
                    "Facebook wants a human step (captcha / 2FA code / 'is this you?'). Complete it "
                    "in the browser window that's open, then click Check sign-in."})
    return {
        "logged_in": False, "needs_human": True, "connected": True, "gate": gate,
        "message": "Typed your credentials — clear the "
                   f"{'captcha' if challenge else 'checkpoint / 2FA'} in the window, then Check sign-in.",
        "handoff": asdict(handoff),
    }



@router.post("/api/runtime/run_batch")
def runtime_run_batch(only_with_sidecar: bool = True, force: bool = False, limit: int = 0):
    """Replay every stored capture through the record-only loop to FILL THE CORPORA.

    For each capture this runs classify → propose → select → (record) and appends a
    `StepRecord` to `cache/loop_steps.jsonl` plus a row to `selection_telemetry.jsonl`
    — no inputs are ever fired. This is the mechanism that turns accumulated captures
    into training rows for the cheap local layers (L3/L4) that will displace Haiku.

    Idempotent by design: each capture's state fingerprint is checked against the
    fingerprints already in `loop_steps.jsonl`; a capture whose state was already
    recorded is SKIPPED (status `skipped_duplicate`) unless `force=True`. Because the
    SELECT cache is also seeded on the first confident pick, re-running the batch
    costs ~$0 (cache hits) even when `force=True`.

    Params:
      * `only_with_sidecar` — skip captures lacking a `.ax.json` (no AX candidates →
        nothing to select). True by default: those rows are just no_match noise.
      * `force` — re-run even captures already represented in the corpus.
      * `limit` — cap the number of captures processed (0 = all). Useful to stay
        well inside the $5/week budget on the first warm-up run.

    Per-capture `task_goal` comes from the capture's own `task_context.goal`."""
    from dataclasses import asdict

    from runtime import run_loop
    from select_stage import fingerprint

    traces_dir = _artifacts_dir() / "observer-traces"
    if not traces_dir.exists():
        return {"processed": 0, "skipped": 0, "captures": [], "total_cost_usd": 0.0}

    def _is_capture(p: Path) -> bool:
        n = p.name
        return n.endswith(".json") and not any(
            n.endswith(s) for s in (".ax.json", ".vision.json", ".meta.json")
        )

    captures = sorted(p.name for p in traces_dir.iterdir() if p.is_file() and _is_capture(p))

    # Fingerprints already present in the corpus. `seen_fps` grows as we process so
    # two captures of the same state in one batch don't both log; `corpus_fps` is the
    # frozen pre-run set, so `force` can refresh cache/telemetry WITHOUT appending a
    # duplicate trajectory row (we only log a state's first occurrence, ever).
    seen_fps: set[str] = set()
    steps_path = traces_dir.parent / "cache" / "loop_steps.jsonl"
    if steps_path.exists():
        for line in steps_path.read_text(encoding="utf-8").splitlines():
            try:
                seen_fps.add(json.loads(line).get("fingerprint", ""))
            except Exception:
                continue
    corpus_fps = set(seen_fps)

    results: list[dict] = []
    processed = skipped = 0
    total_cost = 0.0
    for filename in captures:
        if only_with_sidecar and not (traces_dir / f"{filename}.ax.json").exists():
            skipped += 1
            results.append({"filename": filename, "status": "skipped_no_sidecar"})
            continue

        observation, capture_goal = _observation_from_capture(filename)
        fp = fingerprint.compute(
            url=observation.url, viewport=observation.viewport,
            candidates=observation.ax_candidates, task_goal=capture_goal,
            dom_clickables=observation.dom_clickables,
        )
        if fp in seen_fps and not force:
            skipped += 1
            results.append({"filename": filename, "status": "skipped_duplicate",
                            "fingerprint": fp, "task_goal": capture_goal})
            continue

        result = run_loop(task_goal=capture_goal, proposer=lambda o=observation: o,
                          max_steps=1, log_corpus=fp not in corpus_fps)
        seen_fps.add(fp)
        corpus_fps.add(fp)
        processed += 1
        total_cost += result.total_cost_usd
        step = result.steps[0] if result.steps else None
        results.append({
            "filename": filename, "task_goal": capture_goal,
            "status": result.status.value, "escalation_reason": result.escalation_reason,
            "cost_usd": result.total_cost_usd,
            "step": asdict(step) if step else None,
        })
        if limit and processed >= limit:
            break

    return {
        "processed": processed, "skipped": skipped,
        "total_cost_usd": round(total_cost, 6), "captures": results,
    }


@router.post("/api/runtime/verify_replay")
def runtime_verify_replay(training_session_id: int, db: Session = Depends(get_db)):
    """Replay-verify a session's flow WITHOUT firing any input.

    The verifier is a pure before/after function. Since a capture burst records the
    real flow as consecutive page-states, we pair each capture with the next
    (time-ordered) and run the SAME `verifier.verify` the live loop uses. The result
    is a behavioral ground-truth signal — did each real transition actually advance
    the way the recorded action predicted — with zero risk (nothing is executed).

    This is the same code path that runs live once the executor is trusted; here it
    runs over captured data. Per-transition verdicts are written to
    `cache/verify_replay.jsonl` (keyed by `from_fingerprint`) for the quality gate to
    join against the loop-step corpus.
    """
    from select_stage import fingerprint, verifier

    caps = db.scalars(
        select(TrainingCapture)
        .where(TrainingCapture.training_session_id == training_session_id)
        .order_by(TrainingCapture.captured_at.asc())
    ).all()
    if len(caps) < 2:
        return {"transitions": 0, "advanced": 0, "advance_rate": None, "verdicts": [],
                "reason": "need >=2 captures in the session to form a transition"}

    # The action the loop decided at each state, looked up by fingerprint from the
    # trajectory corpus. If a state isn't in the corpus yet we fall back to "click"
    # (the default "did anything change?" check).
    action_by_fp: dict[str, str] = {}
    corpus_path = _artifacts_dir() / "cache" / "loop_steps.jsonl"
    if corpus_path.exists():
        for line in corpus_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                action_by_fp[row.get("fingerprint", "")] = row.get("action_id", "click")
            except Exception:
                continue

    def _obs_fp(obs, goal: str) -> str:
        return fingerprint.compute(url=obs.url, viewport=obs.viewport,
                                   candidates=obs.ax_candidates, task_goal=goal,
                                   dom_clickables=obs.dom_clickables)

    verdicts: list[dict] = []
    advanced = 0
    prev_obs = prev_goal = prev_fp = None
    for cap in caps:
        try:
            obs, goal = _observation_from_capture(cap.artifact_filename)
        except HTTPException:
            continue  # capture file vanished — skip, don't break the chain hard
        fp = _obs_fp(obs, goal or "")
        if prev_obs is not None:
            action_id = action_by_fp.get(prev_fp, "click")
            vres = verifier.verify(
                action_id=action_id,
                before=prev_obs.snapshot or verifier.Snapshot(),
                after=obs.snapshot or verifier.Snapshot(),
            )
            advanced += 1 if vres.ok else 0
            verdicts.append({
                "from_fingerprint": prev_fp, "to_fingerprint": fp,
                "from_url": prev_obs.url, "to_url": obs.url,
                "action_id": action_id, "ok": vres.ok,
                "predicted": vres.predicted, "observed": vres.observed,
            })
        prev_obs, prev_goal, prev_fp = obs, goal, fp

    # Persist verdicts for the gate (overwrite this session's rows: latest replay wins).
    out_path = _artifacts_dir() / "cache" / "verify_replay.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    this_session_fps = {v["from_fingerprint"] for v in verdicts}
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if row.get("from_fingerprint") not in this_session_fps:
                    existing.append(row)
            except Exception:
                continue
    with out_path.open("w", encoding="utf-8") as fh:
        for row in existing + verdicts:
            fh.write(json.dumps(row) + "\n")

    n = len(verdicts)
    return {
        "training_session_id": training_session_id,
        "transitions": n, "advanced": advanced,
        "advance_rate": round(advanced / n, 3) if n else None,
        "verdicts": verdicts,
    }


#: Quality-gate thresholds. The corpus stays the raw record of every decision; the
#: gate is a DERIVED classification (a view), so the trainer and the scorecard share
#: one policy and the corpus is never mutated. A row is train-eligible only if the
#: teacher was confident, didn't escalate, and the behavioral verify didn't fail.
_GATE_MIN_CONFIDENCE = 0.85


def _gate_verdict(*, confidence: float, needs_human: bool, verify_ok: Optional[bool]):
    """Classify one corpus row → (train_eligible, quarantine_reason).

    Quarantined rows are NOT discarded — they route to human review (the path that
    turns them into golden truth). Reasons are ordered by severity so the most
    important defect is surfaced first."""
    if needs_human:
        return False, "escalated"           # teacher itself punted → never a positive label
    if verify_ok is False:
        return False, "verify_failed"       # action didn't advance the page → bad transition
    if (confidence or 0) < _GATE_MIN_CONFIDENCE:
        return False, "low_confidence"      # borderline teacher pick → needs a human look
    return True, None


#: Auto-promotion band. The verifier turns a Haiku pick into a golden label without a
#: human IF it both confirmed behaviorally (verify_ok) and the teacher was confident.
#: Two tiers (hybrid posture): at/above AUTO we write the golden label outright (machine
#: golden, train-eligible, revocable); in the staged band [GATE_MIN, AUTO) we only flag
#: it 'suggested' and rank it for a one-click human confirm. Below GATE_MIN, or
#: unverified/verify-failed/escalated, it stays an ordinary human label_queue item.
_PROMOTE_AUTO_CONFIDENCE = 0.95


def _promotion_decision(*, confidence: Optional[float], needs_human: bool,
                        verify_ok: Optional[bool], has_candidate: bool):
    """Classify one corpus row → (label_source, skip_reason).

    label_source is 'auto' (write golden now) or 'suggested' (stage for 1-click); when
    None, skip_reason says why this row is left to a human. Behavioral confirmation is
    MANDATORY for both tiers — an unverified pick (no replay verdict yet) never
    auto-promotes, it just waits for a verify_replay pass."""
    if needs_human:
        return None, "escalated"
    if verify_ok is False:
        return None, "verify_failed"
    if verify_ok is None:
        return None, "unverified"            # no replay verdict yet — run verify_replay first
    if not has_candidate:
        return None, "no_candidate_mapping"  # picked node isn't in this capture's AX set
    c = confidence or 0.0
    if c >= _PROMOTE_AUTO_CONFIDENCE:
        return "auto", None
    if c >= _GATE_MIN_CONFIDENCE:
        return "suggested", None
    return None, "low_confidence"


@router.post("/api/training/promote_auto")
def promote_auto(training_session_id: Optional[int] = None, dry_run: bool = False,
                 db: Session = Depends(get_db)):
    """Distillation promotion pass — turn verifier-confirmed Haiku picks into labels.

    For every capture (optionally one session), join the teacher's pick (loop_steps, by
    fingerprint) with the behavioral verdict (verify_replay, by from_fingerprint) and the
    same gate the trainer uses, then:
      • 'auto'      → write positive_candidate_id (machine golden, train-eligible)
      • 'suggested' → flag for a one-click human confirm (no golden written yet)
    Human labels (label_source='human' or review_status in reviewed/approved) are never
    touched. Idempotent: re-running RE-derives machine labels and REVOKES any prior
    'auto'/'suggested' whose pick no longer passes the gate (e.g. a later verify failed).
    `dry_run` reports what would change without writing."""
    from select_stage import fingerprint

    traces_dir = _artifacts_dir() / "observer-traces"

    corpus: dict[str, dict] = {}
    corpus_path = _artifacts_dir() / "cache" / "loop_steps.jsonl"
    if corpus_path.exists():
        for line in corpus_path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                corpus[r.get("fingerprint", "")] = r   # later append wins (most recent decision)
            except Exception:
                continue

    verify_ok_by_fp: dict[str, bool] = {}
    vpath = _artifacts_dir() / "cache" / "verify_replay.jsonl"
    if vpath.exists():
        for line in vpath.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                verify_ok_by_fp[r.get("from_fingerprint", "")] = bool(r.get("ok"))
            except Exception:
                continue

    stmt = select(TrainingCapture)
    if training_session_id is not None:
        stmt = stmt.where(TrainingCapture.training_session_id == training_session_id)
    captures = db.scalars(stmt).all()

    promoted_auto: list[dict] = []
    staged: list[dict] = []
    revoked: list[dict] = []
    skipped: dict[str, int] = {}

    def _skip(reason: str):
        skipped[reason] = skipped.get(reason, 0) + 1

    for cap in captures:
        fn = cap.artifact_filename
        # Never touch a human decision (confirmed/corrected/terminal in the labeler).
        if cap.label_source == "human" or cap.review_status in ("reviewed", "approved"):
            _skip("human_owned")
            continue
        if not (traces_dir / f"{fn}.ax.json").exists():
            _skip("no_candidates")
            continue
        try:
            obs, goal = _observation_from_capture(fn)
        except HTTPException:
            _skip("artifact_missing")
            continue
        fp = fingerprint.compute(url=obs.url, viewport=obs.viewport,
                                 candidates=obs.ax_candidates, task_goal=goal or "",
                                 dom_clickables=obs.dom_clickables)
        row = corpus.get(fp)
        if not row:
            _skip("uncorpused")
            continue
        # Stop-states are classify-stage escalations, not selection tasks — no pick to label.
        if row.get("layer") == "classify" or row.get("reason_code") == "stop_state":
            _skip("stop_state")
            continue

        by_backend = {
            int(c["backend_node_id"]): c["candidate_id"]
            for c in obs.ax_candidates
            if c.get("backend_node_id") is not None and c.get("candidate_id")
        }
        tb = row.get("target_backend_node_id")
        candidate_id = by_backend.get(int(tb)) if tb is not None else None
        source, reason = _promotion_decision(
            confidence=row.get("confidence"), needs_human=bool(row.get("needs_human")),
            verify_ok=verify_ok_by_fp.get(fp), has_candidate=candidate_id is not None,
        )

        prior = cap.label_source  # 'auto' | 'suggested' | None
        if source is None:
            # No longer (or never) eligible. Revoke a stale machine label if present.
            if prior in ("auto", "suggested"):
                revoked.append({"filename": fn, "was": prior, "reason": reason})
                if not dry_run:
                    cap.positive_candidate_id = None
                    cap.label_source = None
                    cap.label_confidence = None
                    cap.verified_at = None
                    cap.review_status = "draft"
            else:
                _skip(reason)
            continue

        conf = float(row.get("confidence") or 0.0)
        if source == "auto":
            promoted_auto.append({"filename": fn, "candidate_id": candidate_id,
                                  "confidence": conf, "was": prior})
            if not dry_run:
                cap.positive_candidate_id = candidate_id
                cap.label_source = "auto"
                cap.label_confidence = conf
                cap.verified_at = datetime.now(timezone.utc)
                cap.review_status = "auto"
        else:  # suggested — stage for one-click confirm, do NOT write a golden label yet
            staged.append({"filename": fn, "candidate_id": candidate_id,
                           "confidence": conf, "was": prior})
            if not dry_run:
                # If it was previously auto and dropped into the staged band, pull the golden.
                if prior == "auto":
                    cap.positive_candidate_id = None
                cap.label_source = "suggested"
                cap.label_confidence = conf
                cap.verified_at = datetime.now(timezone.utc)
                cap.review_status = "suggested"

    if not dry_run:
        db.commit()

    return {
        "dry_run": dry_run,
        "training_session_id": training_session_id,
        "scanned": len(captures),
        "auto_promoted": len(promoted_auto),
        "staged_for_confirm": len(staged),
        "revoked": len(revoked),
        "skipped": skipped,
        "thresholds": {"auto": _PROMOTE_AUTO_CONFIDENCE, "suggested_floor": _GATE_MIN_CONFIDENCE},
        "details": {"auto": promoted_auto, "staged": staged, "revoked": revoked},
    }


@router.get("/api/training/label_queue")
def label_queue(limit: int = 60, training_session_id: Optional[int] = None,
                domain: Optional[str] = None,
                include_labeled: bool = False, db: Session = Depends(get_db)):
    """Active-learning queue for the AX confirm/correct training space.

    Returns captures (that have AX candidates) ordered so human attention lands where
    the model is WEAKEST first: unlabeled states whose teacher pick was escalated or
    low-confidence rank above confident ones, which rank above already-golden rows.
    This is active learning — label the model's blind spots, not random captures.

    Each item carries just enough to render the queue; the panel fetches
    `candidate_suggestion` for the focused item. `include_labeled` keeps already-golden
    rows in (greyed in the UI) so you can revisit/relabel."""
    from select_stage import fingerprint

    traces_dir = _artifacts_dir() / "observer-traces"

    # Teacher signal per fingerprint from the corpus (confidence / escalation).
    corpus: dict[str, dict] = {}
    corpus_path = _artifacts_dir() / "cache" / "loop_steps.jsonl"
    if corpus_path.exists():
        for line in corpus_path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                corpus[r.get("fingerprint", "")] = r
            except Exception:
                continue

    stmt = select(TrainingCapture)
    if training_session_id is not None:
        stmt = stmt.where(TrainingCapture.training_session_id == training_session_id)
    if domain:
        stmt = stmt.where(TrainingCapture.domain_id == domain)  # per-domain labeling (#3)
    stmt = stmt.order_by(TrainingCapture.captured_at.desc())
    captures = db.scalars(stmt).all()

    items: list[dict] = []
    for cap in captures:
        fn = cap.artifact_filename
        if not (traces_dir / f"{fn}.ax.json").exists():
            continue  # no candidates → nothing to confirm/correct
        has_golden = cap.positive_candidate_id is not None
        # Anything already dealt with (golden, terminal, or none/needs-vision → reviewed)
        # leaves the queue, so handled captures don't keep reappearing.
        # 'auto' = machine golden (handled); leaves the queue like 'reviewed' unless
        # the operator opts to revisit. 'suggested' stays IN — it's the one-click confirm.
        if cap.review_status in ("reviewed", "auto") and not include_labeled:
            continue
        try:
            obs, goal = _observation_from_capture(fn)
        except HTTPException:
            continue
        fp = fingerprint.compute(url=obs.url, viewport=obs.viewport,
                                 candidates=obs.ax_candidates, task_goal=goal or "",
                                 dom_clickables=obs.dom_clickables)
        row = corpus.get(fp)
        # Stop-states (captcha/checkpoint) are classify-stage escalations, NOT selection
        # tasks — there's no correct candidate to pick, so they don't belong here.
        if row and (row.get("layer") == "classify" or row.get("reason_code") == "stop_state"):
            continue
        conf = row.get("confidence") if row else None
        escalated = bool(row.get("needs_human")) if row else False
        # Priority puts the highest-value-to-label first. States the model never picked
        # on (uncorpused) sink to the bottom: labeling them yields no accuracy signal
        # until the model has made a pick to compare against.
        if cap.review_status == "suggested":
            # Verifier-confirmed pick in the staged band — ready for a fast confirm, so
            # it sits above cold labels. The pre-highlighted pick comes from candidate_suggestion.
            prio, reason = 3, "suggested_confirm"
        elif row is None:
            prio, reason = -1, "uncorpused"
        elif escalated:
            prio, reason = 2, "escalated"
        elif conf is not None and conf < _GATE_MIN_CONFIDENCE:
            prio, reason = 1, "low_confidence"
        else:
            prio, reason = 0, "confident"
        items.append({
            "filename": fn, "url": obs.url, "domain_id": cap.domain_id,
            "goal_id": cap.goal_id, "captured_at": cap.captured_at.isoformat(),
            "candidate_count": len(obs.ax_candidates),
            "has_golden": has_golden, "suggestion_confidence": conf,
            "label_source": cap.label_source,
            "priority": prio, "priority_reason": reason,
        })

    # higher priority first; older first within a tier (stable progression)
    items.sort(key=lambda it: (-it["priority"], it["captured_at"]))
    return {"total": len(items), "unlabeled": len(items), "items": items[:limit]}


def _candidate_visible(c: dict, bound_w: float, bound_h: float, profile: str) -> bool:
    """Is this AX node actually reachable in the captured screenshot? Facebook's SPA
    dumps zero-size / off-screen / below-the-fold nodes into the AX tree — they aren't
    clickable, so they shouldn't clutter the labeler (or the trainer's negatives).

    bbox coords are in SCREENSHOT (device) pixels, so the caller passes bounds already
    scaled by device_scale_factor. For a viewport capture, anything starting past the
    bottom/right screenshot edge is below the fold / off-screen."""
    b = c.get("bbox") or {}
    w, h, x, y = b.get("width", 0), b.get("height", 0), b.get("x", 0), b.get("y", 0)
    if w < 3 or h < 3 or x < -2 or y < -2 or x > 40000 or y > 80000:
        return False
    if profile == "viewport":
        if bound_w and x >= bound_w:
            return False
        if bound_h and y >= bound_h:
            return False
    return True


@router.get("/api/observations/{filename}/candidate_suggestion")
def candidate_suggestion(filename: str, db: Session = Depends(get_db)):
    """The data contract for the AX-CDP training space: the candidate set + the
    model's SUGGESTED pick (so the UI can pre-highlight it) + the current human golden
    label. The operator then CONFIRMS (accept suggestion) or CORRECTS (pick another) —
    either way persisting `positive_candidate_id` via PATCH /api/observations/{filename}.

    Suggestion is looked up from the loop-step corpus by the capture's fingerprint and
    mapped from `target_backend_node_id` back to a `candidate_id`. If the state isn't in
    the corpus yet, `suggestion` is null and it's a pure cold label."""
    from select_stage import fingerprint

    observation, goal = _observation_from_capture(filename)
    traces_dir = _artifacts_dir() / "observer-traces"
    artifact = json.loads((traces_dir / filename).read_text())
    shots = (artifact.get("acquisition", {}) or {}).get("screenshots") or []
    screenshot_filename = (shots[0].get("filename") if shots else None)
    by_backend = {
        int(c["backend_node_id"]): c["candidate_id"]
        for c in observation.ax_candidates
        if c.get("backend_node_id") is not None and c.get("candidate_id")
    }
    fp = fingerprint.compute(url=observation.url, viewport=observation.viewport,
                             candidates=observation.ax_candidates, task_goal=goal or "",
                             dom_clickables=observation.dom_clickables)

    suggestion = None
    corpus_path = _artifacts_dir() / "cache" / "loop_steps.jsonl"
    if corpus_path.exists():
        for line in corpus_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("fingerprint") != fp:
                continue
            tb = row.get("target_backend_node_id")
            suggestion = {
                "candidate_id": by_backend.get(int(tb)) if tb is not None else None,
                "target_backend_node_id": tb,
                "action_id": row.get("action_id"),
                "confidence": row.get("confidence"),
                "layer": row.get("layer"),
                "needs_human": bool(row.get("needs_human")),
            }
            break

    capture = db.scalar(select(TrainingCapture).where(TrainingCapture.artifact_filename == filename))

    # Mission context + trajectory — a labeled pick is an EDGE in the state graph
    # (from_state, mission) --action--> to_state, not an isolated node. Surface the
    # intent the operator is labeling toward, and the prior/next captured states so
    # they can see where this step came from and leads to.
    context = trajectory = None
    if capture is not None:
        context = {
            "domain_id": capture.domain_id, "goal_id": capture.goal_id,
            "task_id": capture.task_id, "scenario_id": capture.scenario_id,
            "element_query": capture.element_query, "action_type_hint": capture.action_type_hint,
            "observed_page_state": capture.observed_page_state,
            "post_action_state": capture.post_action_state, "notes": capture.notes,
        }

        def _thumb(cap: TrainingCapture):
            refs = cap.screenshot_refs or []
            return refs[0].get("filename") if refs and isinstance(refs[0], dict) else None

        def _neighbor(cap: TrainingCapture):
            return {
                "filename": cap.artifact_filename, "url": cap.url,
                "screenshot_filename": _thumb(cap),
                "observed_page_state": cap.observed_page_state,
                "captured_at": cap.captured_at.isoformat(),
            }

        session_caps = db.scalars(
            select(TrainingCapture)
            .where(TrainingCapture.training_session_id == capture.training_session_id)
            .order_by(TrainingCapture.captured_at.asc())
        ).all()
        pos = next((i for i, c in enumerate(session_caps) if c.id == capture.id), None)
        trajectory = {
            "index": pos, "total": len(session_caps),
            "prev": _neighbor(session_caps[pos - 1]) if pos and pos > 0 else None,
            "next": _neighbor(session_caps[pos + 1]) if pos is not None and pos + 1 < len(session_caps) else None,
        }

    # bbox coords are in screenshot/device pixels → scale the CSS viewport bounds by DPR.
    # Each candidate is tagged `visible`; the labeler hides off-screen ones by default
    # but can reveal them, and the trainer can use the same flag for its negative set.
    dsf = (capture.device_scale_factor if capture and capture.device_scale_factor else 1) or 1
    bound_w = (capture.viewport_width or 0) * dsf if capture else 0
    bound_h = (capture.viewport_height or 0) * dsf if capture else 0
    profile = capture.capture_profile if capture else "viewport"
    all_cands = observation.ax_candidates
    out = [
        {"candidate_id": c.get("candidate_id"), "role": c.get("role"),
         "name": c.get("caption") or c.get("name") or "", "backend_node_id": c.get("backend_node_id"),
         "bbox": c.get("bbox"), "visible": _candidate_visible(c, bound_w, bound_h, profile)}
        for c in all_cands
    ]
    return {
        "filename": filename, "fingerprint": fp[:12], "url": observation.url, "goal": goal,
        "screenshot_filename": screenshot_filename,
        "context": context, "trajectory": trajectory,
        "total_candidates": len(out),
        "hidden_count": sum(1 for c in out if not c["visible"]),
        "candidates": out,
        "suggestion": suggestion,
        "golden": {
            "positive_candidate_id": capture.positive_candidate_id if capture else None,
            "review_status": capture.review_status if capture else None,
            "label_source": capture.label_source if capture else None,
            "label_confidence": capture.label_confidence if capture else None,
            "candidate_labels": (capture.candidate_labels or {}) if capture else {},
        },
    }


@router.get("/api/training/state_graph")
def training_state_graph(db: Session = Depends(get_db)):
    """The agent's map of the world — nodes = page-states, edges = transitions.

    Two edge sources, merged by (from,to):
      * intended  — a capture's observed_page_state → post_action_state (the human-labeled
        "this action should lead there"); carries the action verb. The planner's substrate.
      * observed  — consecutive captures in a session (prev.observed → cur.observed): what
        actually happened. Divergence from intended = where reality branches (captcha, etc).

    Nodes carry metadata (domain/stage/category/terminal) + how many captures use them +
    whether any has a golden pick — so the Lab can render coverage at a glance and you can
    see which nodes are thin (need more L3 examples)."""
    # node metadata from the page-state registry
    meta = {}
    for s in db.scalars(select(PageStateRegistry).where(PageStateRegistry.status == "active")).all():
        meta[s.state_id] = {
            "state_id": s.state_id, "display_name": s.display_name, "scope": s.scope,
            "domain_id": s.domain_id, "goal_id": s.goal_id, "category": s.category or "general",
            "stage": s.stage or "neutral",
        }
    goal_domain = {g.goal_id: g.domain_id for g in db.scalars(select(GoalRegistry)).all()}

    caps = db.scalars(select(TrainingCapture)).all()
    node_stat = {}   # state_id -> {count, golden, domains:set}
    def _bump(state_id, domain_id, golden):
        if not state_id:
            return
        n = node_stat.setdefault(state_id, {"count": 0, "golden": 0, "domains": set()})
        n["count"] += 1
        n["golden"] += 1 if golden else 0
        if domain_id:
            n["domains"].add(domain_id)

    edges = {}  # (from,to) -> {actions:set, intended:int, observed:int}
    def _edge(a, b, action=None, kind="intended"):
        if not a or not b or a == b:
            return
        e = edges.setdefault((a, b), {"actions": set(), "intended": 0, "observed": 0})
        if action:
            e["actions"].add(action)
        e[kind] += 1

    for c in caps:
        _bump(c.observed_page_state, c.domain_id, c.positive_candidate_id is not None)
        if c.observed_page_state and c.post_action_state:
            _edge(c.observed_page_state, c.post_action_state, c.action_type_hint, "intended")

    # observed edges from session ordering
    by_session = {}
    for c in caps:
        by_session.setdefault(c.training_session_id, []).append(c)
    for caps_in in by_session.values():
        ordered = sorted(caps_in, key=lambda c: c.captured_at)
        for prev, cur in zip(ordered, ordered[1:]):
            if prev.observed_page_state and cur.observed_page_state:
                _edge(prev.observed_page_state, cur.observed_page_state, None, "observed")

    def _domain_of(sid):
        m = meta.get(sid, {})
        return m.get("domain_id") or goal_domain.get(m.get("goal_id")) or (
            "global" if m.get("scope") == "global" else "generic")

    # ensure every edge endpoint is a node (a target only seen as "expected next" has no
    # capture observed as it yet → count 0, but it must still appear in the graph)
    for (a, b) in edges:
        for sid in (a, b):
            node_stat.setdefault(sid, {"count": 0, "golden": 0, "domains": set()})

    nodes = []
    for sid, stat in node_stat.items():
        m = meta.get(sid, {"display_name": sid, "category": "general", "stage": "neutral", "scope": "unknown"})
        nodes.append({
            "state_id": sid, "display_name": m.get("display_name", sid),
            "domain": _domain_of(sid), "stage": m.get("stage", "neutral"),
            "category": m.get("category", "general"), "terminal": m.get("category") == "terminal",
            "count": stat["count"], "has_golden": stat["golden"] > 0,
        })
    edge_list = [
        {"from": a, "to": b, "actions": sorted(e["actions"]),
         "intended": e["intended"], "observed": e["observed"],
         "count": e["intended"] + e["observed"]}
        for (a, b), e in edges.items()
    ]
    return {"generated_at": utcnow().isoformat(), "nodes": nodes, "edges": edge_list,
            "domains": sorted({n["domain"] for n in nodes})}


@router.get("/api/training/scorecard")
def training_scorecard(db: Session = Depends(get_db)):
    """Corpus health + the quality gate — the 'is this data good enough to train on?'
    view. Joins three truth signals per state: the teacher's self-confidence
    (loop_steps), the behavioral verify verdict (verify_replay, by fingerprint), and
    human review (TrainingCapture.review_status / positive_candidate_id). Returns a
    per-state gate classification + aggregate strength metrics so bad data is
    quarantined for review instead of silently distilled into the cheap models."""
    cache_dir = _artifacts_dir() / "cache"

    rows: list[dict] = []
    corpus_path = cache_dir / "loop_steps.jsonl"
    if corpus_path.exists():
        for line in corpus_path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:
                continue

    verify_ok_by_fp: dict[str, bool] = {}
    vpath = cache_dir / "verify_replay.jsonl"
    if vpath.exists():
        for line in vpath.read_text(encoding="utf-8").splitlines():
            try:
                v = json.loads(line)
                verify_ok_by_fp[v.get("from_fingerprint", "")] = bool(v.get("ok"))
            except Exception:
                continue

    # Human ground-truth counts (reviewed captures with a confirmed positive candidate).
    reviewed = db.scalar(select(func.count()).select_from(TrainingCapture)
                         .where(TrainingCapture.review_status == "reviewed")) or 0
    with_positive = db.scalar(select(func.count()).select_from(TrainingCapture)
                              .where(TrainingCapture.positive_candidate_id.isnot(None))) or 0
    # Distillation provenance — how the golden labels are being produced. 'auto' rows are
    # machine golden the verifier promoted from Haiku reps (the flywheel); 'human' are
    # operator-confirmed; 'suggested' await a one-click confirm (not yet golden).
    by_source = dict(db.execute(
        select(TrainingCapture.label_source, func.count())
        .where(TrainingCapture.label_source.isnot(None))
        .group_by(TrainingCapture.label_source)
    ).all())
    label_sources = {
        "human": int(by_source.get("human", 0)),
        "auto": int(by_source.get("auto", 0)),
        "suggested": int(by_source.get("suggested", 0)),
    }

    # AGREEMENT: teacher pick vs human golden — the first TRUE accuracy signal (not the
    # teacher grading itself). For each golden-labeled capture, map the model's recorded
    # pick (loop_steps target_backend_node_id, by fingerprint) to a candidate_id and
    # compare. A match on the golden OR an acceptable alternate counts as agreement.
    from select_stage import fingerprint as _fp

    target_by_fp = {r.get("fingerprint", ""): r.get("target_backend_node_id") for r in rows}
    labeled_caps = db.scalars(
        select(TrainingCapture).where(TrainingCapture.positive_candidate_id.isnot(None))
    ).all()
    agree = {"scored": 0, "golden": 0, "acceptable": 0, "miss": 0, "no_model_pick": 0}
    agreement_rows: list[dict] = []
    for cap in labeled_caps:
        try:
            obs, goal = _observation_from_capture(cap.artifact_filename)
        except HTTPException:
            continue
        fp = _fp.compute(url=obs.url, viewport=obs.viewport, candidates=obs.ax_candidates,
                         task_goal=goal or "", dom_clickables=obs.dom_clickables)
        tb = target_by_fp.get(fp)
        if tb is None:
            agree["no_model_pick"] += 1
            continue
        model_pick = next((c.get("candidate_id") for c in obs.ax_candidates
                           if c.get("backend_node_id") is not None and int(c["backend_node_id"]) == int(tb)), None)
        acceptable = {k for k, v in (cap.candidate_labels or {}).items() if v == "acceptable"}
        if model_pick and model_pick == cap.positive_candidate_id:
            verdict = "golden"
        elif model_pick and model_pick in acceptable:
            verdict = "acceptable"
        else:
            verdict = "miss"
        agree["scored"] += 1
        agree[verdict] += 1
        agreement_rows.append({
            "route": _fp.route_template(obs.url), "model_pick": model_pick,
            "human_golden": cap.positive_candidate_id, "verdict": verdict,
        })
    agree_pct = round(100 * (agree["golden"] + agree["acceptable"]) / agree["scored"]) if agree["scored"] else None

    states: list[dict] = []
    counts = {"train_eligible": 0, "escalated": 0, "verify_failed": 0, "low_confidence": 0}
    conf_sum = verified_n = 0.0
    for r in rows:
        fp = r.get("fingerprint", "")
        verify_ok = verify_ok_by_fp.get(fp)  # None = no verdict (unverified)
        eligible, reason = _gate_verdict(
            confidence=r.get("confidence", 0.0),
            needs_human=bool(r.get("needs_human")),
            verify_ok=verify_ok,
        )
        counts["train_eligible" if eligible else reason] += 1
        conf_sum += r.get("confidence", 0.0)
        verified_n += 1 if verify_ok is not None else 0
        states.append({
            "route": r.get("route"), "fingerprint": fp[:12],
            "action_id": r.get("action_id"), "layer": r.get("layer"),
            "confidence": r.get("confidence"), "needs_human": bool(r.get("needs_human")),
            "verify_ok": verify_ok, "candidate_count": r.get("candidate_count"),
            "train_eligible": eligible, "quarantine_reason": reason,
        })

    n = len(rows)
    return {
        "generated_at": utcnow().isoformat(),
        "totals": {
            "states": n,
            "train_eligible": counts["train_eligible"],
            "quarantined": n - counts["train_eligible"],
            "mean_confidence": round(conf_sum / n, 3) if n else None,
            "verified_states": int(verified_n),
            "unverified_states": n - int(verified_n),
            "escalation_rate": round(counts["escalated"] / n, 3) if n else None,
            "human_reviewed": int(reviewed),
            "human_confirmed_label": int(with_positive),
            "agreement_pct": agree_pct,
            "agreement_scored": agree["scored"],
        },
        "label_sources": label_sources,
        "quarantine_breakdown": {
            "escalated": counts["escalated"],
            "verify_failed": counts["verify_failed"],
            "low_confidence": counts["low_confidence"],
        },
        "agreement": {
            "scored": agree["scored"], "golden": agree["golden"], "acceptable": agree["acceptable"],
            "miss": agree["miss"], "no_model_pick": agree["no_model_pick"], "pct": agree_pct,
            "rows": agreement_rows,
        },
        "states": states,
    }


@router.post("/api/select/trajectory")
def save_cursor_trajectory(payload: dict):
    """Persist one recorded human cursor trajectory from the Movement Playground.
    This grows the ground-truth corpus the diffusion input-model will train on."""
    from select_stage import telemetry

    n = telemetry.record_trajectory(payload)
    return {"saved": True, "corpus_size": n}


@router.get("/api/select/trajectories/count")
def get_trajectory_count():
    from select_stage import telemetry

    return {"corpus_size": telemetry.trajectory_count()}


@router.get("/api/select/telemetry")
def get_select_telemetry():
    """SELECT-stage flywheel metrics for the Lab dashboard — cache-hit rate,
    escalation rate, cost-per-task, layer/reason mix, daily trend. Aggregated
    from the same selection telemetry corpus the later local layers train on."""
    from select_stage import telemetry

    return {"generated_at": utcnow().isoformat(), **telemetry.summarize()}


@router.post("/api/observations/{filename}/vision")
async def generate_vision_candidates(filename: str, captions: bool = False):
    """Lazily run the OmniParser proposer for ONE capture and write its sidecar.

    The gate: this is only called when a capture is opened in the labeler (detect-only,
    fast) or when the annotator clicks "Generate captions" (captions=true → loads
    Florence-2). Captures nobody reviews never run the proposer. Proxies to the capture
    server; the client re-fetches the observation to pick up the new candidates.
    """
    traces_dir = _artifacts_dir() / "observer-traces"
    if not (traces_dir / filename).exists():
        raise HTTPException(status_code=404, detail="Observation not found")
    try:
        # Captioning can take minutes on MPS; detect-only is seconds.
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(
                f"{settings.capture_server_url}/proposer/backfill/{filename}",
                params={"include_captions": captions},
            )
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Proposer failed: {exc}")


@router.get("/api/observations")
def list_observations(db: Session = Depends(get_db)):
    traces_dir = _artifacts_dir() / "observer-traces"
    stmt = select(TrainingCapture).order_by(TrainingCapture.captured_at.desc())
    results = []
    for capture in db.scalars(stmt).all():
        session = db.get(TrainingSession, capture.training_session_id)
        trace_path = traces_dir / capture.artifact_filename
        if not trace_path.exists():
            continue
        try:
            data = json.loads(trace_path.read_text())
            meta = read_meta(traces_dir, capture.artifact_filename)
            results.append({
                "filename": capture.artifact_filename,
                "timestamp": capture.captured_at.isoformat(),
                "scenario": data.get("metadata", {}).get("scenario"),
                "source": data.get("metadata", {}).get("source"),
                "candidate_count": capture.candidate_count,
                "ax_candidate_count": capture.ax_candidate_count,
                "group": capture.domain_id,
                "status": capture.review_status,
                "label": capture.goal_id,
                "task_goal": capture.goal_id,
                "review_status": capture.review_status,
                "positive_candidate_id": capture.positive_candidate_id,
                "has_screenshot": len(capture.screenshot_refs or []) > 0,
                "page_url": capture.url,
                "page_title": capture.title,
                "training_session_id": capture.training_session_id,
                "scenario_id": session.scenario_id if session else None,
                "domain_id": capture.domain_id,
                "goal_id": capture.goal_id,
                "task_id": capture.task_id,
                "capture_profile": capture.capture_profile,
                # Organization/tree fields
                "title": meta.get("title"),
                "observed_page_state": capture.observed_page_state,
                "action_type": capture.action_type_hint,
                "action_text": capture.action_text,
                "element_query": capture.element_query,
                "approved_bbox": capture.approved_bbox is not None,
            })
        except Exception:
            continue
    return results


def _delete_observation_files(filename: str) -> bool:
    traces_dir = _artifacts_dir() / "observer-traces"
    screenshots_dir = _artifacts_dir() / "observer-screenshots"
    trace_path = traces_dir / filename
    if not trace_path.exists():
        return False

    try:
        data = json.loads(trace_path.read_text())
        for ref in data.get("acquisition", {}).get("screenshots", []):
            sname = ref.get("filename") or (ref.get("path", "").split("/")[-1])
            if sname:
                sfile = screenshots_dir / sname
                if sfile.exists():
                    sfile.unlink()
    except Exception:
        pass

    # Delete meta sidecar
    meta_path = traces_dir / f"{filename}.meta.json"
    if meta_path.exists():
        meta_path.unlink()

    # Delete vision-proposer sidecar (written by mcp async backfill)
    vision_path = traces_dir / f"{filename}.vision.json"
    if vision_path.exists():
        vision_path.unlink()

    trace_path.unlink()
    return True


@router.delete("/api/observations/{filename}")
def delete_observation(filename: str, db: Session = Depends(get_db)):
    capture = db.scalar(select(TrainingCapture).where(TrainingCapture.artifact_filename == filename))
    if capture is not None:
        db.delete(capture)
        db.commit()
    if not _delete_observation_files(filename):
        raise HTTPException(status_code=404, detail="Observation not found")
    return {"ok": True}


class BulkDeleteRequest(BaseModel):
    filenames: list[str]


@router.post("/api/observations/bulk-delete")
def bulk_delete_observations(body: BulkDeleteRequest, db: Session = Depends(get_db)):
    deleted = 0
    for filename in body.filenames:
        capture = db.scalar(select(TrainingCapture).where(TrainingCapture.artifact_filename == filename))
        if capture is not None:
            db.delete(capture)
            db.commit()
        if _delete_observation_files(filename):
            deleted += 1
    return {"ok": True, "deleted": deleted}


class UpdateMetaRequest(BaseModel):
    group: Optional[str] = None
    status: Optional[str] = None
    label: Optional[str] = None
    title: Optional[str] = None
    training_annotation: Optional[dict] = None
    # Vision annotation fields (shorthand — can also be sent inside training_annotation)
    observed_page_state: Optional[str] = None
    post_action_state: Optional[str] = None
    # Per-capture interaction-layer overrides — let the annotator refine the
    # default that was inherited from the scenario at capture time. Important
    # for multi-step flows where one scenario covers several different actions.
    element_query: Optional[str] = None
    action_type: Optional[str] = None
    action_text: Optional[str] = None


@router.patch("/api/observations/{filename}")
def update_observation_meta(filename: str, body: UpdateMetaRequest, db: Session = Depends(get_db)):
    traces_dir = _artifacts_dir() / "observer-traces"
    if not (traces_dir / filename).exists():
        raise HTTPException(status_code=404, detail="Observation not found")

    capture = db.scalar(select(TrainingCapture).where(TrainingCapture.artifact_filename == filename))
    if capture is None:
        raise HTTPException(status_code=404, detail="Training capture not found")

    meta = read_meta(traces_dir, filename)
    for key in ("group", "status", "label", "title"):
        val = getattr(body, key)
        if val is not None:
            if val == "":
                meta.pop(key, None)
            else:
                meta[key] = val

    if body.training_annotation is not None:
        merged = merge_training_annotation(_training_annotation_from_capture(capture), body.training_annotation)
        capture.review_status = merged["review_status"]
        capture.positive_candidate_id = merged.get("positive_candidate_id")
        capture.rejected_candidate_ids = merged.get("rejected_candidate_ids") or []
        capture.candidate_labels = merged.get("candidate_labels") or {}
        capture.approved_bbox = merged.get("approved_bbox")
        capture.manual_candidates = merged.get("manual_candidates") or []
        # A human touched this label → highest trust, and the auto-promotion pass must
        # never overwrite it (it may CONFIRM a prior 'suggested' or CORRECT an 'auto').
        if merged.get("review_status") in ("reviewed", "approved") or merged.get("positive_candidate_id"):
            capture.label_source = "human"
            capture.verified_at = datetime.now(timezone.utc)
        meta["training_annotation"] = merged
        db.commit()
    elif body.status in {"draft", "reviewed", "approved", "rejected", "archived"}:
        capture.review_status = body.status
        db.commit()

    # Vision annotation fields — persisted directly on the capture row
    vision_dirty = False
    if body.observed_page_state is not None:
        capture.observed_page_state = body.observed_page_state or None
        # A human set the page-state → highest trust; the Haiku auto pass must not overwrite.
        capture.state_label_source = "human" if body.observed_page_state else None
        capture.state_label_confidence = None
        vision_dirty = True
    if body.post_action_state is not None:
        capture.post_action_state = body.post_action_state or None
        vision_dirty = True
    if vision_dirty:
        db.commit()

    # Interaction-layer overrides — annotator-set per capture for multi-step flows.
    # Empty string is treated as "clear back to scenario default" by storing NULL.
    interaction_dirty = False
    if body.element_query is not None:
        capture.element_query = body.element_query or None
        interaction_dirty = True
    if body.action_type is not None:
        capture.action_type_hint = body.action_type or "any"
        interaction_dirty = True
    if body.action_text is not None:
        capture.action_text = body.action_text or None
        interaction_dirty = True
    if interaction_dirty:
        db.commit()

    write_meta(traces_dir, filename, meta)
    return {"ok": True, **meta}


class TrainRequest(BaseModel):
    rebuild_dataset: bool = True


REVIEWED_CAPTURE_STATUSES = ("reviewed", "approved")


def _reviewed_training_captures(db: Session):
    return db.scalars(
        select(TrainingCapture)
        .where(TrainingCapture.review_status.in_(REVIEWED_CAPTURE_STATUSES))
        .order_by(TrainingCapture.captured_at.asc())
    ).all()


@router.post("/api/training/build-dataset")
def build_training_dataset(db: Session = Depends(get_db)):
    captures = _reviewed_training_captures(db)
    manifest = build_grounding_dataset(_artifacts_dir(), captures=captures)
    return {"ok": True, **manifest}


@router.post("/api/training/build-vision-dataset")
def build_vision_training_dataset(db: Session = Depends(get_db)):
    """Build a vision-grounding dataset: (screenshot, element_query) → bbox pairs."""
    captures = _reviewed_training_captures(db)
    manifest = build_vision_dataset(_artifacts_dir(), captures=captures)
    return {"ok": True, **manifest}


@router.post("/api/training/train")
def train_grounding(body: TrainRequest, db: Session = Depends(get_db)):
    captures = _reviewed_training_captures(db)
    manifest = build_grounding_dataset(_artifacts_dir(), captures=captures) if body.rebuild_dataset else None
    result = train_grounding_model(_artifacts_dir(), dataset_manifest=manifest)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result


@router.get("/api/training/target-comparison")
def training_target_comparison(db: Session = Depends(get_db)):
    captures = _reviewed_training_captures(db)
    return compare_training_targets(_artifacts_dir(), captures=captures)


@router.post("/api/training/train_stage_observer")
def train_stage_observer_endpoint(db: Session = Depends(get_db)):
    """Train the coarse page-state observer (L3 v0): a cheap 3-way auth-stage classifier
    (authenticated/unauthenticated/neutral) over URL+AX features. Labels come from each
    capture's observed_page_state mapped through the page-state registry's lifecycle
    stage. Trainable today on existing labels; the planner's #1 'am I logged in?' signal."""
    import state_observer

    stage_by_state = {
        row.state_id: row.stage
        for row in db.scalars(select(PageStateRegistry)).all() if row.stage
    }
    captures = db.scalars(
        select(TrainingCapture).where(TrainingCapture.observed_page_state.isnot(None))
    ).all()
    result = state_observer.train_stage_observer(
        _artifacts_dir(), captures=captures, stage_by_state=stage_by_state)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result


@router.post("/api/training/train_perception")
def train_perception_endpoint(promote: bool = True):
    """Fit the two perception witnesses and persist them (PLAN_perception_v1 §3.2).

    Witness A (DOM/AX TF-IDF centroid) trains on every labeled capture; witness B (a frozen image
    encoder + prototype bank) on those with a screenshot. Minutes of CPU, no GPU, no download with
    the default encoder — cheap enough to re-run after a drive rather than as a ceremony.
    `promote` points the runtime at this build; leave it off to fit and inspect without swapping
    what `load_observer()` returns."""
    from perception import train as perception_train

    fitted = perception_train.fit()
    model_dir = perception_train.save(fitted, promote=promote)
    return {"ok": True, "model_dir": str(model_dir), "promoted": promote, **fitted["metrics"]}


@router.post("/api/training/train_state_transition")
def train_state_transition_endpoint(db: Session = Depends(get_db)):
    """Train the state-transition model (the planner's look-ahead edge-model): given
    (from_state, action) predict to_state. Built from captures with BOTH observed_page_state
    and post_action_state — the same intended edges the state graph renders. A smoothed
    transition table with a (from_state) backoff; the substrate planner graph-search uses."""
    import state_transition

    captures = db.scalars(
        select(TrainingCapture)
        .where(TrainingCapture.observed_page_state.isnot(None))
        .where(TrainingCapture.post_action_state.isnot(None))
    ).all()
    result = state_transition.train_transition_model(_artifacts_dir(), captures=captures)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result


#: Page-state auto-label band. The Haiku page-state teacher WRITES observed_page_state
#: only when it confidently matches a KNOWN state — below this it stays a read-only
#: suggestion for one-click human confirm (keeps the L3/transition training label clean).
_PAGE_STATE_AUTO_CONFIDENCE = 0.9


def _candidate_states_for(db: Session, *, domain_id: Optional[str], goal_id: Optional[str],
                          scenario_id: Optional[str]) -> list[dict[str, Any]]:
    """The state menu shown to the teacher: global + this capture's domain/goal/scenario
    states (same scoping the labeler uses), so it can't pick a cross-domain state."""
    states = db.scalars(select(PageStateRegistry).where(PageStateRegistry.status == "active")).all()

    def relevant(s: PageStateRegistry) -> bool:
        if s.scope == "global":
            return True
        if s.scope == "domain":
            return domain_id is not None and s.domain_id == domain_id
        if s.scope == "goal":
            if goal_id is None or s.goal_id != goal_id:
                return False
            return s.domain_id is None or domain_id is None or s.domain_id == domain_id
        if s.scope == "scenario":
            return scenario_id is not None and s.scenario_id == scenario_id
        return False

    return [{"state_id": s.state_id, "display_name": s.display_name, "description": s.description}
            for s in states if relevant(s)]


@router.post("/api/training/suggest_page_state")
def suggest_page_state(filename: str, write: bool = True, db: Session = Depends(get_db)):
    """Haiku page-state TEACHER for one capture: classify which known state the screenshot
    shows, growing the observed_page_state corpus that L3 + the transition model distill from.

    One budget-gated Haiku call (supervised, not a batch — keeps spend controlled). WRITES
    observed_page_state only on a confident match to a KNOWN state (≥0.9, not is_new,
    not needs_human) tagged state_label_source='auto'; otherwise returns a read-only
    suggestion for one-click human confirm. Never overwrites a human label."""
    from select_stage import haiku_page_state

    capture = db.scalar(select(TrainingCapture).where(TrainingCapture.artifact_filename == filename))
    if capture is None:
        raise HTTPException(status_code=404, detail="Training capture not found")
    traces_dir = _artifacts_dir() / "observer-traces"
    if not (traces_dir / filename).exists():
        raise HTTPException(status_code=404, detail="Capture artifact not found")
    artifact = json.loads((traces_dir / filename).read_text())
    acq = artifact.get("acquisition", {}) or {}
    shots = acq.get("screenshots") or []
    if not shots:
        raise HTTPException(status_code=400, detail="Capture has no screenshot")
    screenshot_path = shots[0].get("path") or str(
        _artifacts_dir() / "observer-screenshots" / shots[0].get("filename", ""))
    url = (acq.get("page_identity", {}) or {}).get("url", "")
    page_text = (acq.get("js_state", {}) or {}).get("body_text_preview", "") or ""

    candidate_states = _candidate_states_for(
        db, domain_id=capture.domain_id, goal_id=capture.goal_id, scenario_id=capture.scenario_id)
    if not candidate_states:
        raise HTTPException(status_code=400, detail="No candidate page-states registered for this scope")

    try:
        pred = haiku_page_state.classify(
            screenshot_path=screenshot_path, candidate_states=candidate_states,
            url=url, page_text=page_text, meta={"filename": filename, "kind": "page_state"})
    except anthropic_usage.BudgetExceededError as exc:
        raise HTTPException(status_code=429, detail=f"Budget exceeded — try later: {exc}")

    # A human label is anything already set that the auto pass didn't write.
    human_owned = bool(capture.observed_page_state) and capture.state_label_source != "auto"
    written = None
    if (write and not human_owned and pred["state_id"] and not pred["is_new"]
            and not pred["needs_human"] and pred["confidence"] >= _PAGE_STATE_AUTO_CONFIDENCE):
        capture.observed_page_state = pred["state_id"]
        capture.state_label_source = "auto"
        capture.state_label_confidence = pred["confidence"]
        db.commit()
        written = "auto"

    # A state the classifier judged NEW is the most valuable thing it can tell us — and it used
    # to be returned here and dropped, so the registry never grew from what we actually met.
    # Record it as a CANDIDATE (inert until approved: the menus filter status == "active").
    candidate = None
    if write and pred.get("is_new") and pred.get("proposed_name"):
        import page_state_candidates
        candidate = page_state_candidates.record_candidate(
            db, proposed_name=pred["proposed_name"], domain_id=capture.domain_id,
            goal_id=capture.goal_id, scenario_id=capture.scenario_id, url=url)

    return {
        "filename": filename,
        "suggestion": pred,
        "written": written,
        "write_mode": "auto" if written else ("suggested_only" if pred["state_id"] else "abstained"),
        "human_owned": human_owned,
        "auto_threshold": _PAGE_STATE_AUTO_CONFIDENCE,
        "candidate_state_count": len(candidate_states),
        "candidate_recorded": candidate,
    }


# ===== Models registry + eval =====
#
# v0 ships `vision_element_grounding__v0_zero_shot_florence2_base` — a
# zero-shot Florence-2 baseline. The eval contract is the durable part:
# (screenshot, element_query) -> bbox, scored against approved_bbox on the
# stable eval split (training._stable_split). See docs/v0-florence.md.

V0_FLORENCE_TARGET = "vision_element_grounding"
V0_FLORENCE_IMPL = "v0_zero_shot_florence2_base"
V0_FLORENCE_MODEL_NAME = "microsoft/Florence-2-base"


def _eval_summary_for(run: Optional[ModelEvalRun]) -> Optional[ModelEvalRunSummary]:
    if run is None:
        return None
    metrics = run.metrics or {}
    return ModelEvalRunSummary(
        id=run.id,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        record_count=run.record_count,
        mean_bbox_iou=metrics.get("mean_bbox_iou"),
        iou_at_50_accuracy=metrics.get("iou_at_50_accuracy"),
        center_in_target_accuracy=metrics.get("center_in_target_accuracy"),
    )


def _model_read(db: Session, row: ModelRegistry) -> ModelRead:
    last = model_registry.get_last_eval(db, row.id)
    return ModelRead(
        id=row.id,
        target_id=row.target_id,
        implementation=row.implementation,
        model_name=row.model_name,
        config=row.config,
        created_at=row.created_at,
        archived_at=row.archived_at,
        last_eval=_eval_summary_for(last),
    )


@router.get("/api/models", response_model=list[ModelRead])
def list_registered_models(db: Session = Depends(get_db)):
    return [_model_read(db, row) for row in model_registry.list_models(db)]


@router.post("/api/models/seed")
def seed_v0_florence_baseline(db: Session = Depends(get_db)):
    """Idempotent: register both zero-shot Florence-2 baselines if missing.

    Same model, two implementations — raw query vs. heuristically-normalized
    query. The pair is the first head-to-head comparison the Registry shows,
    and demonstrates the platform's swap-point: adding a model = adding a row +
    a wrapper function in model_lib/eval.py:IMPLEMENTATIONS.
    """
    # Florence-2-base family — general phrase grounding model, three query
    # preprocessing variants. Two-stage (OmniParser+Florence) is intentionally
    # NOT seeded here; the eval showed it was strictly worse than Florence alone,
    # and the wrapper stays in IMPLEMENTATIONS for posterity / future revisits.
    florence_seeds = [
        ("v0_zero_shot_florence2_base", {"query_preprocessor": "none"}),
        ("v0_zero_shot_florence2_base_short_query", {"query_preprocessor": "heuristic_noun_phrase"}),
        ("v0_zero_shot_florence2_base_descriptive_query", {"query_preprocessor": "heuristic_noun_phrase + action_type_tag"}),
    ]
    rows = []
    florence_base = {"task_prompt": "<CAPTION_TO_PHRASE_GROUNDING>", "num_beams": 3, "dtype": "float32"}
    for impl, extra in florence_seeds:
        row = model_registry.register_model(
            db,
            target_id=V0_FLORENCE_TARGET,
            implementation=impl,
            model_name=V0_FLORENCE_MODEL_NAME,
            config={**florence_base, **extra},
        )
        rows.append(_model_read(db, row).model_dump(mode="json"))

    # UGround-V1-2B — UI-specialized GUI grounder. Different output shape
    # (point, not bbox), wrapped as a small synthetic bbox for IoU. The
    # honest metric for this row is center_in_target.
    uground_row = model_registry.register_model(
        db,
        target_id=V0_FLORENCE_TARGET,
        implementation="v0_zero_shot_uground_v1_2b",
        model_name="osunlp/UGround-V1-2B",
        config={
            "base_architecture": "Qwen2-VL-2B",
            "output_type": "point",
            "synthetic_bbox_size_px": 40,
            "dtype": "float32",
            "honest_metric": "center_in_target",
        },
    )
    rows.append(_model_read(db, uground_row).model_dump(mode="json"))
    return rows


@router.get("/api/models/eval-runs", response_model=list[ModelEvalRunRead])
def list_recent_eval_runs(model_id: Optional[str] = None, limit: int = 50, db: Session = Depends(get_db)):
    return model_registry.recent_eval_runs(db, model_id=model_id, limit=limit)


@router.get("/api/models/eval-runs/{run_id}", response_model=ModelEvalRunDetail)
def get_eval_run_detail(run_id: str, db: Session = Depends(get_db)):
    run = db.get(ModelEvalRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Eval run not found")
    sample = model_eval.read_predictions_sample(run.artifact_dir, limit=25)
    return ModelEvalRunDetail(
        id=run.id,
        model_id=run.model_id,
        dataset_id=run.dataset_id,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        record_count=run.record_count,
        metrics=run.metrics,
        artifact_dir=run.artifact_dir,
        error=run.error,
        predictions_sample=sample,
    )


@router.get("/api/models/{model_id}")
def get_model_detail(model_id: str, db: Session = Depends(get_db)):
    row = model_registry.get_model(db, model_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Model not found")
    runs = model_registry.recent_eval_runs(db, model_id=model_id, limit=10)
    return {
        "model": _model_read(db, row).model_dump(),
        "recent_runs": [ModelEvalRunRead.model_validate(r).model_dump(mode="json") for r in runs],
    }


@router.delete("/api/models/eval-runs/{run_id}")
def delete_eval_run(run_id: str, db: Session = Depends(get_db)):
    """Remove an eval run (DB row + on-disk artifact dir). Useful for cleaning up
    accidental re-runs while iterating."""
    import shutil
    run = db.get(ModelEvalRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Eval run not found")
    artifact_dir = run.artifact_dir
    db.delete(run)
    db.commit()
    if artifact_dir:
        try:
            shutil.rmtree(artifact_dir, ignore_errors=True)
        except Exception:
            pass
    return {"ok": True, "deleted_run_id": run_id}


def _spawn_eval_thread(run_id: str) -> None:
    """Run the eval in a real daemon thread so the HTTP response can return
    immediately. The thread carries its own DB session (see execute_eval_run).
    uvicorn --reload will still kill the worker on file edits, but resumability
    handles that case — predictions persist per-capture and `POST /resume`
    picks up where we stopped.
    """
    import threading
    artifacts_root = _artifacts_dir()
    t = threading.Thread(
        target=model_eval.execute_eval_run,
        kwargs={"run_id": run_id, "artifacts_root": artifacts_root},
        daemon=True,
        name=f"eval-{run_id[:8]}",
    )
    t.start()


@router.post("/api/models/{model_id}/eval", response_model=ModelEvalRunRead)
def run_model_eval(model_id: str, db: Session = Depends(get_db)):
    """Schedule an eval run in a background thread and return the row immediately.

    The UI polls the returned run_id for live progress (`progress.completed`,
    `progress.current_capture`, `progress.current_step`). Per-capture
    predictions are written to disk as they complete, so a mid-run crash is
    recoverable via `POST /eval-runs/{id}/resume`.
    """
    if model_registry.get_model(db, model_id) is None:
        raise HTTPException(status_code=404, detail="Model not found")
    try:
        run = model_eval.create_eval_run(db=db, model_id=model_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _spawn_eval_thread(run.id)
    return run


@router.get("/api/models/eval-runs/{run_id}/log")
def read_eval_run_log(run_id: str, tail: int = 200, db: Session = Depends(get_db)):
    """Return the tail of the run's run.log file.

    The eval worker writes one line per major step (load, per-capture progress,
    per-capture timing, cancel/exit) so this is the live window into a running
    eval — much finer than the `progress` field which only updates between
    captures. The UI polls this while a run is in-flight.
    """
    run = db.get(ModelEvalRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Eval run not found")
    if not run.artifact_dir:
        return {"run_id": run_id, "lines": [], "note": "no log file yet (worker has not started writing)"}
    log_path = Path(run.artifact_dir) / "run.log"
    if not log_path.exists():
        return {"run_id": run_id, "lines": [], "note": "log file does not exist yet"}
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"run_id": run_id, "lines": [], "note": f"read failed: {exc}"}
    all_lines = text.splitlines()
    tail = max(1, min(tail, 5000))
    return {
        "run_id": run_id,
        "lines": all_lines[-tail:],
        "total_lines": len(all_lines),
        "log_path": str(log_path),
    }


@router.post("/api/models/eval-runs/{run_id}/cancel", response_model=ModelEvalRunRead)
def cancel_eval_run(run_id: str, db: Session = Depends(get_db)):
    """Request a clean cancel. The background runner checks this flag between
    captures and exits with status=cancelled after the next checkpoint."""
    run = model_eval.request_cancel(db=db, run_id=run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Eval run not found")
    return run


@router.post("/api/models/eval-runs/{run_id}/resume", response_model=ModelEvalRunRead)
def resume_eval_run(run_id: str, db: Session = Depends(get_db)):
    """Start a NEW run that picks up the prior run's predictions.jsonl and only
    processes captures that weren't completed. The original run row is left
    alone; the new run links back via `resumed_from`."""
    prev = db.get(ModelEvalRun, run_id)
    if prev is None:
        raise HTTPException(status_code=404, detail="Eval run not found")
    if prev.status == "running":
        raise HTTPException(status_code=400, detail="Run is still active; cancel it first")
    try:
        new_run = model_eval.create_eval_run(db=db, model_id=prev.model_id, resumed_from=prev.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _spawn_eval_thread(new_run.id)
    return new_run


# ---------------------------------------------------------------------------
# Application factory (docs/TARGET_ARCHITECTURE.md Layer 2). Assemble the control
# plane from its routers + bootstrap. The remaining inline routes live on the
# module-level `router`; as domains are extracted they move to routers/*.py and
# the `router` include shrinks toward the goal shape: factory + routers, no route
# logic left in main.py.
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    app = FastAPI(title="Control Plane API", version="0.0.1")
    app.add_middleware(
        CORSMiddleware,
        # Any localhost port — the Vite dev server (5173) plus preview/test servers on other ports.
        allow_origin_regex=r"http://localhost:\d+",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _api_access_log(request, call_next):
        """Record each API touch into the in-memory ring — the 'api' source of the Session Activity
        feed (what's going in/out of the system). Never affects the response; skips the feed's own
        endpoint so it doesn't observe itself."""
        import time
        t0 = time.perf_counter()
        response = await call_next(request)
        try:
            path = request.url.path
            if path.startswith("/api/") and not path.startswith("/api/activity"):
                import api_access
                api_access.record(request.method, path, response.status_code,
                                  (time.perf_counter() - t0) * 1000.0)
        except Exception:  # noqa: BLE001
            pass
        return response

    _assets.ASSETS_ROOT.mkdir(parents=True, exist_ok=True)
    app.mount("/assets", StaticFiles(directory=str(_assets.ASSETS_ROOT)), name="assets")

    app.include_router(router)  # core routes not yet extracted into a domain module
    app.include_router(accounts_router.router)
    app.include_router(activity_router.router)
    app.include_router(application_answers_router.router)
    app.include_router(career_search_router.router)
    app.include_router(controller_router.router)
    app.include_router(drive_lock_router.router)
    app.include_router(events_router.router)
    app.include_router(facebook_router.router)
    app.include_router(inventory_router.router)
    app.include_router(providers_router.router)
    app.include_router(session_control_router.router)
    app.include_router(sessions_router.router)
    app.include_router(workspace_router.router)

    app.on_event("startup")(on_startup)  # same hook as before, registered by the factory
    return app


app = create_app()
