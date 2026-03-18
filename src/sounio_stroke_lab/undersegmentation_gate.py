from __future__ import annotations

from typing import Any

import numpy as np
from scipy.ndimage import gaussian_filter

from sounio_stroke_lab.features import normalize_feature
from sounio_stroke_lab.schemas import TrainedModelArtifact


SOUNIO_UNDERSEGMENTATION_GATE_POLICY = "sounio-underseg-gate-v1"
SOUNIO_UNDERSEGMENTATION_GATE_NO_VAL_POLICY = "no-disjoint-val-v1"
SOUNIO_UNDERSEGMENTATION_GATE_INSUFFICIENT_POLICY = "insufficient-positive-val-v1"
SOUNIO_UNDERSEGMENTATION_GATE_ORACLE_GAINS = (1.00, 1.04, 1.08, 1.12, 1.16)
SOUNIO_UNDERSEGMENTATION_GATE_GAIN_MIN = 1.00
SOUNIO_UNDERSEGMENTATION_GATE_GAIN_MAX = 1.18
SOUNIO_UNDERSEGMENTATION_GATE_ACTIVATION_THRESHOLD = 0.60
SOUNIO_UNDERSEGMENTATION_GATE_FEATURE_NAMES = [
    "region_score_max",
    "region_score_mean",
    "heatmap_p95",
    "heatmap_p995",
    "heatmap_fraction_ge_010",
    "heatmap_fraction_ge_015",
    "heatmap_fraction_ge_025",
    "predicted_burden_to_current_ratio",
    "broad_local_support_fraction",
    "heatmap_support_overlap",
    "atlas_support_coverage_fraction",
    "top3_deficit_mean",
    "top3_asymmetry_mean",
    "top3_gradient_suppression_mean",
]


def _sigmoid(value: np.ndarray | float) -> np.ndarray | float:
    clipped = np.clip(value, -50.0, 50.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _safe_top3_mean(values: list[float]) -> float:
    array = np.asarray(values, dtype=np.float32)
    if array.size == 0:
        return 0.0
    topk = np.sort(array)[-min(3, array.size) :]
    return float(topk.mean()) if topk.size else 0.0


def undersegmentation_support_maps(
    feature_maps: dict[str, np.ndarray],
    region_map: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    support_gate = np.clip(region_map / (float(region_map.max()) or 1.0), 0.0, 1.0).astype(np.float32)
    local_support = normalize_feature(
        0.65 * feature_maps["deficit"]
        + 0.20 * feature_maps["asymmetry"]
        + 0.15 * feature_maps["gradient_suppression"]
    )
    underseg_support = np.clip(local_support * support_gate, 0.0, 1.0).astype(np.float32)
    return underseg_support, support_gate


def undersegmentation_eligibility_mask(
    heatmap: np.ndarray,
    underseg_support: np.ndarray,
    support_gate: np.ndarray,
) -> np.ndarray:
    return np.asarray(
        (underseg_support >= 0.35) & ((heatmap >= 0.10) | (support_gate >= 0.35)),
        dtype=bool,
    )


def undersegmentation_gate_feature_row(
    *,
    heatmap: np.ndarray,
    region_scores: list[float],
    region_feature_vectors: dict[str, list[float]],
    burden_voxels: float | None,
    reference_threshold: float,
    underseg_support: np.ndarray,
    support_gate: np.ndarray,
) -> np.ndarray:
    current_voxels = max(float(np.count_nonzero(heatmap >= reference_threshold)), 1.0)
    predicted_ratio = float(burden_voxels / current_voxels) if burden_voxels is not None else 1.0
    low_heatmap_mask = np.asarray(heatmap >= 0.10, dtype=bool)
    support_mask = np.asarray(underseg_support >= 0.35, dtype=bool)
    overlap_union = np.logical_or(low_heatmap_mask, support_mask)
    overlap = (
        float(np.logical_and(low_heatmap_mask, support_mask).sum()) / float(overlap_union.sum())
        if np.any(overlap_union)
        else 0.0
    )
    feature_row = np.asarray(
        [
            float(np.max(region_scores, initial=0.0)),
            float(np.mean(region_scores)) if region_scores else 0.0,
            float(np.quantile(heatmap, 0.95)),
            float(np.quantile(heatmap, 0.995)),
            float(np.mean(heatmap >= 0.10)),
            float(np.mean(heatmap >= 0.15)),
            float(np.mean(heatmap >= 0.25)),
            predicted_ratio,
            float(np.mean(underseg_support >= 0.35)),
            overlap,
            float(np.mean(support_gate >= 0.35)),
            _safe_top3_mean(region_feature_vectors.get("deficit", [])),
            _safe_top3_mean(region_feature_vectors.get("asymmetry", [])),
            _safe_top3_mean(region_feature_vectors.get("gradient_suppression", [])),
        ],
        dtype=np.float32,
    )
    return feature_row


def undersegmentation_gate_noop_payload(
    *,
    policy: str,
    calibration_split: str | None,
    positive_case_count: int,
) -> dict[str, Any]:
    return {
        "undersegmentation_gate_feature_names": list(SOUNIO_UNDERSEGMENTATION_GATE_FEATURE_NAMES),
        "undersegmentation_gate_classifier_weights": [],
        "undersegmentation_gate_classifier_bias": None,
        "undersegmentation_gate_classifier_feature_mean": [],
        "undersegmentation_gate_classifier_feature_std": [],
        "undersegmentation_gate_regressor_weights": [],
        "undersegmentation_gate_regressor_bias": None,
        "undersegmentation_gate_regressor_feature_mean": [],
        "undersegmentation_gate_regressor_feature_std": [],
        "undersegmentation_gate_gain_min": SOUNIO_UNDERSEGMENTATION_GATE_GAIN_MIN,
        "undersegmentation_gate_gain_max": SOUNIO_UNDERSEGMENTATION_GATE_GAIN_MAX,
        "undersegmentation_gate_activation_threshold": SOUNIO_UNDERSEGMENTATION_GATE_ACTIVATION_THRESHOLD,
        "undersegmentation_gate_policy": policy,
        "undersegmentation_gate_calibration_split": calibration_split,
        "undersegmentation_gate_positive_case_count": positive_case_count,
    }


def is_undersegmentation_gate_configured(artifact: TrainedModelArtifact) -> bool:
    return (
        bool(artifact.undersegmentation_gate_feature_names)
        and bool(artifact.undersegmentation_gate_classifier_weights)
        and artifact.undersegmentation_gate_classifier_bias is not None
        and bool(artifact.undersegmentation_gate_regressor_weights)
        and artifact.undersegmentation_gate_regressor_bias is not None
    )


def predict_undersegmentation_gate_probability(
    feature_row: np.ndarray,
    artifact: TrainedModelArtifact,
) -> float | None:
    if (
        not artifact.undersegmentation_gate_classifier_weights
        or artifact.undersegmentation_gate_classifier_bias is None
        or not artifact.undersegmentation_gate_classifier_feature_mean
        or not artifact.undersegmentation_gate_classifier_feature_std
    ):
        return None
    mean = np.asarray(artifact.undersegmentation_gate_classifier_feature_mean, dtype=np.float32)
    std = np.asarray(artifact.undersegmentation_gate_classifier_feature_std, dtype=np.float32)
    standardized = (feature_row - mean) / std
    weights = np.asarray(artifact.undersegmentation_gate_classifier_weights, dtype=np.float32)
    logit = float(np.dot(standardized, weights) + float(artifact.undersegmentation_gate_classifier_bias))
    return float(_sigmoid(logit))


def predict_undersegmentation_gate_gain(
    feature_row: np.ndarray,
    artifact: TrainedModelArtifact,
) -> float | None:
    if (
        not artifact.undersegmentation_gate_regressor_weights
        or artifact.undersegmentation_gate_regressor_bias is None
        or not artifact.undersegmentation_gate_regressor_feature_mean
        or not artifact.undersegmentation_gate_regressor_feature_std
    ):
        return None
    mean = np.asarray(artifact.undersegmentation_gate_regressor_feature_mean, dtype=np.float32)
    std = np.asarray(artifact.undersegmentation_gate_regressor_feature_std, dtype=np.float32)
    standardized = (feature_row - mean) / std
    weights = np.asarray(artifact.undersegmentation_gate_regressor_weights, dtype=np.float32)
    predicted_delta = float(np.dot(standardized, weights) + float(artifact.undersegmentation_gate_regressor_bias))
    gain = SOUNIO_UNDERSEGMENTATION_GATE_GAIN_MIN + max(predicted_delta, 0.0)
    return float(
        np.clip(
            gain,
            float(artifact.undersegmentation_gate_gain_min or SOUNIO_UNDERSEGMENTATION_GATE_GAIN_MIN),
            float(artifact.undersegmentation_gate_gain_max or SOUNIO_UNDERSEGMENTATION_GATE_GAIN_MAX),
        )
    )


def apply_undersegmentation_gate(
    *,
    heatmap: np.ndarray,
    underseg_support: np.ndarray,
    support_gate: np.ndarray,
    feature_row: np.ndarray,
    artifact: TrainedModelArtifact,
) -> tuple[np.ndarray, bool, float, float | None]:
    probability = predict_undersegmentation_gate_probability(feature_row, artifact)
    if probability is None:
        return heatmap, False, 1.0, None
    activation_threshold = float(
        artifact.undersegmentation_gate_activation_threshold or SOUNIO_UNDERSEGMENTATION_GATE_ACTIVATION_THRESHOLD
    )
    if probability < activation_threshold:
        return heatmap, False, 1.0, probability
    gain = predict_undersegmentation_gate_gain(feature_row, artifact)
    if gain is None or gain <= 1.0:
        return heatmap, False, 1.0, probability
    eligible = undersegmentation_eligibility_mask(heatmap, underseg_support, support_gate)
    if not np.any(eligible):
        return heatmap, False, 1.0, probability
    expanded = np.asarray(heatmap, dtype=np.float32).copy()
    expanded[eligible] = np.clip(expanded[eligible] * gain, 0.0, 1.0)
    smoothed = np.clip(gaussian_filter(expanded, sigma=0.35), 0.0, 1.0).astype(np.float32)
    gated = np.asarray(heatmap, dtype=np.float32).copy()
    gated[eligible] = np.maximum(gated[eligible], smoothed[eligible])
    return np.clip(gated, 0.0, 1.0).astype(np.float32), True, float(gain), probability
