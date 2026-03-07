from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from sounio_stroke_lab.schemas import BenchmarkRun


def create_case_figure(
    volume: np.ndarray,
    lesion_mask: np.ndarray,
    heatmap: np.ndarray,
    output_path: Path,
    title: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    center = volume.shape[0] // 2
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.6))
    panels = [
        (volume[center], "Input volume", "gray"),
        (lesion_mask[center], "Ground truth lesion", "inferno"),
        (heatmap[center], "Predicted heatmap", "magma"),
    ]
    for axis, (image, label, cmap) in zip(axes, panels, strict=True):
        axis.imshow(image, cmap=cmap)
        axis.set_title(label)
        axis.axis("off")
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def create_metric_summary_figure(
    metrics: dict[str, dict[str, dict[str, float | None]]],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    model_names = list(metrics)
    aspects = [float(metrics[name]["aspects_mae"]["value"]) for name in model_names]
    auc = [float(metrics[name]["auc"]["value"]) for name in model_names]
    dice = [float(metrics[name]["isles_dice"]["value"]) for name in model_names]

    fig, axes = plt.subplots(1, 3, figsize=(13, 3.8))
    panels = [
        ("ASPECTS MAE", aspects, False),
        ("AUC", auc, True),
        ("Dice", dice, True),
    ]
    for axis, (title, values, higher_is_better) in zip(axes, panels, strict=True):
        bars = axis.bar(model_names, values, color=["#0d6e6e", "#7a8b99", "#b08968", "#6c757d"][: len(model_names)])
        axis.set_title(title)
        axis.tick_params(axis="x", rotation=20)
        if title == "ASPECTS MAE":
            axis.set_ylim(0.0, max(values) * 1.15 + 0.01)
        else:
            axis.set_ylim(0.0, 1.0)
        best_index = int(np.argmin(values) if not higher_is_better else np.argmax(values))
        bars[best_index].set_edgecolor("black")
        bars[best_index].set_linewidth(1.5)
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def create_calibration_figure(
    evaluations_by_runner: dict[str, list[object]],
    output_path: Path,
    bins: int = 10,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, axis = plt.subplots(1, 1, figsize=(5.8, 4.6))
    axis.plot([0.0, 1.0], [0.0, 1.0], linestyle="--", color="black", linewidth=1.0, label="ideal")
    palette = ["#0d6e6e", "#7a8b99", "#b08968", "#6c757d"]

    for color, (model_name, evaluations) in zip(palette, evaluations_by_runner.items(), strict=False):
        probabilities = np.asarray(
            [probability for item in evaluations for probability in item.region_probabilities],
            dtype=np.float32,
        )
        labels = np.asarray([truth for item in evaluations for truth in item.region_truth], dtype=np.float32)
        curve_x: list[float] = []
        curve_y: list[float] = []
        for start in np.linspace(0.0, 0.9, bins):
            end = start + 0.1
            mask = (probabilities >= start) & (probabilities <= end) if end >= 1.0 else (probabilities >= start) & (probabilities < end)
            if not np.any(mask):
                continue
            curve_x.append(float(probabilities[mask].mean()))
            curve_y.append(float(labels[mask].mean()))
        if curve_x:
            axis.plot(curve_x, curve_y, marker="o", linewidth=1.6, color=color, label=model_name)

    axis.set_title("Region Calibration")
    axis.set_xlabel("Predicted probability")
    axis.set_ylabel("Observed frequency")
    axis.set_xlim(0.0, 1.0)
    axis.set_ylim(0.0, 1.0)
    axis.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def create_effect_size_figure(
    comparisons: dict[str, object],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    labels = []
    deltas = []
    lower = []
    upper = []
    for baseline_name, comparison in comparisons.items():
        auc_gain = comparison.metrics.get("auc_gain")
        if auc_gain is None:
            continue
        labels.append(baseline_name)
        deltas.append(float(auc_gain.delta))
        lower.append(float(auc_gain.lower_ci if auc_gain.lower_ci is not None else auc_gain.delta))
        upper.append(float(auc_gain.upper_ci if auc_gain.upper_ci is not None else auc_gain.delta))

    fig, axis = plt.subplots(1, 1, figsize=(6.2, 3.8))
    if labels:
        y_positions = np.arange(len(labels))
        axis.hlines(y_positions, lower, upper, color="#0d6e6e", linewidth=2.0)
        axis.plot(deltas, y_positions, "o", color="#7a1f1f")
        axis.axvline(0.0, linestyle="--", color="black", linewidth=1.0)
        axis.set_yticks(y_positions)
        axis.set_yticklabels(labels)
    axis.set_xlabel("AUC gain for Sounio")
    axis.set_title("Pairwise Effect Sizes")
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def render_report(run: BenchmarkRun) -> str:
    def format_metric(metric: object) -> str:
        if not isinstance(metric, dict):
            return "n/a"
        value = metric.get("value")
        if not isinstance(value, (int, float)):
            return "n/a"
        return f"{value:.3f}"

    rows = []
    isles_rows = []
    for model_name, metrics in run.metrics.items():
        if not isinstance(metrics, dict):
            continue
        aspects = metrics.get("aspects_mae", {})
        auc = metrics.get("auc", {})
        coherence = metrics.get("interpretability_coherence", {})
        isles_dice = metrics.get("isles_dice", {})
        isles_avd = metrics.get("isles_absolute_volume_difference_ml", {})
        isles_lf1 = metrics.get("isles_lesionwise_f1", {})
        rows.append(
            f"| {model_name} | {format_metric(aspects)} | {format_metric(auc)} | {format_metric(coherence)} |"
        )
        isles_rows.append(
            f"| {model_name} | {format_metric(isles_dice)} | {format_metric(isles_avd)} | {format_metric(isles_lf1)} |"
        )
    table = "\n".join(rows) if rows else "| n/a | n/a | n/a | n/a |"
    isles_table = "\n".join(isles_rows) if isles_rows else "| n/a | n/a | n/a | n/a |"
    comparison_rows = []
    for comparison_name, comparison in run.comparisons.items():
        aspects = comparison.metrics.get("aspects_mae_gain")
        auc_gain = comparison.metrics.get("auc_gain")
        coherence_gain = comparison.metrics.get("coherence_gain")
        comparison_rows.append(
            "| "
            f"{comparison_name} | "
            f"{getattr(aspects, 'delta', 0.0):.3f} | "
            f"{getattr(auc_gain, 'delta', 0.0):.3f} | "
            f"{getattr(coherence_gain, 'delta', 0.0):.3f} | "
            f"{getattr(auc_gain, 'p_value', 1.0):.4f} |"
        )
    comparison_table = "\n".join(comparison_rows) if comparison_rows else "| none | 0.000 | 0.000 | 0.000 | 1.0000 |"
    ablations = "\n".join(
        f"| {item.ablation_name} | {item.metric_delta:.3f} | {item.interpretability_delta:.3f} | {item.notes} |"
        for item in run.ablations
    )
    if not ablations:
        ablations = "| none | 0.000 | 0.000 | no ablations |"
    fairness = "\n".join(f"- {item}" for item in run.fairness_checks)
    artifact_lines = "\n".join(f"- `{artifact.kind}`: {artifact.path}" for artifact in run.artifacts)
    cohort_shift_artifact = next((artifact.path for artifact in run.artifacts if artifact.name == "cohort_shift"), "n/a")
    failure_artifact = next((artifact.path for artifact in run.artifacts if artifact.name == "failure_analysis"), "n/a")
    leaderboard_rows = "\n".join(
        f"| {item['rank']} | {item['model_name']} | {item['mean_rank']:.3f} |"
        for item in run.leaderboard.get("overall_rank", [])
    )
    if not leaderboard_rows:
        leaderboard_rows = "| 1 | n/a | 0.000 |"
    return f"""# Technical report: Sounio stroke benchmark

## Thesis

This benchmark evaluates whether the Sounio hypercomplex framework offers better support for early ischemia and ASPECTS estimation than comparable conventional stacks under a fixed protocol.

## Fairness controls

{fairness}

## Quantitative summary

| Arm | ASPECTS MAE | AUC | Heatmap coherence |
| --- | ---: | ---: | ---: |
{table}

## ISLES-style segmentation summary

| Arm | Dice | Absolute volume difference (mL) | Lesion-wise F1 |
| --- | ---: | ---: | ---: |
{isles_table}

## Pairwise comparison against Sounio

| Baseline arm | MAE gain | AUC gain | Coherence gain | AUC p-value |
| --- | ---: | ---: | ---: | ---: |
{comparison_table}

## Composite leaderboard

| Rank | Arm | Mean rank |
| --- | --- | ---: |
{leaderboard_rows}

## Ablations

| Ablation | Primary metric delta | Interpretability delta | Notes |
| --- | ---: | ---: | --- |
{ablations}

## Cohort shift and failure analysis

- Cohort shift summary: {cohort_shift_artifact}
- Failure analysis summary: {failure_artifact}
- Stratified metrics are included to show behaviour by lesion presence, hemisphere and ASPECTS severity.

## Theoretical argument

- The Sounio arm is trained on the same manifest split as the baselines, but keeps a richer hypercomplex feature basis built from deficit, asymmetry, smoothness, gradient suppression, energy and phase coupling.
- That representation preserves cross-channel relationships that the reduced scalar baselines discard before fitting.
- All arms operate on the same atlas-defined ASPECTS regions, so observed deltas come from the representation and scoring stack rather than from a different preprocessing contract.

## Artifacts

{artifact_lines}
"""
