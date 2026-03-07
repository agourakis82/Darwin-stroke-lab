from __future__ import annotations

import itertools

import numpy as np
from scipy.ndimage import generate_binary_structure, label


BINARY_SEGMENTATION_THRESHOLD = 0.5
LESION_MATCH_IOU_THRESHOLD = 0.2


def binary_prediction_mask(probability_map: np.ndarray, threshold: float = BINARY_SEGMENTATION_THRESHOLD) -> np.ndarray:
    return np.asarray(probability_map >= threshold, dtype=bool)


def dice_score(ground_truth: np.ndarray, prediction: np.ndarray, empty_value: float = 1.0) -> float:
    truth = np.asarray(ground_truth, dtype=bool)
    pred = np.asarray(prediction, dtype=bool)
    truth_total = int(truth.sum())
    pred_total = int(pred.sum())
    if truth_total == 0 and pred_total == 0:
        return float(empty_value)
    if truth_total == 0 or pred_total == 0:
        return 0.0
    overlap = int(np.logical_and(truth, pred).sum())
    return float((2.0 * overlap) / (truth_total + pred_total))


def absolute_volume_difference_ml(ground_truth: np.ndarray, prediction: np.ndarray, voxel_volume_ml: float) -> float:
    truth = np.asarray(ground_truth, dtype=bool)
    pred = np.asarray(prediction, dtype=bool)
    return float(abs(int(truth.sum()) - int(pred.sum())) * float(voxel_volume_ml))


def lesionwise_f1_and_count_difference(
    ground_truth: np.ndarray,
    prediction: np.ndarray,
    iou_threshold: float = LESION_MATCH_IOU_THRESHOLD,
    empty_value: float = 1.0,
) -> tuple[float, int]:
    truth = np.asarray(ground_truth, dtype=bool)
    pred = np.asarray(prediction, dtype=bool)
    structure = generate_binary_structure(rank=truth.ndim, connectivity=truth.ndim)
    truth_labels, truth_count = label(truth, structure=structure)
    pred_labels, pred_count = label(pred, structure=structure)

    if truth_count == 0 and pred_count == 0:
        return float(empty_value), 0
    if truth_count == 0 or pred_count == 0:
        return 0.0, abs(truth_count - pred_count)

    truth_components = [(truth_labels == index) for index in range(1, truth_count + 1)]
    pred_components = [(pred_labels == index) for index in range(1, pred_count + 1)]

    candidate_pairs: list[tuple[float, int, int]] = []
    for truth_index, pred_index in itertools.product(range(truth_count), range(pred_count)):
        overlap = np.logical_and(truth_components[truth_index], pred_components[pred_index])
        if not np.any(overlap):
            continue
        union = np.logical_or(truth_components[truth_index], pred_components[pred_index])
        iou = float(overlap.sum() / max(int(union.sum()), 1))
        if iou >= iou_threshold:
            candidate_pairs.append((iou, truth_index, pred_index))

    matched_truth: set[int] = set()
    matched_pred: set[int] = set()
    true_positives = 0
    for _, truth_index, pred_index in sorted(candidate_pairs, reverse=True):
        if truth_index in matched_truth or pred_index in matched_pred:
            continue
        matched_truth.add(truth_index)
        matched_pred.add(pred_index)
        true_positives += 1

    precision = true_positives / pred_count if pred_count else 0.0
    recall = true_positives / truth_count if truth_count else 0.0
    if precision == 0.0 and recall == 0.0:
        f1_score = 0.0
    else:
        f1_score = float((2.0 * precision * recall) / (precision + recall))
    return f1_score, abs(truth_count - pred_count)
