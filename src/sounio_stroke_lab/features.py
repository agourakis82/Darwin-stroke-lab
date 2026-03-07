from __future__ import annotations

import numpy as np

from sounio_stroke_lab.config import ATLAS_REGIONS
from sounio_stroke_lab.hypercomplex import phase_coupling, quaternion_channels, quaternion_energy
from sounio_stroke_lab.preprocessing import PreprocessedVolume


def normalize_feature(values: np.ndarray, quantile: float = 0.995) -> np.ndarray:
    scale = float(np.quantile(values, quantile)) or 1.0
    return np.clip(values / scale, 0.0, 1.0)


def extract_feature_maps(preprocessed: PreprocessedVolume) -> dict[str, np.ndarray]:
    deficit = normalize_feature(np.clip(0.54 - preprocessed.volume, 0.0, None))
    asymmetry = normalize_feature(preprocessed.asymmetry)
    gradient_suppression = normalize_feature(1.0 - preprocessed.gradient)
    smoothness = normalize_feature(1.0 - preprocessed.contrast)
    channels = quaternion_channels(deficit, asymmetry, smoothness, gradient_suppression)
    energy = normalize_feature(quaternion_energy(channels))
    coupling = normalize_feature(np.clip(phase_coupling(channels), 0.0, None))
    return {
        "deficit": deficit,
        "asymmetry": asymmetry,
        "gradient_suppression": gradient_suppression,
        "smoothness": smoothness,
        "energy": energy,
        "coupling": coupling,
    }


def region_feature_vectors(preprocessed: PreprocessedVolume, feature_maps: dict[str, np.ndarray]) -> dict[str, list[float]]:
    vectors: dict[str, list[float]] = {name: [] for name in feature_maps}
    for region_name in ATLAS_REGIONS:
        mask = preprocessed.atlas[region_name]
        for feature_name, values in feature_maps.items():
            vectors[feature_name].append(float(values[mask].mean()))
    return vectors
