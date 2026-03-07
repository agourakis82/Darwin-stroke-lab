from __future__ import annotations

import numpy as np

from sounio_stroke_lab.evaluation import absolute_volume_difference_ml, dice_score, lesionwise_f1_and_count_difference


def test_official_segmentation_metrics_capture_overlap_and_count():
    truth = np.zeros((8, 8, 8), dtype=np.float32)
    pred = np.zeros((8, 8, 8), dtype=np.float32)
    truth[1:3, 1:3, 1:3] = 1.0
    truth[5:7, 5:7, 5:7] = 1.0
    pred[1:3, 1:3, 1:3] = 1.0
    pred[4:6, 4:6, 4:6] = 1.0

    dice = dice_score(truth, pred)
    lesion_f1, lesion_count_diff = lesionwise_f1_and_count_difference(truth, pred)
    avd = absolute_volume_difference_ml(truth, pred, voxel_volume_ml=0.001)

    assert 0.0 < dice < 1.0
    assert 0.0 < lesion_f1 < 1.0
    assert lesion_count_diff == 0
    assert avd >= 0.0


def test_empty_segmentation_metrics_return_perfect_when_both_empty():
    truth = np.zeros((4, 4, 4), dtype=np.float32)
    pred = np.zeros((4, 4, 4), dtype=np.float32)

    lesion_f1, lesion_count_diff = lesionwise_f1_and_count_difference(truth, pred)

    assert dice_score(truth, pred) == 1.0
    assert lesion_f1 == 1.0
    assert lesion_count_diff == 0
