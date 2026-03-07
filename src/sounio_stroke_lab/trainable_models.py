from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from sounio_stroke_lab.atlas import affected_regions_from_mask, build_aspects_atlas
from sounio_stroke_lab.config import ATLAS_REGIONS
from sounio_stroke_lab.dataset_manifest import LoadedBenchmarkCase
from sounio_stroke_lab.features import extract_feature_maps, region_feature_vectors
from sounio_stroke_lab.preprocessing import prepare_volume
from sounio_stroke_lab.schemas import ModelFamily, TrainedModelArtifact


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


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -50.0, 50.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _logit(probability: float) -> float:
    clipped = min(max(probability, 1e-4), 1.0 - 1e-4)
    return float(np.log(clipped / (1.0 - clipped)))


def _fit_binary_logistic(X: np.ndarray, y: np.ndarray, lr: float = 0.15, epochs: int = 500, reg: float = 0.01) -> tuple[np.ndarray, float]:
    if X.ndim != 2:
        raise ValueError("Expected a 2D design matrix.")
    weights = np.zeros(X.shape[1], dtype=np.float32)
    bias = _logit(float(y.mean())) if float(y.mean()) not in {0.0, 1.0} else _logit(0.05 if y.mean() == 0.0 else 0.95)
    if np.all(y == y[0]):
        return weights, float(bias)
    for _ in range(epochs):
        logits = X @ weights + bias
        predictions = _sigmoid(logits)
        error = predictions - y
        grad_w = (X.T @ error) / X.shape[0] + reg * weights
        grad_b = float(error.mean())
        weights -= lr * grad_w
        bias -= lr * grad_b
    return weights.astype(np.float32), float(bias)


def prepare_training_rows(cases: list[LoadedBenchmarkCase]) -> PreparedTrainingRows:
    features_by_case: list[dict[str, list[float]]] = []
    labels_by_case: list[set[str]] = []
    for case in cases:
        prepared = prepare_volume(case.volume)
        prepared.hemisphere = case.hemisphere
        prepared.atlas = build_aspects_atlas(tuple(prepared.volume.shape))[case.hemisphere]
        feature_maps = extract_feature_maps(prepared)
        features_by_case.append(region_feature_vectors(prepared, feature_maps))
        labels_by_case.append(affected_regions_from_mask(case.lesion_mask, case.hemisphere))
    return PreparedTrainingRows(features_by_case=features_by_case, labels_by_case=labels_by_case)


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
        region_weights, region_bias = _fit_binary_logistic(X, y)
        weights.append([float(value) for value in region_weights])
        bias.append(float(region_bias))
    return TrainedModelArtifact(
        model_family=model_family,
        feature_names=feature_names,
        weights=weights,
        bias=bias,
        feature_mean=[float(value) for value in feature_mean],
        feature_std=[float(value) for value in feature_std],
        trained_on_split=trained_on_split,
        dataset_manifest_path=dataset_manifest_path,
        removed_component=removed_component,
        notes=[
            "Per-region logistic model fitted on manifest training split.",
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
) -> dict[str, TrainedModelArtifact]:
    prepared_rows = prepare_training_rows(cases)
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
