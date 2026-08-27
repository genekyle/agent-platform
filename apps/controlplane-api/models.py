from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Float, String, DateTime, ForeignKey, Integer, Boolean
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


class PageStateRegistry(Base):
    """Canonical store for page states, replacing the hardcoded globals + the
    per-domain page_states JSON blob. Two organizing axes:
      - scope: global | domain | scenario  (how widely the state applies)
      - category: thematic group for UI organization (auth, navigation, ...)
    The labeler shows global + the capture's domain + the capture's scenario states,
    grouped by category. state_id is a globally-unique slug (the value stored on
    TrainingCapture.observed_page_state / post_action_state).
    """
    __tablename__ = "page_state_registry"

    state_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    scope: Mapped[str] = mapped_column(String(20), default="global", index=True)  # global|domain|goal|scenario
    domain_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("domain_registry.domain_id"), nullable=True, index=True,
    )
    goal_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("goal_registry.goal_id"), nullable=True, index=True,
    )
    scenario_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("scenario_registry.scenario_id"), nullable=True, index=True,
    )
    category: Mapped[str] = mapped_column(String(60), default="general", index=True)
    # Agent-lifecycle phase: unauthenticated | authenticated | neutral. Goal-scoped
    # states inherit it from their goal; domain-wide states (homepage, navigation)
    # declare it directly. Nullable = not yet classified.
    stage: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ActionRegistry(Base):
    """The vocabulary of actions an agent can perform at a labeled element.
    Stored in a registry (not a hardcoded list) so annotators can add new ones.
    action_id is the value stored on TrainingCapture.action_type_hint.
    value_label names the payload field for that action (e.g. type -> 'Text to Type').
    Built-ins are protected from deletion.
    """
    __tablename__ = "action_registry"

    action_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    label: Mapped[str] = mapped_column(String(120))
    value_label: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    status: Mapped[str] = mapped_column(String(50), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GoalRegistry(Base):
    __tablename__ = "goal_registry"

    goal_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    domain_id: Mapped[Optional[str]] = mapped_column(ForeignKey("domain_registry.domain_id"), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    action_type_hints: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(50), default="active")
    # Agent lifecycle phase this objective belongs to: unauthenticated | authenticated | neutral.
    # The canonical hierarchy is Domain ▸ Stage ▸ Objective(=goal) ▸ Task ▸ States.
    stage: Mapped[str] = mapped_column(String(30), default="neutral", index=True)

    # Training configuration
    # Semantic description of what completing this goal means for the agent
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # HTML/ARIA element types typically involved — feeds the grounding model as a prior
    typical_element_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Human-readable description of what a successful outcome looks like
    success_criteria: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    domain: Mapped[Optional["DomainRegistry"]] = relationship()


class TaskRegistry(Base):
    __tablename__ = "task_registry"

    task_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    scope_level: Mapped[str] = mapped_column(String(50), index=True)
    domain_id: Mapped[Optional[str]] = mapped_column(ForeignKey("domain_registry.domain_id"), nullable=True, index=True)
    goal_id: Mapped[Optional[str]] = mapped_column(ForeignKey("goal_registry.goal_id"), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(50), default="active")

    # Training configuration
    # Step-by-step description of what this task flow involves
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Rough number of interactions in this task: "1-3" | "4-10" | "10+"
    estimated_steps: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Whether this task can be run multiple times in a single training session
    is_repeatable: Mapped[bool] = mapped_column(Boolean, default=True)

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

    # Vision training fields
    # Natural-language prompt the vision model receives at inference: "click the Apply Now button"
    element_query: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Expected page state after the action completes (for transition labeling)
    expected_outcome_state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Relative training difficulty for curriculum learning: easy | medium | hard
    difficulty: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Held-out scenarios are never included in training builds — only in eval benchmarks
    is_eval_only: Mapped[bool] = mapped_column(Boolean, default=False)

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
    # Separates the catchall-training path from the workhorse product:
    #   data_collection -> run the full proposer stack incl. the vision catchall,
    #                      so every capture yields rich candidates to label.
    #   production       -> cheapest-confident-first cascade (CDP-AX -> Haiku ->
    #                      human); vision only fires as an AX-gap fallback.
    purpose: Mapped[str] = mapped_column(String(30), default="data_collection", index=True)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    browser_session_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    # When set, the session attaches to a SHARED, persistent Chrome user-data-dir
    # (persistent-profiles/<name>) that survives across sessions — so a one-time supervised
    # login stays authenticated for future runs. When None, each session gets a fresh
    # throwaway profile (the default for data-collection captures).
    persistent_profile: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    # Which configured account (accounts.py) this session runs as. The account's persistent
    # profile is what actually isolates one account's Chrome from another's; this column just
    # records the binding so the session manager can label a live session by account.
    account_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    # Human-owned / do-not-touch guard. A protected session is never reaped by the
    # persistent-profile conflict sweep, and stop/relaunch/delete refuse it without force=true.
    # This is the "don't let a new run disturb my live session" safety flag.
    protected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
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
    # AX faucet yield (v16): how many CDP-AX candidates the capture-time proposer produced,
    # recorded straight from the /capture response so the faucet's per-drive output is durable
    # and queryable WITHOUT statting the .ax.json sidecar. 0 means the sidecar is empty (browser
    # was unreachable / node-ids stale at capture time) — a capture that looks healthy but carries
    # no Select-training data. This is the number that says "did this drive actually teach us
    # anything." See docs/LEARNINGS.md ("the faucet is already open") and the emission site
    # apps/mcp/app/main_server.py (_write_ax_sidecar).
    ax_candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    review_status: Mapped[str] = mapped_column(String(50), default="draft", index=True)
    positive_candidate_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Distillation provenance (v9): how the golden label was set.
    #   'human'     — an operator confirmed/corrected in the labeler (highest trust)
    #   'auto'      — verifier-confirmed Haiku pick, conf>=AUTO threshold; train-eligible, revocable
    #   'suggested' — verifier-confirmed Haiku pick in the staged band; awaits 1-click human confirm
    # Human labels are never overwritten by the auto-promotion pass.
    label_source: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    label_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Page-state label provenance (v10): how observed_page_state was set. 'human' (labeler)
    # or 'auto' (high-confidence Haiku page-state classification). Only these two ever WRITE
    # observed_page_state; lower-confidence Haiku guesses stay read-only suggestions. Keeps
    # the L3 / transition training label trustworthy. Parallels label_source for selections.
    state_label_source: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    state_label_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Multi-tenant / cross-platform axes (v11). domain_id is the PLATFORM ("workday",
    # "greenhouse"); tenant_id is the INSTANCE ("acme", "bigco") — lets the trainer
    # stratify by tenant to MEASURE cross-tenant generalization on platforms like
    # Workday where 1000s of companies share the same UI but different tenants.
    # predecessor_capture_id makes cross-platform FLOWS explicit: an indeed posting
    # that redirects to a workday tenant via "Apply on company site" produces a
    # workday capture whose predecessor is that indeed capture — the planner sees a
    # real cross-platform edge in the state graph, not a mystery jump.
    tenant_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    predecessor_capture_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("training_captures.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
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

    # Vision training fields — populated at capture time from the session's scenario
    scenario_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    # The NL query the vision model receives: copied from scenario.element_query at capture time
    element_query: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Annotator-set: which page state is shown in this screenshot
    observed_page_state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Annotator-set: what page state the agent lands on after this interaction
    post_action_state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Annotator-created candidates that the observer did not detect (e.g. SVG/image
    # elements like the Google logo). Each entry: {candidate_id, bbox, name, role, created_at}.
    # candidate_id is prefixed "manual-..." so it never collides with observer candidate IDs.
    # Used downstream by state_transition / task_outcome models when the bbox alone isn't
    # enough — preserves element identity even without a DOM selector.
    manual_candidates: Mapped[list[dict]] = mapped_column(JSON, default=list)

    # Interaction-layer payload — for "type" actions, the literal text the agent should
    # enter at this step (e.g. "user@example.com" for an email field). NULL for click/scroll
    # actions which don't carry a text payload. Combined with action_type_hint and approved_bbox,
    # this gives a complete (state, action, target, payload) tuple per capture.
    action_text: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    training_session: Mapped["TrainingSession"] = relationship(back_populates="captures")


class ModelRegistry(Base):
    """A registered model implementation for a given training target.

    `id` is the stable composite `{target_id}__{implementation}` — same string the
    UI and HTTP endpoints address. Adding a new model = inserting a row here + a
    wrapper module dispatched in model_lib/eval.py:IMPLEMENTATIONS.
    """
    __tablename__ = "model_registry"

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(100), index=True)
    implementation: Mapped[str] = mapped_column(String(100), index=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ModelEvalRun(Base):
    """One eval run of a registered model against the eval split of reviewed captures.

    Long-running evals can take many minutes for big models on MPS. To make
    those manageable:
      - `progress` is updated after every capture so the UI can show a live
        counter and "current capture" string.
      - `predictions.jsonl` is appended to on-disk as each capture completes,
        so a mid-run crash doesn't lose work.
      - `cancel_requested` is checked between captures; a clean cancel
        flushes whatever's done and exits with status=cancelled.
      - Resume creates a new run that copies the prior run's predictions and
        continues from where it stopped.
    """
    __tablename__ = "model_eval_run"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # uuid hex
    model_id: Mapped[str] = mapped_column(ForeignKey("model_registry.id"), index=True)
    dataset_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    artifact_dir: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Live-progress fields. progress shape:
    # {completed, total, current_capture, current_step, started_at, last_update_at}
    progress: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # If this run resumes a previous one, the originating run id (for traceability).
    resumed_from: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    model: Mapped["ModelRegistry"] = relationship()


class ApplicationAnswer(Base):
    """A stored, reusable answer to a repeatable job-application question.

    Job applications (Indeed quick-apply, Workday, Greenhouse, ...) ask the same
    questions over and over — demographics (race/gender/veteran/disability), salary
    expectation, work authorization, etc. Rather than re-deriving each time, we keep a
    canonical answer per `answer_key` and a list of `question_patterns` (the many ways
    that question gets phrased) so a matcher can map an arbitrary on-screen question to
    the right stored answer. Platform-agnostic on purpose: the same salary answer serves
    Indeed and Workday. This is the data the (future) form-fill executor reads from;
    today it's operator-managed in the Indeed workspace UI.
    """
    __tablename__ = "application_answers"

    answer_key: Mapped[str] = mapped_column(String(80), primary_key=True)  # canonical slug
    display_name: Mapped[str] = mapped_column(String(200))
    # compensation | demographics | eligibility | logistics | experience | custom
    category: Mapped[str] = mapped_column(String(40), default="custom", index=True)
    value: Mapped[str] = mapped_column(String(2000), default="")
    # Example phrasings of the question, used by the matcher to recognize it on a page.
    question_patterns: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Hint for the future executor: number | text | select | radio | boolean | textarea
    input_hint: Mapped[str] = mapped_column(String(20), default="text")
    # For select/radio: the exact option label to choose (when value isn't the label).
    options: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # 'human' (operator-entered, trusted) — parallels label provenance elsewhere.
    source: Mapped[str] = mapped_column(String(20), default="human")
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ObservedJob(Base):
    """A job posting the agent has seen (Indeed/Workday/LinkedIn), deduped by identity.

    Dedup is the whole point: the same job shows up across many searches and scrolls, so
    we key by `job_id` = "{platform}:{external_id}" (Indeed's jk, Workday req id, ...) and
    bump `seen_count` + `last_seen_at` instead of inserting duplicates. The dashboard reads
    this table for "jobs found / duplicates / applied". One row per real job; provenance
    (which searches/captures surfaced it) is kept in JSON so nothing is lost.
    """
    __tablename__ = "observed_jobs"

    job_id: Mapped[str] = mapped_column(String(160), primary_key=True)  # "{platform}:{external_id}"
    platform: Mapped[str] = mapped_column(String(40), default="indeed", index=True)
    external_id: Mapped[str] = mapped_column(String(120), index=True)   # raw jk / req id
    tenant_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(400), default="")
    company: Mapped[str] = mapped_column(String(300), default="", index=True)
    location: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(String(1200), default="")
    # seen | viewed | applied | skipped | rejected
    application_status: Mapped[str] = mapped_column(String(30), default="seen", index=True)
    seen_count: Mapped[int] = mapped_column(Integer, default=1)
    # `search_queries` LIVED HERE until 2026-08-27 (SESSION 15) — a JSON list any caller could
    # assert into, beside `SearchSighting` which records the same fact with a search behind it.
    # One fact, one place (§16): the queries that surfaced a job are DERIVED by
    # `observed_jobs.queries_for`; the API payload key survives, the store does not. The 20 rows
    # whose claims nothing could adjudicate are flagged `provenance_quarantined` below — written
    # by the live pass BEFORE the column drop in migrations.py, because the claims left with it.
    capture_filenames: Mapped[list[str]] = mapped_column(JSON, default=list)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Richer signal captured by clicking INTO a posting — powers matching + resume tailoring.
    salary: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # full JD text
    # 'quick_apply' (Indeed on-site) | 'company_site' (redirects to Workday/Greenhouse/...)
    apply_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, index=True)
    # Finer cross-site routing: indeed_quick_apply | workday | greenhouse | lever | icims |
    # ... | company_site. Drives which per-platform apply recipe runs (apply is NOT siloed).
    application_platform: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    # The canonical `Job` this sighting resolves to. Nullable because a sighting exists the moment
    # a card is scraped, and resolution is a separate (cheap, later) step — never a scrape-time
    # blocker. See `job_dedup.py` and the `Job` docstring below.
    canonical_job_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    # QUARANTINED (SESSION 15): this row once carried a query claim no sighting of its own could
    # support (the 2026-08-26 audit's "unadjudicable" class — most likely written by a path that
    # recorded a query and created no link). Its query history is therefore KNOWN-INCOMPLETE: the
    # sighting-derived list is true but may be missing the search that actually surfaced it. Any
    # consumer that LEARNS a query→job association must exclude flagged rows — they must not vote.
    # Set once by the quarantine pass (2026-08-27, 20 rows in the live corpus), never auto-cleared;
    # display reads (queries_for) stay truthful and unaffected.
    provenance_quarantined: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class Search(Base):
    """One executed search — the durable identity a session's findings hang off.

    A SESSION is a browser with a signed-in account: cookies, tabs, liveness — worth keeping
    alive across many queries (operator, 2026-08-10). A SEARCH is one query actually run inside
    one: engine + query + location, with its date. Sightings and applications tie HERE, so
    "what did the 08-10 'data analytics' Boston search yield?" is a WHERE clause instead of an
    archaeology over per-row JSON lists. The blackboard's `SearchState` remains the live cursor;
    this row is the identity it writes through — created lazily the first time a page of results
    is recorded, reused while (session, engine, query, location) still match, and never a
    replacement for the session (closing a search must not close the browser)."""
    __tablename__ = "searches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    #: BrowserSession.id, kept loose (no FK) like every cross-table id in this file — a search
    #: outlives its session row's lifecycle states.
    session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    engine: Mapped[str] = mapped_column(String(40), default="indeed", index=True)
    #: WHICH PROCESS THIS IS — `query` (someone typed a search) or `feed` (the engine's own
    #: front-page suggestions). Operator, 2026-08-26: working the suggestion feed *"shouldn't
    #: require a new session since all actions are still being performed on indeed — so instead it
    #: will be a new PROCESS or WORKFLOW within a domain."* This row was already that concept: a
    #: unit of work inside a living session that sightings and applications hang off. It needed a
    #: discriminator, not a twin table — a parallel FeedRun would have split provenance in half and
    #: given `SearchSighting`, `Application.search_id` and the cockpit's list two things to mean.
    #:
    #: THE MODELLING RULING FOR THE LINKEDIN PREFERENCES LANDING (SESSION 15, 2026-08-27):
    #: 2026-08-26 measured a surface that is a FEED by provenance (nobody typed a query; it comes
    #: from stored preferences) and a SEARCH by shape (visible page numbers, has_next, ?start=
    #: paging). `kind` stays a TWO-VALUE provenance axis and does NOT grow a third value for it:
    #: paginated-vs-appending is a fact about the TRAVERSAL (recorded per state in the recipe's
    #: traversal spec), while `kind` answers only "did a person ask for this set?" — and there the
    #: answer is plainly `feed`, with `surface` naming which one (e.g. "preferences_landing").
    #: Collapsing the two axes into one enum is exactly how "paginated and unrequested are
    #: independent axes" would get re-forgotten; keeping them in their own homes is the ruling.
    kind: Mapped[str] = mapped_column(String(20), default="query", index=True)
    #: For a feed, WHICH feed — engines have more than one suggestion surface, and "the front page"
    #: is not a query we can key on. Empty for a query-kind row, whose identity is the query itself.
    surface: Mapped[str] = mapped_column(String(60), default="")
    query: Mapped[str] = mapped_column(String(300), default="", index=True)
    location: Mapped[str] = mapped_column(String(300), default="")
    radius_miles: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    #: THE FILTERS THIS SET WAS GATHERED UNDER, as the engine's own URL states them
    #: (`{"f_AL": "true", "keywords": "…"}`, JSON). Provenance travels WITH the data or it is not
    #: provenance: on 2026-08-26 an Easy-Apply filter turned on mid-sweep and 23 rows landed under a
    #: row claiming nothing about it, and there was no column in which that could have been noticed.
    #: Written once, at creation, from the live results URL — never overwritten, because a filter
    #: that changed later is a DIFFERENT set and the sweep's job is to stop, not to relabel.
    filters: Mapped[str] = mapped_column(String, default="")
    #: active | exhausted | abandoned — set by the ladder/operator; re-declaring the same query
    #: while active reuses the row rather than minting a twin.
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    pages_swept: Mapped[int] = mapped_column(Integer, default=0)
    results_seen: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SearchSighting(Base):
    """One search surfaced one sighting — the pair the JSON query-list can't answer from.

    `ObservedJob` dedupes by job identity across every search, which is right for the job and
    wrong for provenance: "what did THIS search find?" and "which searches keep surfacing this
    job?" both need the association. One row per (search, job); the page it first appeared on
    rides along for the triage view."""
    __tablename__ = "search_sightings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    search_id: Mapped[int] = mapped_column(Integer, index=True)
    job_id: Mapped[str] = mapped_column(String(160), index=True)     # ObservedJob.job_id
    page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    """One real job in the world — the canonical entity, independent of where we met it.

    `ObservedJob` is a SIGHTING: "a card with this id appeared on Indeed for query X at time T".
    That is not the same thing as a job, and conflating the two is what breaks every question the
    operator actually wants answered:

      * Indeed's `jk` rotates per search session, so one posting yields several sightings.
      * The same requisition seen on LinkedIn is a different row entirely — measured 2026-07-30:
        Wellington Management's "Financial Reporting Analyst, US Funds" was on file twice, once
        per platform, with no way to tell.
      * "Which companies respond most?" is a question about jobs and applications, and cannot be
        asked of a table whose grain is "times I scrolled past something".

    So: many `ObservedJob` → one `Job` → at most one `Application` (with its own event timeline).

    --------------------------------------------------------------------------------------
    `job_key` is a PROMISE to other domains
    --------------------------------------------------------------------------------------
    This key is what Gmail, trackers and anything else will join on, so it must never change and
    never 404. It is minted deterministically from the first sighting's id (so a re-run of the
    backfill is idempotent) and, when two jobs turn out to be one, the loser is NOT deleted — it
    keeps its row and points at the winner via `merged_into_key`. A reference saved by another
    system last month still resolves after a merge. Resolve through the tombstone, never around it.
    """
    __tablename__ = "jobs"

    job_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Set when this job was folded into another; the row survives so old references still resolve.
    merged_into_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    company: Mapped[str] = mapped_column(String(300), default="", index=True)
    # Company reduced to its identifying words (see job_dedup.normalize_company) — the grouping key
    # for dedup, stored so the DB can narrow candidates instead of loading the table.
    company_norm: Mapped[str] = mapped_column(String(300), default="", index=True)
    title: Mapped[str] = mapped_column(String(400), default="")
    location: Mapped[str] = mapped_column(String(300), default="")
    canonical_url: Mapped[str] = mapped_column(String(1200), default="")

    # Where an application would actually be filed (workday | greenhouse | icims | lever |
    # indeed_quick_apply | company_site). Distinct from the platforms we SAW it on.
    ats: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    # The ATS requisition id, once we have landed on the ATS and can read one. Measured
    # 2026-07-30: only 2 of 355 sightings carried a parseable req id, because we store the Indeed
    # url, not the url we end up on. This column is where the apply epilogue writes it back.
    requisition_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)

    salary: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # indeed_pane | ats | manual — a JD read off the ATS beats one scraped from a results pane.
    description_source: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    # Every platform this job has been sighted on, e.g. ["indeed", "linkedin"].
    source_platforms: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Two different numbers, kept apart because conflating them makes both meaningless:
    #   sighting_count — how many DISTINCT sighting rows resolve here (2 = seen on two boards)
    #   seen_count     — how many times it has been observed in total across every search
    # The second is the interesting one for triage: a posting still surfacing after six sweeps is
    # still open, which is not something the first number can tell you. Both are recomputed from
    # the sightings rather than incremented, so they are self-healing after any merge.
    sighting_count: Mapped[int] = mapped_column(Integer, default=0)
    seen_count: Mapped[int] = mapped_column(Integer, default=0)

    # new | shortlisted | applied | skipped | closed — the operator's triage state for the JOB.
    # Distinct from Application.status, which tracks what the EMPLOYER has done since.
    status: Mapped[str] = mapped_column(String(30), default="new", index=True)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class JobMatch(Base):
    """A proposed or decided "these two jobs are one" — the dedup review queue AND its audit log.

    Certain matches (same id, same requisition) are merged on sight and land here already decided,
    so there is always a record of WHY two rows became one. Uncertain matches land here `pending`
    and wait for the operator, because a wrong merge silently hides a job they wanted.

    That caution is measured, not hypothetical. Running the existing fuzzy matcher over the real
    355-row corpus on 2026-07-30 produced 31 above-threshold pairs of which only ~3 were true
    duplicates — it happily equated "Software Engineer" with "Senior Data Integration Software
    Engineer" and "Financial Analyst III" with "Financial Analyst II". `job_dedup.py` fixes those
    specific failures, but the shape of the error (a generic title is a subset of every richer one
    at the same employer) is permanent, so the weak tier stays advisory forever.

    Rejections are kept, not deleted: a pair the operator has already said "different" about must
    never be proposed again.
    """
    __tablename__ = "job_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    # `kept_key` survives the merge; `folded_key` is the one that gets a tombstone. Ordered at
    # proposal time (oldest-first) so the same pair never enqueues twice under two orderings.
    kept_key: Mapped[str] = mapped_column(String(64), index=True)
    folded_key: Mapped[str] = mapped_column(String(64), index=True)

    # exact | requisition | identical_title | fuzzy_title — descending trustworthiness.
    tier: Mapped[str] = mapped_column(String(30), index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    evidence: Mapped[list[str]] = mapped_column(JSON, default=list)

    # pending | merged | rejected
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decided_by: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # auto | human
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Application(Base):
    """My application to one job, and what has happened to it since.

    Split from the job on purpose. `Job.status` is what *I* decided (shortlisted, skipped);
    `Application.status` is what the *employer* has done (acknowledged, rejected, interviewing),
    and only the second one can answer "which companies actually respond".

    `status` is DERIVED — it is the furthest-along state implied by the events, recomputed on every
    write (see `application_events.reduce_status`). The events are the truth; this column exists so
    the job table can be filtered and sorted without replaying a timeline per row.
    """
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    job_key: Mapped[str] = mapped_column(String(64), index=True, unique=True)

    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    # Where the application was launched FROM (indeed | linkedin | direct) — the referral path,
    # which is itself a thing worth measuring: do Indeed applies get answered less than direct ones?
    via_platform: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    ats: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    # The SEARCH that led here (models.Search) — provenance to the query+date that surfaced the
    # job, kept loose like every cross-table id. Nullable: manual marks and legacy rows have none.
    search_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    # applied | acknowledged | responded | screening | interview | offer | rejected | withdrawn.
    # Derived from events; see the class docstring.
    status: Mapped[str] = mapped_column(String(30), default="applied", index=True)
    last_event_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    events: Mapped[list["ApplicationEvent"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="ApplicationEvent.occurred_at",
    )


class ApplicationEvent(Base):
    """One thing that happened to an application, and how we know.

    The operator's answer on 2026-07-30, choosing manual marking to start: *"start with the manual
    but know that this will eventually be polled from gmail, the ATS status page itself, etc."*
    That future is designed in here rather than deferred — `source` names WHO observed the event
    and `evidence` carries whatever that observer can prove it with:

        human       {}                                     — the operator ticked it in the UI
        gmail       {message_id, from_address, subject}    — a matched inbox message
        ats_portal  {url, status_text, captured_at}        — a candidate-portal status read
        agent       {run_id, capture_filename}             — the apply epilogue, at submit time

    Adding those observers later adds rows, not columns. Nothing about this table changes when
    Gmail starts writing to it — which is the entire point of putting the timeline here now.

    Events are append-only and never overwritten, so a rejection arriving after an interview
    invite leaves both on the record. `Application.status` is a projection of this list.
    """
    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), index=True)

    # When it happened in the WORLD (an email's date), which is not when we recorded it.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    # applied | confirmation | viewed | recruiter_contact | screening_invite | interview_invite |
    # assessment | rejection | offer | withdrawn | note
    kind: Mapped[str] = mapped_column(String(40), index=True)
    # human | gmail | ats_portal | agent — parallels label provenance elsewhere in the system.
    source: Mapped[str] = mapped_column(String(20), default="human", index=True)
    summary: Mapped[str] = mapped_column(String(500), default="")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    application: Mapped["Application"] = relationship(back_populates="events")


class InboxEmail(Base):
    """One inbox row the application tracker has looked at — the Gmail matcher's ledger.

    Three jobs in one table, in the same shape `JobMatch` gave the dedup queue:

      * **Idempotency.** The list reader has no message id, so `fingerprint` (sender + subject +
        received timestamp) is the identity of a mail across sweeps; a row seen once is never
        reprocessed, and re-sweeping an unchanged inbox writes nothing.
      * **The review queue.** `needs_review` rows are the matcher's honest "I can see this is
        about an application but I will not guess which/what" — surfaced with the candidates and
        the proposed kind prefilled, resolved by a human into `confirmed` or `dismissed`.
      * **Audit.** `recorded` rows point (via `event_id`) at the ApplicationEvent they wrote, so
        every gmail-sourced entry on a timeline can be traced back to the exact mail and sweep
        that produced it.

    PRIVACY: the inbox is a personal mailbox. `ignored` rows (mail that is about nothing we
    applied to) keep the fingerprint ONLY — sender, subject and snippet are stored blank, because
    remembering personal mail is capture-of-secrets, not evidence (PRINCIPLES §4).
    """
    __tablename__ = "inbox_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fingerprint: Mapped[str] = mapped_column(String(24), unique=True, index=True)

    from_address: Mapped[str] = mapped_column(String(200), default="")
    sender_name: Mapped[str] = mapped_column(String(200), default="")
    subject: Mapped[str] = mapped_column(String(300), default="")
    snippet: Mapped[str] = mapped_column(String(300), default="")
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    #: What the matcher read off the row: sender-domain ATS, proposed event kind, matched job.
    ats_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    kind: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    job_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    #: [{job_key, company, title, ats, score, reasons}] — every application that could plausibly
    #: own this mail, kept so the review screen can offer them instead of a free-text field.
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    reasons: Mapped[list] = mapped_column(JSON, default=list)

    #: recorded | needs_review | ignored | confirmed | dismissed
    status: Mapped[str] = mapped_column(String(20), index=True)
    #: The ApplicationEvent written (on `recorded` and `confirmed` rows), for the audit trail.
    event_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    decided_by: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # auto | human
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class JobDecision(Base):
    """One triage decision about one job — AND the choice set it was made in.

    Separate from `Job.status` on purpose. That column is current state: it says the job is
    `skipped` and cannot say it was skipped on page 2 of a 21-card 'data engineer' search where
    three others were picked instead. A decision is an EVENT, and the alternatives are half of it.

    **The negatives are the perishable half.** Recording only what was picked cannot teach a
    boundary — a model trained on picks alone learns "everything is worth applying to". The
    twenty-one cards on screen at the moment of choosing exist nowhere else once the page moves,
    which is why every job under review gets a row here, `picked` or `passed`, the instant the
    operator decides (operator, 2026-08-04: *"the actual decisions should be saved"*).

    **This is not a training target yet, and saying so is the point.** The reward signal for
    "was this a good choice" is a reply, a callback, an interview — sparse, weeks late, and
    confounded by resume and timing. It arrives on `ApplicationEvent`, joins here through
    `job_key`, and until there are enough of them this table is an audit log that happens to be
    shaped like training data. `decided_by` already distinguishes `operator` from `rule:` /
    `classifier:`, so the day a decider proposes picks, its agreements and its overrides are
    separable in the same table without a migration.
    """
    __tablename__ = "job_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    # The canonical key, tombstone-resolved at write time so a later merge cannot orphan the
    # decision. `job_id` is kept beside it because a sighting we could not canonicalise is still
    # a decision worth having.
    job_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    job_id: Mapped[str] = mapped_column(String(160), index=True)

    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    # picked | passed — what the decider did with THIS job at this moment.
    decision: Mapped[str] = mapped_column(String(20), index=True)
    # operator | rule:<name> | classifier:<name> — same vocabulary `choose` already enforces.
    decided_by: Mapped[str] = mapped_column(String(60), default="operator", index=True)
    # Why, in the decider's own words. Empty is honest and common; an invented reason would be
    # worse than none, so nothing fabricates this.
    reason: Mapped[str] = mapped_column(String(500), default="")

    # --- the choice set: what else was on the table when this was decided
    session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    page: Mapped[int] = mapped_column(Integer, default=1)
    # Position on the page. Recorded because position bias is real — a model that cannot see rank
    # will happily learn the search engine's ordering and call it judgement — and because it is
    # free now and unreconstructible later.
    rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    shown_count: Mapped[int] = mapped_column(Integer, default=0)
    # The search that surfaced it: provenance travels WITH the row (state is context-bound).
    query: Mapped[str] = mapped_column(String(300), default="")
    platform: Mapped[str] = mapped_column(String(40), default="", index=True)
    # The Search row itself (models.Search) — the query string above stays for display, the id is
    # the join. Nullable: decisions recorded before searches were rows have only the string.
    search_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    # A snapshot of what the decider could actually SEE at decision time — title, company,
    # location, salary as rendered on the card. The canonical Job drifts (a description gets
    # enriched from the ATS later); the decision was made on THIS, and training on the enriched
    # version would be training on evidence the decider never had.
    card: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- the ATS database ------------------------------------------------------------------------
# Added 2026-08-20 after the first pass over the transition corpus (docs/ANALYSIS_ats_corpus.md).
# Before this, ATS knowledge lived in three places that could not be joined: a hardcoded vendor
# list in `ats_registry.py`, one denormalised `Application.ats` string, and 356 orphan transition
# rows keyed by session. The vendor catalogue stays in code — it is small, reviewed, and
# hand-curated. What had nowhere to live is everything BELOW the vendor: the tenant, the measured
# characteristic, and the flow that ties a real application to the states it actually passed through.

class AtsInstance(Base):
    """One employer's tenant of one ATS vendor — `workday:cswg`, `paylocity:isabella-...`.

    The row the system was missing. `classify_ats` names the vendor; nothing named the INSTANCE,
    and because every vendor encodes tenancy differently (subdomain / path / query / none — see
    `ats_tenancy`), counting instances by hostname undercounted every path-tenanted vendor. We had
    driven Paylocity for two employers and the host axis reported one.

    `tenant_source` records WHICH rule produced the tenant, so a wrong instance is traceable to the
    extractor rather than being an unexplained duplicate.
    """
    __tablename__ = "ats_instances"

    instance_key: Mapped[str] = mapped_column(String(160), primary_key=True)  # "<ats_id>:<tenant>"
    ats_id: Mapped[str] = mapped_column(String(40), index=True)
    tenant: Mapped[str] = mapped_column(String(120), default="", index=True)
    #: subdomain | path_index | path_regex | query_param | hostname | none | fallback:hostname
    tenant_source: Mapped[str] = mapped_column(String(40), default="")
    #: The employer as we know them (from the job/application), when we know them. Nullable on
    #: purpose: a tenant slug is often all a URL gives, and inventing the company name from it
    #: would be exactly the confident-wrong this repo keeps paying for.
    employer: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    host: Mapped[str] = mapped_column(String(200), default="")
    sample_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    first_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AtsCharacteristic(Base):
    """One MEASURED fact about a vendor or an instance, with the evidence that produced it.

    Replaces the prose in `ats_registry`'s `notes` fields for anything a query or a model should be
    able to read. The notes stay — they are good prose and a human reads them — but "auth: account"
    and "the resume slot is at the account gate" are facts, and a fact in a Python string cannot be
    filtered, counted, or trained on.

    Scope is deliberately two-level: a characteristic can belong to the VENDOR (`instance_key` null
    — true of every tenant, e.g. "tenancy is path-encoded") or to one INSTANCE (e.g. "this employer
    requires a cover letter"). Confusing the two is how a single tenant's quirk becomes a rule about
    the vendor, which is the over-generalisation the operator flagged on 2026-08-19.
    """
    __tablename__ = "ats_characteristics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    ats_id: Mapped[str] = mapped_column(String(40), index=True)
    #: NULL = the characteristic is about the vendor, not one tenant.
    instance_key: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)

    #: auth | tenancy | requirement | mismatch_rate | state_seen | quirk | spine
    kind: Mapped[str] = mapped_column(String(40), index=True)
    key: Mapped[str] = mapped_column(String(120), index=True)
    value: Mapped[str] = mapped_column(String(600), default="")

    #: measured | assumed | operator. `assumed` exists so a starting guess can be stored WITHOUT
    #: being mistaken for a measurement — the registry's own standing rule.
    confidence: Mapped[str] = mapped_column(String(20), default="measured", index=True)
    #: What was actually seen. A characteristic with no evidence is an opinion.
    evidence: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    observations: Mapped[int] = mapped_column(Integer, default=1)
    session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AtsFlow(Base):
    """One attempted application through one ATS instance — the join that did not exist.

    `Application` knows a job was applied to; the transition corpus knows which screens a session
    passed through; nothing connected them, so 356 traces sat orphaned from the 22 applications
    they belonged to. This row is the connection, and it is what gives
    `apply_requirements.summarise()` the denominator it currently has to be handed by hand: without
    "we have driven Paylocity twice", a requirement seen once is indistinguishable from a rule.

    `states` is the ordered spine actually walked — the recipe as observed, not as declared.
    """
    __tablename__ = "ats_flows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    instance_key: Mapped[str] = mapped_column(String(160), index=True)
    ats_id: Mapped[str] = mapped_column(String(40), index=True)
    session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    #: Links to `Job`/`Application` when the flow belongs to a real prospect. Nullable because a
    #: backfilled flow may predate the ledger that would have recorded the job.
    job_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    #: submitted | parked:* | abandoned:* | unknown — the apply_steps terminal vocabulary.
    terminal: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    #: The ordered distinct states walked, e.g. ["paylocity_job_posting", "paylocity_application_form"].
    states: Mapped[list] = mapped_column(JSON, default=list)
    transitions: Mapped[int] = mapped_column(Integer, default=0)
    confirmed: Mapped[int] = mapped_column(Integer, default=0)
    mismatched: Mapped[int] = mapped_column(Integer, default=0)
    corrections: Mapped[int] = mapped_column(Integer, default=0)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
