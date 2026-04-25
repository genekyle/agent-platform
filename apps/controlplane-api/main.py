import json
import os
import socket
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from db import Base, engine, get_db
from models import (
    DomainRegistry,
    GoalRegistry,
    Run,
    Step,
    TaskRegistry,
    ScenarioRegistry,
    TrainingCapture,
    TrainingSession,
    Worker,
)
from schemas import (
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
from training import (
    build_grounding_dataset,
    compare_training_targets,
    merge_training_annotation,
    read_meta,
    train_grounding_model,
    write_meta,
)

app = FastAPI(title="Control Plane API", version="0.0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def utcnow():
    return datetime.now(timezone.utc)


REGISTRY_SEED = {
    "domains": [
        {
            "domain_id": "facebook_marketplace",
            "display_name": "Facebook Marketplace",
            "host_patterns": ["facebook.com", "www.facebook.com"],
            "page_states": [
                {"page_state_id": "listing_feed", "display_name": "Listing Feed"},
                {"page_state_id": "buyer_inbox", "display_name": "Buyer Inbox"},
                {"page_state_id": "login_wall", "display_name": "Login Wall"},
            ],
            "capture_defaults": {"profile": "viewport", "shot_types": ["viewport", "fullpage"]},
            "validation_expectations": [{"kind": "host_match", "value": "facebook.com"}],
            "config_version": "v1",
        },
        {
            "domain_id": "indeed_jobs",
            "display_name": "Indeed Jobs",
            "host_patterns": ["indeed.com", "www.indeed.com"],
            "page_states": [
                {"page_state_id": "search_results", "display_name": "Search Results"},
                {"page_state_id": "company_page", "display_name": "Company Page"},
                {"page_state_id": "email_alert", "display_name": "Email Alert"},
                {"page_state_id": "job_detail", "display_name": "Job Detail"},
                {"page_state_id": "login_wall", "display_name": "Login Wall"},
            ],
            "capture_defaults": {"profile": "viewport", "shot_types": ["viewport", "fullpage", "sweep_1", "sweep_2"]},
            "validation_expectations": [{"kind": "host_match", "value": "indeed.com"}],
            "config_version": "v1",
        },
        {
            "domain_id": "linkedin_jobs",
            "display_name": "LinkedIn Jobs",
            "host_patterns": ["linkedin.com", "www.linkedin.com"],
            "page_states": [
                {"page_state_id": "job_search", "display_name": "Job Search"},
                {"page_state_id": "job_detail", "display_name": "Job Detail"},
                {"page_state_id": "login_wall", "display_name": "Login Wall"},
            ],
            "capture_defaults": {"profile": "viewport", "shot_types": ["viewport", "fullpage"]},
            "validation_expectations": [{"kind": "host_match", "value": "linkedin.com"}],
            "config_version": "v1",
        },
    ],
    "goals": [
        {"goal_id": "log_in", "domain_id": None, "display_name": "Log In", "action_type_hints": ["type", "click"]},
        {"goal_id": "review_posted_items", "domain_id": "facebook_marketplace", "display_name": "Review Posted Items", "action_type_hints": ["click"]},
        {"goal_id": "reply_to_buyer", "domain_id": "facebook_marketplace", "display_name": "Reply to Buyer", "action_type_hints": ["click", "type"]},
        {"goal_id": "search_jobs", "domain_id": "indeed_jobs", "display_name": "Search Jobs", "action_type_hints": ["type", "click"]},
        {"goal_id": "open_job_posting", "domain_id": "indeed_jobs", "display_name": "Open Job Posting", "action_type_hints": ["click"]},
        {"goal_id": "apply_to_job", "domain_id": "indeed_jobs", "display_name": "Apply to Job", "action_type_hints": ["click", "type", "select"]},
        {"goal_id": "search_linkedin_jobs", "domain_id": "linkedin_jobs", "display_name": "Search LinkedIn Jobs", "action_type_hints": ["type", "click"]},
        {"goal_id": "open_linkedin_job", "domain_id": "linkedin_jobs", "display_name": "Open LinkedIn Job", "action_type_hints": ["click"]},
    ],
    "tasks": [
        {"task_id": "browser_open_tab", "scope_level": "browser", "domain_id": None, "goal_id": None, "display_name": "Open target tab"},
        {"task_id": "marketplace_open_inbox", "scope_level": "domain", "domain_id": "facebook_marketplace", "goal_id": None, "display_name": "Open marketplace inbox"},
        {"task_id": "indeed_apply_flow", "scope_level": "goal", "domain_id": "indeed_jobs", "goal_id": "apply_to_job", "display_name": "Complete Indeed apply flow"},
    ],
    "scenarios": [
        {
            "scenario_id": "indeed_search_results_open_job_posting",
            "domain_id": "indeed_jobs",
            "goal_id": "open_job_posting",
            "task_id": None,
            "display_name": "Search Results -> Open Job Posting",
            "start_page_state": "search_results",
            "description": "Start from Indeed search results and open a job posting.",
            "capture_profile_override": None,
        },
        {
            "scenario_id": "indeed_company_page_open_job_posting",
            "domain_id": "indeed_jobs",
            "goal_id": "open_job_posting",
            "task_id": None,
            "display_name": "Company Page -> Open Job Posting",
            "start_page_state": "company_page",
            "description": "Start from a company page and open a job posting.",
            "capture_profile_override": None,
        },
        {
            "scenario_id": "indeed_email_alert_open_job_posting",
            "domain_id": "indeed_jobs",
            "goal_id": "open_job_posting",
            "task_id": None,
            "display_name": "Email Alert -> Open Job Posting",
            "start_page_state": "email_alert",
            "description": "Start from an email-alert landing page and open a posting.",
            "capture_profile_override": None,
        },
        {
            "scenario_id": "indeed_login_wall_log_in",
            "domain_id": "indeed_jobs",
            "goal_id": "log_in",
            "task_id": None,
            "display_name": "Login Wall -> Log In",
            "start_page_state": "login_wall",
            "description": "Start at the Indeed login wall and authenticate.",
            "capture_profile_override": None,
        },
        {
            "scenario_id": "indeed_job_detail_apply_to_job",
            "domain_id": "indeed_jobs",
            "goal_id": "apply_to_job",
            "task_id": "indeed_apply_flow",
            "display_name": "Job Detail -> Apply to Job",
            "start_page_state": "job_detail",
            "description": "Start from a job detail page and enter the apply flow.",
            "capture_profile_override": "fullpage",
        },
    ],
}


def seed_training_registry(db: Session) -> None:
    if db.scalar(select(DomainRegistry.domain_id).limit(1)):
        return

    for payload in REGISTRY_SEED["domains"]:
        db.add(DomainRegistry(status="active", **payload))
    for payload in REGISTRY_SEED["goals"]:
        db.add(GoalRegistry(status="active", **payload))
    for payload in REGISTRY_SEED["tasks"]:
        db.add(TaskRegistry(status="active", **payload))
    for payload in REGISTRY_SEED["scenarios"]:
        db.add(ScenarioRegistry(status="active", **payload))
    db.commit()


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


def _next_training_port(db: Session) -> int:
    active_ports = db.scalars(
        select(TrainingSession.chrome_debug_port).where(
            TrainingSession.chrome_debug_port.is_not(None),
            TrainingSession.status.in_(["active", "starting"]),
        )
    ).all()
    port = settings.training_chrome_port_start
    active_port_set = {value for value in active_ports if value is not None}
    while port in active_port_set:
        port += 1
    return port


def _session_action_hint(goal: GoalRegistry) -> str:
    return str((goal.action_type_hints or ["any"])[0])


def _launch_training_chrome(db: Session, session: TrainingSession) -> TrainingSession:
    if session.status == "active" and session.chrome_process_pid:
        return session

    port = _next_training_port(db)
    profile_dir = _training_profiles_root() / f"training-session-{session.id}"
    profile_dir.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            _resolve_chrome_binary(),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
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
    return session


def _stop_training_chrome(session: TrainingSession) -> None:
    if not session.chrome_process_pid:
        return
    try:
        os.kill(session.chrome_process_pid, 15)
    except ProcessLookupError:
        pass


def _session_browser_url(session: TrainingSession) -> str:
    if not session.chrome_debug_port:
        raise HTTPException(status_code=400, detail="Training session Chrome port is not configured")
    return f"http://127.0.0.1:{session.chrome_debug_port}"


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
        "rejected_candidate_ids": capture.rejected_candidate_ids or [],
        "candidate_labels": capture.candidate_labels or {},
        "approved_bbox": capture.approved_bbox,
        "browser_session_id": capture.browser_session_id,
    }


def _capture_metadata_from_artifact(
    *,
    artifact: dict,
    session: TrainingSession,
    goal: GoalRegistry,
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
        "action_type_hint": training_metadata.get("action_type_hint") or _session_action_hint(goal),
        "notes": training_metadata.get("notes") or session.notes,
        "capture_profile": training_metadata.get("capture_profile") or session.capture_profile,
        "screenshot_refs": screenshots,
    }


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        seed_training_registry(db)


@app.get("/health")
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


@app.get("/api/system/status")
def get_system_status():
    services = collect_system_services()
    return {
        "generated_at": utcnow().isoformat(),
        "overall_status": _overall_status_for_services(services),
        "services": services,
    }


@app.get("/api/training/domains", response_model=list[DomainRead])
def list_training_domains(include_inactive: bool = False, db: Session = Depends(get_db)):
    stmt = select(DomainRegistry)
    if not include_inactive:
        stmt = stmt.where(DomainRegistry.status == "active")
    return db.scalars(stmt.order_by(DomainRegistry.display_name.asc())).all()


@app.post("/api/training/domains", response_model=DomainRead)
def create_training_domain(body: DomainWrite, db: Session = Depends(get_db)):
    if db.get(DomainRegistry, body.domain_id):
        raise HTTPException(status_code=409, detail="Domain already exists")
    domain = DomainRegistry(**body.model_dump())
    db.add(domain)
    db.commit()
    db.refresh(domain)
    return domain


@app.patch("/api/training/domains/{domain_id}", response_model=DomainRead)
def update_training_domain(domain_id: str, body: DomainUpdate, db: Session = Depends(get_db)):
    domain = db.get(DomainRegistry, domain_id)
    if domain is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(domain, key, value)
    db.commit()
    db.refresh(domain)
    return domain


@app.delete("/api/training/domains/{domain_id}")
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


@app.get("/api/training/goals", response_model=list[GoalRead])
def list_training_goals(domain_id: Optional[str] = None, db: Session = Depends(get_db)):
    stmt = select(GoalRegistry).where(GoalRegistry.status == "active")
    if domain_id:
        stmt = stmt.where((GoalRegistry.domain_id == domain_id) | (GoalRegistry.domain_id.is_(None)))
    return db.scalars(stmt.order_by(GoalRegistry.display_name.asc())).all()


@app.post("/api/training/goals", response_model=GoalRead)
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


@app.patch("/api/training/goals/{goal_id}", response_model=GoalRead)
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


@app.delete("/api/training/goals/{goal_id}")
def archive_training_goal(goal_id: str, db: Session = Depends(get_db)):
    goal = db.get(GoalRegistry, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    goal.status = "archived"
    for scenario in db.scalars(select(ScenarioRegistry).where(ScenarioRegistry.goal_id == goal_id)).all():
        scenario.status = "archived"
    db.commit()
    return {"ok": True}


@app.get("/api/training/tasks", response_model=list[TaskRead])
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


@app.post("/api/training/tasks", response_model=TaskRead)
def create_training_task(body: TaskWrite, db: Session = Depends(get_db)):
    if db.get(TaskRegistry, body.task_id):
        raise HTTPException(status_code=409, detail="Task already exists")
    _validate_registry_refs(db, domain_id=body.domain_id, goal_id=body.goal_id)
    task = TaskRegistry(**body.model_dump())
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@app.patch("/api/training/tasks/{task_id}", response_model=TaskRead)
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


@app.delete("/api/training/tasks/{task_id}")
def archive_training_task(task_id: str, db: Session = Depends(get_db)):
    task = db.get(TaskRegistry, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = "archived"
    for scenario in db.scalars(select(ScenarioRegistry).where(ScenarioRegistry.task_id == task_id)).all():
        scenario.task_id = None
    db.commit()
    return {"ok": True}


@app.get("/api/training/scenarios", response_model=list[ScenarioRead])
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


@app.post("/api/training/scenarios", response_model=ScenarioRead)
def create_training_scenario(body: ScenarioWrite, db: Session = Depends(get_db)):
    if db.get(ScenarioRegistry, body.scenario_id):
        raise HTTPException(status_code=409, detail="Scenario already exists")
    _validate_registry_refs(db, domain_id=body.domain_id, goal_id=body.goal_id, task_id=body.task_id)
    scenario = ScenarioRegistry(**body.model_dump())
    db.add(scenario)
    db.commit()
    db.refresh(scenario)
    return scenario


@app.patch("/api/training/scenarios/{scenario_id}", response_model=ScenarioRead)
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


@app.delete("/api/training/scenarios/{scenario_id}")
def archive_training_scenario(scenario_id: str, db: Session = Depends(get_db)):
    scenario = db.get(ScenarioRegistry, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    scenario.status = "archived"
    db.commit()
    return {"ok": True}


@app.get("/api/training/sessions", response_model=list[TrainingSessionRead])
def list_training_sessions(db: Session = Depends(get_db)):
    stmt = select(TrainingSession).order_by(TrainingSession.created_at.desc())
    return db.scalars(stmt).all()


@app.post("/api/training/sessions", response_model=TrainingSessionRead)
def create_training_session(body: TrainingSessionCreate, db: Session = Depends(get_db)):
    domain = db.get(DomainRegistry, body.domain_id)
    scenario = db.get(ScenarioRegistry, body.scenario_id)
    if domain is None:
        raise HTTPException(status_code=404, detail="Domain not found")
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    if scenario.domain_id != body.domain_id:
        raise HTTPException(status_code=400, detail="Scenario is not allowed for the selected domain")

    session = TrainingSession(
        domain_id=body.domain_id,
        scenario_id=body.scenario_id,
        goal_id=scenario.goal_id,
        task_id=scenario.task_id,
        capture_profile=scenario.capture_profile_override or (domain.capture_defaults or {}).get("profile", "viewport"),
        notes=body.notes,
        status="draft",
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@app.post("/api/training/sessions/{session_id}/start", response_model=TrainingSessionRead)
def start_training_session(session_id: int, db: Session = Depends(get_db)):
    session = db.get(TrainingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    return _launch_training_chrome(db, session)


@app.post("/api/training/sessions/{session_id}/stop", response_model=TrainingSessionRead)
def stop_training_session(session_id: int, db: Session = Depends(get_db)):
    session = db.get(TrainingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    _stop_training_chrome(session)
    now = utcnow()
    session.status = "stopped"
    session.chrome_stopped_at = now
    session.completed_at = now
    db.commit()
    db.refresh(session)
    return session


@app.get("/api/training/sessions/{session_id}/tabs")
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


@app.get("/api/training/sessions/{session_id}/captures", response_model=list[TrainingCaptureRead])
def list_training_session_captures(session_id: int, db: Session = Depends(get_db)):
    stmt = (
        select(TrainingCapture)
        .where(TrainingCapture.training_session_id == session_id)
        .order_by(TrainingCapture.captured_at.desc())
    )
    return db.scalars(stmt).all()


@app.get("/api/runs", response_model=list[RunRead])
def list_runs(db: Session = Depends(get_db)):
    stmt = select(Run).options(selectinload(Run.steps)).order_by(Run.id.desc())
    runs = db.scalars(stmt).all()
    return runs


@app.post("/api/runs", response_model=RunCreateResponse)
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


@app.post("/api/workers/{worker_id}/heartbeat", response_model=WorkerHeartbeatResponse)
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


@app.get("/api/workers/{worker_id}/next-step", response_model=Optional[StepLeaseResponse])
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


@app.post("/api/steps/{step_id}/result")
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
    tab_id: str
    tab_url: Optional[str] = None
    scenario: str = "training_capture"


@app.get("/api/tabs")
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


@app.post("/api/capture")
async def trigger_capture(body: CaptureRequest, db: Session = Depends(get_db)):
    session = db.get(TrainingSession, body.training_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Training session not found")
    if session.status != "active":
        raise HTTPException(status_code=400, detail="Training session is not active")
    goal = db.get(GoalRegistry, session.goal_id)
    if goal is None:
        raise HTTPException(status_code=400, detail="Training session goal is missing")

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
                    **_capture_metadata_from_artifact(
                        artifact=artifact,
                        session=session,
                        goal=goal,
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
            detail=f"mcp-mock capture server not reachable at {settings.capture_server_url}",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Observation artifact endpoints
# ---------------------------------------------------------------------------

def _artifacts_dir() -> Path:
    return Path(settings.observer_artifacts_dir)


@app.get("/api/observations/screenshots/{filename}")
def get_observation_screenshot(filename: str):
    path = _artifacts_dir() / "observer-screenshots" / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(str(path))


@app.get("/api/observations/{filename}")
def get_observation(filename: str, db: Session = Depends(get_db)):
    path = _artifacts_dir() / "observer-traces" / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Observation not found")
    data = json.loads(path.read_text())
    capture = db.scalar(select(TrainingCapture).where(TrainingCapture.artifact_filename == filename))
    meta = read_meta(_artifacts_dir() / "observer-traces", filename)
    if capture is not None:
        meta["training_annotation"] = _training_annotation_from_capture(capture)
    data["meta"] = meta
    return data


@app.get("/api/observations")
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
            results.append({
                "filename": capture.artifact_filename,
                "timestamp": capture.captured_at.isoformat(),
                "scenario": data.get("metadata", {}).get("scenario"),
                "source": data.get("metadata", {}).get("source"),
                "candidate_count": capture.candidate_count,
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

    trace_path.unlink()
    return True


@app.delete("/api/observations/{filename}")
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


@app.post("/api/observations/bulk-delete")
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
    training_annotation: Optional[dict] = None


@app.patch("/api/observations/{filename}")
def update_observation_meta(filename: str, body: UpdateMetaRequest, db: Session = Depends(get_db)):
    traces_dir = _artifacts_dir() / "observer-traces"
    if not (traces_dir / filename).exists():
        raise HTTPException(status_code=404, detail="Observation not found")

    capture = db.scalar(select(TrainingCapture).where(TrainingCapture.artifact_filename == filename))
    if capture is None:
        raise HTTPException(status_code=404, detail="Training capture not found")

    meta = read_meta(traces_dir, filename)
    for key in ("group", "status", "label"):
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
        meta["training_annotation"] = merged
        db.commit()
    elif body.status in {"draft", "reviewed", "approved", "rejected", "archived"}:
        capture.review_status = body.status
        db.commit()
    write_meta(traces_dir, filename, meta)
    return {"ok": True, **meta}


class TrainRequest(BaseModel):
    rebuild_dataset: bool = True


REVIEWED_CAPTURE_STATUSES = ("reviewed", "approved")


def _reviewed_training_captures(db: Session):
    captures = db.scalars(
        select(TrainingCapture)
        .where(TrainingCapture.review_status.in_(REVIEWED_CAPTURE_STATUSES))
        .order_by(TrainingCapture.captured_at.asc())
    ).all()
    for capture in captures:
        session = db.get(TrainingSession, capture.training_session_id)
        setattr(capture, "scenario_id", session.scenario_id if session else None)
    return captures


@app.post("/api/training/build-dataset")
def build_training_dataset(db: Session = Depends(get_db)):
    captures = _reviewed_training_captures(db)
    manifest = build_grounding_dataset(_artifacts_dir(), captures=captures)
    return {"ok": True, **manifest}


@app.post("/api/training/train")
def train_grounding(body: TrainRequest, db: Session = Depends(get_db)):
    captures = _reviewed_training_captures(db)
    manifest = build_grounding_dataset(_artifacts_dir(), captures=captures) if body.rebuild_dataset else None
    result = train_grounding_model(_artifacts_dir(), dataset_manifest=manifest)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result


@app.get("/api/training/target-comparison")
def training_target_comparison(db: Session = Depends(get_db)):
    captures = _reviewed_training_captures(db)
    return compare_training_targets(_artifacts_dir(), captures=captures)
