from __future__ import annotations

from pathlib import Path

from sounio_stroke_lab.schemas import BenchmarkDatasetManifest, BenchmarkRequest


def build_protocol_payload(
    request: BenchmarkRequest,
    train_manifest_path: Path,
    train_manifest: BenchmarkDatasetManifest,
    test_manifest_path: Path,
    test_manifest: BenchmarkDatasetManifest,
) -> dict[str, object]:
    external_validation = train_manifest_path != test_manifest_path
    return {
        "study_design": "external_validation" if external_validation else "internal_split_validation",
        "reporting_guidelines": [
            "CLAIM",
            "TRIPOD+AI",
        ],
        "training_dataset": {
            "dataset_name": train_manifest.dataset_name,
            "dataset_version": train_manifest.dataset_version,
            "manifest_path": str(train_manifest_path),
            "split": request.train_split,
            "source": train_manifest.source,
        },
        "test_dataset": {
            "dataset_name": test_manifest.dataset_name,
            "dataset_version": test_manifest.dataset_version,
            "manifest_path": str(test_manifest_path),
            "split": request.test_split,
            "source": test_manifest.source,
        },
        "benchmark_arms": [
            "sounio_hypercomplex",
            "python_3d_conventional",
            "julia_equivalent",
            "cpp_equivalent",
        ],
        "primary_metrics": [
            "aspects_mae",
            "auc",
            "isles_dice",
            "isles_lesionwise_f1",
        ],
        "secondary_metrics": [
            "interpretability_coherence",
            "region_calibration_error",
            "isles_absolute_volume_difference_ml",
            "isles_absolute_lesion_count_difference",
            "mean_latency_ms",
        ],
        "statistical_plan": {
            "paired_confidence_intervals": "bootstrap",
            "paired_p_values": "permutation",
        },
        "notes": [
            "Current models consume NCCT-derived features only, even when the manifest exposes CTA/CTP or clinical metadata.",
            "Ablation results are reported for the Sounio arm by retraining after removing individual hypercomplex components.",
        ],
    }
