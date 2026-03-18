from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter

from sounio_stroke_lab.atlas import build_aspects_atlas
from sounio_stroke_lab.config import DEFAULT_TARGET_SHAPE


def _base_volume() -> np.ndarray:
    shape = DEFAULT_TARGET_SHAPE
    zz, yy, xx = np.meshgrid(
        np.linspace(-1.0, 1.0, shape[0], dtype=np.float32),
        np.linspace(-1.0, 1.0, shape[1], dtype=np.float32),
        np.linspace(-1.0, 1.0, shape[2], dtype=np.float32),
        indexing="ij",
    )
    radial = np.sqrt((xx * 1.2) ** 2 + yy**2 + (zz * 0.9) ** 2)
    return np.clip(0.82 - 0.45 * radial + 0.02 * np.sin(yy * np.pi * 2.0), 0.0, 1.0).astype(np.float32)


def _write_case(
    case_dir: Path,
    case_id: str,
    hemisphere: str,
    regions: list[str],
    lesion_drop: float = 0.22,
    spread_sigma: float = 1.0,
) -> tuple[str, str]:
    atlas = build_aspects_atlas(DEFAULT_TARGET_SHAPE)[hemisphere]
    volume = _base_volume()
    lesion_mask = np.zeros(DEFAULT_TARGET_SHAPE, dtype=np.float32)
    for region in regions:
        lesion_mask[atlas[region]] = 1.0
    if regions:
        lesion_field = gaussian_filter(lesion_mask, sigma=spread_sigma)
        lesion_field = lesion_field / (float(lesion_field.max()) or 1.0)
        volume -= lesion_drop * lesion_field
        volume -= 0.05 * gaussian_filter(lesion_field, sigma=1.2)
        volume = np.clip(volume, 0.0, 1.0)
    volume_path = case_dir / f"{case_id}_volume.npy"
    mask_path = case_dir / f"{case_id}_mask.npy"
    np.save(volume_path, volume)
    np.save(mask_path, lesion_mask)
    return str(volume_path), str(mask_path)


def create_benchmark_manifest(tmp_path: Path) -> Path:
    case_dir = tmp_path / "cases"
    case_dir.mkdir(parents=True, exist_ok=True)
    case_specs = [
        ("case-001", "train", "right", [], 0.20, 0.9),
        ("case-002", "train", "left", ["insula", "m2"], 0.24, 1.2),
        ("case-003", "train", "right", ["caudate", "lentiform"], 0.22, 1.0),
        ("case-004", "train", "left", ["m5"], 0.19, 0.8),
        ("case-005", "train", "right", ["m1", "m4"], 0.21, 1.1),
        ("case-006", "train", "left", [], 0.20, 0.9),
        ("case-007", "train", "right", ["internal_capsule", "insula"], 0.25, 1.3),
        ("case-008", "train", "left", ["m3", "m6"], 0.23, 1.0),
        ("case-009", "test", "right", [], 0.20, 0.9),
        ("case-010", "test", "left", ["insula", "m2", "m5"], 0.24, 1.2),
        ("case-011", "test", "right", ["caudate", "internal_capsule"], 0.23, 1.0),
        ("case-012", "test", "left", ["m4", "m6"], 0.22, 1.1),
    ]
    manifest = {
        "dataset_name": "fixture-real-benchmark",
        "dataset_version": "fixture-v1",
        "split_policy": "fixed train/test fixture",
        "source": "local pytest fixture",
        "cases": [],
    }
    for case_id, split, hemisphere, regions, lesion_drop, spread_sigma in case_specs:
        volume_path, mask_path = _write_case(
            case_dir,
            case_id,
            hemisphere,
            regions,
            lesion_drop=lesion_drop,
            spread_sigma=spread_sigma,
        )
        manifest["cases"].append(
            {
                "case_id": case_id,
                "split": split,
                "volume_path": volume_path,
                "lesion_mask_path": mask_path,
                "hemisphere": hemisphere,
                "aspects_score": 10 - len(regions),
            }
        )
    manifest_path = tmp_path / "benchmark_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def create_external_benchmark_manifest(tmp_path: Path) -> Path:
    case_dir = tmp_path / "external_cases"
    case_dir.mkdir(parents=True, exist_ok=True)
    case_specs = [
        ("external-101", "test", "right", ["insula", "m2"], 0.24, 1.2),
        ("external-102", "test", "left", ["caudate"], 0.20, 0.9),
        ("external-103", "test", "right", [], 0.20, 0.9),
        ("external-104", "test", "left", ["m4", "m5", "m6"], 0.26, 1.3),
    ]
    manifest = {
        "dataset_name": "fixture-external-benchmark",
        "dataset_version": "fixture-external-v1",
        "split_policy": "test-only external fixture",
        "source": "local pytest external fixture",
        "cases": [],
    }
    for case_id, split, hemisphere, regions, lesion_drop, spread_sigma in case_specs:
        volume_path, mask_path = _write_case(
            case_dir,
            case_id,
            hemisphere,
            regions,
            lesion_drop=lesion_drop,
            spread_sigma=spread_sigma,
        )
        manifest["cases"].append(
            {
                "case_id": case_id,
                "split": split,
                "volume_path": volume_path,
                "lesion_mask_path": mask_path,
                "hemisphere": hemisphere,
                "aspects_score": 10 - len(regions),
            }
        )
    manifest_path = tmp_path / "external_benchmark_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def create_segmentation_only_manifest(tmp_path: Path) -> Path:
    manifest_path = create_benchmark_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest["cases"]:
        item["hemisphere"] = None
        item["aspects_score"] = None
        item["metadata"] = {
            "reference_scope": "segmentation_only",
            "segmentation_reference_available": True,
            "hemisphere_reference_available": False,
            "region_reference_available": False,
            "aspects_reference_available": False,
        }
    manifest["dataset_version"] = "fixture-segmentation-only-v1"
    manifest["split_policy"] = "fixed train/test segmentation-only fixture"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def create_positive_only_segmentation_manifest(tmp_path: Path) -> Path:
    manifest_path = create_benchmark_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    positive_cases = []
    for item in manifest["cases"]:
        if item["split"] == "test" and item["aspects_score"] == 10:
            continue
        if item["split"] == "train" and item["aspects_score"] == 10:
            continue
        item["hemisphere"] = None
        item["aspects_score"] = None
        item["metadata"] = {
            "reference_scope": "segmentation_only",
            "segmentation_reference_available": True,
            "hemisphere_reference_available": False,
            "region_reference_available": False,
            "aspects_reference_available": False,
        }
        positive_cases.append(item)
    manifest["cases"] = positive_cases
    manifest["dataset_version"] = "fixture-positive-only-segmentation-v1"
    manifest["split_policy"] = "fixed train/test positive-only segmentation fixture"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def create_benchmark_manifest_with_val(tmp_path: Path) -> Path:
    manifest_path = create_benchmark_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest["cases"]:
        if item["case_id"] in {"case-007", "case-008"}:
            item["split"] = "val"
    manifest["dataset_version"] = "fixture-v1-with-val"
    manifest["split_policy"] = "fixed train/val/test fixture"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path
