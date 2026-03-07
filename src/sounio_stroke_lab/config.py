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


def get_storage_root() -> Path:
    raw = os.getenv("SOUNIO_STROKE_DATA_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.cwd() / ".sounio-stroke-lab").resolve()
