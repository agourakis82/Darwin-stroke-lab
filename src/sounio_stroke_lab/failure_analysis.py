from __future__ import annotations

from sounio_stroke_lab.schemas import ModelFamily


def summarize_failures(evaluations_by_runner: dict[str, list[object]]) -> dict[str, object]:
    payload: dict[str, object] = {"worst_cases_by_model": {}, "sounio_disagreements": {}}

    for model_name, evaluations in evaluations_by_runner.items():
        ranked = sorted(
            evaluations,
            key=lambda item: (
                -item.aspects_absolute_error,
                item.isles_dice,
                -abs(item.isles_absolute_volume_difference_ml),
            ),
        )
        payload["worst_cases_by_model"][model_name] = [
            {
                "case_id": item.case.case_id,
                "aspects_score_truth": item.case.aspects_score,
                "aspects_score_predicted": item.predicted_aspects,
                "aspects_absolute_error": item.aspects_absolute_error,
                "isles_dice": round(item.isles_dice, 4),
                "isles_lesionwise_f1": round(item.isles_lesionwise_f1, 4),
                "isles_absolute_volume_difference_ml": round(item.isles_absolute_volume_difference_ml, 4),
                "global_confidence": round(item.global_confidence, 4),
            }
            for item in ranked[:5]
        ]

    sounio_items = {item.case.case_id: item for item in evaluations_by_runner[ModelFamily.sounio_hypercomplex.value]}
    for baseline in (
        ModelFamily.python_3d_conventional.value,
        ModelFamily.julia_equivalent.value,
        ModelFamily.cpp_equivalent.value,
    ):
        disagreements = []
        for item in evaluations_by_runner[baseline]:
            sounio_item = sounio_items[item.case.case_id]
            disagreements.append(
                {
                    "case_id": item.case.case_id,
                    "sounio_aspects": sounio_item.predicted_aspects,
                    "baseline_aspects": item.predicted_aspects,
                    "score_gap": abs(sounio_item.predicted_aspects - item.predicted_aspects),
                    "dice_gap": round(abs(sounio_item.isles_dice - item.isles_dice), 4),
                    "confidence_gap": round(abs(sounio_item.global_confidence - item.global_confidence), 4),
                }
            )
        payload["sounio_disagreements"][baseline] = sorted(
            disagreements,
            key=lambda item: (-item["score_gap"], -item["dice_gap"], -item["confidence_gap"]),
        )[:5]

    return payload
