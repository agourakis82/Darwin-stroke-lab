from __future__ import annotations


def _rank_metric(items: list[tuple[str, float]], higher_is_better: bool) -> dict[str, int]:
    ranked = sorted(items, key=lambda item: item[1], reverse=higher_is_better)
    return {model_name: rank + 1 for rank, (model_name, _) in enumerate(ranked)}


def build_leaderboard(metrics: dict[str, dict[str, dict[str, float | None]]]) -> dict[str, object]:
    metric_specs = {
        "isles_dice": True,
        "isles_lesionwise_f1": True,
        "isles_absolute_volume_difference_ml": False,
        "isles_absolute_lesion_count_difference": False,
        "auc": True,
        "aspects_mae": False,
    }

    per_metric_ranks: dict[str, dict[str, int]] = {}
    score_totals: dict[str, float] = {model_name: 0.0 for model_name in metrics}
    score_counts: dict[str, int] = {model_name: 0 for model_name in metrics}
    for metric_name, higher_is_better in metric_specs.items():
        values = [
            (model_name, float(model_metrics[metric_name]["value"]))
            for model_name, model_metrics in metrics.items()
            if metric_name in model_metrics
            and model_metrics[metric_name].get("value") is not None
        ]
        if not values:
            continue
        ranks = _rank_metric(values, higher_is_better=higher_is_better)
        per_metric_ranks[metric_name] = ranks
        for model_name, rank in ranks.items():
            score_totals[model_name] += rank
            score_counts[model_name] += 1

    mean_ranks = {
        model_name: round(total / max(score_counts[model_name], 1), 4)
        for model_name, total in score_totals.items()
    }
    overall = sorted(mean_ranks.items(), key=lambda item: item[1])
    return {
        "metric_rankings": per_metric_ranks,
        "mean_rank": mean_ranks,
        "overall_rank": [
            {
                "model_name": model_name,
                "rank": index + 1,
                "mean_rank": mean_rank,
            }
            for index, (model_name, mean_rank) in enumerate(overall)
        ],
    }
