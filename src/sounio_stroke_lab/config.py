from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "Sounio Stroke Lab"
PIPELINE_VERSION = "common-preprocess-v1"
DATASET_VERSION = "manifest-benchmark-v1"
FAIRNESS_POLICY = (
    "same splits, same preprocessing, same atlas registration, same compute budget; "
    "only representation/model stack changes"
)
DEFAULT_TARGET_SHAPE = (32, 64, 64)
DEFAULT_SEGMENTATION_THRESHOLD = 0.5
AISD_EXPERIMENTAL_SEGMENTATION_THRESHOLD = 0.25
ATLAS_REGIONS = (
    "caudate",
    "lentiform",
    "internal_capsule",
    "insula",
    "m1",
    "m2",
    "m3",
    "m4",
    "m5",
    "m6",
)


def normalize_target_shape(
    raw: tuple[int, int, int] | list[int] | None,
) -> tuple[int, int, int]:
    if raw is None:
        return DEFAULT_TARGET_SHAPE
    if len(raw) != 3:
        raise ValueError("target_shape must contain exactly 3 integers: z y x.")
    shape = tuple(int(value) for value in raw)
    if any(value <= 0 for value in shape):
        raise ValueError("target_shape entries must be positive integers.")
    return shape


def normalize_segmentation_threshold(raw: float | None) -> float:
    if raw is None:
        return DEFAULT_SEGMENTATION_THRESHOLD
    threshold = float(raw)
    if threshold <= 0.0 or threshold >= 1.0:
        raise ValueError("segmentation_threshold must be between 0 and 1.")
    return threshold


def get_storage_root() -> Path:
    raw = os.getenv("SOUNIO_STROKE_DATA_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.cwd() / ".sounio-stroke-lab").resolve()
