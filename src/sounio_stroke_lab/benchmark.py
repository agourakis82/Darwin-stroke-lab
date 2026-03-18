from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from sounio_stroke_lab.atlas import affected_regions_from_mask, aspects_from_probability_map, build_aspects_atlas
from sounio_stroke_lab.cohort_analysis import compare_cohort_summaries, summarize_manifest_cohort
from sounio_stroke_lab.case_exports import write_case_level_csv
from sounio_stroke_lab.config import (
    AISD_EXPERIMENTAL_SEGMENTATION_THRESHOLD,
    DEFAULT_TARGET_SHAPE,
    FAIRNESS_POLICY,
    PIPELINE_VERSION,
    normalize_segmentation_threshold,
    normalize_target_shape,
)
from sounio_stroke_lab.dataset_manifest import LoadedBenchmarkCase, load_manifest_cases, validate_benchmark_manifest
from sounio_stroke_lab.evaluation import (
    _align_binary_mask,
    aligned_segmentation_volumes_ml,
    absolute_volume_difference_ml,
    binary_prediction_mask,
    dice_score,
    lesionwise_f1_and_count_difference,
)
from sounio_stroke_lab.failure_analysis import summarize_failures
from sounio_stroke_lab.features import extract_feature_maps, region_feature_vectors
from sounio_stroke_lab.leaderboard import build_leaderboard
from sounio_stroke_lab.preprocessing import PreprocessedVolume, prepare_volume
from sounio_stroke_lab.research_protocol import build_protocol_payload
from sounio_stroke_lab.reproducibility import build_reproducibility_snapshot
from sounio_stroke_lab.reporting import (
    create_calibration_figure,
    create_case_figure,
    create_effect_size_figure,
    create_metric_summary_figure,
    render_report,
)
from sounio_stroke_lab.scientific_reporting import (
    build_claim_checklist,
    build_tripod_ai_checklist,
    render_manuscript_draft,
)
from sounio_stroke_lab.runners import (
    BaseRunner,
    _artifact_feature_rows_from_vectors,
    _base_heatmap_from_artifact,
    _predict_burden_voxels,
    build_runner,
    comparative_runners,
)
from sounio_stroke_lab.schemas import (
    AblationResult,
    ArtifactDescriptor,
    BenchmarkComparison,
    BenchmarkDatasetManifest,
    BenchmarkRequest,
    BenchmarkRun,
    ComparativeMetric,
    LanguageStack,
    MetricInterval,
    ModelFamily,
    TrainedModelArtifact,
)
from sounio_stroke_lab.storage import StorageManager
from sounio_stroke_lab.trainable_models import (
    _fit_binary_logistic,
    fit_all_model_artifacts,
    fit_model_artifact,
    prepare_training_rows,
)
from sounio_stroke_lab.undersegmentation_gate import (
    SOUNIO_UNDERSEGMENTATION_GATE_ACTIVATION_THRESHOLD,
    SOUNIO_UNDERSEGMENTATION_GATE_FEATURE_NAMES,
    SOUNIO_UNDERSEGMENTATION_GATE_GAIN_MAX,
    SOUNIO_UNDERSEGMENTATION_GATE_GAIN_MIN,
    SOUNIO_UNDERSEGMENTATION_GATE_INSUFFICIENT_POLICY,
    SOUNIO_UNDERSEGMENTATION_GATE_NO_VAL_POLICY,
    SOUNIO_UNDERSEGMENTATION_GATE_ORACLE_GAINS,
    SOUNIO_UNDERSEGMENTATION_GATE_POLICY,
    apply_undersegmentation_gate,
    undersegmentation_gate_feature_row,
    undersegmentation_gate_noop_payload,
    undersegmentation_support_maps,
)


@dataclass
class CaseEvaluation:
    case: LoadedBenchmarkCase
    predicted_aspects: int
    aspects_absolute_error: int | None
    global_confidence: float
    region_probabilities: list[float]
    region_truth: list[bool]
    coherence: float
    elapsed_ms: float
    heatmap: np.ndarray
    binary_mask: np.ndarray
    true_lesion_volume_ml: float
    predicted_lesion_volume_ml: float
    isles_dice: float
    isles_absolute_volume_difference_ml: float
    isles_absolute_lesion_count_difference: int
    isles_lesionwise_f1: float


@dataclass
class PreparedCase:
    case: LoadedBenchmarkCase
    prepared_volume: PreprocessedVolume
    ground_truth_regions: set[str]


@dataclass
class UndersegmentationCalibrationCase:
    prepared_case: PreparedCase
    feature_row: np.ndarray
    heatmap: np.ndarray
    underseg_support: np.ndarray
    support_gate: np.ndarray
    baseline_evaluation: CaseEvaluation


class PipelineContractError(RuntimeError):
    pass


def _dice(prediction: np.ndarray, truth: np.ndarray) -> float:
    pred_mask = prediction >= 0.55
    truth_mask = _align_binary_mask(truth >= 0.25, pred_mask.shape)
    pred_total = int(pred_mask.sum())
    truth_total = int(truth_mask.sum())
    if pred_total == 0 and truth_total == 0:
        return 1.0
    if pred_total == 0 or truth_total == 0:
        return 0.0
    overlap = int(np.logical_and(pred_mask, truth_mask).sum())
    return (2.0 * overlap) / float(pred_total + truth_total)


def _roc_auc(labels: list[bool], scores: list[float]) -> float:
    positives = [score for label, score in zip(labels, scores, strict=True) if label]
    negatives = [score for label, score in zip(labels, scores, strict=True) if not label]
    if not positives or not negatives:
        return 0.5
    wins = 0.0
    for positive in positives:
        for negative in negatives:
            if positive > negative:
                wins += 1.0
            elif positive == negative:
                wins += 0.5
    return wins / float(len(positives) * len(negatives))


def _expected_calibration_error(probabilities: list[float], labels: list[bool], bins: int = 10) -> float:
    probs = np.asarray(probabilities, dtype=np.float32)
    truth = np.asarray(labels, dtype=np.float32)
    if probs.size == 0:
        return 0.0
    ece = 0.0
    for start in np.linspace(0.0, 0.9, bins):
        end = start + 0.1
        mask = (probs >= start) & (probs <= end) if end >= 1.0 else (probs >= start) & (probs < end)
        if not np.any(mask):
            continue
        confidence = float(probs[mask].mean())
        accuracy = float(truth[mask].mean())
        ece += abs(confidence - accuracy) * float(mask.mean())
    return ece


def _bootstrap_interval(
    evaluations: list[CaseEvaluation],
    metric_fn: Callable[[list[CaseEvaluation]], float | None],
    seed: int,
    rounds: int = 200,
) -> MetricInterval:
    value = metric_fn(evaluations)
    if value is None:
        return MetricInterval(value=None)
    if len(evaluations) < 2:
        return MetricInterval(value=round(value, 4))
    rng = np.random.default_rng(seed)
    samples = []
    for _ in range(rounds):
        indices = rng.integers(0, len(evaluations), size=len(evaluations))
        sampled = [evaluations[index] for index in indices]
        sample_value = metric_fn(sampled)
        if sample_value is not None:
            samples.append(sample_value)
    if not samples:
        return MetricInterval(value=round(value, 4))
    lower, upper = np.percentile(samples, [2.5, 97.5])
    return MetricInterval(value=round(value, 4), lower_ci=round(float(lower), 4), upper_ci=round(float(upper), 4))


def _prepare_benchmark_case(
    case: LoadedBenchmarkCase,
    target_shape: tuple[int, int, int] = DEFAULT_TARGET_SHAPE,
) -> PreparedCase:
    prepared = prepare_volume(case.volume, shape=target_shape)
    prepared.hemisphere = case.hemisphere
    prepared.atlas = build_aspects_atlas(tuple(prepared.volume.shape))[case.hemisphere]
    return PreparedCase(
        case=case,
        prepared_volume=prepared,
        ground_truth_regions=set(case.reference_regions),
    )


def _evaluate_case(
    prepared_case: PreparedCase,
    runner: BaseRunner,
    segmentation_threshold: float,
) -> CaseEvaluation:
    case = prepared_case.case
    output = runner.run(prepared_case.prepared_volume)
    predicted_aspects, region_scores = aspects_from_probability_map(output.heatmap, case.hemisphere)
    binary_mask = binary_prediction_mask(output.heatmap, threshold=segmentation_threshold)
    lesionwise_f1, lesion_count_difference = lesionwise_f1_and_count_difference(case.lesion_mask, binary_mask)
    true_lesion_volume_ml, predicted_lesion_volume_ml = aligned_segmentation_volumes_ml(
        case.lesion_mask,
        binary_mask,
        case.voxel_volume_ml,
    )
    return CaseEvaluation(
        case=case,
        predicted_aspects=predicted_aspects,
        aspects_absolute_error=(
            abs(predicted_aspects - case.aspects_score) if case.aspects_reference_available else None
        ),
        global_confidence=output.global_confidence,
        region_probabilities=[score.probability for score in region_scores],
        region_truth=[score.region in prepared_case.ground_truth_regions for score in region_scores],
        coherence=_dice(output.heatmap, case.lesion_mask),
        elapsed_ms=output.elapsed_ms,
        heatmap=output.heatmap,
        binary_mask=binary_mask,
        true_lesion_volume_ml=true_lesion_volume_ml,
        predicted_lesion_volume_ml=predicted_lesion_volume_ml,
        isles_dice=dice_score(case.lesion_mask, binary_mask),
        isles_absolute_volume_difference_ml=absolute_volume_difference_ml(
            case.lesion_mask,
            binary_mask,
            case.voxel_volume_ml,
        ),
        isles_absolute_lesion_count_difference=lesion_count_difference,
        isles_lesionwise_f1=lesionwise_f1,
    )


def _aggregate_metrics(evaluations: list[CaseEvaluation], seed: int) -> dict[str, dict[str, float | None]]:
    def aspects_mae(items: list[CaseEvaluation]) -> float | None:
        supported = [item.aspects_absolute_error for item in items if item.aspects_absolute_error is not None]
        if not supported:
            return None
        return float(np.mean(supported))

    def auc(items: list[CaseEvaluation]) -> float | None:
        supported_items = [item for item in items if item.case.segmentation_reference_available]
        if not supported_items:
            return None
        labels = [item.case.lesion_positive for item in supported_items]
        if len(set(labels)) < 2:
            return None
        scores = [item.global_confidence for item in supported_items]
        return float(_roc_auc(labels, scores))

    def region_sensitivity(items: list[CaseEvaluation]) -> float | None:
        supported_items = [item for item in items if item.case.region_reference_available]
        if not supported_items:
            return None
        tp = 0
        fn = 0
        for item in supported_items:
            for truth, probability in zip(item.region_truth, item.region_probabilities, strict=True):
                if truth and probability >= 0.45:
                    tp += 1
                elif truth:
                    fn += 1
        return float(tp / max(tp + fn, 1))

    def interpretability_coherence(items: list[CaseEvaluation]) -> float | None:
        if not items:
            return None
        return float(np.mean([item.coherence for item in items]))

    def calibration_error(items: list[CaseEvaluation]) -> float | None:
        supported_items = [item for item in items if item.case.region_reference_available]
        if not supported_items:
            return None
        probabilities = [probability for item in supported_items for probability in item.region_probabilities]
        labels = [truth for item in supported_items for truth in item.region_truth]
        return float(_expected_calibration_error(probabilities, labels))

    def isles_dice(items: list[CaseEvaluation]) -> float | None:
        if not items:
            return None
        return float(np.mean([item.isles_dice for item in items]))

    def isles_absolute_volume_difference_ml(items: list[CaseEvaluation]) -> float | None:
        if not items:
            return None
        return float(np.mean([item.isles_absolute_volume_difference_ml for item in items]))

    def isles_absolute_lesion_count_difference(items: list[CaseEvaluation]) -> float | None:
        if not items:
            return None
        return float(np.mean([item.isles_absolute_lesion_count_difference for item in items]))

    def isles_lesionwise_f1(items: list[CaseEvaluation]) -> float | None:
        if not items:
            return None
        return float(np.mean([item.isles_lesionwise_f1 for item in items]))

    def latency(items: list[CaseEvaluation]) -> float | None:
        if not items:
            return None
        return float(np.mean([item.elapsed_ms for item in items]))

    metric_functions = {
        "aspects_mae": aspects_mae,
        "auc": auc,
        "region_sensitivity": region_sensitivity,
        "interpretability_coherence": interpretability_coherence,
        "region_calibration_error": calibration_error,
        "isles_dice": isles_dice,
        "isles_absolute_volume_difference_ml": isles_absolute_volume_difference_ml,
        "isles_absolute_lesion_count_difference": isles_absolute_lesion_count_difference,
        "isles_lesionwise_f1": isles_lesionwise_f1,
        "mean_latency_ms": latency,
    }
    return {
        name: _bootstrap_interval(evaluations, metric_fn, seed + index * 53).model_dump()
        for index, (name, metric_fn) in enumerate(metric_functions.items())
    }


def _paired_metric_delta(
    left: list[CaseEvaluation],
    right: list[CaseEvaluation],
    metric_fn: Callable[[list[CaseEvaluation]], float | None],
    higher_is_better: bool,
) -> float | None:
    left_value = metric_fn(left)
    right_value = metric_fn(right)
    if left_value is None or right_value is None:
        return None
    return left_value - right_value if higher_is_better else right_value - left_value


def _paired_bootstrap_delta(
    left: list[CaseEvaluation],
    right: list[CaseEvaluation],
    metric_fn: Callable[[list[CaseEvaluation]], float | None],
    higher_is_better: bool,
    seed: int,
    rounds: int = 120,
) -> tuple[float | None, float | None, float | None]:
    observed = _paired_metric_delta(left, right, metric_fn, higher_is_better=higher_is_better)
    if observed is None:
        return None, None, None
    rng = np.random.default_rng(seed)
    deltas = []
    for _ in range(rounds):
        indices = rng.integers(0, len(left), size=len(left))
        left_sample = [left[index] for index in indices]
        right_sample = [right[index] for index in indices]
        sample_delta = _paired_metric_delta(left_sample, right_sample, metric_fn, higher_is_better=higher_is_better)
        if sample_delta is not None:
            deltas.append(sample_delta)
    if not deltas:
        return round(observed, 4), None, None
    lower, upper = np.percentile(deltas, [2.5, 97.5])
    return round(observed, 4), round(float(lower), 4), round(float(upper), 4)


def _paired_permutation_pvalue(
    left: list[CaseEvaluation],
    right: list[CaseEvaluation],
    metric_fn: Callable[[list[CaseEvaluation]], float | None],
    higher_is_better: bool,
    seed: int,
    rounds: int = 200,
) -> float | None:
    observed = _paired_metric_delta(left, right, metric_fn, higher_is_better=higher_is_better)
    if observed is None:
        return None
    rng = np.random.default_rng(seed)
    exceedances = 1
    valid_rounds = 0
    for _ in range(rounds):
        perm_left: list[CaseEvaluation] = []
        perm_right: list[CaseEvaluation] = []
        swap_mask = rng.random(len(left)) >= 0.5
        for index, swap in enumerate(swap_mask):
            if swap:
                perm_left.append(right[index])
                perm_right.append(left[index])
            else:
                perm_left.append(left[index])
                perm_right.append(right[index])
        delta = _paired_metric_delta(perm_left, perm_right, metric_fn, higher_is_better=higher_is_better)
        if delta is None:
            continue
        valid_rounds += 1
        if abs(delta) >= abs(observed):
            exceedances += 1
    return round(exceedances / float(valid_rounds + 1), 4)


def _pairwise_comparisons(
    evaluations_by_runner: dict[str, list[CaseEvaluation]],
    seed: int,
) -> dict[str, BenchmarkComparison]:
    sounio_items = evaluations_by_runner[ModelFamily.sounio_hypercomplex.value]

    def aspects_mae(items: list[CaseEvaluation]) -> float | None:
        supported = [item.aspects_absolute_error for item in items if item.aspects_absolute_error is not None]
        if not supported:
            return None
        return float(np.mean(supported))

    def auc(items: list[CaseEvaluation]) -> float | None:
        supported_items = [item for item in items if item.case.segmentation_reference_available]
        if not supported_items:
            return None
        labels = [item.case.lesion_positive for item in supported_items]
        if len(set(labels)) < 2:
            return None
        scores = [item.global_confidence for item in supported_items]
        return float(_roc_auc(labels, scores))

    def coherence(items: list[CaseEvaluation]) -> float | None:
        if not items:
            return None
        return float(np.mean([item.coherence for item in items]))

    def dice(items: list[CaseEvaluation]) -> float | None:
        if not items:
            return None
        return float(np.mean([item.isles_dice for item in items]))

    def lesionwise_f1(items: list[CaseEvaluation]) -> float | None:
        if not items:
            return None
        return float(np.mean([item.isles_lesionwise_f1 for item in items]))

    def volume_difference(items: list[CaseEvaluation]) -> float | None:
        if not items:
            return None
        return float(np.mean([item.isles_absolute_volume_difference_ml for item in items]))

    def lesion_count_difference(items: list[CaseEvaluation]) -> float | None:
        if not items:
            return None
        return float(np.mean([item.isles_absolute_lesion_count_difference for item in items]))

    metric_specs = {
        "aspects_mae_gain": (aspects_mae, False, "Positive delta means lower ASPECTS MAE for Sounio."),
        "auc_gain": (auc, True, "Positive delta means higher case-level AUC for Sounio."),
        "coherence_gain": (coherence, True, "Positive delta means higher lesion/heatmap overlap for Sounio."),
        "dice_gain": (
            dice,
            True,
            "Positive delta means higher ISLES-style Dice for Sounio.",
        ),
        "lesionwise_f1_gain": (
            lesionwise_f1,
            True,
            "Positive delta means higher lesion-wise F1 for Sounio.",
        ),
        "volume_difference_gain": (
            volume_difference,
            False,
            "Positive delta means lower absolute lesion volume difference for Sounio.",
        ),
        "lesion_count_difference_gain": (
            lesion_count_difference,
            False,
            "Positive delta means lower absolute lesion count difference for Sounio.",
        ),
    }

    comparisons: dict[str, BenchmarkComparison] = {}
    for index, against in enumerate(
        (
            ModelFamily.python_3d_conventional,
            ModelFamily.julia_equivalent,
            ModelFamily.cpp_equivalent,
        )
    ):
        baseline_items = evaluations_by_runner[against.value]
        metric_payload: dict[str, ComparativeMetric] = {}
        for metric_index, (name, (metric_fn, higher_is_better, interpretation)) in enumerate(metric_specs.items()):
            delta, lower, upper = _paired_bootstrap_delta(
                sounio_items,
                baseline_items,
                metric_fn,
                higher_is_better=higher_is_better,
                seed=seed + 701 + index * 41 + metric_index * 7,
            )
            p_value = _paired_permutation_pvalue(
                sounio_items,
                baseline_items,
                metric_fn,
                higher_is_better=higher_is_better,
                seed=seed + 1103 + index * 41 + metric_index * 7,
            )
            metric_payload[name] = ComparativeMetric(
                delta=delta,
                lower_ci=lower,
                upper_ci=upper,
                p_value=p_value,
                interpretation=interpretation,
            )
        comparisons[against.value] = BenchmarkComparison(
            against=against,
            metrics=metric_payload,
            notes=["Paired bootstrap CI and paired permutation p-value computed on the fixed test split."],
        )
    return comparisons


def _aspects_bucket(score: int) -> str:
    if score <= 4:
        return "severe_0_4"
    if score <= 7:
        return "moderate_5_7"
    return "mild_8_10"


def _stratified_metrics(
    evaluations_by_runner: dict[str, list[CaseEvaluation]],
    seed: int,
) -> dict[str, dict[str, dict[str, dict[str, float | None]]]]:
    stratified: dict[str, dict[str, dict[str, dict[str, float | None]]]] = {}
    for model_index, (model_name, evaluations) in enumerate(evaluations_by_runner.items()):
        groups = {
            "lesion_present": [item for item in evaluations if item.case.lesion_positive],
            "no_lesion": [item for item in evaluations if not item.case.lesion_positive],
            "left_hemisphere": [
                item
                for item in evaluations
                if item.case.hemisphere_reference_available and item.case.hemisphere == "left"
            ],
            "right_hemisphere": [
                item
                for item in evaluations
                if item.case.hemisphere_reference_available and item.case.hemisphere == "right"
            ],
        }
        for bucket in ("severe_0_4", "moderate_5_7", "mild_8_10"):
            groups[bucket] = [
                item
                for item in evaluations
                if item.case.aspects_reference_available and _aspects_bucket(item.case.aspects_score) == bucket
            ]
        stratified[model_name] = {
            group_name: _aggregate_metrics(items, seed + model_index * 17 + group_index * 3)
            for group_index, (group_name, items) in enumerate(groups.items())
            if items
        }
    return stratified


def _evaluation_scope_summary(cases: list[LoadedBenchmarkCase]) -> dict[str, object]:
    reference_scope_counts: dict[str, int] = {}
    for case in cases:
        reference_scope_counts[case.reference_scope] = reference_scope_counts.get(case.reference_scope, 0) + 1
    return {
        "case_count": len(cases),
        "reference_scope_counts": reference_scope_counts,
        "segmentation_reference_cases": sum(int(case.segmentation_reference_available) for case in cases),
        "hemisphere_reference_cases": sum(int(case.hemisphere_reference_available) for case in cases),
        "region_reference_cases": sum(int(case.region_reference_available) for case in cases),
        "aspects_reference_cases": sum(int(case.aspects_reference_available) for case in cases),
    }


def _is_aisd_manifest(manifest: BenchmarkDatasetManifest) -> bool:
    dataset_name = manifest.dataset_name.lower()
    source = manifest.source.lower()
    split_policy = manifest.split_policy.lower()
    return "aisd" in dataset_name or "griffinliang/aisd" in source or "aisd" in split_policy


def _resolve_segmentation_threshold(
    raw_threshold: float | None,
    test_manifest: BenchmarkDatasetManifest,
) -> tuple[float, str]:
    if raw_threshold is not None:
        return normalize_segmentation_threshold(raw_threshold), "explicit"
    if _is_aisd_manifest(test_manifest):
        return AISD_EXPERIMENTAL_SEGMENTATION_THRESHOLD, "aisd-experimental-v1"
    return normalize_segmentation_threshold(None), "default-v1"


def _evaluate_case_from_heatmap(
    prepared_case: PreparedCase,
    heatmap: np.ndarray,
    segmentation_threshold: float,
) -> CaseEvaluation:
    case = prepared_case.case
    predicted_aspects, region_scores = aspects_from_probability_map(heatmap, case.hemisphere)
    binary_mask = binary_prediction_mask(heatmap, threshold=segmentation_threshold)
    lesionwise_f1, lesion_count_difference = lesionwise_f1_and_count_difference(case.lesion_mask, binary_mask)
    true_lesion_volume_ml, predicted_lesion_volume_ml = aligned_segmentation_volumes_ml(
        case.lesion_mask,
        binary_mask,
        case.voxel_volume_ml,
    )
    return CaseEvaluation(
        case=case,
        predicted_aspects=predicted_aspects,
        aspects_absolute_error=(
            abs(predicted_aspects - case.aspects_score) if case.aspects_reference_available else None
        ),
        global_confidence=float(np.quantile(heatmap, 0.995)),
        region_probabilities=[score.probability for score in region_scores],
        region_truth=[score.region in prepared_case.ground_truth_regions for score in region_scores],
        coherence=_dice(heatmap, case.lesion_mask),
        elapsed_ms=0.0,
        heatmap=heatmap,
        binary_mask=binary_mask,
        true_lesion_volume_ml=true_lesion_volume_ml,
        predicted_lesion_volume_ml=predicted_lesion_volume_ml,
        isles_dice=dice_score(case.lesion_mask, binary_mask),
        isles_absolute_volume_difference_ml=absolute_volume_difference_ml(
            case.lesion_mask,
            binary_mask,
            case.voxel_volume_ml,
        ),
        isles_absolute_lesion_count_difference=lesion_count_difference,
        isles_lesionwise_f1=lesionwise_f1,
    )


def _fixed_undersegmentation_gate_artifact(
    artifact: TrainedModelArtifact,
    *,
    gain: float,
    calibration_split: str | None,
    positive_case_count: int,
) -> TrainedModelArtifact:
    feature_count = len(SOUNIO_UNDERSEGMENTATION_GATE_FEATURE_NAMES)
    zeros = [0.0 for _ in range(feature_count)]
    ones = [1.0 for _ in range(feature_count)]
    return artifact.model_copy(
        deep=True,
        update={
            "undersegmentation_gate_feature_names": list(SOUNIO_UNDERSEGMENTATION_GATE_FEATURE_NAMES),
            "undersegmentation_gate_classifier_weights": list(zeros),
            "undersegmentation_gate_classifier_bias": 20.0,
            "undersegmentation_gate_classifier_feature_mean": list(zeros),
            "undersegmentation_gate_classifier_feature_std": list(ones),
            "undersegmentation_gate_regressor_weights": list(zeros),
            "undersegmentation_gate_regressor_bias": float(gain - 1.0),
            "undersegmentation_gate_regressor_feature_mean": list(zeros),
            "undersegmentation_gate_regressor_feature_std": list(ones),
            "undersegmentation_gate_gain_min": SOUNIO_UNDERSEGMENTATION_GATE_GAIN_MIN,
            "undersegmentation_gate_gain_max": SOUNIO_UNDERSEGMENTATION_GATE_GAIN_MAX,
            "undersegmentation_gate_activation_threshold": SOUNIO_UNDERSEGMENTATION_GATE_ACTIVATION_THRESHOLD,
            "undersegmentation_gate_policy": "oracle-candidate-grid-v1",
            "undersegmentation_gate_calibration_split": calibration_split,
            "undersegmentation_gate_positive_case_count": positive_case_count,
        },
    )


def _fit_undersegmentation_gate_regressor(
    X: np.ndarray,
    y: np.ndarray,
    reg: float = 0.05,
) -> tuple[np.ndarray, float, np.ndarray, np.ndarray]:
    if X.ndim != 2:
        raise ValueError("Expected a 2D design matrix for gate regression.")
    if X.shape[0] == 0:
        raise ValueError("Cannot fit gate regression without positive calibration cases.")
    feature_mean = X.mean(axis=0)
    feature_std = X.std(axis=0)
    feature_std = np.where(feature_std < 1e-5, 1.0, feature_std)
    standardized = (X - feature_mean) / feature_std
    centered_y = y - float(y.mean())
    ridge = reg * np.eye(standardized.shape[1], dtype=np.float32)
    weights = np.linalg.solve(standardized.T @ standardized + ridge, standardized.T @ centered_y)
    return weights.astype(np.float32), float(y.mean()), feature_mean.astype(np.float32), feature_std.astype(np.float32)


def _build_sounio_undersegmentation_calibration_cases(
    artifact: TrainedModelArtifact,
    calibration_cases: list[LoadedBenchmarkCase],
    target_shape: tuple[int, int, int],
    segmentation_threshold: float,
) -> list[UndersegmentationCalibrationCase]:
    if not calibration_cases:
        return []
    prepared_cases = [_prepare_benchmark_case(case, target_shape=target_shape) for case in calibration_cases]
    runner = build_runner(
        ModelFamily.sounio_hypercomplex,
        removed_component=artifact.removed_component,
        model_artifact=artifact,
    )
    calibration_rows: list[UndersegmentationCalibrationCase] = []
    for prepared_case in prepared_cases:
        feature_maps = extract_feature_maps(prepared_case.prepared_volume)
        region_vectors = region_feature_vectors(prepared_case.prepared_volume, feature_maps)
        rows = _artifact_feature_rows_from_vectors(region_vectors, artifact)
        region_scores = runner._artifact_region_scores(rows)
        heatmap, region_map = _base_heatmap_from_artifact(
            prepared_case.prepared_volume,
            feature_maps,
            artifact,
            region_scores,
        )
        underseg_support, support_gate = undersegmentation_support_maps(feature_maps, region_map)
        feature_row = undersegmentation_gate_feature_row(
            heatmap=heatmap,
            region_scores=region_scores,
            region_feature_vectors=region_vectors,
            burden_voxels=_predict_burden_voxels(rows, artifact),
            reference_threshold=float(artifact.burden_prior_reference_threshold or 0.25),
            underseg_support=underseg_support,
            support_gate=support_gate,
        )
        calibration_rows.append(
            UndersegmentationCalibrationCase(
                prepared_case=prepared_case,
                feature_row=feature_row,
                heatmap=heatmap,
                underseg_support=underseg_support,
                support_gate=support_gate,
                baseline_evaluation=_evaluate_case_from_heatmap(
                    prepared_case,
                    heatmap,
                    segmentation_threshold=segmentation_threshold,
                ),
            )
        )
    return calibration_rows


def _undersegmentation_gate_oracle_objective(
    baseline: CaseEvaluation,
    candidate: CaseEvaluation,
) -> tuple[bool, float]:
    if candidate.isles_dice < (baseline.isles_dice - 0.002):
        return False, float("-inf")
    objective = (
        float(candidate.isles_dice)
        + 0.10 * float(candidate.isles_lesionwise_f1)
        - 0.00002 * max(candidate.isles_absolute_volume_difference_ml - baseline.isles_absolute_volume_difference_ml, 0.0)
    )
    return True, objective


def _burden_prior_validation_objective(
    evaluations: list[CaseEvaluation],
) -> tuple[float, dict[str, float]]:
    if not evaluations:
        return float("-inf"), {
            "dice": 0.0,
            "lesionwise_f1": 0.0,
            "volume_alignment": 0.0,
            "count_alignment": 0.0,
        }
    dice = float(np.mean([item.isles_dice for item in evaluations]))
    lesionwise_f1 = float(np.mean([item.isles_lesionwise_f1 for item in evaluations]))
    volume_alignment = float(
        np.mean(
            [
                np.exp(
                    -abs(
                        np.log1p(item.predicted_lesion_volume_ml)
                        - np.log1p(item.true_lesion_volume_ml)
                    )
                )
                for item in evaluations
            ]
        )
    )
    count_alignment = float(
        np.mean(
            [
                1.0 / (1.0 + abs(item.isles_absolute_lesion_count_difference))
                for item in evaluations
            ]
        )
    )
    objective = (
        0.55 * dice
        + 0.25 * volume_alignment
        + 0.15 * lesionwise_f1
        + 0.05 * count_alignment
    )
    return objective, {
        "dice": round(dice, 4),
        "lesionwise_f1": round(lesionwise_f1, 4),
        "volume_alignment": round(volume_alignment, 4),
        "count_alignment": round(count_alignment, 4),
    }


def _burden_prior_case_calibration_score(
    baseline: CaseEvaluation,
    candidate: CaseEvaluation,
) -> tuple[float, bool]:
    true_volume = float(baseline.true_lesion_volume_ml)
    baseline_predicted = float(baseline.predicted_lesion_volume_ml)
    candidate_predicted = float(candidate.predicted_lesion_volume_ml)
    baseline_dice = float(baseline.isles_dice)
    candidate_dice = float(candidate.isles_dice)
    baseline_f1 = float(baseline.isles_lesionwise_f1)
    candidate_f1 = float(candidate.isles_lesionwise_f1)

    if true_volume <= 1e-6:
        false_positive_growth = max(candidate_predicted - baseline_predicted, 0.0)
        score = 1.8 * (candidate_dice - baseline_dice) - 0.03 * float(np.log1p(false_positive_growth))
        return score, False

    baseline_ratio = baseline_predicted / max(true_volume, 1e-6)
    candidate_ratio = candidate_predicted / max(true_volume, 1e-6)
    baseline_abs_error = abs(baseline_ratio - 1.0)
    candidate_abs_error = abs(candidate_ratio - 1.0)
    dice_delta = candidate_dice - baseline_dice
    f1_delta = candidate_f1 - baseline_f1

    clearly_undersegmented = baseline_ratio < 0.35 or (baseline_ratio < 0.6 and baseline_dice < 0.03)
    if clearly_undersegmented:
        recovered_ratio = max(candidate_ratio - baseline_ratio, 0.0)
        capped_recovery = min(recovered_ratio, max(1.0 - baseline_ratio, 0.0))
        overshoot_penalty = max(candidate_ratio - 1.35, 0.0)
        shrink_penalty = max(baseline_ratio - candidate_ratio, 0.0)
        score = (
            2.4 * max(dice_delta, 0.0)
            + 0.9 * max(f1_delta, 0.0)
            + 0.8 * capped_recovery
            - 0.8 * max(-dice_delta, 0.0)
            - 0.25 * shrink_penalty
            - 0.35 * overshoot_penalty
        )
        return score, True

    shrink_improvement = max(baseline_abs_error - candidate_abs_error, 0.0) if candidate_ratio <= baseline_ratio else 0.0
    extra_expansion = max(candidate_ratio - baseline_ratio, 0.0)
    score = (
        1.6 * dice_delta
        + 0.3 * max(f1_delta, 0.0)
        + 0.01 * shrink_improvement
        - 0.65 * max(-dice_delta, 0.0)
        - 0.08 * extra_expansion
    )
    return score, False


def _burden_prior_asymmetric_calibration_objective(
    baseline_evaluations: list[CaseEvaluation],
    candidate_evaluations: list[CaseEvaluation],
) -> tuple[float, dict[str, float]]:
    case_scores: list[float] = []
    undersegmented_scores: list[float] = []
    stable_scores: list[float] = []
    for baseline, candidate in zip(baseline_evaluations, candidate_evaluations, strict=True):
        score, undersegmented = _burden_prior_case_calibration_score(baseline, candidate)
        case_scores.append(score)
        if undersegmented:
            undersegmented_scores.append(score)
        else:
            stable_scores.append(score)

    if undersegmented_scores:
        objective = float(np.mean(undersegmented_scores)) + 0.02 * float(np.mean(stable_scores) if stable_scores else 0.0)
    else:
        objective = float(np.mean(stable_scores)) if stable_scores else float("-inf")
    dice = float(np.mean([item.isles_dice for item in candidate_evaluations])) if candidate_evaluations else 0.0
    lesionwise_f1 = (
        float(np.mean([item.isles_lesionwise_f1 for item in candidate_evaluations]))
        if candidate_evaluations
        else 0.0
    )
    volume_difference = (
        float(np.mean([item.isles_absolute_volume_difference_ml for item in candidate_evaluations]))
        if candidate_evaluations
        else 0.0
    )
    return objective, {
        "dice": round(dice, 4),
        "lesionwise_f1": round(lesionwise_f1, 4),
        "mean_volume_difference_ml": round(volume_difference, 4),
        "mean_case_score": round(objective, 4),
        "undersegmented_case_score": round(float(np.mean(undersegmented_scores)) if undersegmented_scores else 0.0, 4),
        "stable_case_score": round(float(np.mean(stable_scores)) if stable_scores else 0.0, 4),
        "undersegmented_case_count": float(len(undersegmented_scores)),
    }


class BenchmarkHarness:
    def __init__(self, storage: StorageManager):
        self.storage = storage

    def validate_pipeline_contract(self, runners: dict[str, BaseRunner]) -> None:
        versions = {runner.pipeline_version for runner in runners.values()}
        if versions != {PIPELINE_VERSION}:
            raise PipelineContractError(f"Mismatched pipeline versions: {sorted(versions)}")

    def _calibrate_burden_prior_artifacts(
        self,
        trained_model_artifacts: dict[str, TrainedModelArtifact],
        calibration_cases: list[LoadedBenchmarkCase],
        calibration_split: str | None,
        target_shape: tuple[int, int, int],
        segmentation_threshold: float,
        seed: int,
    ) -> dict[str, object]:
        candidate_strengths = [0.0, 0.1, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5]
        summary: dict[str, object] = {
            "policy": "fixed-v1",
            "split": calibration_split,
            "case_count": len(calibration_cases),
            "objective": "asymmetric expand-first score relative to strength=0.0 baseline",
            "candidate_strengths": candidate_strengths,
            "selected_strengths": {
                model_name: (
                    float(artifact.burden_prior_gain_strength)
                    if artifact.burden_prior_gain_strength is not None
                    else 0.3
                )
                for model_name, artifact in trained_model_artifacts.items()
                if artifact.model_family != ModelFamily.sounio_hypercomplex
            },
            "selected": {},
        }
        if not calibration_cases or calibration_split is None:
            return summary

        prepared_cases = [_prepare_benchmark_case(case, target_shape=target_shape) for case in calibration_cases]
        summary["policy"] = "val-calibrated-v1"
        selected: dict[str, dict[str, float | str | None]] = {}

        for index, (model_name, artifact) in enumerate(trained_model_artifacts.items()):
            if artifact.model_family == ModelFamily.sounio_hypercomplex:
                continue
            if not artifact.burden_prior_weights:
                continue
            baseline_artifact = artifact.model_copy(
                deep=True,
                update={
                    "burden_prior_gain_strength": 0.0,
                    "burden_prior_gain_strength_policy": "candidate-grid-v1",
                    "burden_prior_gain_calibration_split": calibration_split,
                },
            )
            baseline_runner = build_runner(
                baseline_artifact.model_family,
                removed_component=baseline_artifact.removed_component,
                model_artifact=baseline_artifact,
            )
            baseline_evaluations = [
                _evaluate_case(
                    prepared_case,
                    baseline_runner,
                    segmentation_threshold=segmentation_threshold,
                )
                for prepared_case in prepared_cases
            ]
            best_rank: tuple[float, float, float] | None = None
            best_payload: dict[str, float | str | None] | None = None
            best_artifact: TrainedModelArtifact | None = None
            for strength in candidate_strengths:
                candidate_artifact = artifact.model_copy(
                    deep=True,
                    update={
                        "burden_prior_gain_strength": strength,
                        "burden_prior_gain_strength_policy": "candidate-grid-v1",
                        "burden_prior_gain_calibration_split": calibration_split,
                    },
                )
                runner = build_runner(
                    candidate_artifact.model_family,
                    removed_component=candidate_artifact.removed_component,
                    model_artifact=candidate_artifact,
                )
                evaluations = [
                    _evaluate_case(
                        prepared_case,
                        runner,
                        segmentation_threshold=segmentation_threshold,
                    )
                    for prepared_case in prepared_cases
                ]
                objective, breakdown = _burden_prior_asymmetric_calibration_objective(
                    baseline_evaluations,
                    evaluations,
                )
                rank = (
                    round(objective, 6),
                    breakdown["dice"],
                    -strength,
                )
                if best_rank is not None and rank <= best_rank:
                    continue
                best_rank = rank
                best_payload = {
                    "strength": strength,
                    "objective": round(objective, 4),
                    **breakdown,
                }
                best_artifact = candidate_artifact
            if best_artifact is None or best_payload is None:
                continue
            notes = [
                note
                for note in best_artifact.notes
                if not note.startswith("Burden-prior gain strength selected")
            ]
            notes.append(
                "Burden-prior gain strength selected on validation split "
                f"'{calibration_split}' ({len(calibration_cases)} cases) with balanced segmentation objective."
            )
            best_artifact.notes = notes
            best_artifact.burden_prior_gain_strength_policy = "val-calibrated-v1"
            best_artifact.burden_prior_gain_calibration_split = calibration_split
            trained_model_artifacts[model_name] = best_artifact
            selected[model_name] = best_payload

        summary["selected"] = selected
        summary["selected_strengths"] = {
            model_name: (
                float(artifact.burden_prior_gain_strength)
                if artifact.burden_prior_gain_strength is not None
                else 0.3
            )
            for model_name, artifact in trained_model_artifacts.items()
            if artifact.model_family != ModelFamily.sounio_hypercomplex
        }
        return summary

    def _calibrate_sounio_undersegmentation_gate(
        self,
        trained_model_artifacts: dict[str, TrainedModelArtifact],
        calibration_cases: list[LoadedBenchmarkCase],
        calibration_split: str | None,
        target_shape: tuple[int, int, int],
        segmentation_threshold: float,
    ) -> dict[str, object]:
        sounio_key = next(
            (
                model_name
                for model_name, candidate in trained_model_artifacts.items()
                if candidate.model_family == ModelFamily.sounio_hypercomplex
            ),
            ModelFamily.sounio_hypercomplex.value,
        )
        artifact = trained_model_artifacts[sounio_key]
        summary: dict[str, object] = {
            "policy": SOUNIO_UNDERSEGMENTATION_GATE_NO_VAL_POLICY,
            "split": calibration_split,
            "case_count": len(calibration_cases),
            "positive_case_count": 0,
            "active": False,
            "feature_names": list(SOUNIO_UNDERSEGMENTATION_GATE_FEATURE_NAMES),
            "oracle_candidate_gains": list(SOUNIO_UNDERSEGMENTATION_GATE_ORACLE_GAINS),
            "selected_policy": SOUNIO_UNDERSEGMENTATION_GATE_NO_VAL_POLICY,
            "cases": [],
        }
        if not calibration_cases or calibration_split is None:
            trained_model_artifacts[sounio_key] = artifact.model_copy(
                deep=True,
                update=undersegmentation_gate_noop_payload(
                    policy=SOUNIO_UNDERSEGMENTATION_GATE_NO_VAL_POLICY,
                    calibration_split=None,
                    positive_case_count=0,
                ),
            )
            return summary

        calibration_rows = _build_sounio_undersegmentation_calibration_cases(
            artifact,
            calibration_cases=calibration_cases,
            target_shape=target_shape,
            segmentation_threshold=segmentation_threshold,
        )
        oracle_cases: list[dict[str, object]] = []
        feature_rows: list[np.ndarray] = []
        expand_labels: list[float] = []
        positive_feature_rows: list[np.ndarray] = []
        positive_gain_deltas: list[float] = []

        for calibration_row in calibration_rows:
            baseline = calibration_row.baseline_evaluation
            best_gain = 1.0
            best_rank: tuple[float, float, float, float] | None = None
            candidate_summaries: list[dict[str, float]] = []
            for gain in SOUNIO_UNDERSEGMENTATION_GATE_ORACLE_GAINS:
                candidate_artifact = _fixed_undersegmentation_gate_artifact(
                    artifact,
                    gain=gain,
                    calibration_split=calibration_split,
                    positive_case_count=0,
                )
                candidate_heatmap, active, _, _ = apply_undersegmentation_gate(
                    heatmap=calibration_row.heatmap,
                    underseg_support=calibration_row.underseg_support,
                    support_gate=calibration_row.support_gate,
                    feature_row=calibration_row.feature_row,
                    artifact=candidate_artifact,
                )
                candidate_eval = _evaluate_case_from_heatmap(
                    calibration_row.prepared_case,
                    candidate_heatmap,
                    segmentation_threshold=segmentation_threshold,
                )
                valid, objective = _undersegmentation_gate_oracle_objective(baseline, candidate_eval)
                candidate_summaries.append(
                    {
                        "gain": float(gain),
                        "valid": 1.0 if valid else 0.0,
                        "dice": round(candidate_eval.isles_dice, 4),
                        "lesionwise_f1": round(candidate_eval.isles_lesionwise_f1, 4),
                        "avd_ml": round(candidate_eval.isles_absolute_volume_difference_ml, 4),
                        "active": 1.0 if active else 0.0,
                        "objective": round(objective, 6) if np.isfinite(objective) else float("-inf"),
                    }
                )
                if not valid:
                    continue
                rank = (
                    round(objective, 6),
                    round(candidate_eval.isles_dice, 6),
                    round(candidate_eval.isles_lesionwise_f1, 6),
                    -float(gain),
                )
                if best_rank is None or rank > best_rank:
                    best_rank = rank
                    best_gain = float(gain)

            should_expand = best_gain > 1.0
            feature_rows.append(calibration_row.feature_row)
            expand_labels.append(1.0 if should_expand else 0.0)
            if should_expand:
                positive_feature_rows.append(calibration_row.feature_row)
                positive_gain_deltas.append(best_gain - 1.0)
            oracle_cases.append(
                {
                    "case_id": calibration_row.prepared_case.case.case_id,
                    "baseline_dice": round(baseline.isles_dice, 4),
                    "baseline_lesionwise_f1": round(baseline.isles_lesionwise_f1, 4),
                    "baseline_avd_ml": round(baseline.isles_absolute_volume_difference_ml, 4),
                    "oracle_gain": round(best_gain, 2),
                    "should_expand": should_expand,
                    "candidates": candidate_summaries,
                }
            )

        positive_case_count = int(sum(expand_labels))
        summary.update(
            {
                "positive_case_count": positive_case_count,
                "cases": oracle_cases,
            }
        )
        if positive_case_count < 5:
            trained_model_artifacts[sounio_key] = artifact.model_copy(
                deep=True,
                update=undersegmentation_gate_noop_payload(
                    policy=SOUNIO_UNDERSEGMENTATION_GATE_INSUFFICIENT_POLICY,
                    calibration_split=calibration_split,
                    positive_case_count=positive_case_count,
                ),
            )
            summary["policy"] = SOUNIO_UNDERSEGMENTATION_GATE_INSUFFICIENT_POLICY
            summary["selected_policy"] = SOUNIO_UNDERSEGMENTATION_GATE_INSUFFICIENT_POLICY
            return summary

        classifier_X = np.asarray(feature_rows, dtype=np.float32)
        classifier_y = np.asarray(expand_labels, dtype=np.float32)
        classifier_mean = classifier_X.mean(axis=0)
        classifier_std = classifier_X.std(axis=0)
        classifier_std = np.where(classifier_std < 1e-5, 1.0, classifier_std)
        classifier_standardized = (classifier_X - classifier_mean) / classifier_std
        classifier_weights, classifier_bias = _fit_binary_logistic(
            classifier_standardized,
            classifier_y,
            lr=0.12,
            epochs=600,
            reg=0.02,
        )
        regressor_weights, regressor_bias, regressor_mean, regressor_std = _fit_undersegmentation_gate_regressor(
            np.asarray(positive_feature_rows, dtype=np.float32),
            np.asarray(positive_gain_deltas, dtype=np.float32),
        )
        calibrated_artifact = artifact.model_copy(
            deep=True,
            update={
                "artifact_version": "regional-logistic-v4",
                "undersegmentation_gate_feature_names": list(SOUNIO_UNDERSEGMENTATION_GATE_FEATURE_NAMES),
                "undersegmentation_gate_classifier_weights": [float(value) for value in classifier_weights],
                "undersegmentation_gate_classifier_bias": float(classifier_bias),
                "undersegmentation_gate_classifier_feature_mean": [float(value) for value in classifier_mean],
                "undersegmentation_gate_classifier_feature_std": [float(value) for value in classifier_std],
                "undersegmentation_gate_regressor_weights": [float(value) for value in regressor_weights],
                "undersegmentation_gate_regressor_bias": float(regressor_bias),
                "undersegmentation_gate_regressor_feature_mean": [float(value) for value in regressor_mean],
                "undersegmentation_gate_regressor_feature_std": [float(value) for value in regressor_std],
                "undersegmentation_gate_gain_min": SOUNIO_UNDERSEGMENTATION_GATE_GAIN_MIN,
                "undersegmentation_gate_gain_max": SOUNIO_UNDERSEGMENTATION_GATE_GAIN_MAX,
                "undersegmentation_gate_activation_threshold": SOUNIO_UNDERSEGMENTATION_GATE_ACTIVATION_THRESHOLD,
                "undersegmentation_gate_policy": SOUNIO_UNDERSEGMENTATION_GATE_POLICY,
                "undersegmentation_gate_calibration_split": calibration_split,
                "undersegmentation_gate_positive_case_count": positive_case_count,
            },
        )
        calibrated_artifact.notes = [
            note
            for note in calibrated_artifact.notes
            if "undersegmentation gate" not in note.lower()
        ] + [
            "Sounio undersegmentation gate learned on validation split "
            f"'{calibration_split}' with oracle expand-only supervision."
        ]
        trained_model_artifacts[sounio_key] = calibrated_artifact
        summary["policy"] = SOUNIO_UNDERSEGMENTATION_GATE_POLICY
        summary["selected_policy"] = SOUNIO_UNDERSEGMENTATION_GATE_POLICY
        summary["active"] = True
        return summary

    def run_suite(self, request: BenchmarkRequest) -> BenchmarkRun:
        manifest_path = Path(request.dataset_manifest_path).expanduser().resolve()
        test_manifest_path = (
            Path(request.external_test_manifest_path).expanduser().resolve()
            if request.external_test_manifest_path
            else manifest_path
        )
        target_shape = normalize_target_shape(request.target_shape)
        external_validation = manifest_path != test_manifest_path
        _, manifest_issues = validate_benchmark_manifest(
            manifest_path,
            required_splits={request.train_split} if external_validation else {request.train_split, request.test_split},
        )
        if manifest_issues:
            raise ValueError("Invalid benchmark manifest:\n" + "\n".join(manifest_issues))
        _, test_manifest_issues = validate_benchmark_manifest(test_manifest_path, required_splits={request.test_split})
        if test_manifest_issues:
            raise ValueError("Invalid external test manifest:\n" + "\n".join(test_manifest_issues))
        manifest, train_cases = load_manifest_cases(manifest_path, split=request.train_split)
        test_manifest, test_cases = load_manifest_cases(test_manifest_path, split=request.test_split)
        segmentation_threshold, threshold_policy = _resolve_segmentation_threshold(
            request.segmentation_threshold,
            test_manifest,
        )
        calibration_split = "val" if request.train_split != "val" and request.test_split != "val" else None
        calibration_cases: list[LoadedBenchmarkCase] = []
        if calibration_split is not None:
            _, raw_calibration_cases = load_manifest_cases(manifest_path, split=calibration_split)
            train_ids = {case.case_id for case in train_cases}
            test_ids = {case.case_id for case in test_cases}
            calibration_cases = [
                case
                for case in raw_calibration_cases
                if case.case_id not in train_ids and case.case_id not in test_ids
            ]
        self._validate_split_contract(manifest_path, test_manifest_path, train_cases, test_cases, request)

        trained_model_artifacts = fit_all_model_artifacts(
            train_cases,
            dataset_manifest_path=str(manifest_path),
            trained_on_split=request.train_split,
            target_shape=target_shape,
        )
        burden_prior_calibration = self._calibrate_burden_prior_artifacts(
            trained_model_artifacts,
            calibration_cases=calibration_cases,
            calibration_split=calibration_split if calibration_cases else None,
            target_shape=target_shape,
            segmentation_threshold=segmentation_threshold,
            seed=request.seed,
        )
        undersegmentation_gate_calibration = self._calibrate_sounio_undersegmentation_gate(
            trained_model_artifacts,
            calibration_cases=calibration_cases,
            calibration_split=calibration_split if calibration_cases else None,
            target_shape=target_shape,
            segmentation_threshold=segmentation_threshold,
        )
        runners = comparative_runners(trained_model_artifacts)
        self.validate_pipeline_contract(runners)

        prepared_test_cases = [_prepare_benchmark_case(case, target_shape=target_shape) for case in test_cases]
        evaluations_by_runner: dict[str, list[CaseEvaluation]] = {name: [] for name in runners}
        for prepared_case in prepared_test_cases:
            for name, runner in runners.items():
                evaluations_by_runner[name].append(
                    _evaluate_case(
                        prepared_case,
                        runner,
                        segmentation_threshold=segmentation_threshold,
                    )
                )

        metrics = {
            name: _aggregate_metrics(evaluations, request.seed + index * 31)
            for index, (name, evaluations) in enumerate(evaluations_by_runner.items())
        }
        comparisons = _pairwise_comparisons(evaluations_by_runner, request.seed)
        stratified_metrics = _stratified_metrics(evaluations_by_runner, request.seed)
        leaderboard = build_leaderboard(metrics)
        training_summary = summarize_manifest_cohort(manifest, train_cases, manifest_path, request.train_split)
        test_summary = summarize_manifest_cohort(test_manifest, test_cases, test_manifest_path, request.test_split)
        cohort_shift = compare_cohort_summaries(training_summary, test_summary)
        failure_analysis = summarize_failures(evaluations_by_runner)
        evaluation_scope = {
            "training": _evaluation_scope_summary(train_cases),
            "test": _evaluation_scope_summary(test_cases),
        }

        full_sounio_auc = metrics[ModelFamily.sounio_hypercomplex.value]["auc"]["value"]
        full_sounio_coherence = metrics[ModelFamily.sounio_hypercomplex.value]["interpretability_coherence"]["value"]
        ablations, ablation_artifacts = self._run_ablations(
            train_cases=train_cases,
            prepared_test_cases=prepared_test_cases,
            seed=request.seed,
            full_auc=float(full_sounio_auc if isinstance(full_sounio_auc, (int, float)) else 0.0),
            full_coherence=float(full_sounio_coherence if isinstance(full_sounio_coherence, (int, float)) else 0.0),
            manifest_path=manifest_path,
            train_split=request.train_split,
            target_shape=target_shape,
            segmentation_threshold=segmentation_threshold,
            calibration_cases=calibration_cases,
            calibration_split=calibration_split if calibration_cases else None,
        )

        run_id = uuid.uuid4().hex
        artifacts = self._write_artifacts(
            run_id=run_id,
            manifest_path=manifest_path,
            test_manifest_path=test_manifest_path,
            manifest_payload=manifest.model_dump(),
            test_manifest_payload=test_manifest.model_dump(),
            train_cases=train_cases,
            test_cases=test_cases,
            trained_model_artifacts=trained_model_artifacts,
            ablation_artifacts=ablation_artifacts,
            evaluations_by_runner=evaluations_by_runner,
            metrics=metrics,
            comparisons=comparisons,
            stratified_metrics=stratified_metrics,
            leaderboard=leaderboard,
            training_summary=training_summary,
            test_summary=test_summary,
            evaluation_scope=evaluation_scope,
            cohort_shift=cohort_shift,
            failure_analysis=failure_analysis,
            ablations=ablations,
            request=request,
            segmentation_threshold=segmentation_threshold,
            threshold_policy=threshold_policy,
            burden_prior_calibration=burden_prior_calibration,
            undersegmentation_gate_calibration=undersegmentation_gate_calibration,
        )

        fairness_checks = [
            f"Training manifest: {manifest_path}",
            f"Test manifest: {test_manifest_path}",
            f"Training split: {request.train_split}",
            f"Test split: {request.test_split}",
            f"Training cases: {len(train_cases)}",
            f"Test cases: {len(test_cases)}",
            (
                "Training and test case ids are disjoint."
                if not external_validation
                else "Training and test cases come from separate manifests (external validation)."
            ),
            "All runners were trained on the same manifest and training split.",
            "All runners were evaluated on the same explicit test case list.",
            "All runners use the same preprocess pipeline version.",
            "All runners use the same atlas registration contract.",
            "All runners use the same fixed compute budget profile.",
            "All trained arms use the same lesion-burden-aware regional fitting objective.",
            f"Target shape: {list(target_shape)}",
            f"Segmentation threshold: {segmentation_threshold:.2f}",
            f"Segmentation threshold policy: {threshold_policy}",
            (
                "Burden-prior gain strength calibrated on validation split "
                f"'{calibration_split}' ({len(calibration_cases)} cases)."
                if burden_prior_calibration["policy"] == "val-calibrated-v1"
                else "Burden-prior gain strength uses fixed fallback because no disjoint validation split was available."
            ),
            (
                "Sounio undersegmentation gate learned on validation split "
                f"'{undersegmentation_gate_calibration['split']}' "
                f"({undersegmentation_gate_calibration['case_count']} cases, "
                f"{undersegmentation_gate_calibration['positive_case_count']} positive oracle cases)."
                if undersegmentation_gate_calibration["active"]
                else "Sounio undersegmentation gate stayed no-op "
                f"({undersegmentation_gate_calibration['policy']})."
            ),
            FAIRNESS_POLICY,
        ]
        if threshold_policy == "aisd-experimental-v1":
            fairness_checks.append(
                "AISD experimental default threshold 0.25 applied to balance Dice gains against volume inflation."
            )
        if any(
            any(
                case.metadata.get(key)
                for key in ("cta_path", "ctp_path", "perfusion_maps", "clinical_baseline_csv", "outcome_csv")
            )
            for case in train_cases + test_cases
        ):
            fairness_checks.append(
                "Manifest exposes CTA/CTP/clinical fields, but current benchmark arms consume NCCT-derived features only."
            )
        if evaluation_scope["test"]["aspects_reference_cases"] < len(test_cases):
            fairness_checks.append(
                "Test export is segmentation-first: ASPECTS, hemisphere and atlas-region truth are unsupported for some cases and reported as n/a."
            )
        if evaluation_scope["training"]["aspects_reference_cases"] < len(train_cases):
            fairness_checks.append(
                "Training export lacks validated ASPECTS/hemisphere labels for some cases; regional supervision is operational and should not be claimed as benchmark-grade truth."
            )
        if cohort_shift["source_changed"] or cohort_shift["dataset_changed"]:
            fairness_checks.append("Training and test cohorts differ in source provenance; review cohort_shift artifact before claims.")

        run = BenchmarkRun(
            run_id=run_id,
            dataset_version=(
                manifest.dataset_version
                if not external_validation
                else f"{manifest.dataset_version}__to__{test_manifest.dataset_version}"
            ),
            pipeline_version=PIPELINE_VERSION,
            language_stack=LanguageStack.multi,
            model_family=ModelFamily.benchmark_suite,
            compute_budget={
                "budget_profile": "matched-v1",
                "device": "cpu",
                "train_cases": len(train_cases),
                "test_cases": len(test_cases),
                "dataset_manifest_path": str(manifest_path),
                "test_manifest_path": str(test_manifest_path),
                "external_validation": external_validation,
                "target_shape": list(target_shape),
                "segmentation_threshold": segmentation_threshold,
                "segmentation_threshold_policy": threshold_policy,
                "training_weighting": "lesion-burden-aware-v1",
                "burden_prior_gain_strength_policy": burden_prior_calibration["policy"],
                "burden_prior_gain_calibration_split": burden_prior_calibration["split"],
                "burden_prior_gain_calibration_cases": burden_prior_calibration["case_count"],
                "burden_prior_gain_strengths": burden_prior_calibration["selected_strengths"],
                "sounio_undersegmentation_gate_policy": undersegmentation_gate_calibration["policy"],
                "sounio_undersegmentation_gate_calibration_split": undersegmentation_gate_calibration["split"],
                "sounio_undersegmentation_gate_calibration_cases": undersegmentation_gate_calibration["case_count"],
                "sounio_undersegmentation_gate_positive_cases": undersegmentation_gate_calibration["positive_case_count"],
                "sounio_undersegmentation_gate_active": undersegmentation_gate_calibration["active"],
            },
            evaluation_scope=evaluation_scope,
            metrics=metrics,
            comparisons=comparisons,
            stratified_metrics=stratified_metrics,
            leaderboard=leaderboard,
            artifacts=artifacts,
            fairness_checks=fairness_checks,
            ablations=ablations,
        )
        self.storage.save_benchmark_run(run)
        return run

    def _validate_split_contract(
        self,
        manifest_path: Path,
        test_manifest_path: Path,
        train_cases: list[LoadedBenchmarkCase],
        test_cases: list[LoadedBenchmarkCase],
        request: BenchmarkRequest,
    ) -> None:
        if not train_cases:
            raise ValueError(f"No training cases found for split '{request.train_split}' in {manifest_path}.")
        if not test_cases:
            raise ValueError(f"No test cases found for split '{request.test_split}' in {test_manifest_path}.")
        if manifest_path != test_manifest_path:
            return
        train_ids = {case.case_id for case in train_cases}
        test_ids = {case.case_id for case in test_cases}
        overlap = sorted(train_ids & test_ids)
        if overlap:
            raise ValueError(
                "Training and test splits must be disjoint. "
                f"Found overlap in {manifest_path}: {', '.join(overlap[:5])}"
            )

    def _run_ablations(
        self,
        train_cases: list[LoadedBenchmarkCase],
        prepared_test_cases: list[PreparedCase],
        seed: int,
        full_auc: float,
        full_coherence: float,
        manifest_path: Path,
        train_split: str,
        target_shape: tuple[int, int, int],
        segmentation_threshold: float,
        calibration_cases: list[LoadedBenchmarkCase],
        calibration_split: str | None,
    ) -> tuple[list[AblationResult], dict[str, TrainedModelArtifact]]:
        items: list[AblationResult] = []
        artifacts: dict[str, TrainedModelArtifact] = {}
        prepared_rows = prepare_training_rows(train_cases, target_shape=target_shape)
        for index, component in enumerate(("hypercomplex_phase", "hypercomplex_energy", "asymmetry_channel")):
            artifact = fit_model_artifact(
                ModelFamily.sounio_hypercomplex,
                train_cases,
                dataset_manifest_path=str(manifest_path),
                trained_on_split=train_split,
                removed_component=component,
                prepared_rows=prepared_rows,
            )
            calibration_bucket = {component: artifact}
            self._calibrate_sounio_undersegmentation_gate(
                calibration_bucket,
                calibration_cases=calibration_cases,
                calibration_split=calibration_split if calibration_cases else None,
                target_shape=target_shape,
                segmentation_threshold=segmentation_threshold,
            )
            artifact = calibration_bucket[component]
            artifacts[component] = artifact
            runner = build_runner(
                ModelFamily.sounio_hypercomplex,
                removed_component=component,
                model_artifact=artifact,
            )
            evaluations = [
                _evaluate_case(prepared_case, runner, segmentation_threshold=segmentation_threshold)
                for prepared_case in prepared_test_cases
            ]
            metrics = _aggregate_metrics(evaluations, seed + 503 + index * 13)
            auc_value = metrics["auc"]["value"]
            coherence_value = metrics["interpretability_coherence"]["value"]
            auc = float(auc_value) if isinstance(auc_value, (int, float)) else None
            coherence = float(coherence_value) if isinstance(coherence_value, (int, float)) else 0.0
            items.append(
                AblationResult(
                    ablation_name=f"sounio_without_{component}",
                    removed_component=component,
                    metric_delta=round(full_auc - auc, 4) if auc is not None else 0.0,
                    interpretability_delta=round(full_coherence - coherence, 4),
                    notes="Positive deltas mean the removed component hurts the trained Sounio arm.",
                )
            )
        return items, artifacts

    def _write_artifacts(
        self,
        run_id: str,
        manifest_path: Path,
        test_manifest_path: Path,
        manifest_payload: dict,
        test_manifest_payload: dict,
        train_cases: list[LoadedBenchmarkCase],
        test_cases: list[LoadedBenchmarkCase],
        trained_model_artifacts: dict[str, TrainedModelArtifact],
        ablation_artifacts: dict[str, TrainedModelArtifact],
        evaluations_by_runner: dict[str, list[CaseEvaluation]],
        metrics: dict[str, dict[str, dict[str, float | None]]],
        comparisons: dict[str, BenchmarkComparison],
        stratified_metrics: dict[str, dict[str, dict[str, dict[str, float | None]]]],
        leaderboard: dict[str, object],
        training_summary: dict[str, object],
        test_summary: dict[str, object],
        evaluation_scope: dict[str, object],
        cohort_shift: dict[str, object],
        failure_analysis: dict[str, object],
        ablations: list[AblationResult],
        request: BenchmarkRequest,
        segmentation_threshold: float,
        threshold_policy: str,
        burden_prior_calibration: dict[str, object],
        undersegmentation_gate_calibration: dict[str, object],
    ) -> list[ArtifactDescriptor]:
        artifacts: list[ArtifactDescriptor] = []
        target_shape = normalize_target_shape(request.target_shape)
        manifest_copy_path = self.storage.write_artifact_json(
            f"{run_id}/train_manifest.json",
            {
                "source_manifest_path": str(manifest_path),
                "request": request.model_dump(),
                "dataset_manifest": manifest_payload,
                "training_cases": [case.case_id for case in train_cases],
            },
        )
        artifacts.append(
            ArtifactDescriptor(
                name="train_manifest",
                kind="json",
                path=manifest_copy_path,
                description="Training manifest and explicit training case list used for this run.",
            )
        )
        test_manifest_copy_path = self.storage.write_artifact_json(
            f"{run_id}/test_manifest.json",
            {
                "source_manifest_path": str(test_manifest_path),
                "request": request.model_dump(),
                "dataset_manifest": test_manifest_payload,
                "test_cases": [case.case_id for case in test_cases],
            },
        )
        artifacts.append(
            ArtifactDescriptor(
                name="test_manifest",
                kind="json",
                path=test_manifest_copy_path,
                description="Test manifest and explicit test case list used for this run.",
            )
        )

        protocol_path = self.storage.write_artifact_json(
            f"{run_id}/research_protocol.json",
            build_protocol_payload(
                request=request,
                train_manifest_path=manifest_path,
                train_manifest=BenchmarkDatasetManifest.model_validate(manifest_payload),
                test_manifest_path=test_manifest_path,
                test_manifest=BenchmarkDatasetManifest.model_validate(test_manifest_payload),
            ),
        )
        artifacts.append(
            ArtifactDescriptor(
                name="research_protocol",
                kind="json",
                path=protocol_path,
                description="Structured study protocol with dataset provenance, metrics and statistical plan.",
            )
        )
        reproducibility_path = self.storage.write_artifact_json(
            f"{run_id}/reproducibility_snapshot.json",
            build_reproducibility_snapshot(Path.cwd()),
        )
        artifacts.append(
            ArtifactDescriptor(
                name="reproducibility_snapshot",
                kind="json",
                path=reproducibility_path,
                description="Environment, package, git and Sounio runtime snapshot for exact run reproduction.",
            )
        )
        claim_path = self.storage.write_artifact_json(
            f"{run_id}/claim_checklist.json",
            build_claim_checklist(
                request=request,
                run=BenchmarkRun(
                    run_id=run_id,
                    dataset_version=(
                        manifest_payload["dataset_version"]
                        if manifest_path == test_manifest_path
                        else f"{manifest_payload['dataset_version']}__to__{test_manifest_payload['dataset_version']}"
                    ),
                    pipeline_version=PIPELINE_VERSION,
                    language_stack=LanguageStack.multi,
                    model_family=ModelFamily.benchmark_suite,
                    compute_budget={
                        "budget_profile": "matched-v1",
                        "device": "cpu",
                        "train_cases": len(train_cases),
                        "test_cases": len(test_cases),
                        "external_validation": manifest_path != test_manifest_path,
                        "target_shape": list(target_shape),
                        "segmentation_threshold": segmentation_threshold,
                        "segmentation_threshold_policy": threshold_policy,
                        "training_weighting": "lesion-burden-aware-v1",
                        "burden_prior_gain_strength_policy": burden_prior_calibration["policy"],
                        "burden_prior_gain_calibration_split": burden_prior_calibration["split"],
                        "burden_prior_gain_calibration_cases": burden_prior_calibration["case_count"],
                        "burden_prior_gain_strengths": burden_prior_calibration["selected_strengths"],
                        "sounio_undersegmentation_gate_policy": undersegmentation_gate_calibration["policy"],
                        "sounio_undersegmentation_gate_calibration_split": undersegmentation_gate_calibration["split"],
                        "sounio_undersegmentation_gate_calibration_cases": undersegmentation_gate_calibration["case_count"],
                        "sounio_undersegmentation_gate_positive_cases": undersegmentation_gate_calibration["positive_case_count"],
                        "sounio_undersegmentation_gate_active": undersegmentation_gate_calibration["active"],
                    },
                    evaluation_scope=evaluation_scope,
                    metrics=metrics,
                    comparisons=comparisons,
                    stratified_metrics=stratified_metrics,
                    artifacts=[],
                    fairness_checks=[],
                    ablations=ablations,
                ),
                training_summary=training_summary,
                test_summary=test_summary,
                cohort_shift=cohort_shift,
            ),
        )
        artifacts.append(
            ArtifactDescriptor(
                name="claim_checklist",
                kind="json",
                path=claim_path,
                description="Operational CLAIM-aligned reporting audit for the current benchmark run.",
            )
        )
        tripod_path = self.storage.write_artifact_json(
            f"{run_id}/tripod_ai_checklist.json",
            build_tripod_ai_checklist(
                request=request,
                run=BenchmarkRun(
                    run_id=run_id,
                    dataset_version=(
                        manifest_payload["dataset_version"]
                        if manifest_path == test_manifest_path
                        else f"{manifest_payload['dataset_version']}__to__{test_manifest_payload['dataset_version']}"
                    ),
                    pipeline_version=PIPELINE_VERSION,
                    language_stack=LanguageStack.multi,
                    model_family=ModelFamily.benchmark_suite,
                    compute_budget={
                        "budget_profile": "matched-v1",
                        "device": "cpu",
                        "train_cases": len(train_cases),
                        "test_cases": len(test_cases),
                        "external_validation": manifest_path != test_manifest_path,
                        "target_shape": list(target_shape),
                        "segmentation_threshold": segmentation_threshold,
                        "segmentation_threshold_policy": threshold_policy,
                        "training_weighting": "lesion-burden-aware-v1",
                        "burden_prior_gain_strength_policy": burden_prior_calibration["policy"],
                        "burden_prior_gain_calibration_split": burden_prior_calibration["split"],
                        "burden_prior_gain_calibration_cases": burden_prior_calibration["case_count"],
                        "burden_prior_gain_strengths": burden_prior_calibration["selected_strengths"],
                        "sounio_undersegmentation_gate_policy": undersegmentation_gate_calibration["policy"],
                        "sounio_undersegmentation_gate_calibration_split": undersegmentation_gate_calibration["split"],
                        "sounio_undersegmentation_gate_calibration_cases": undersegmentation_gate_calibration["case_count"],
                        "sounio_undersegmentation_gate_positive_cases": undersegmentation_gate_calibration["positive_case_count"],
                        "sounio_undersegmentation_gate_active": undersegmentation_gate_calibration["active"],
                    },
                    evaluation_scope=evaluation_scope,
                    metrics=metrics,
                    comparisons=comparisons,
                    stratified_metrics=stratified_metrics,
                    artifacts=[],
                    fairness_checks=[],
                    ablations=ablations,
                ),
                training_summary=training_summary,
                test_summary=test_summary,
            ),
        )
        artifacts.append(
            ArtifactDescriptor(
                name="tripod_ai_checklist",
                kind="json",
                path=tripod_path,
                description="Operational TRIPOD-AI-aligned reporting audit for the current benchmark run.",
            )
        )
        case_export_path = write_case_level_csv(
            evaluations_by_runner,
            self.storage.artifact_dir / run_id / "case_level_predictions.csv",
        )
        artifacts.append(
            ArtifactDescriptor(
                name="case_level_predictions",
                kind="csv",
                path=case_export_path,
                description="Per-case benchmark outputs for every arm, suitable for audit and secondary analysis.",
            )
        )

        comparisons_path = self.storage.write_artifact_json(
            f"{run_id}/comparisons.json",
            {name: comparison.model_dump(mode="json") for name, comparison in comparisons.items()},
        )
        artifacts.append(
            ArtifactDescriptor(
                name="pairwise_comparisons",
                kind="json",
                path=comparisons_path,
                description="Paired statistical comparison between Sounio and each baseline arm.",
            )
        )
        stratified_path = self.storage.write_artifact_json(
            f"{run_id}/stratified_metrics.json",
            stratified_metrics,
        )
        artifacts.append(
            ArtifactDescriptor(
                name="stratified_metrics",
                kind="json",
                path=stratified_path,
                description="Metrics stratified by lesion presence, hemisphere and ASPECTS severity bucket.",
            )
        )
        leaderboard_path = self.storage.write_artifact_json(
            f"{run_id}/leaderboard.json",
            leaderboard,
        )
        artifacts.append(
            ArtifactDescriptor(
                name="leaderboard",
                kind="json",
                path=leaderboard_path,
                description="Composite ranking across core segmentation and ASPECTS metrics.",
            )
        )
        training_summary_path = self.storage.write_artifact_json(
            f"{run_id}/training_cohort_summary.json",
            training_summary,
        )
        artifacts.append(
            ArtifactDescriptor(
                name="training_cohort_summary",
                kind="json",
                path=training_summary_path,
                description="Summary of the training cohort composition, metadata coverage and modality availability.",
            )
        )
        test_summary_path = self.storage.write_artifact_json(
            f"{run_id}/test_cohort_summary.json",
            test_summary,
        )
        artifacts.append(
            ArtifactDescriptor(
                name="test_cohort_summary",
                kind="json",
                path=test_summary_path,
                description="Summary of the test cohort composition, metadata coverage and modality availability.",
            )
        )
        cohort_shift_path = self.storage.write_artifact_json(
            f"{run_id}/cohort_shift.json",
            cohort_shift,
        )
        artifacts.append(
            ArtifactDescriptor(
                name="cohort_shift",
                kind="json",
                path=cohort_shift_path,
                description="High-level train-vs-test cohort shift summary for external or internal validation.",
            )
        )
        failure_analysis_path = self.storage.write_artifact_json(
            f"{run_id}/failure_analysis.json",
            failure_analysis,
        )
        artifacts.append(
            ArtifactDescriptor(
                name="failure_analysis",
                kind="json",
                path=failure_analysis_path,
                description="Worst cases per arm and largest Sounio-vs-baseline disagreements.",
            )
        )
        burden_prior_calibration_path = self.storage.write_artifact_json(
            f"{run_id}/burden_prior_calibration.json",
            burden_prior_calibration,
        )
        artifacts.append(
            ArtifactDescriptor(
                name="burden_prior_calibration",
                kind="json",
                path=burden_prior_calibration_path,
                description="Validation-time calibration summary for burden-prior gain strength.",
            )
        )
        undersegmentation_gate_calibration_path = self.storage.write_artifact_json(
            f"{run_id}/undersegmentation_gate_calibration.json",
            undersegmentation_gate_calibration,
        )
        artifacts.append(
            ArtifactDescriptor(
                name="undersegmentation_gate_calibration",
                kind="json",
                path=undersegmentation_gate_calibration_path,
                description="Validation-time oracle calibration summary for the Sounio undersegmentation gate.",
            )
        )

        summary_figure_path = self.storage.artifact_dir / run_id / "benchmark_summary.png"
        create_metric_summary_figure(metrics, summary_figure_path)
        artifacts.append(
            ArtifactDescriptor(
                name="benchmark_summary",
                kind="png",
                path=str(summary_figure_path),
                description="Summary figure comparing ASPECTS MAE, AUC and Dice across benchmark arms.",
            )
        )

        calibration_figure_path = self.storage.artifact_dir / run_id / "calibration_curves.png"
        create_calibration_figure(evaluations_by_runner, calibration_figure_path)
        artifacts.append(
            ArtifactDescriptor(
                name="calibration_curves",
                kind="png",
                path=str(calibration_figure_path),
                description="Region-level calibration curves for all benchmark arms.",
            )
        )
        effect_size_figure_path = self.storage.artifact_dir / run_id / "effect_sizes.png"
        create_effect_size_figure(comparisons, effect_size_figure_path)
        artifacts.append(
            ArtifactDescriptor(
                name="effect_sizes",
                kind="png",
                path=str(effect_size_figure_path),
                description="Pairwise AUC gain confidence intervals for Sounio versus each baseline.",
            )
        )

        for model_name, artifact in trained_model_artifacts.items():
            artifact_path = self.storage.write_artifact_json(
                f"{run_id}/models/{model_name}.json",
                artifact.model_dump(),
            )
            artifacts.append(
                ArtifactDescriptor(
                    name=f"model_{model_name}",
                    kind="trained-model",
                    path=artifact_path,
                    description=f"Fitted {model_name} artifact used for test-set evaluation.",
                )
            )

        for component, artifact in ablation_artifacts.items():
            artifact_path = self.storage.write_artifact_json(
                f"{run_id}/ablations/{component}.json",
                artifact.model_dump(),
            )
            artifacts.append(
                ArtifactDescriptor(
                    name=f"ablation_{component}",
                    kind="trained-model-ablation",
                    path=artifact_path,
                    description=f"Fitted Sounio ablation artifact with {component} removed.",
                )
            )

        sounio_evaluations = evaluations_by_runner[ModelFamily.sounio_hypercomplex.value]
        def qualitative_priority(item: CaseEvaluation, *, strongest: bool) -> tuple[float, float]:
            aspects_penalty = float(item.aspects_absolute_error or 0)
            if item.aspects_absolute_error is None:
                aspects_penalty = 0.0
            return (
                aspects_penalty - item.coherence if strongest else aspects_penalty + (1.0 - item.coherence),
                item.isles_absolute_volume_difference_ml if strongest else -item.isles_absolute_volume_difference_ml,
            )
        strongest = min(
            sounio_evaluations,
            key=lambda item: qualitative_priority(item, strongest=True),
        )
        weakest = max(
            sounio_evaluations,
            key=lambda item: qualitative_priority(item, strongest=False),
        )
        for label, item in (("strong_case", strongest), ("weak_case", weakest)):
            figure_path = self.storage.artifact_dir / run_id / f"{label}.png"
            create_case_figure(
                item.case.volume,
                item.case.lesion_mask,
                item.heatmap,
                figure_path,
                f"{label.replace('_', ' ').title()} - {item.case.case_id}",
            )
            artifacts.append(
                ArtifactDescriptor(
                    name=label,
                    kind="png",
                    path=str(figure_path),
                    description=f"Sounio {label.replace('_', ' ')} example for qualitative review.",
                )
            )

        report_stub = BenchmarkRun(
            run_id=run_id,
            dataset_version=manifest_payload["dataset_version"],
            pipeline_version=PIPELINE_VERSION,
            language_stack=LanguageStack.multi,
            model_family=ModelFamily.benchmark_suite,
            compute_budget={
                "budget_profile": "matched-v1",
                "device": "cpu",
                "train_cases": len(train_cases),
                "test_cases": len(test_cases),
                "target_shape": list(target_shape),
                "segmentation_threshold": segmentation_threshold,
                "segmentation_threshold_policy": threshold_policy,
                "training_weighting": "lesion-burden-aware-v1",
                "burden_prior_gain_strength_policy": burden_prior_calibration["policy"],
                "burden_prior_gain_calibration_split": burden_prior_calibration["split"],
                "burden_prior_gain_calibration_cases": burden_prior_calibration["case_count"],
                "burden_prior_gain_strengths": burden_prior_calibration["selected_strengths"],
                "sounio_undersegmentation_gate_policy": undersegmentation_gate_calibration["policy"],
                "sounio_undersegmentation_gate_calibration_split": undersegmentation_gate_calibration["split"],
                "sounio_undersegmentation_gate_calibration_cases": undersegmentation_gate_calibration["case_count"],
                "sounio_undersegmentation_gate_positive_cases": undersegmentation_gate_calibration["positive_case_count"],
                "sounio_undersegmentation_gate_active": undersegmentation_gate_calibration["active"],
            },
            evaluation_scope=evaluation_scope,
            metrics=metrics,
            comparisons=comparisons,
            stratified_metrics=stratified_metrics,
            leaderboard=leaderboard,
            artifacts=artifacts,
            fairness_checks=[
                f"Source manifest: {manifest_path}",
                f"Training split: {request.train_split}",
                f"Test split: {request.test_split}",
                f"Target shape: {list(target_shape)}",
                f"Segmentation threshold: {segmentation_threshold:.2f}",
                f"Segmentation threshold policy: {threshold_policy}",
                (
                    "Burden-prior gain strength calibrated on validation split "
                    f"'{burden_prior_calibration['split']}' ({burden_prior_calibration['case_count']} cases)."
                    if burden_prior_calibration["policy"] == "val-calibrated-v1"
                    else "Burden-prior gain strength uses fixed fallback because no disjoint validation split was available."
                ),
                (
                    "Sounio undersegmentation gate learned on validation split "
                    f"'{undersegmentation_gate_calibration['split']}' "
                    f"({undersegmentation_gate_calibration['case_count']} cases, "
                    f"{undersegmentation_gate_calibration['positive_case_count']} positive oracle cases)."
                    if undersegmentation_gate_calibration["active"]
                    else "Sounio undersegmentation gate stayed no-op "
                    f"({undersegmentation_gate_calibration['policy']})."
                ),
                "Training and test case ids are disjoint.",
                "Same preprocessing and atlas registration for all arms.",
                "Same compute budget profile for all arms.",
                "Same lesion-burden-aware regional fitting objective for all trained arms.",
            ],
            ablations=ablations,
        )
        report_path = self.storage.write_artifact_text(f"{run_id}/report.md", render_report(report_stub))
        artifacts.append(
            ArtifactDescriptor(
                name="technical_report",
                kind="markdown",
                path=report_path,
                description="Paper-style summary of the benchmark evidence.",
            )
        )
        manuscript_path = self.storage.write_artifact_text(
            f"{run_id}/manuscript_draft.md",
            render_manuscript_draft(
                run=report_stub,
                request=request,
                training_summary=training_summary,
                test_summary=test_summary,
                cohort_shift=cohort_shift,
            ),
        )
        artifacts.append(
            ArtifactDescriptor(
                name="manuscript_draft",
                kind="markdown",
                path=manuscript_path,
                description="Structured manuscript draft generated from the benchmark evidence package.",
            )
        )
        return artifacts
