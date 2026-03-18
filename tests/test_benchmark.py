from pathlib import Path
import csv
import json

import numpy as np
import pytest

from sounio_stroke_lab.benchmark import BenchmarkHarness, PipelineContractError
from sounio_stroke_lab.dataset_manifest import load_manifest_cases
from sounio_stroke_lab.runners import SounioHypercomplexRunner
from sounio_stroke_lab.schemas import BenchmarkRequest, ModelFamily, TrainedModelArtifact
from sounio_stroke_lab.storage import StorageManager
from sounio_stroke_lab.trainable_models import prepare_training_rows
from sounio_stroke_lab.undersegmentation_gate import (
    SOUNIO_UNDERSEGMENTATION_GATE_ACTIVATION_THRESHOLD,
    SOUNIO_UNDERSEGMENTATION_GATE_FEATURE_NAMES,
    SOUNIO_UNDERSEGMENTATION_GATE_GAIN_MAX,
    SOUNIO_UNDERSEGMENTATION_GATE_GAIN_MIN,
    apply_undersegmentation_gate,
)
from tests.support import (
    create_benchmark_manifest,
    create_benchmark_manifest_with_val,
    create_external_benchmark_manifest,
    create_positive_only_segmentation_manifest,
    create_segmentation_only_manifest,
)


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
    assert run.compute_budget["target_shape"] == [32, 64, 64]
    assert run.compute_budget["segmentation_threshold"] == 0.5
    assert run.compute_budget["segmentation_threshold_policy"] == "default-v1"
    assert run.compute_budget["training_weighting"] == "lesion-burden-aware-v1"
    assert run.compute_budget["burden_prior_gain_strength_policy"] == "fixed-v1"
    assert run.compute_budget["burden_prior_gain_calibration_cases"] == 0
    assert run.compute_budget["sounio_undersegmentation_gate_policy"] == "no-disjoint-val-v1"
    assert run.compute_budget["sounio_undersegmentation_gate_calibration_cases"] == 0
    assert run.compute_budget["sounio_undersegmentation_gate_positive_cases"] == 0
    assert run.compute_budget["sounio_undersegmentation_gate_active"] is False
    assert any("lesion-burden-aware" in item for item in run.fairness_checks)
    assert any("Segmentation threshold: 0.50" in item for item in run.fairness_checks)
    assert any("Segmentation threshold policy: default-v1" in item for item in run.fairness_checks)
    assert any("undersegmentation gate stayed no-op" in item.lower() for item in run.fairness_checks)


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


def test_sounio_artifact_heatmap_stays_calibrated_on_fixture_failures(tmp_path: Path):
    storage = StorageManager(tmp_path / "data")
    storage.initialize()
    harness = BenchmarkHarness(storage)
    manifest_path = create_benchmark_manifest(tmp_path)

    run = harness.run_suite(BenchmarkRequest(dataset_manifest_path=str(manifest_path), seed=44))

    case_csv = next(item.path for item in run.artifacts if item.name == "case_level_predictions")
    rows = list(csv.DictReader(Path(case_csv).open(newline="", encoding="utf-8")))

    def sounio_row(case_id: str) -> dict[str, str]:
        return next(
            row
            for row in rows
            if row["case_id"] == case_id and row["model_name"] == ModelFamily.sounio_hypercomplex.value
        )

    case_010 = sounio_row("case-010")
    assert int(case_010["predicted_aspects"]) == 4
    assert float(case_010["isles_dice"]) >= 0.90
    assert float(case_010["isles_absolute_volume_difference_ml"]) <= 520.0

    case_011 = sounio_row("case-011")
    assert int(case_011["predicted_aspects"]) == 6
    assert float(case_011["isles_dice"]) >= 0.70
    assert float(case_011["isles_absolute_volume_difference_ml"]) <= 550.0


def test_segmentation_only_manifest_marks_aspects_metrics_unsupported(tmp_path: Path):
    storage = StorageManager(tmp_path / "data")
    storage.initialize()
    harness = BenchmarkHarness(storage)
    manifest_path = create_segmentation_only_manifest(tmp_path)

    run = harness.run_suite(BenchmarkRequest(dataset_manifest_path=str(manifest_path), seed=13))

    sounio_metrics = run.metrics[ModelFamily.sounio_hypercomplex.value]
    assert sounio_metrics["aspects_mae"]["value"] is None
    assert sounio_metrics["region_sensitivity"]["value"] is None
    assert sounio_metrics["region_calibration_error"]["value"] is None
    assert sounio_metrics["auc"]["value"] is not None
    assert run.evaluation_scope["test"]["reference_scope_counts"]["segmentation_only"] == 4
    assert any("segmentation-first" in item.lower() for item in run.fairness_checks)

    case_csv = next(item.path for item in run.artifacts if item.name == "case_level_predictions")
    rows = list(csv.DictReader(Path(case_csv).open(newline="", encoding="utf-8")))
    sounio_row = next(
        row for row in rows if row["case_id"] == "case-010" and row["model_name"] == ModelFamily.sounio_hypercomplex.value
    )
    assert sounio_row["reference_scope"] == "segmentation_only"
    assert sounio_row["aspects_reference_available"] == "0"
    assert sounio_row["truth_aspects"] == ""


def test_positive_only_segmentation_manifest_marks_auc_unsupported(tmp_path: Path):
    storage = StorageManager(tmp_path / "data")
    storage.initialize()
    harness = BenchmarkHarness(storage)
    manifest_path = create_positive_only_segmentation_manifest(tmp_path)

    run = harness.run_suite(BenchmarkRequest(dataset_manifest_path=str(manifest_path), seed=13))

    sounio_metrics = run.metrics[ModelFamily.sounio_hypercomplex.value]
    assert sounio_metrics["auc"]["value"] is None
    assert run.leaderboard["metric_rankings"].get("auc") is None


def test_benchmark_custom_target_shape_and_case_volume_exports(tmp_path: Path):
    storage = StorageManager(tmp_path / "data")
    storage.initialize()
    harness = BenchmarkHarness(storage)
    manifest_path = create_benchmark_manifest(tmp_path)

    run = harness.run_suite(
        BenchmarkRequest(
            dataset_manifest_path=str(manifest_path),
            seed=21,
            target_shape=(24, 48, 48),
            segmentation_threshold=0.35,
        )
    )

    assert run.compute_budget["target_shape"] == [24, 48, 48]
    assert run.compute_budget["segmentation_threshold"] == 0.35
    assert run.compute_budget["segmentation_threshold_policy"] == "explicit"
    assert any("Target shape: [24, 48, 48]" in item for item in run.fairness_checks)
    assert any("Segmentation threshold: 0.35" in item for item in run.fairness_checks)
    assert any("Segmentation threshold policy: explicit" in item for item in run.fairness_checks)

    case_csv = next(item.path for item in run.artifacts if item.name == "case_level_predictions")
    rows = list(csv.DictReader(Path(case_csv).open(newline="", encoding="utf-8")))
    sounio_row = next(row for row in rows if row["model_name"] == ModelFamily.sounio_hypercomplex.value)
    assert "true_lesion_volume_ml" in sounio_row
    assert "predicted_lesion_volume_ml" in sounio_row
    assert float(sounio_row["true_lesion_volume_ml"]) >= 0.0
    assert float(sounio_row["predicted_lesion_volume_ml"]) >= 0.0


def test_benchmark_records_insufficient_positive_val_for_sounio_undersegmentation_gate(tmp_path: Path):
    storage = StorageManager(tmp_path / "data")
    storage.initialize()
    harness = BenchmarkHarness(storage)
    manifest_path = create_benchmark_manifest_with_val(tmp_path)

    run = harness.run_suite(BenchmarkRequest(dataset_manifest_path=str(manifest_path), seed=21))

    assert run.compute_budget["sounio_undersegmentation_gate_policy"] == "insufficient-positive-val-v1"
    assert run.compute_budget["sounio_undersegmentation_gate_calibration_split"] == "val"
    assert run.compute_budget["sounio_undersegmentation_gate_calibration_cases"] == 2
    assert run.compute_budget["sounio_undersegmentation_gate_positive_cases"] < 5
    assert run.compute_budget["sounio_undersegmentation_gate_active"] is False
    assert any("insufficient-positive-val-v1" in item for item in run.fairness_checks)
    calibration_artifact_path = next(
        item.path for item in run.artifacts if item.name == "undersegmentation_gate_calibration"
    )
    calibration_payload = json.loads(Path(calibration_artifact_path).read_text(encoding="utf-8"))
    assert calibration_payload["policy"] == "insufficient-positive-val-v1"
    assert calibration_payload["split"] == "val"
    assert calibration_payload["case_count"] == 2
    assert calibration_payload["positive_case_count"] < 5
    assert len(calibration_payload["cases"]) == 2
    sounio_artifact_path = next(
        item.path for item in run.artifacts if item.name == "model_sounio_hypercomplex"
    )
    sounio_artifact = json.loads(Path(sounio_artifact_path).read_text(encoding="utf-8"))
    assert sounio_artifact["artifact_version"] == "regional-logistic-v4"
    assert sounio_artifact["undersegmentation_gate_policy"] == "insufficient-positive-val-v1"
    assert sounio_artifact["undersegmentation_gate_calibration_split"] == "val"
    assert sounio_artifact["undersegmentation_gate_positive_case_count"] < 5
    assert sounio_artifact["undersegmentation_gate_classifier_weights"] == []
    assert sounio_artifact["undersegmentation_gate_regressor_weights"] == []


def test_undersegmentation_gate_is_expand_only(tmp_path: Path):
    artifact = TrainedModelArtifact(
        artifact_version="regional-logistic-v4",
        model_family=ModelFamily.sounio_hypercomplex,
        feature_names=["deficit"],
        weights=[[0.0] for _ in range(10)],
        bias=[0.0 for _ in range(10)],
        feature_mean=[0.0],
        feature_std=[1.0],
        trained_on_split="train",
        dataset_manifest_path=str(tmp_path / "manifest.json"),
        undersegmentation_gate_feature_names=list(SOUNIO_UNDERSEGMENTATION_GATE_FEATURE_NAMES),
        undersegmentation_gate_classifier_weights=[0.0 for _ in SOUNIO_UNDERSEGMENTATION_GATE_FEATURE_NAMES],
        undersegmentation_gate_classifier_bias=20.0,
        undersegmentation_gate_classifier_feature_mean=[0.0 for _ in SOUNIO_UNDERSEGMENTATION_GATE_FEATURE_NAMES],
        undersegmentation_gate_classifier_feature_std=[1.0 for _ in SOUNIO_UNDERSEGMENTATION_GATE_FEATURE_NAMES],
        undersegmentation_gate_regressor_weights=[0.0 for _ in SOUNIO_UNDERSEGMENTATION_GATE_FEATURE_NAMES],
        undersegmentation_gate_regressor_bias=0.12,
        undersegmentation_gate_regressor_feature_mean=[0.0 for _ in SOUNIO_UNDERSEGMENTATION_GATE_FEATURE_NAMES],
        undersegmentation_gate_regressor_feature_std=[1.0 for _ in SOUNIO_UNDERSEGMENTATION_GATE_FEATURE_NAMES],
        undersegmentation_gate_gain_min=SOUNIO_UNDERSEGMENTATION_GATE_GAIN_MIN,
        undersegmentation_gate_gain_max=SOUNIO_UNDERSEGMENTATION_GATE_GAIN_MAX,
        undersegmentation_gate_activation_threshold=SOUNIO_UNDERSEGMENTATION_GATE_ACTIVATION_THRESHOLD,
        undersegmentation_gate_policy="test-gate",
    )
    base_heatmap = np.zeros((8, 8, 8), dtype=np.float32)
    base_heatmap[2:4, 2:4, 2:4] = 0.18
    underseg_support = np.zeros_like(base_heatmap)
    support_gate = np.zeros_like(base_heatmap)
    underseg_support[1:5, 1:5, 1:5] = 0.5
    support_gate[1:5, 1:5, 1:5] = 0.6
    feature_row = np.asarray([0.2 for _ in SOUNIO_UNDERSEGMENTATION_GATE_FEATURE_NAMES], dtype=np.float32)

    gated, active, gain, probability = apply_undersegmentation_gate(
        heatmap=base_heatmap,
        underseg_support=underseg_support,
        support_gate=support_gate,
        feature_row=feature_row,
        artifact=artifact,
    )

    assert active is True
    assert gain > 1.0
    assert probability is not None and probability > 0.6
    assert np.all(gated >= base_heatmap)
    assert int(np.count_nonzero(gated >= 0.25)) >= int(np.count_nonzero(base_heatmap >= 0.25))


def test_prepare_training_rows_emphasizes_higher_burden_cases(tmp_path: Path):
    manifest_path = create_benchmark_manifest(tmp_path)
    _, train_cases = load_manifest_cases(manifest_path, split="train")

    prepared = prepare_training_rows(train_cases)

    assert len(prepared.case_weights) == len(train_cases)
    assert min(prepared.case_weights) > 0.0
    assert max(prepared.case_weights) > min(prepared.case_weights)
    weighted_cases = sorted(
        zip((case.case_id for case in train_cases), prepared.case_weights, strict=True),
        key=lambda item: item[1],
        reverse=True,
    )
    assert weighted_cases[0][0] != weighted_cases[-1][0]


def test_aisd_manifest_uses_experimental_default_threshold(tmp_path: Path):
    storage = StorageManager(tmp_path / "data")
    storage.initialize()
    harness = BenchmarkHarness(storage)
    manifest_path = create_segmentation_only_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dataset_name"] = "AISD"
    manifest["source"] = "https://github.com/GriffinLiang/AISD"
    manifest["split_policy"] = "AISD fixed export"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    run = harness.run_suite(BenchmarkRequest(dataset_manifest_path=str(manifest_path), seed=13))

    assert run.compute_budget["segmentation_threshold"] == 0.25
    assert run.compute_budget["segmentation_threshold_policy"] == "aisd-experimental-v1"
    assert any("AISD experimental default threshold 0.25 applied" in item for item in run.fairness_checks)
