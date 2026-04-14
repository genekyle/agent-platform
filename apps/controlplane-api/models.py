from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Float, String, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from db import Base


def utcnow():
    return datetime.now(timezone.utc)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    status: Mapped[str] = mapped_column(String(50), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    steps: Mapped[list["Step"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="Step.order_index"
    )


class Step(Base):
    __tablename__ = "steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    order_index: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(50), default="PENDING")
    payload: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    assigned_worker_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped["Run"] = relationship(back_populates="steps")


class Worker(Base):
    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    status: Mapped[str] = mapped_column(String(50), default="ONLINE")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    current_run_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    current_step_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class DomainRegistry(Base):
    __tablename__ = "domain_registry"

    domain_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    host_patterns: Mapped[list[str]] = mapped_column(JSON, default=list)
    page_states: Mapped[list[dict]] = mapped_column(JSON, default=list)
    capture_defaults: Mapped[dict] = mapped_column(JSON, default=dict)
    validation_expectations: Mapped[list[dict]] = mapped_column(JSON, default=list)
    config_version: Mapped[str] = mapped_column(String(50), default="v1")
    status: Mapped[str] = mapped_column(String(50), default="active")


class GoalRegistry(Base):
    __tablename__ = "goal_registry"

    goal_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    domain_id: Mapped[Optional[str]] = mapped_column(ForeignKey("domain_registry.domain_id"), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    action_type_hints: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(50), default="active")

    domain: Mapped[Optional["DomainRegistry"]] = relationship()


class TaskRegistry(Base):
    __tablename__ = "task_registry"

    task_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    scope_level: Mapped[str] = mapped_column(String(50), index=True)
    domain_id: Mapped[Optional[str]] = mapped_column(ForeignKey("domain_registry.domain_id"), nullable=True, index=True)
    goal_id: Mapped[Optional[str]] = mapped_column(ForeignKey("goal_registry.goal_id"), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(50), default="active")

    domain: Mapped[Optional["DomainRegistry"]] = relationship()
    goal: Mapped[Optional["GoalRegistry"]] = relationship()


class ScenarioRegistry(Base):
    __tablename__ = "scenario_registry"

    scenario_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    domain_id: Mapped[str] = mapped_column(ForeignKey("domain_registry.domain_id"), index=True)
    goal_id: Mapped[str] = mapped_column(ForeignKey("goal_registry.goal_id"), index=True)
    task_id: Mapped[Optional[str]] = mapped_column(ForeignKey("task_registry.task_id"), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    start_page_state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    capture_profile_override: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="active")

    domain: Mapped["DomainRegistry"] = relationship()
    goal: Mapped["GoalRegistry"] = relationship()
    task: Mapped[Optional["TaskRegistry"]] = relationship()


class TrainingSession(Base):
    __tablename__ = "training_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    status: Mapped[str] = mapped_column(String(50), default="draft", index=True)
    domain_id: Mapped[str] = mapped_column(ForeignKey("domain_registry.domain_id"), index=True)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenario_registry.scenario_id"), index=True)
    goal_id: Mapped[str] = mapped_column(ForeignKey("goal_registry.goal_id"), index=True)
    task_id: Mapped[Optional[str]] = mapped_column(ForeignKey("task_registry.task_id"), nullable=True, index=True)
    capture_profile: Mapped[str] = mapped_column(String(100), default="viewport")
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    browser_session_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    chrome_debug_port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    chrome_user_data_dir: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    chrome_process_pid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    chrome_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    chrome_stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    domain: Mapped["DomainRegistry"] = relationship()
    scenario: Mapped["ScenarioRegistry"] = relationship()
    goal: Mapped["GoalRegistry"] = relationship()
    task: Mapped[Optional["TaskRegistry"]] = relationship()
    captures: Mapped[list["TrainingCapture"]] = relationship(
        back_populates="training_session",
        cascade="all, delete-orphan",
        order_by="TrainingCapture.captured_at.desc()",
    )


class TrainingCapture(Base):
    __tablename__ = "training_captures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    training_session_id: Mapped[int] = mapped_column(ForeignKey("training_sessions.id"), index=True)
    artifact_filename: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    review_status: Mapped[str] = mapped_column(String(50), default="draft", index=True)
    positive_candidate_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    rejected_candidate_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    candidate_labels: Mapped[dict] = mapped_column(JSON, default=dict)
    approved_bbox: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    url: Mapped[str] = mapped_column(String, default="")
    title: Mapped[str] = mapped_column(String, default="")
    viewport_width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    viewport_height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    device_scale_factor: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    scroll_x: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    scroll_y: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tab_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    browser_session_id: Mapped[str] = mapped_column(String(120), index=True)
    domain_id: Mapped[str] = mapped_column(String(100), index=True)
    goal_id: Mapped[str] = mapped_column(String(100), index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    action_type_hint: Mapped[str] = mapped_column(String(100), default="any")
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    capture_profile: Mapped[str] = mapped_column(String(100), default="viewport")
    screenshot_refs: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    training_session: Mapped["TrainingSession"] = relationship(back_populates="captures")
