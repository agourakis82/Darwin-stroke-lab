from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InputMode(str, Enum):
    dicom = "dicom"
    demo = "demo"
    research = "research"


class StudyStatus(str, Enum):
    created = "created"
    analyzed = "analyzed"
    failed = "failed"


class LanguageStack(str, Enum):
    sounio = "sounio"
    python = "python"
    julia = "julia"
    cpp = "cpp"
    multi = "multi"


class ModelFamily(str, Enum):
    sounio_hypercomplex = "sounio_hypercomplex"
    python_3d_conventional = "python_3d_conventional"
    julia_equivalent = "julia_equivalent"
    cpp_equivalent = "cpp_equivalent"
    benchmark_suite = "benchmark_suite"


class MetricInterval(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    value: float
    lower_ci: float | None = None
    upper_ci: float | None = None


class ComparativeMetric(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    delta: float
    lower_ci: float | None = None
    upper_ci: float | None = None
    p_value: float | None = None
    interpretation: str = ""


class BenchmarkComparison(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    against: ModelFamily
    metrics: dict[str, ComparativeMetric]
    notes: list[str] = Field(default_factory=list)


class ArtifactDescriptor(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    name: str
    kind: str
    path: str
    description: str = ""


class BenchmarkContext(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    run_id: str
    dataset_version: str
    pipeline_version: str
    language_stack: LanguageStack
    fairness_policy: str
    compute_budget: dict[str, Any]
    compared_against: list[str] = Field(default_factory=list)


class RegionScore(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    region: str
    probability: float
    affected: bool
    score_delta: int
    atlas_overlap: float
    summary: str


class AnalysisResult(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    study_id: str
    created_at: datetime = Field(default_factory=utc_now)
    model_family: ModelFamily
    input_mode: InputMode
    aspects_score: int
    region_scores: list[RegionScore]
    global_confidence: float
    heatmap_volume_ref: str
    warnings: list[str] = Field(default_factory=list)
    benchmark_context: BenchmarkContext | None = None
    baseline_comparison: dict[str, Any] | None = None


class AblationResult(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    ablation_name: str
    removed_component: str
    metric_delta: float
    interpretability_delta: float
    notes: str


class BenchmarkRun(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    run_id: str
    created_at: datetime = Field(default_factory=utc_now)
    dataset_version: str
    pipeline_version: str
    language_stack: LanguageStack
    model_family: ModelFamily
    compute_budget: dict[str, Any]
    metrics: dict[str, Any]
    comparisons: dict[str, BenchmarkComparison] = Field(default_factory=dict)
    stratified_metrics: dict[str, Any] = Field(default_factory=dict)
    leaderboard: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactDescriptor] = Field(default_factory=list)
    fairness_checks: list[str] = Field(default_factory=list)
    ablations: list[AblationResult] = Field(default_factory=list)


class BenchmarkCaseManifest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    case_id: str
    split: str
    volume_path: str
    lesion_mask_path: str
    hemisphere: str | None = None
    aspects_score: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BenchmarkDatasetManifest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    dataset_name: str
    dataset_version: str
    split_policy: str
    source: str = ""
    cases: list[BenchmarkCaseManifest]


class TrainedModelArtifact(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    artifact_version: str = "regional-logistic-v1"
    model_family: ModelFamily
    feature_names: list[str]
    weights: list[list[float]]
    bias: list[float]
    feature_mean: list[float]
    feature_std: list[float]
    trained_on_split: str
    dataset_manifest_path: str
    removed_component: str | None = None
    notes: list[str] = Field(default_factory=list)


class StudyRecord(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    study_id: str
    created_at: datetime = Field(default_factory=utc_now)
    input_mode: InputMode
    status: StudyStatus
    files: list[str]
    warnings: list[str] = Field(default_factory=list)


class AnalyzeStudyRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    model_family: ModelFamily = ModelFamily.sounio_hypercomplex
    include_baseline_comparison: bool = True
    model_artifact_path: str | None = None


class BenchmarkRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    dataset_manifest_path: str
    external_test_manifest_path: str | None = None
    train_split: str = "train"
    test_split: str = "test"
    seed: int = 13


class WorkflowKind(str, Enum):
    generic_agent_task = "generic_agent_task"


class RunStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class StepStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class ExecutorKind(str, Enum):
    workspace = "workspace"
    agent = "agent"
    system = "system"


class RunSubmitRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    workspace_id: str | None = None
    user_id: str = "workspace-user"
    workflow_kind: WorkflowKind = WorkflowKind.generic_agent_task
    workspace_path: str = "/workspace"
    repo_path: str = "/workspace/src"
    task_name: str = "inventory-workspace"
    parameters: dict[str, Any] = Field(default_factory=dict)


class RunRecord(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    run_id: str
    workspace_id: str
    user_id: str
    workflow_kind: WorkflowKind
    status: RunStatus
    submitted_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    current_step_seq: int = 0
    temporal_workflow_id: str
    event_stream: str
    run_root_uri: str
    workspace_uri: str
    repo_path: str
    task_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    resume_token: str
    failure_reason: str | None = None


class StepRecord(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    step_id: str
    run_id: str
    seq: int
    name: str
    executor_kind: ExecutorKind
    status: StepStatus
    attempt: int = 0
    started_at: datetime | None = None
    ended_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    checkpoint_uri: str | None = None
    stdout_artifact_id: str | None = None
    stderr_artifact_id: str | None = None
    error_code: str | None = None
    resume_hint: str | None = None


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    artifact_id: str
    run_id: str
    step_id: str | None = None
    kind: str
    uri: str
    content_type: str
    size_bytes: int
    sha256: str
    created_at: datetime = Field(default_factory=utc_now)
    is_checkpoint: bool = False
    preview_text: str | None = None


class RunEvent(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    event_id: str
    run_id: str
    seq: int
    created_at: datetime = Field(default_factory=utc_now)
    step_id: str | None = None
    event_type: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ResumeSummary(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    run_id: str
    status: RunStatus
    current_step: StepRecord | None = None
    last_completed_step: StepRecord | None = None
    last_heartbeat_at: datetime | None = None
    workspace_uri: str
    recent_artifacts: list[ArtifactRecord] = Field(default_factory=list)
    recent_events: list[RunEvent] = Field(default_factory=list)
    resume_instructions: list[str] = Field(default_factory=list)
    operator_note: str | None = None
