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


class RecordOwnerType(str, Enum):
    study = "study"
    benchmark_run = "benchmark_run"
    agent_run = "agent_run"
    research_brief = "research_brief"
    campaign = "campaign"
    portfolio = "portfolio"
    program = "program"


class JobKind(str, Enum):
    benchmark = "benchmark"
    agent_run = "agent_run"
    study_analysis = "study_analysis"


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ArtifactRef(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    artifact_id: str
    created_at: datetime = Field(default_factory=utc_now)
    owner_type: RecordOwnerType
    owner_id: str
    name: str
    kind: str
    path: str = ""
    uri: str | None = None
    description: str = ""
    content_type: str = ""
    bytes: int | None = None


class JobRecord(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    job_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    owner_type: RecordOwnerType
    owner_id: str
    kind: JobKind
    status: JobStatus
    queue_name: str = "local-inline"
    executor: str = "in-process"
    request_payload: dict[str, Any] = Field(default_factory=dict)
    result_payload: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class WorkerHeartbeat(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    worker_id: str
    worker_kind: str
    backend: str
    executor: str
    hostname: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    notes: str = ""


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
    job_id: str | None = None
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


class CampaignKind(str, Enum):
    benchmark = "benchmark"


class CampaignStatus(str, Enum):
    created = "created"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ProgramStatus(str, Enum):
    created = "created"
    active = "active"
    completed = "completed"
    blocked = "blocked"
    archived = "archived"


class CampaignBenchmarkSpec(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    label: str = ""
    request: BenchmarkRequest


class CampaignEntry(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    entry_id: str
    label: str
    request: BenchmarkRequest
    job_id: str | None = None
    status: JobStatus = JobStatus.queued
    benchmark_run_id: str | None = None
    error: str = ""


class CampaignRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    name: str
    objective: str = ""
    benchmark_specs: list[CampaignBenchmarkSpec]
    notes: str = ""
    program_id: str | None = None


class CampaignFollowUpProposal(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    proposal_id: str
    title: str
    rationale: str
    benchmark_specs: list[CampaignBenchmarkSpec]


class CampaignFollowUpLaunchRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    proposal_id: str | None = None
    name: str | None = None
    notes: str = ""


class ExperimentSuccessCriterion(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    metric: str
    comparator: str
    target: float | str
    rationale: str = ""


class CampaignExperimentPlan(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    plan_id: str
    title: str
    hypothesis: str
    target_cohort: str
    rationale: str = ""
    required_baselines: list[ModelFamily] = Field(default_factory=list)
    success_criteria: list[ExperimentSuccessCriterion] = Field(default_factory=list)
    benchmark_specs: list[CampaignBenchmarkSpec] = Field(default_factory=list)
    recommended_agent_request: dict[str, Any] = Field(default_factory=dict)


class CampaignExperimentPlanReport(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    plan_id: str
    title: str
    acceptance_status: str
    launch_ready: bool = False
    target_cohort: str
    current_best_run_id: str | None = None
    current_leading_model: str | None = None
    baseline_control: str = ""
    acceptance_criteria: list[ExperimentSuccessCriterion] = Field(default_factory=list)
    acceptance_notes: list[str] = Field(default_factory=list)


class PortfolioCampaignSummary(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    campaign_id: str
    name: str
    status: CampaignStatus
    total_runs: int = 0
    completed_runs: int = 0
    failed_runs: int = 0
    best_run_id: str | None = None
    leading_model: str | None = None
    sounio_isles_dice: float = 0.0
    sounio_auc: float = 0.0
    sounio_aspects_mae: float = 999.0
    ready_plan_count: int = 0
    watch_plan_count: int = 0
    blocked_plan_count: int = 0
    top_plan_id: str | None = None


class PortfolioReport(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    portfolio_id: str
    generated_at: datetime = Field(default_factory=utc_now)
    total_campaigns: int = 0
    completed_campaigns: int = 0
    running_campaigns: int = 0
    failed_campaigns: int = 0
    leading_campaign_id: str | None = None
    campaigns: list[PortfolioCampaignSummary] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class PortfolioProgramSummary(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    program_id: str
    name: str
    status: ProgramStatus
    total_campaigns: int = 0
    completed_campaigns: int = 0
    running_campaigns: int = 0
    failed_campaigns: int = 0
    leading_campaign_id: str | None = None
    leading_model: str | None = None
    sounio_isles_dice: float = 0.0
    sounio_auc: float = 0.0
    sounio_aspects_mae: float = 999.0
    ready_plan_count: int = 0
    watch_plan_count: int = 0
    blocked_plan_count: int = 0


class ProgramPortfolioReport(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    portfolio_id: str
    generated_at: datetime = Field(default_factory=utc_now)
    total_programs: int = 0
    active_programs: int = 0
    completed_programs: int = 0
    blocked_programs: int = 0
    leading_program_id: str | None = None
    programs: list[PortfolioProgramSummary] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class CampaignExperimentPlanLaunchRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    name: str | None = None
    notes: str = ""


class CampaignExperimentPlanAgentLaunchRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    objective: str | None = None
    notes: str = ""


class CampaignRecord(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    campaign_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    name: str
    objective: str = ""
    kind: CampaignKind = CampaignKind.benchmark
    program_id: str | None = None
    status: CampaignStatus = CampaignStatus.created
    benchmark_specs: list[CampaignEntry] = Field(default_factory=list)
    notes: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)


class ProgramRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    name: str
    objective: str = ""
    hypothesis: str = ""
    notes: str = ""
    campaign_ids: list[str] = Field(default_factory=list)


class ProgramCampaignAttachRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    campaign_id: str


class ProgramRecord(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    program_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    name: str
    objective: str = ""
    hypothesis: str = ""
    notes: str = ""
    status: ProgramStatus = ProgramStatus.created
    campaign_ids: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class AgentSurface(str, Enum):
    research = "research"
    clinical = "clinical"
    dual = "dual"


class AgentRunStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class AgentStepKind(str, Enum):
    handoff = "handoff"
    tool_call = "tool_call"
    guardrail = "guardrail"
    artifact_write = "artifact_write"
    review = "review"


class AgentStepStatus(str, Enum):
    started = "started"
    completed = "completed"
    failed = "failed"
    blocked = "blocked"
    skipped = "skipped"


class MCPTransport(str, Enum):
    stdio = "stdio"
    streamable_http = "streamable_http"


class ToolTrustLevel(str, Enum):
    local = "local"
    public = "public"


class PrivacyMode(str, Enum):
    local_deidentified = "local_deidentified"


class AutonomyMode(str, Enum):
    autonomous_lab = "autonomous_lab"


class MCPServerHealthStatus(str, Enum):
    healthy = "healthy"
    degraded = "degraded"
    unavailable = "unavailable"


class SafetyPolicy(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    privacy_mode: PrivacyMode = PrivacyMode.local_deidentified
    autonomy_mode: AutonomyMode = AutonomyMode.autonomous_lab
    remote_tool_policy: str = "public_only_deidentified"
    write_scope: list[str] = Field(default_factory=list)
    public_tool_allowlist: list[str] = Field(default_factory=list)
    local_tool_allowlist: list[str] = Field(default_factory=list)


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    objective: str
    surface: AgentSurface = AgentSurface.dual
    dataset_manifest_path: str | None = None
    external_test_manifest_path: str | None = None
    train_split: str = "train"
    test_split: str = "test"
    seed: int = 13
    study_id: str | None = None
    study_file_paths: list[str] = Field(default_factory=list)
    include_baseline_comparison: bool = True
    model_family: ModelFamily = ModelFamily.sounio_hypercomplex
    research_question: str | None = None
    notes: str = ""


class AgentStep(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    step_id: str
    run_id: str
    created_at: datetime = Field(default_factory=utc_now)
    agent_name: str
    kind: AgentStepKind
    status: AgentStepStatus
    summary: str
    server_name: str | None = None
    tool_name: str | None = None
    artifact_path: str | None = None
    duration_ms: float | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ToolAudit(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    audit_id: str
    run_id: str
    created_at: datetime = Field(default_factory=utc_now)
    agent_name: str
    server_name: str
    tool_name: str
    trust_level: ToolTrustLevel
    input_preview: str
    sanitized_input_preview: str
    result_preview: str = ""
    blocked_reason: str = ""


class TraceEvent(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    event_id: str
    run_id: str
    trace_id: str
    created_at: datetime = Field(default_factory=utc_now)
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentRun(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    run_id: str
    job_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    objective: str
    surface: AgentSurface
    status: AgentRunStatus
    root_agent: str
    trace_id: str
    session_id: str
    safety_policy: SafetyPolicy
    request_payload: dict[str, Any] = Field(default_factory=dict)
    final_output: str = ""
    error: str = ""
    dataset_manifest_path: str | None = None
    external_test_manifest_path: str | None = None
    benchmark_run_id: str | None = None
    study_id: str | None = None
    research_brief_id: str | None = None
    artifact_paths: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MCPServerDescriptor(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    name: str
    transport: MCPTransport
    trust_level: ToolTrustLevel
    description: str
    allowed_tools: list[str] = Field(default_factory=list)
    command: list[str] = Field(default_factory=list)
    health_status: MCPServerHealthStatus = MCPServerHealthStatus.healthy
    last_seen: datetime = Field(default_factory=utc_now)
    notes: list[str] = Field(default_factory=list)


class MCPServerHealth(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    name: str
    health_status: MCPServerHealthStatus
    checked_at: datetime = Field(default_factory=utc_now)
    available_tools: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ResearchBriefRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    question: str
    benchmark_run_id: str | None = None
    campaign_id: str | None = None
    dataset_manifest_path: str | None = None
    external_test_manifest_path: str | None = None
    study_id: str | None = None


class ResearchBrief(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    brief_id: str
    created_at: datetime = Field(default_factory=utc_now)
    question: str
    inclusion_criteria: list[str] = Field(default_factory=list)
    exclusion_criteria: list[str] = Field(default_factory=list)
    evidence_table_path: str
    claim_summary: str
    protocol_recommendations: list[str] = Field(default_factory=list)
    supporting_run_id: str | None = None
    supporting_campaign_id: str | None = None
    dataset_manifest_path: str | None = None
    source_links: list[str] = Field(default_factory=list)
