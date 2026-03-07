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
