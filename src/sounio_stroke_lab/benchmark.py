from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from sounio_stroke_lab.atlas import affected_regions_from_mask, aspects_from_probability_map, build_aspects_atlas
from sounio_stroke_lab.cohort_analysis import compare_cohort_summaries, summarize_manifest_cohort
from sounio_stroke_lab.case_exports import write_case_level_csv
from sounio_stroke_lab.config import FAIRNESS_POLICY, PIPELINE_VERSION
from sounio_stroke_lab.dataset_manifest import LoadedBenchmarkCase, load_manifest_cases, validate_benchmark_manifest
from sounio_stroke_lab.evaluation import (
    absolute_volume_difference_ml,
    binary_prediction_mask,
    dice_score,
    lesionwise_f1_and_count_difference,
)
from sounio_stroke_lab.failure_analysis import summarize_failures
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
from sounio_stroke_lab.runners import BaseRunner, build_runner, comparative_runners
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
    RecordOwnerType,
    TrainedModelArtifact,
)
from sounio_stroke_lab.storage import StorageManager
from sounio_stroke_lab.trainable_models import fit_all_model_artifacts, fit_model_artifact, prepare_training_rows


@dataclass
class CaseEvaluation:
    case: LoadedBenchmarkCase
    predicted_aspects: int
    aspects_absolute_error: int
    global_confidence: float
    region_probabilities: list[float]
    region_truth: list[bool]
    coherence: float
    elapsed_ms: float
    heatmap: np.ndarray
    binary_mask: np.ndarray
    isles_dice: float
    isles_absolute_volume_difference_ml: float
    isles_absolute_lesion_count_difference: int
    isles_lesionwise_f1: float


@dataclass
class PreparedCase:
    case: LoadedBenchmarkCase
    prepared_volume: PreprocessedVolume
    ground_truth_regions: set[str]


class PipelineContractError(RuntimeError):
    pass


def _dice(prediction: np.ndarray, truth: np.ndarray) -> float:
    pred_mask = prediction >= 0.55
    truth_mask = truth >= 0.25
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
    metric_fn: Callable[[list[CaseEvaluation]], float],
    seed: int,
    rounds: int = 200,
) -> MetricInterval:
    value = metric_fn(evaluations)
    if len(evaluations) < 2:
        return MetricInterval(value=round(value, 4))
    rng = np.random.default_rng(seed)
    samples = []
    for _ in range(rounds):
        indices = rng.integers(0, len(evaluations), size=len(evaluations))
        sampled = [evaluations[index] for index in indices]
        samples.append(metric_fn(sampled))
    lower, upper = np.percentile(samples, [2.5, 97.5])
    return MetricInterval(value=round(value, 4), lower_ci=round(float(lower), 4), upper_ci=round(float(upper), 4))


def _prepare_benchmark_case(case: LoadedBenchmarkCase) -> PreparedCase:
    prepared = prepare_volume(case.volume)
    prepared.hemisphere = case.hemisphere
    prepared.atlas = build_aspects_atlas(tuple(prepared.volume.shape))[case.hemisphere]
    return PreparedCase(
        case=case,
        prepared_volume=prepared,
        ground_truth_regions=affected_regions_from_mask(case.lesion_mask, case.hemisphere),
    )


def _evaluate_case(prepared_case: PreparedCase, runner: BaseRunner) -> CaseEvaluation:
    case = prepared_case.case
    output = runner.run(prepared_case.prepared_volume)
    predicted_aspects, region_scores = aspects_from_probability_map(output.heatmap, case.hemisphere)
    binary_mask = binary_prediction_mask(output.heatmap)
    lesionwise_f1, lesion_count_difference = lesionwise_f1_and_count_difference(case.lesion_mask, binary_mask)
    return CaseEvaluation(
        case=case,
        predicted_aspects=predicted_aspects,
        aspects_absolute_error=abs(predicted_aspects - case.aspects_score),
        global_confidence=output.global_confidence,
        region_probabilities=[score.probability for score in region_scores],
        region_truth=[score.region in prepared_case.ground_truth_regions for score in region_scores],
        coherence=_dice(output.heatmap, case.lesion_mask),
        elapsed_ms=output.elapsed_ms,
        heatmap=output.heatmap,
        binary_mask=binary_mask,
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
    def aspects_mae(items: list[CaseEvaluation]) -> float:
        return float(np.mean([item.aspects_absolute_error for item in items]))

    def auc(items: list[CaseEvaluation]) -> float:
        labels = [bool(item.case.affected_regions) for item in items]
        scores = [item.global_confidence for item in items]
        return float(_roc_auc(labels, scores))

    def region_sensitivity(items: list[CaseEvaluation]) -> float:
        tp = 0
        fn = 0
        for item in items:
            for truth, probability in zip(item.region_truth, item.region_probabilities, strict=True):
                if truth and probability >= 0.45:
                    tp += 1
                elif truth:
                    fn += 1
        return float(tp / max(tp + fn, 1))

    def interpretability_coherence(items: list[CaseEvaluation]) -> float:
        return float(np.mean([item.coherence for item in items]))

    def calibration_error(items: list[CaseEvaluation]) -> float:
        probabilities = [probability for item in items for probability in item.region_probabilities]
        labels = [truth for item in items for truth in item.region_truth]
        return float(_expected_calibration_error(probabilities, labels))

    def isles_dice(items: list[CaseEvaluation]) -> float:
        return float(np.mean([item.isles_dice for item in items]))

    def isles_absolute_volume_difference_ml(items: list[CaseEvaluation]) -> float:
        return float(np.mean([item.isles_absolute_volume_difference_ml for item in items]))

    def isles_absolute_lesion_count_difference(items: list[CaseEvaluation]) -> float:
        return float(np.mean([item.isles_absolute_lesion_count_difference for item in items]))

    def isles_lesionwise_f1(items: list[CaseEvaluation]) -> float:
        return float(np.mean([item.isles_lesionwise_f1 for item in items]))

    def latency(items: list[CaseEvaluation]) -> float:
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
    metric_fn: Callable[[list[CaseEvaluation]], float],
    higher_is_better: bool,
) -> float:
    left_value = metric_fn(left)
    right_value = metric_fn(right)
    return left_value - right_value if higher_is_better else right_value - left_value


def _paired_bootstrap_delta(
    left: list[CaseEvaluation],
    right: list[CaseEvaluation],
    metric_fn: Callable[[list[CaseEvaluation]], float],
    higher_is_better: bool,
    seed: int,
    rounds: int = 120,
) -> tuple[float, float, float]:
    observed = _paired_metric_delta(left, right, metric_fn, higher_is_better=higher_is_better)
    rng = np.random.default_rng(seed)
    deltas = []
    for _ in range(rounds):
        indices = rng.integers(0, len(left), size=len(left))
        left_sample = [left[index] for index in indices]
        right_sample = [right[index] for index in indices]
        deltas.append(_paired_metric_delta(left_sample, right_sample, metric_fn, higher_is_better=higher_is_better))
    lower, upper = np.percentile(deltas, [2.5, 97.5])
    return round(observed, 4), round(float(lower), 4), round(float(upper), 4)


def _paired_permutation_pvalue(
    left: list[CaseEvaluation],
    right: list[CaseEvaluation],
    metric_fn: Callable[[list[CaseEvaluation]], float],
    higher_is_better: bool,
    seed: int,
    rounds: int = 200,
) -> float:
    observed = _paired_metric_delta(left, right, metric_fn, higher_is_better=higher_is_better)
    rng = np.random.default_rng(seed)
    exceedances = 1
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
        if abs(delta) >= abs(observed):
            exceedances += 1
    return round(exceedances / float(rounds + 1), 4)


def _pairwise_comparisons(
    evaluations_by_runner: dict[str, list[CaseEvaluation]],
    seed: int,
) -> dict[str, BenchmarkComparison]:
    sounio_items = evaluations_by_runner[ModelFamily.sounio_hypercomplex.value]

    def aspects_mae(items: list[CaseEvaluation]) -> float:
        return float(np.mean([item.aspects_absolute_error for item in items]))

    def auc(items: list[CaseEvaluation]) -> float:
        labels = [bool(item.case.affected_regions) for item in items]
        scores = [item.global_confidence for item in items]
        return float(_roc_auc(labels, scores))

    def coherence(items: list[CaseEvaluation]) -> float:
        return float(np.mean([item.coherence for item in items]))

    def dice(items: list[CaseEvaluation]) -> float:
        return float(np.mean([item.isles_dice for item in items]))

    def lesionwise_f1(items: list[CaseEvaluation]) -> float:
        return float(np.mean([item.isles_lesionwise_f1 for item in items]))

    def volume_difference(items: list[CaseEvaluation]) -> float:
        return float(np.mean([item.isles_absolute_volume_difference_ml for item in items]))

    def lesion_count_difference(items: list[CaseEvaluation]) -> float:
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
            "lesion_present": [item for item in evaluations if bool(item.case.affected_regions)],
            "no_lesion": [item for item in evaluations if not bool(item.case.affected_regions)],
            "left_hemisphere": [item for item in evaluations if item.case.hemisphere == "left"],
            "right_hemisphere": [item for item in evaluations if item.case.hemisphere == "right"],
        }
        for bucket in ("severe_0_4", "moderate_5_7", "mild_8_10"):
            groups[bucket] = [item for item in evaluations if _aspects_bucket(item.case.aspects_score) == bucket]
        stratified[model_name] = {
            group_name: _aggregate_metrics(items, seed + model_index * 17 + group_index * 3)
            for group_index, (group_name, items) in enumerate(groups.items())
            if items
        }
    return stratified


class BenchmarkHarness:
    def __init__(self, storage: StorageManager):
        self.storage = storage

    def validate_pipeline_contract(self, runners: dict[str, BaseRunner]) -> None:
        versions = {runner.pipeline_version for runner in runners.values()}
        if versions != {PIPELINE_VERSION}:
            raise PipelineContractError(f"Mismatched pipeline versions: {sorted(versions)}")

    def run_suite(
        self,
        request: BenchmarkRequest,
        *,
        run_id: str | None = None,
        job_id: str | None = None,
    ) -> BenchmarkRun:
        run_id = run_id or uuid.uuid4().hex
        manifest_path = Path(request.dataset_manifest_path).expanduser().resolve()
        test_manifest_path = (
            Path(request.external_test_manifest_path).expanduser().resolve()
            if request.external_test_manifest_path
            else manifest_path
        )
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
        self._validate_split_contract(manifest_path, test_manifest_path, train_cases, test_cases, request)

        trained_model_artifacts = fit_all_model_artifacts(
            train_cases,
            dataset_manifest_path=str(manifest_path),
            trained_on_split=request.train_split,
        )
        runners = comparative_runners(trained_model_artifacts)
        self.validate_pipeline_contract(runners)

        prepared_test_cases = [_prepare_benchmark_case(case) for case in test_cases]
        evaluations_by_runner: dict[str, list[CaseEvaluation]] = {name: [] for name in runners}
        for prepared_case in prepared_test_cases:
            for name, runner in runners.items():
                evaluations_by_runner[name].append(_evaluate_case(prepared_case, runner))

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

        full_sounio_auc = float(metrics[ModelFamily.sounio_hypercomplex.value]["auc"]["value"])
        full_sounio_coherence = float(
            metrics[ModelFamily.sounio_hypercomplex.value]["interpretability_coherence"]["value"]
        )
        ablations, ablation_artifacts = self._run_ablations(
            train_cases=train_cases,
            prepared_test_cases=prepared_test_cases,
            seed=request.seed,
            full_auc=full_sounio_auc,
            full_coherence=full_sounio_coherence,
            manifest_path=manifest_path,
            train_split=request.train_split,
        )

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
            cohort_shift=cohort_shift,
            failure_analysis=failure_analysis,
            ablations=ablations,
            request=request,
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
            FAIRNESS_POLICY,
        ]
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
        if cohort_shift["source_changed"] or cohort_shift["dataset_changed"]:
            fairness_checks.append("Training and test cohorts differ in source provenance; review cohort_shift artifact before claims.")

        run = BenchmarkRun(
            run_id=run_id,
            job_id=job_id,
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
            },
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
    ) -> tuple[list[AblationResult], dict[str, TrainedModelArtifact]]:
        items: list[AblationResult] = []
        artifacts: dict[str, TrainedModelArtifact] = {}
        prepared_rows = prepare_training_rows(train_cases)
        for index, component in enumerate(("hypercomplex_phase", "hypercomplex_energy", "asymmetry_channel")):
            artifact = fit_model_artifact(
                ModelFamily.sounio_hypercomplex,
                train_cases,
                dataset_manifest_path=str(manifest_path),
                trained_on_split=train_split,
                removed_component=component,
                prepared_rows=prepared_rows,
            )
            artifacts[component] = artifact
            runner = build_runner(
                ModelFamily.sounio_hypercomplex,
                removed_component=component,
                model_artifact=artifact,
            )
            evaluations = [_evaluate_case(prepared_case, runner) for prepared_case in prepared_test_cases]
            metrics = _aggregate_metrics(evaluations, seed + 503 + index * 13)
            auc = float(metrics["auc"]["value"])
            coherence = float(metrics["interpretability_coherence"]["value"])
            items.append(
                AblationResult(
                    ablation_name=f"sounio_without_{component}",
                    removed_component=component,
                    metric_delta=round(full_auc - auc, 4),
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
        cohort_shift: dict[str, object],
        failure_analysis: dict[str, object],
        ablations: list[AblationResult],
        request: BenchmarkRequest,
    ) -> list[ArtifactDescriptor]:
        artifacts: list[ArtifactDescriptor] = []
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
                    },
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
                    },
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
        strongest = min(
            sounio_evaluations,
            key=lambda item: abs(item.predicted_aspects - item.case.aspects_score) - item.coherence,
        )
        weakest = max(
            sounio_evaluations,
            key=lambda item: abs(item.predicted_aspects - item.case.aspects_score) + (1.0 - item.coherence),
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
            },
            metrics=metrics,
            comparisons=comparisons,
            stratified_metrics=stratified_metrics,
            leaderboard=leaderboard,
            artifacts=artifacts,
            fairness_checks=[
                f"Source manifest: {manifest_path}",
                f"Training split: {request.train_split}",
                f"Test split: {request.test_split}",
                "Training and test case ids are disjoint.",
                "Same preprocessing and atlas registration for all arms.",
                "Same compute budget profile for all arms.",
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
        for artifact in artifacts:
            self.storage.register_artifact(
                RecordOwnerType.benchmark_run,
                run_id,
                name=artifact.name,
                kind=artifact.kind,
                path=artifact.path,
                description=artifact.description,
            )
        return artifacts
