from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class StepRead(BaseModel):
    id: int
    order_index: int
    type: str
    status: str
    payload: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    assigned_worker_id: Optional[str] = None
    lease_expires_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class RunRead(BaseModel):
    id: int
    status: str
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    steps: list[StepRead] = []

    model_config = {"from_attributes": True}


class RunCreateResponse(BaseModel):
    id: int
    status: str

    model_config = {"from_attributes": True}


class WorkerHeartbeatIn(BaseModel):
    status: str = "ONLINE"


class WorkerHeartbeatResponse(BaseModel):
    id: str
    status: str
    last_seen_at: datetime

    model_config = {"from_attributes": True}


class StepLeaseResponse(BaseModel):
    id: int
    run_id: int
    order_index: int
    type: str
    status: str
    payload: Optional[str] = None


class StepResultIn(BaseModel):
    status: str
    result_payload: Optional[str] = None


class DomainRead(BaseModel):
    domain_id: str
    display_name: str
    host_patterns: list[str]
    page_states: list[dict]
    capture_defaults: dict
    validation_expectations: list[dict]
    config_version: str
    status: str

    model_config = {"from_attributes": True}


class DomainWrite(BaseModel):
    domain_id: str
    display_name: str
    host_patterns: list[str] = Field(default_factory=list)
    page_states: list[dict] = Field(default_factory=list)
    capture_defaults: dict = Field(default_factory=dict)
    validation_expectations: list[dict] = Field(default_factory=list)
    config_version: str = "v1"
    status: str = "active"


class DomainUpdate(BaseModel):
    display_name: Optional[str] = None
    host_patterns: Optional[list[str]] = None
    page_states: Optional[list[dict]] = None
    capture_defaults: Optional[dict] = None
    validation_expectations: Optional[list[dict]] = None
    config_version: Optional[str] = None
    status: Optional[str] = None


class GoalRead(BaseModel):
    goal_id: str
    domain_id: Optional[str] = None
    display_name: str
    action_type_hints: list[str]
    status: str
    stage: str = "neutral"
    description: Optional[str] = None
    typical_element_types: list[str] = Field(default_factory=list)
    success_criteria: Optional[str] = None

    model_config = {"from_attributes": True}


class GoalWrite(BaseModel):
    goal_id: str
    domain_id: Optional[str] = None
    display_name: str
    action_type_hints: list[str] = Field(default_factory=list)
    status: str = "active"
    stage: str = "neutral"
    description: Optional[str] = None
    typical_element_types: list[str] = Field(default_factory=list)
    success_criteria: Optional[str] = None


class GoalUpdate(BaseModel):
    domain_id: Optional[str] = None
    display_name: Optional[str] = None
    action_type_hints: Optional[list[str]] = None
    status: Optional[str] = None
    stage: Optional[str] = None
    description: Optional[str] = None
    typical_element_types: Optional[list[str]] = None
    success_criteria: Optional[str] = None


class TaskRead(BaseModel):
    task_id: str
    scope_level: str
    domain_id: Optional[str] = None
    goal_id: Optional[str] = None
    display_name: str
    status: str
    description: Optional[str] = None
    estimated_steps: Optional[str] = None
    is_repeatable: bool = True

    model_config = {"from_attributes": True}


class TaskWrite(BaseModel):
    task_id: str
    scope_level: str = "domain"
    domain_id: Optional[str] = None
    goal_id: Optional[str] = None
    display_name: str
    status: str = "active"
    description: Optional[str] = None
    estimated_steps: Optional[str] = None
    is_repeatable: bool = True


class TaskUpdate(BaseModel):
    scope_level: Optional[str] = None
    domain_id: Optional[str] = None
    goal_id: Optional[str] = None
    display_name: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None
    estimated_steps: Optional[str] = None
    is_repeatable: Optional[bool] = None


class ScenarioRead(BaseModel):
    scenario_id: str
    domain_id: str
    goal_id: str
    task_id: Optional[str] = None
    display_name: str
    start_page_state: Optional[str] = None
    description: Optional[str] = None
    capture_profile_override: Optional[str] = None
    status: str
    # Vision training fields
    element_query: Optional[str] = None
    expected_outcome_state: Optional[str] = None
    difficulty: Optional[str] = None
    is_eval_only: bool = False

    model_config = {"from_attributes": True}


class ScenarioWrite(BaseModel):
    scenario_id: str
    domain_id: str
    goal_id: str
    task_id: Optional[str] = None
    display_name: str
    start_page_state: Optional[str] = None
    description: Optional[str] = None
    capture_profile_override: Optional[str] = None
    status: str = "active"
    # Vision training fields
    element_query: Optional[str] = None
    expected_outcome_state: Optional[str] = None
    difficulty: Optional[str] = None
    is_eval_only: bool = False


class ScenarioUpdate(BaseModel):
    domain_id: Optional[str] = None
    goal_id: Optional[str] = None
    task_id: Optional[str] = None
    display_name: Optional[str] = None
    start_page_state: Optional[str] = None
    description: Optional[str] = None
    capture_profile_override: Optional[str] = None
    status: Optional[str] = None
    # Vision training fields
    element_query: Optional[str] = None
    expected_outcome_state: Optional[str] = None
    difficulty: Optional[str] = None
    is_eval_only: Optional[bool] = None


class TrainingSessionCreate(BaseModel):
    domain_id: str
    scenario_id: str
    notes: Optional[str] = None


class TrainingSessionRead(BaseModel):
    id: int
    status: str
    domain_id: str
    scenario_id: str
    goal_id: str
    task_id: Optional[str] = None
    capture_profile: str
    notes: Optional[str] = None
    browser_session_id: Optional[str] = None
    chrome_debug_port: Optional[int] = None
    chrome_user_data_dir: Optional[str] = None
    chrome_process_pid: Optional[int] = None
    chrome_started_at: Optional[datetime] = None
    chrome_stopped_at: Optional[datetime] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TrainingCaptureRead(BaseModel):
    id: int
    training_session_id: int
    artifact_filename: str
    candidate_count: int
    review_status: str
    positive_candidate_id: Optional[str] = None
    rejected_candidate_ids: list[str]
    candidate_labels: dict
    approved_bbox: Optional[dict] = None
    captured_at: datetime
    url: str
    title: str
    viewport_width: Optional[int] = None
    viewport_height: Optional[int] = None
    device_scale_factor: Optional[float] = None
    scroll_x: Optional[float] = None
    scroll_y: Optional[float] = None
    tab_id: Optional[str] = None
    browser_session_id: str
    domain_id: str
    goal_id: str
    task_id: Optional[str] = None
    action_type_hint: str
    notes: Optional[str] = None
    capture_profile: str
    screenshot_refs: list[dict]
    created_at: datetime
    # Vision training fields
    scenario_id: Optional[str] = None
    element_query: Optional[str] = None
    action_text: Optional[str] = None
    observed_page_state: Optional[str] = None
    post_action_state: Optional[str] = None

    model_config = {"from_attributes": True}


class CaptureAnnotationPatch(BaseModel):
    """Fields an annotator can set during capture review."""
    review_status: Optional[str] = None
    positive_candidate_id: Optional[str] = None
    rejected_candidate_ids: Optional[list[str]] = None
    candidate_labels: Optional[dict] = None
    approved_bbox: Optional[dict] = None
    notes: Optional[str] = None
    # Vision annotation fields
    observed_page_state: Optional[str] = None   # which page state is shown in this screenshot
    post_action_state: Optional[str] = None     # what state results from this interaction


# ===== Models registry / eval =====

class ModelEvalRunSummary(BaseModel):
    """Compact eval-run summary embedded under a ModelRead row."""
    id: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    record_count: int
    mean_bbox_iou: Optional[float] = None
    iou_at_50_accuracy: Optional[float] = None
    center_in_target_accuracy: Optional[float] = None


class ModelRead(BaseModel):
    id: str
    target_id: str
    implementation: str
    model_name: Optional[str] = None
    config: Optional[dict] = None
    created_at: datetime
    archived_at: Optional[datetime] = None
    last_eval: Optional[ModelEvalRunSummary] = None

    model_config = {"from_attributes": True}


class ModelEvalRunRead(BaseModel):
    id: str
    model_id: str
    dataset_id: Optional[str] = None
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    record_count: int
    metrics: Optional[dict] = None
    artifact_dir: Optional[str] = None
    error: Optional[str] = None
    progress: Optional[dict] = None
    cancel_requested: bool = False
    resumed_from: Optional[str] = None

    model_config = {"from_attributes": True}


class ModelEvalRunDetail(ModelEvalRunRead):
    """Eval run with a sample of predictions inlined for the run-detail view."""
    predictions_sample: list[dict] = []
