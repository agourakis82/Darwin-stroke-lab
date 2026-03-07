import pytest

from sounio_stroke_lab.sounio_runtime import SounioRuntime


def test_sounio_runtime_scores_regions_when_available():
    runtime = SounioRuntime.auto()
    if runtime is None:
        pytest.skip("Official Sounio runtime is not available in this environment.")

    scores = runtime.score_regions(
        [0.05, 0.75, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05],
        [0.02, 0.65, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02],
        [0.80, 0.92, 0.80, 0.80, 0.80, 0.80, 0.80, 0.80, 0.80, 0.80],
        [0.70, 0.88, 0.70, 0.70, 0.70, 0.70, 0.70, 0.70, 0.70, 0.70],
    )

    assert len(scores) == 10
    assert scores[1] > 0.9
    assert scores[0] < scores[1]


def test_sounio_runtime_scores_linear_artifact_when_available():
    runtime = SounioRuntime.auto()
    if runtime is None:
        pytest.skip("Official Sounio runtime is not available in this environment.")

    feature_rows = [
        [0.0, 0.0, 0.0],
        [2.0, 1.5, 0.5],
        [0.1, 0.0, 0.0],
        [0.1, 0.0, 0.0],
        [0.1, 0.0, 0.0],
        [0.1, 0.0, 0.0],
        [0.1, 0.0, 0.0],
        [0.1, 0.0, 0.0],
        [0.1, 0.0, 0.0],
        [0.1, 0.0, 0.0],
    ]
    weights = [[1.1, 0.6, 0.3] for _ in range(10)]
    bias = [-1.0 for _ in range(10)]

    scores = runtime.score_linear_regions(["deficit", "asymmetry", "coupling"], feature_rows, weights, bias)

    assert len(scores) == 10
    assert scores[1] > scores[0]
