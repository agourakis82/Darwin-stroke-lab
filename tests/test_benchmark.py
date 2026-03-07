from pathlib import Path

import pytest

from sounio_stroke_lab.benchmark import BenchmarkHarness, PipelineContractError
from sounio_stroke_lab.runners import SounioHypercomplexRunner
from sounio_stroke_lab.schemas import BenchmarkRequest, ModelFamily
from sounio_stroke_lab.storage import StorageManager
from tests.support import create_benchmark_manifest, create_external_benchmark_manifest


class BrokenRunner(SounioHypercomplexRunner):
    pipeline_version = "broken-v0"


def test_pipeline_contract_rejects_mismatched_versions(tmp_path: Path):
    storage = StorageManager(tmp_path / "data")
    storage.initialize()
    harness = BenchmarkHarness(storage)

    with pytest.raises(PipelineContractError):
        harness.validate_pipeline_contract(
            {
                "good": SounioHypercomplexRunner(),
                "bad": BrokenRunner(),
            }
        )


def test_benchmark_suite_generates_comparison_and_artifacts(tmp_path: Path):
    storage = StorageManager(tmp_path / "data")
    storage.initialize()
    harness = BenchmarkHarness(storage)
    manifest_path = create_benchmark_manifest(tmp_path)

    run = harness.run_suite(BenchmarkRequest(dataset_manifest_path=str(manifest_path), seed=21))

    assert run.model_family == ModelFamily.benchmark_suite
    assert ModelFamily.sounio_hypercomplex.value in run.metrics
    assert "isles_dice" in run.metrics[ModelFamily.sounio_hypercomplex.value]
    assert len(run.ablations) == 3
    assert any(item.kind == "markdown" for item in run.artifacts)
    assert any(item.kind == "trained-model" for item in run.artifacts)
    assert any(item.name == "pairwise_comparisons" for item in run.artifacts)
    assert any(item.name == "benchmark_summary" for item in run.artifacts)
    assert any(item.name == "calibration_curves" for item in run.artifacts)
    assert any(item.name == "effect_sizes" for item in run.artifacts)
    assert any(item.name == "stratified_metrics" for item in run.artifacts)
    assert any(item.name == "leaderboard" for item in run.artifacts)
    assert any(item.name == "training_cohort_summary" for item in run.artifacts)
    assert any(item.name == "test_cohort_summary" for item in run.artifacts)
    assert any(item.name == "cohort_shift" for item in run.artifacts)
    assert any(item.name == "failure_analysis" for item in run.artifacts)
    assert any(item.name == "claim_checklist" for item in run.artifacts)
    assert any(item.name == "tripod_ai_checklist" for item in run.artifacts)
    assert any(item.name == "manuscript_draft" for item in run.artifacts)
    assert any(item.name == "reproducibility_snapshot" for item in run.artifacts)
    assert any(item.name == "case_level_predictions" for item in run.artifacts)
    assert all(Path(item.path).exists() for item in run.artifacts)
    assert ModelFamily.python_3d_conventional.value in run.comparisons
    assert "dice_gain" in run.comparisons[ModelFamily.python_3d_conventional.value].metrics
    assert "lesion_present" in run.stratified_metrics[ModelFamily.sounio_hypercomplex.value]
    assert run.leaderboard["overall_rank"][0]["model_name"]
    assert "Training split: train" in run.fairness_checks
    assert "Test split: test" in run.fairness_checks
    assert run.compute_budget["train_cases"] == 8
    assert run.compute_budget["test_cases"] == 4


def test_benchmark_suite_supports_external_validation(tmp_path: Path):
    storage = StorageManager(tmp_path / "data")
    storage.initialize()
    harness = BenchmarkHarness(storage)
    train_manifest_path = create_benchmark_manifest(tmp_path)
    external_manifest_path = create_external_benchmark_manifest(tmp_path)

    run = harness.run_suite(
        BenchmarkRequest(
            dataset_manifest_path=str(train_manifest_path),
            external_test_manifest_path=str(external_manifest_path),
            seed=17,
        )
    )

    assert run.compute_budget["external_validation"] is True
    assert run.dataset_version == "fixture-v1__to__fixture-external-v1"
    assert any("external validation" in item.lower() for item in run.fairness_checks)
    assert any(item.name == "research_protocol" for item in run.artifacts)
