from __future__ import annotations

from collections import Counter
from pathlib import Path

from sounio_stroke_lab.dataset_manifest import LoadedBenchmarkCase
from sounio_stroke_lab.schemas import BenchmarkDatasetManifest


def _safe_percentage(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _numeric_summary(values: list[float]) -> dict[str, float] | dict[str, None]:
    if not values:
        return {"mean": None, "min": None, "max": None}
    return {
        "mean": round(sum(values) / len(values), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
    }


def summarize_manifest_cohort(
    manifest: BenchmarkDatasetManifest,
    cases: list[LoadedBenchmarkCase],
    manifest_path: Path,
    split: str,
) -> dict[str, object]:
    case_count = len(cases)
    lesion_positive_cases = [case for case in cases if case.lesion_positive]
    hemisphere_reference_cases = [case for case in cases if case.hemisphere_reference_available]
    aspects_reference_cases = [case for case in cases if case.aspects_reference_available]
    region_reference_cases = [case for case in cases if case.region_reference_available]
    hemisphere_counts = Counter(case.hemisphere for case in hemisphere_reference_cases)
    aspects_bucket_counts = Counter(
        "severe_0_4" if case.aspects_score <= 4 else "moderate_5_7" if case.aspects_score <= 7 else "mild_8_10"
        for case in aspects_reference_cases
    )
    reference_scope_counts = Counter(case.reference_scope for case in cases)
    metadata_keys = Counter(key for case in cases for key in case.metadata)
    manufacturer_counts = Counter(
        str(case.metadata.get("manufacturer"))
        for case in cases
        if case.metadata.get("manufacturer")
    )
    kernel_counts = Counter(
        str(case.metadata.get("convolution_kernel"))
        for case in cases
        if case.metadata.get("convolution_kernel")
    )
    slice_thickness_values = [
        float(case.metadata["slice_thickness_mm"])
        for case in cases
        if isinstance(case.metadata.get("slice_thickness_mm"), (int, float))
    ]
    modality_availability = {}
    for key in ("cta_path", "ctp_path", "clinical_baseline_csv", "outcome_csv"):
        present = sum(int(bool(case.metadata.get(key))) for case in cases)
        modality_availability[key] = {
            "count": present,
            "fraction": _safe_percentage(present, case_count),
        }
    perfusion_present = sum(int(bool(case.metadata.get("perfusion_maps"))) for case in cases)
    modality_availability["perfusion_maps"] = {
        "count": perfusion_present,
        "fraction": _safe_percentage(perfusion_present, case_count),
    }

    return {
        "dataset_name": manifest.dataset_name,
        "dataset_version": manifest.dataset_version,
        "source": manifest.source,
        "manifest_path": str(manifest_path),
        "split": split,
        "case_count": case_count,
        "reference_scope_counts": dict(reference_scope_counts),
        "segmentation_reference_cases": sum(int(case.segmentation_reference_available) for case in cases),
        "hemisphere_reference_cases": len(hemisphere_reference_cases),
        "region_reference_cases": len(region_reference_cases),
        "aspects_reference_cases": len(aspects_reference_cases),
        "lesion_positive_cases": len(lesion_positive_cases),
        "lesion_positive_fraction": _safe_percentage(len(lesion_positive_cases), case_count),
        "hemisphere_counts": dict(hemisphere_counts),
        "aspects_bucket_counts": dict(aspects_bucket_counts),
        "aspects_score_summary": _numeric_summary([float(case.aspects_score) for case in aspects_reference_cases]),
        "voxel_volume_ml_summary": _numeric_summary([float(case.voxel_volume_ml) for case in cases]),
        "modality_availability": modality_availability,
        "metadata_coverage": {
            key: {
                "count": value,
                "fraction": _safe_percentage(value, case_count),
            }
            for key, value in metadata_keys.most_common()
        },
        "dicom_series_summary": {
            "manufacturer_counts": dict(manufacturer_counts),
            "convolution_kernel_counts": dict(kernel_counts),
            "slice_thickness_mm": _numeric_summary(slice_thickness_values),
        },
    }


def compare_cohort_summaries(
    training_summary: dict[str, object],
    test_summary: dict[str, object],
) -> dict[str, object]:
    train_lesion_fraction = float(training_summary["lesion_positive_fraction"])
    test_lesion_fraction = float(test_summary["lesion_positive_fraction"])
    train_aspects_mean = training_summary["aspects_score_summary"]["mean"]
    test_aspects_mean = test_summary["aspects_score_summary"]["mean"]
    train_slice_mean = training_summary["dicom_series_summary"]["slice_thickness_mm"]["mean"]
    test_slice_mean = test_summary["dicom_series_summary"]["slice_thickness_mm"]["mean"]

    return {
        "external_validation": training_summary["manifest_path"] != test_summary["manifest_path"],
        "source_changed": training_summary["source"] != test_summary["source"],
        "dataset_changed": training_summary["dataset_version"] != test_summary["dataset_version"],
        "lesion_prevalence_delta": round(test_lesion_fraction - train_lesion_fraction, 4),
        "aspects_mean_delta": (
            round(float(test_aspects_mean) - float(train_aspects_mean), 4)
            if train_aspects_mean is not None and test_aspects_mean is not None
            else None
        ),
        "slice_thickness_mean_delta_mm": (
            round(float(test_slice_mean) - float(train_slice_mean), 4)
            if train_slice_mean is not None and test_slice_mean is not None
            else None
        ),
        "modality_availability_delta": {
            key: round(
                float(test_summary["modality_availability"][key]["fraction"])
                - float(training_summary["modality_availability"][key]["fraction"]),
                4,
            )
            for key in training_summary["modality_availability"]
        },
    }
