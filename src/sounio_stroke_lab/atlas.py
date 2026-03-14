from __future__ import annotations

from functools import lru_cache

import numpy as np

from sounio_stroke_lab.config import ATLAS_REGIONS, DEFAULT_TARGET_SHAPE
from sounio_stroke_lab.schemas import RegionScore


def _normalized_grid(shape: tuple[int, int, int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    z = np.linspace(-1.0, 1.0, shape[0], dtype=np.float32)
    y = np.linspace(-1.0, 1.0, shape[1], dtype=np.float32)
    x = np.linspace(-1.0, 1.0, shape[2], dtype=np.float32)
    return np.meshgrid(z, y, x, indexing="ij")


@lru_cache(maxsize=4)
def _right_hemisphere_masks(shape: tuple[int, int, int]) -> dict[str, np.ndarray]:
    zz, yy, xx = _normalized_grid(shape)
    brain = xx >= 0.0
    masks = {
        "caudate": brain & (xx > 0.05) & (xx < 0.25) & (yy > -0.15) & (yy < 0.2) & (zz > -0.35) & (zz < 0.1),
        "lentiform": brain & (xx > 0.22) & (xx < 0.42) & (yy > -0.25) & (yy < 0.15) & (zz > -0.3) & (zz < 0.15),
        "internal_capsule": brain & (xx > 0.14) & (xx < 0.28) & (yy > -0.32) & (yy < 0.05) & (zz > -0.2) & (zz < 0.18),
        "insula": brain & (xx > 0.08) & (xx < 0.3) & (yy > 0.02) & (yy < 0.35) & (zz > -0.15) & (zz < 0.25),
        "m1": brain & (xx > 0.25) & (xx < 0.75) & (yy > 0.28) & (yy < 0.7) & (zz > -0.1) & (zz < 0.35),
        "m2": brain & (xx > 0.2) & (xx < 0.65) & (yy > -0.1) & (yy < 0.32) & (zz > -0.05) & (zz < 0.35),
        "m3": brain & (xx > 0.18) & (xx < 0.58) & (yy > -0.55) & (yy < -0.08) & (zz > -0.05) & (zz < 0.35),
        "m4": brain & (xx > 0.25) & (xx < 0.75) & (yy > 0.32) & (yy < 0.75) & (zz > 0.35) & (zz < 0.85),
        "m5": brain & (xx > 0.18) & (xx < 0.65) & (yy > -0.05) & (yy < 0.3) & (zz > 0.3) & (zz < 0.85),
        "m6": brain & (xx > 0.15) & (xx < 0.58) & (yy > -0.58) & (yy < -0.08) & (zz > 0.28) & (zz < 0.85),
    }
    return {name: mask.astype(bool) for name, mask in masks.items()}


@lru_cache(maxsize=8)
def build_aspects_atlas(shape: tuple[int, int, int] = DEFAULT_TARGET_SHAPE) -> dict[str, dict[str, np.ndarray]]:
    right = _right_hemisphere_masks(shape)
    left = {name: np.flip(mask, axis=2) for name, mask in right.items()}
    return {"left": left, "right": right}


def region_overlaps(probability_map: np.ndarray, hemisphere: str) -> dict[str, float]:
    atlas = build_aspects_atlas(tuple(probability_map.shape))[hemisphere]
    scores: dict[str, float] = {}
    for region, mask in atlas.items():
        if not np.any(mask):
            scores[region] = 0.0
            continue
        scores[region] = float(probability_map[mask].mean())
    return scores


def affected_regions_from_mask(mask: np.ndarray, hemisphere: str, threshold: float = 0.2) -> set[str]:
    atlas = build_aspects_atlas(tuple(mask.shape))[hemisphere]
    affected: set[str] = set()
    for region, region_mask in atlas.items():
        overlap = float(mask[region_mask].mean()) if np.any(region_mask) else 0.0
        if overlap >= threshold:
            affected.add(region)
    return affected


def aspects_from_probability_map(
    probability_map: np.ndarray,
    hemisphere: str,
    threshold: float = 0.42,
) -> tuple[int, list[RegionScore]]:
    overlaps = region_overlaps(probability_map, hemisphere)
    region_scores: list[RegionScore] = []
    affected_count = 0
    for region in ATLAS_REGIONS:
        probability = overlaps.get(region, 0.0)
        affected = probability >= threshold
        affected_count += int(affected)
        region_scores.append(
            RegionScore(
                region=region,
                probability=round(probability, 4),
                affected=affected,
                score_delta=-1 if affected else 0,
                atlas_overlap=round(probability, 4),
                summary=(
                    f"{region} suggests early ischemia"
                    if affected
                    else f"{region} remains below ischemic threshold"
                ),
            )
        )
    return len(ATLAS_REGIONS) - affected_count, region_scores
