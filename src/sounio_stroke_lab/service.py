from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np

from sounio_stroke_lab.atlas import aspects_from_probability_map
from sounio_stroke_lab.benchmark import BenchmarkHarness
from sounio_stroke_lab.config import DATASET_VERSION, FAIRNESS_POLICY, PIPELINE_VERSION, get_storage_root
from sounio_stroke_lab.dataset_manifest import load_benchmark_manifest
from sounio_stroke_lab.preprocessing import load_volume_from_paths, prepare_volume
from sounio_stroke_lab.runners import build_runner
from sounio_stroke_lab.schemas import (
    AnalysisResult,
    AnalyzeStudyRequest,
    BenchmarkContext,
    BenchmarkRequest,
    BenchmarkRun,
    InputMode,
    ModelFamily,
    StudyRecord,
    StudyStatus,
)
from sounio_stroke_lab.storage import StorageManager
from sounio_stroke_lab.trainable_models import load_trained_model_artifact


class StrokeResearchService:
    def __init__(self, storage_root: Path | None = None):
        self.storage = StorageManager(storage_root or get_storage_root())
        self.storage.initialize()
        self.benchmark = BenchmarkHarness(self.storage)

    async def create_study_from_uploads(self, uploads) -> StudyRecord:
        if not uploads:
            raise ValueError("At least one file is required.")
        provisional = self.storage.create_study(files=[], input_mode=InputMode.demo, warnings=[])
        study_dir = self.storage.allocate_study_dir(provisional.study_id)
        saved_files: list[str] = []
        for index, upload in enumerate(uploads):
            original_name = Path(upload.filename or f"upload-{index}.bin").name
            filename = original_name or f"upload-{index}.bin"
            target = study_dir / filename
            target.write_bytes(await upload.read())
            saved_files.append(str(target))
        loaded = load_volume_from_paths([Path(path) for path in saved_files])
        record = provisional.model_copy(
            update={
                "files": saved_files,
                "input_mode": loaded.input_mode,
                "warnings": loaded.warnings,
            }
        )
        self.storage.save_study(record)
        return record

    def analyze_study(self, study_id: str, request: AnalyzeStudyRequest) -> AnalysisResult:
        study = self.storage.get_study(study_id)
        loaded = load_volume_from_paths([Path(path) for path in study.files])
        prepared = prepare_volume(loaded.volume, warnings=loaded.warnings)
        artifact = self._load_requested_artifact(request.model_artifact_path)
        runner = build_runner(request.model_family, model_artifact=artifact)
        output = runner.run(prepared)
        aspects_score, region_scores = aspects_from_probability_map(output.heatmap, prepared.hemisphere)
        heatmap_path = self._persist_heatmap(study_id, request.model_family, output.heatmap)
        baseline_comparison = None
        compared_against: list[str] = []
        if request.include_baseline_comparison:
            baseline_comparison = self._compare_against_baselines(
                prepared,
                output.heatmap,
                request.model_family,
                self._resolve_comparison_artifacts(request.model_artifact_path),
            )
            compared_against = sorted(baseline_comparison)
        dataset_version = DATASET_VERSION
        if artifact is not None:
            dataset_version = self._dataset_version_from_artifact(artifact)
        result = AnalysisResult(
            study_id=study_id,
            model_family=request.model_family,
            input_mode=loaded.input_mode,
            aspects_score=aspects_score,
            region_scores=region_scores,
            global_confidence=round(output.global_confidence, 4),
            heatmap_volume_ref=heatmap_path,
            warnings=study.warnings + output.notes,
            benchmark_context=BenchmarkContext(
                run_id=f"study-{study_id}-{uuid.uuid4().hex[:8]}",
                dataset_version=dataset_version,
                pipeline_version=PIPELINE_VERSION,
                language_stack=output.language_stack,
                fairness_policy=FAIRNESS_POLICY,
                compute_budget=output.compute_budget,
                compared_against=compared_against,
            ),
            baseline_comparison=baseline_comparison,
        )
        self.storage.save_analysis(result)
        self.storage.update_study_status(study_id, StudyStatus.analyzed, warnings=result.warnings)
        return result

    def get_analysis_result(self, study_id: str) -> AnalysisResult:
        return self.storage.get_analysis(study_id)

    def run_benchmark(self, request: BenchmarkRequest) -> BenchmarkRun:
        return self.benchmark.run_suite(request)

    def get_benchmark_run(self, run_id: str) -> BenchmarkRun:
        return self.storage.get_benchmark_run(run_id)

    def _persist_heatmap(self, study_id: str, model_family: ModelFamily | str, heatmap: np.ndarray) -> str:
        study_dir = self.storage.allocate_study_dir(study_id)
        model_name = model_family.value if hasattr(model_family, "value") else str(model_family)
        path = study_dir / f"{model_name}_heatmap.npy"
        np.save(path, heatmap)
        return str(path)

    def _compare_against_baselines(
        self,
        prepared,
        primary_heatmap: np.ndarray,
        model_family: ModelFamily | str,
        comparison_artifacts: dict[str, str],
    ) -> dict[str, dict[str, float]]:
        results: dict[str, dict[str, float]] = {}
        primary_score, primary_regions = aspects_from_probability_map(primary_heatmap, prepared.hemisphere)
        primary_labels = [region.affected for region in primary_regions]
        model_name = model_family.value if hasattr(model_family, "value") else str(model_family)
        for baseline in (
            ModelFamily.sounio_hypercomplex,
            ModelFamily.python_3d_conventional,
            ModelFamily.julia_equivalent,
            ModelFamily.cpp_equivalent,
        ):
            if baseline.value == model_name:
                continue
            runner = build_runner(baseline, model_artifact_path=comparison_artifacts.get(baseline.value))
            output = runner.run(prepared)
            baseline_score, baseline_regions = aspects_from_probability_map(output.heatmap, prepared.hemisphere)
            baseline_labels = [region.affected for region in baseline_regions]
            agreement = sum(
                int(left == right) for left, right in zip(primary_labels, baseline_labels, strict=True)
            ) / len(primary_labels)
            results[baseline.value] = {
                "score_gap": round(float(primary_score - baseline_score), 4),
                "region_agreement": round(float(agreement), 4),
                "mean_heatmap_gap": round(float(np.mean(np.abs(primary_heatmap - output.heatmap))), 4),
                "latency_ms": round(output.elapsed_ms, 4),
                "uses_trained_artifact": bool(comparison_artifacts.get(baseline.value)),
            }
        return results

    def _load_requested_artifact(self, model_artifact_path: str | None):
        if not model_artifact_path:
            return None
        return load_trained_model_artifact(Path(model_artifact_path).expanduser().resolve())

    def _resolve_comparison_artifacts(self, model_artifact_path: str | None) -> dict[str, str]:
        if not model_artifact_path:
            return {}
        artifact_path = Path(model_artifact_path).expanduser().resolve()
        sibling_dir = artifact_path.parent
        if not sibling_dir.exists():
            return {}
        resolved: dict[str, str] = {}
        for family in (
            ModelFamily.sounio_hypercomplex,
            ModelFamily.python_3d_conventional,
            ModelFamily.julia_equivalent,
            ModelFamily.cpp_equivalent,
        ):
            candidate = sibling_dir / f"{family.value}.json"
            if candidate.exists():
                resolved[family.value] = str(candidate)
        return resolved

    def _dataset_version_from_artifact(self, artifact) -> str:
        manifest_path = Path(artifact.dataset_manifest_path).expanduser()
        if not manifest_path.exists():
            return DATASET_VERSION
        try:
            manifest = load_benchmark_manifest(manifest_path)
        except Exception:
            return DATASET_VERSION
        return manifest.dataset_version
