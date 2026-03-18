from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from sounio_stroke_lab.atlas import affected_regions_from_mask, build_aspects_atlas
from sounio_stroke_lab.config import ATLAS_REGIONS, DEFAULT_TARGET_SHAPE
from sounio_stroke_lab.dataset_manifest import LoadedBenchmarkCase
from sounio_stroke_lab.evaluation import _align_binary_mask
from sounio_stroke_lab.features import extract_feature_maps, region_feature_vectors
from sounio_stroke_lab.preprocessing import prepare_volume
from sounio_stroke_lab.schemas import ModelFamily, TrainedModelArtifact
from sounio_stroke_lab.undersegmentation_gate import (
    SOUNIO_UNDERSEGMENTATION_GATE_NO_VAL_POLICY,
    undersegmentation_gate_noop_payload,
)


FEATURE_SETS = {
    ModelFamily.sounio_hypercomplex: ["deficit", "asymmetry", "smoothness", "gradient_suppression", "energy", "coupling"],
    ModelFamily.python_3d_conventional: ["deficit", "asymmetry", "gradient_suppression"],
    ModelFamily.julia_equivalent: ["deficit", "asymmetry", "smoothness"],
    ModelFamily.cpp_equivalent: ["deficit", "asymmetry"],
}

ABLATION_FEATURES = {
    "hypercomplex_phase": {"coupling"},
    "hypercomplex_energy": {"energy"},
    "asymmetry_channel": {"asymmetry"},
}


@dataclass
class PreparedTrainingRows:
    features_by_case: list[dict[str, list[float]]]
    labels_by_case: list[set[str]]
    case_weights: list[float]
    aligned_lesion_voxels: list[float]


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -50.0, 50.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _logit(probability: float) -> float:
    clipped = min(max(probability, 1e-4), 1.0 - 1e-4)
    return float(np.log(clipped / (1.0 - clipped)))


def _fit_binary_logistic(
    X: np.ndarray,
    y: np.ndarray,
    sample_weight: np.ndarray | None = None,
    lr: float = 0.15,
    epochs: int = 500,
    reg: float = 0.01,
) -> tuple[np.ndarray, float]:
    if X.ndim != 2:
        raise ValueError("Expected a 2D design matrix.")
    sample_weight = (
        np.asarray(sample_weight, dtype=np.float32)
        if sample_weight is not None
        else np.ones(X.shape[0], dtype=np.float32)
    )
    sample_weight = np.clip(sample_weight, 1e-4, None)
    weight_total = float(sample_weight.sum()) or float(X.shape[0])
    weights = np.zeros(X.shape[1], dtype=np.float32)
    positive_rate = float(np.average(y, weights=sample_weight))
    bias = _logit(positive_rate) if positive_rate not in {0.0, 1.0} else _logit(0.05 if positive_rate == 0.0 else 0.95)
    if np.all(y == y[0]):
        return weights, float(bias)
    for _ in range(epochs):
        logits = X @ weights + bias
        predictions = _sigmoid(logits)
        error = (predictions - y) * sample_weight
        grad_w = (X.T @ error) / weight_total + reg * weights
        grad_b = float(error.sum() / weight_total)
        weights -= lr * grad_w
        bias -= lr * grad_b
    return weights.astype(np.float32), float(bias)


def _case_training_weights(cases: list[LoadedBenchmarkCase]) -> list[float]:
    burdens = np.asarray(
        [
            float(np.asarray(case.lesion_mask > 0.25, dtype=np.float32).sum()) * float(case.voxel_volume_ml)
            for case in cases
        ],
        dtype=np.float32,
    )
    if burdens.size == 0:
        return []
    log_burdens = np.log1p(burdens)
    low = float(np.percentile(log_burdens, 25))
    high = float(np.percentile(log_burdens, 90))
    if high <= low:
        return [1.0 for _ in cases]
    normalized = np.clip((log_burdens - low) / (high - low), 0.0, 1.0)
    weights = 0.9 + 1.1 * normalized
    weights = weights / (float(weights.mean()) or 1.0)
    return [float(value) for value in weights]


def _burden_prior_feature_names(feature_names: list[str]) -> list[str]:
    return [f"{name}_{suffix}" for name in feature_names for suffix in ("mean", "max", "top3_mean")]


def _burden_prior_row(features: dict[str, list[float]], feature_names: list[str]) -> list[float]:
    summary: list[float] = []
    for feature_name in feature_names:
        values = np.asarray(features[feature_name], dtype=np.float32)
        sorted_values = np.sort(values)
        topk = sorted_values[-min(3, len(sorted_values)) :]
        summary.extend(
            [
                float(values.mean()),
                float(values.max(initial=0.0)),
                float(topk.mean() if topk.size else 0.0),
            ]
        )
    return summary


def _fit_burden_prior(
    features_by_case: list[dict[str, list[float]]],
    aligned_lesion_voxels: list[float],
    feature_names: list[str],
    case_weights: list[float],
) -> tuple[list[str], list[float], float, list[float], list[float]]:
    prior_feature_names = _burden_prior_feature_names(feature_names)
    X = np.asarray(
        [_burden_prior_row(features, feature_names) for features in features_by_case],
        dtype=np.float32,
    )
    feature_mean = X.mean(axis=0)
    feature_std = X.std(axis=0)
    feature_std = np.where(feature_std < 1e-5, 1.0, feature_std)
    standardized = (X - feature_mean) / feature_std
    y = np.log1p(np.asarray(aligned_lesion_voxels, dtype=np.float32))
    centered_y = y - float(y.mean())
    weights = np.asarray(case_weights, dtype=np.float32)
    weights = np.clip(weights, 1e-4, None)
    sqrt_w = np.sqrt(weights)[:, np.newaxis]
    Xw = standardized * sqrt_w
    yw = centered_y * sqrt_w[:, 0]
    ridge = 0.05
    system = Xw.T @ Xw + ridge * np.eye(Xw.shape[1], dtype=np.float32)
    target = Xw.T @ yw
    coef = np.linalg.solve(system, target)
    return (
        prior_feature_names,
        [float(value) for value in coef],
        float(y.mean()),
        [float(value) for value in feature_mean],
        [float(value) for value in feature_std],
    )


def prepare_training_rows(
    cases: list[LoadedBenchmarkCase],
    target_shape: tuple[int, int, int] = DEFAULT_TARGET_SHAPE,
) -> PreparedTrainingRows:
    features_by_case: list[dict[str, list[float]]] = []
    labels_by_case: list[set[str]] = []
    case_weights = _case_training_weights(cases)
    aligned_lesion_voxels: list[float] = []
    for case in cases:
        prepared = prepare_volume(case.volume, shape=target_shape)
        prepared.hemisphere = case.hemisphere
        prepared.atlas = build_aspects_atlas(tuple(prepared.volume.shape))[case.hemisphere]
        feature_maps = extract_feature_maps(prepared)
        features_by_case.append(region_feature_vectors(prepared, feature_maps))
        labels_by_case.append(affected_regions_from_mask(case.lesion_mask, case.hemisphere))
        aligned_mask = _align_binary_mask(case.lesion_mask > 0.25, prepared.volume.shape)
        aligned_lesion_voxels.append(float(aligned_mask.sum()))
    return PreparedTrainingRows(
        features_by_case=features_by_case,
        labels_by_case=labels_by_case,
        case_weights=case_weights,
        aligned_lesion_voxels=aligned_lesion_voxels,
    )


def feature_names_for_model(model_family: ModelFamily, removed_component: str | None = None) -> list[str]:
    feature_names = list(FEATURE_SETS[model_family])
    if removed_component is None:
        return feature_names
    removed_features = ABLATION_FEATURES.get(removed_component)
    if removed_features is None:
        raise ValueError(f"Unknown ablation component: {removed_component}")
    ablated = [feature_name for feature_name in feature_names if feature_name not in removed_features]
    if not ablated:
        raise ValueError(f"Ablation '{removed_component}' removed all features for {model_family.value}.")
    return ablated


def fit_model_artifact(
    model_family: ModelFamily,
    cases: list[LoadedBenchmarkCase],
    dataset_manifest_path: str,
    trained_on_split: str,
    removed_component: str | None = None,
    prepared_rows: PreparedTrainingRows | None = None,
) -> TrainedModelArtifact:
    if not cases:
        raise ValueError("Cannot fit a model artifact without training cases.")
    feature_names = feature_names_for_model(model_family, removed_component=removed_component)
    prepared_rows = prepared_rows or prepare_training_rows(cases)
    features_by_case = prepared_rows.features_by_case
    labels_by_case = prepared_rows.labels_by_case
    case_weights = prepared_rows.case_weights
    aligned_lesion_voxels = prepared_rows.aligned_lesion_voxels
    all_rows = []
    for features in features_by_case:
        for region_index in range(len(ATLAS_REGIONS)):
            all_rows.append([features[name][region_index] for name in feature_names])
    design = np.asarray(all_rows, dtype=np.float32)
    feature_mean = design.mean(axis=0)
    feature_std = design.std(axis=0)
    feature_std = np.where(feature_std < 1e-5, 1.0, feature_std)

    weights: list[list[float]] = []
    bias: list[float] = []
    for region_name in ATLAS_REGIONS:
        rows = []
        labels = []
        for features, ground_truth in zip(features_by_case, labels_by_case, strict=True):
            rows.append([features[name][ATLAS_REGIONS.index(region_name)] for name in feature_names])
            labels.append(float(region_name in ground_truth))
        X = (np.asarray(rows, dtype=np.float32) - feature_mean) / feature_std
        y = np.asarray(labels, dtype=np.float32)
        sample_weight = np.asarray(case_weights, dtype=np.float32)
        region_weights, region_bias = _fit_binary_logistic(X, y, sample_weight=sample_weight)
        weights.append([float(value) for value in region_weights])
        bias.append(float(region_bias))
    (
        burden_prior_feature_names,
        burden_prior_weights,
        burden_prior_bias,
        burden_prior_feature_mean,
        burden_prior_feature_std,
    ) = _fit_burden_prior(
        features_by_case,
        aligned_lesion_voxels,
        feature_names,
        case_weights,
    )
    if model_family == ModelFamily.sounio_hypercomplex:
        artifact_version = "regional-logistic-v4"
        undersegmentation_gate_payload = undersegmentation_gate_noop_payload(
            policy=SOUNIO_UNDERSEGMENTATION_GATE_NO_VAL_POLICY,
            calibration_split=None,
            positive_case_count=0,
        )
    else:
        artifact_version = "regional-logistic-v3"
        undersegmentation_gate_payload = {}
    return TrainedModelArtifact(
        artifact_version=artifact_version,
        model_family=model_family,
        feature_names=feature_names,
        weights=weights,
        bias=bias,
        feature_mean=[float(value) for value in feature_mean],
        feature_std=[float(value) for value in feature_std],
        trained_on_split=trained_on_split,
        dataset_manifest_path=dataset_manifest_path,
        burden_prior_feature_names=burden_prior_feature_names,
        burden_prior_weights=burden_prior_weights,
        burden_prior_bias=burden_prior_bias,
        burden_prior_feature_mean=burden_prior_feature_mean,
        burden_prior_feature_std=burden_prior_feature_std,
        burden_prior_reference_threshold=0.25,
        burden_prior_gain_strength=0.3,
        burden_prior_gain_strength_policy="fixed-v1",
        burden_prior_gain_calibration_split=None,
        **undersegmentation_gate_payload,
        removed_component=removed_component,
        notes=[
            "Per-region logistic model fitted on manifest training split.",
            "Training uses lesion-burden-aware case weighting shared across all benchmark arms.",
            "Artifact carries a simple burden prior calibrated on aligned lesion voxel counts.",
            *(
                [
                    "Sounio artifacts default to a no-op undersegmentation gate until validation-time calibration learns a case-level expand policy."
                ]
                if model_family == ModelFamily.sounio_hypercomplex
                else ["Burden-prior gain defaults to a fixed fallback until a validation split calibrates it."]
            ),
            *(
                [f"Ablation training removed component: {removed_component}."]
                if removed_component is not None
                else []
            ),
        ],
    )


def fit_all_model_artifacts(
    cases: list[LoadedBenchmarkCase],
    dataset_manifest_path: str,
    trained_on_split: str,
    target_shape: tuple[int, int, int] = DEFAULT_TARGET_SHAPE,
) -> dict[str, TrainedModelArtifact]:
    prepared_rows = prepare_training_rows(cases, target_shape=target_shape)
    return {
        family.value: fit_model_artifact(
            family,
            cases,
            dataset_manifest_path=dataset_manifest_path,
            trained_on_split=trained_on_split,
            prepared_rows=prepared_rows,
        )
        for family in FEATURE_SETS
    }


def load_trained_model_artifact(path: Path) -> TrainedModelArtifact:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return TrainedModelArtifact.model_validate(payload)
