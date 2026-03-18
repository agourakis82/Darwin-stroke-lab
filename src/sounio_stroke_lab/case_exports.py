from __future__ import annotations

import csv
from pathlib import Path


def write_case_level_csv(
    evaluations_by_runner: dict[str, list[object]],
    output_path: Path,
) -> str:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "model_name",
        "case_id",
        "reference_scope",
        "segmentation_reference_available",
        "aspects_reference_available",
        "hemisphere",
        "lesion_positive",
        "truth_aspects",
        "predicted_aspects",
        "aspects_absolute_error",
        "global_confidence",
        "true_lesion_volume_ml",
        "predicted_lesion_volume_ml",
        "isles_dice",
        "isles_lesionwise_f1",
        "isles_absolute_volume_difference_ml",
        "isles_absolute_lesion_count_difference",
        "elapsed_ms",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for model_name, evaluations in evaluations_by_runner.items():
            for item in evaluations:
                writer.writerow(
                    {
                        "model_name": model_name,
                        "case_id": item.case.case_id,
                        "reference_scope": item.case.reference_scope,
                        "segmentation_reference_available": int(item.case.segmentation_reference_available),
                        "aspects_reference_available": int(item.case.aspects_reference_available),
                        "hemisphere": item.case.hemisphere,
                        "lesion_positive": int(item.case.lesion_positive),
                        "truth_aspects": item.case.aspects_score if item.case.aspects_reference_available else "",
                        "predicted_aspects": item.predicted_aspects,
                        "aspects_absolute_error": (
                            item.aspects_absolute_error if item.aspects_absolute_error is not None else ""
                        ),
                        "global_confidence": round(item.global_confidence, 6),
                        "true_lesion_volume_ml": round(item.true_lesion_volume_ml, 6),
                        "predicted_lesion_volume_ml": round(item.predicted_lesion_volume_ml, 6),
                        "isles_dice": round(item.isles_dice, 6),
                        "isles_lesionwise_f1": round(item.isles_lesionwise_f1, 6),
                        "isles_absolute_volume_difference_ml": round(item.isles_absolute_volume_difference_ml, 6),
                        "isles_absolute_lesion_count_difference": item.isles_absolute_lesion_count_difference,
                        "elapsed_ms": round(item.elapsed_ms, 6),
                    }
                )
    return str(output_path)
