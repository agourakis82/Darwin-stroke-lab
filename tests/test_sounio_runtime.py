from pathlib import Path

import numpy as np
import pytest

from sounio_stroke_lab.config import ATLAS_REGIONS
from sounio_stroke_lab.sounio_runtime import SounioRuntime


def _direct_runtime() -> SounioRuntime | None:
    souc_path = Path.home() / "sounio" / "compiler" / "target" / "release" / "souc"
    stdlib_path = Path.home() / "sounio" / "stdlib"
    if not souc_path.exists() or not stdlib_path.exists():
        return None
    return SounioRuntime(souc_path=souc_path, stdlib_path=stdlib_path, source="direct-test-runtime")


def _small_atlas(shape: tuple[int, int, int]) -> dict[str, np.ndarray]:
    atlas = {region: np.zeros(shape, dtype=bool) for region in ATLAS_REGIONS}
    atlas["caudate"][0, 0, 0] = True
    atlas["m1"][1, 1, 1] = True
    return atlas


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


def test_sounio_runtime_runs_volumetric_hypercomplex_core_when_binary_exists():
    runtime = _direct_runtime()
    if runtime is None:
        pytest.skip("Local Sounio release binary is not available in this environment.")

    shape = (2, 2, 2)
    volume = np.full(shape, 0.62, dtype=np.float32)
    volume[0, 0, 0] = 0.08
    asymmetry = np.zeros(shape, dtype=np.float32)
    asymmetry[0, 0, 0] = 0.95
    gradient = np.full(shape, 0.9, dtype=np.float32)
    gradient[0, 0, 0] = 0.05
    contrast = np.full(shape, 0.85, dtype=np.float32)
    contrast[0, 0, 0] = 0.15

    result = runtime.infer_hypercomplex_heatmap(
        volume=volume,
        asymmetry=asymmetry,
        gradient=gradient,
        contrast=contrast,
        atlas=_small_atlas(shape),
    )

    assert result.heatmap.shape == shape
    assert len(result.region_scores) == 10
    assert result.region_scores[0] > result.region_scores[4]
    assert float(result.heatmap.max()) > 0.0


def test_sounio_runtime_runs_volumetric_artifact_core_when_binary_exists():
    runtime = _direct_runtime()
    if runtime is None:
        pytest.skip("Local Sounio release binary is not available in this environment.")

    shape = (2, 2, 2)
    deficit = np.zeros(shape, dtype=np.float32)
    deficit[0, 0, 0] = 1.0
    asymmetry = np.zeros(shape, dtype=np.float32)
    asymmetry[0, 0, 0] = 0.8
    coupling = np.zeros(shape, dtype=np.float32)
    coupling[0, 0, 0] = 0.7
    atlas = _small_atlas(shape)

    result = runtime.infer_artifact_heatmap(
        feature_maps={
            "deficit": deficit,
            "asymmetry": asymmetry,
            "coupling": coupling,
        },
        atlas=atlas,
        feature_names=["deficit", "asymmetry", "coupling"],
        weights=[[1.1, 0.7, 0.3] for _ in range(10)],
        bias=[-1.2 for _ in range(10)],
        feature_mean=[0.0, 0.0, 0.0],
        feature_std=[1.0, 1.0, 1.0],
    )

    assert result.heatmap.shape == shape
    assert len(result.region_scores) == 10
    assert result.region_scores[0] > result.region_scores[4]
    assert float(result.heatmap[0, 0, 0]) > 0.1
