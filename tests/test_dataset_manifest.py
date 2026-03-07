from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from sounio_stroke_lab.cli import main as cli_main
from sounio_stroke_lab.dataset_manifest import (
    build_aisd_manifest,
    build_dicom_cohort_manifest,
    build_index_manifest,
    load_benchmark_manifest,
    validate_benchmark_manifest,
)


def _write_dummy_dicom_series(series_dir: Path, slices: int = 5) -> None:
    series_dir.mkdir(parents=True, exist_ok=True)
    for index in range(slices):
        (series_dir / f"slice-{index:03d}.dcm").write_bytes(b"DICM")


def test_build_dicom_cohort_manifest_discovers_cases_and_validates(tmp_path: Path):
    cohort_root = tmp_path / "cohort"
    for index in range(12):
        case_dir = cohort_root / f"case-{index:03d}"
        _write_dummy_dicom_series(case_dir / ("dicom" if index % 2 == 0 else "NCCT"))
        np.save(
            case_dir / ("lesion_mask.npy" if index % 2 == 0 else "mask.npy"),
            np.full((8, 8, 8), float(index % 3 == 0), dtype=np.float32),
        )

    manifest_path = build_dicom_cohort_manifest(cohort_root, tmp_path / "dicom_manifest.json")

    manifest, issues = validate_benchmark_manifest(manifest_path)

    assert manifest.dataset_name == "local-ncct-dicom"
    assert len(manifest.cases) == 12
    assert issues == []
    assert any(item.split == "train" for item in manifest.cases)
    assert any(item.split == "test" for item in manifest.cases)


def test_build_index_manifest_from_csv_and_cli_validate(tmp_path: Path, capsys):
    volume_path = tmp_path / "case_volume.npy"
    mask_path = tmp_path / "case_mask.npy"
    np.save(volume_path, np.zeros((8, 8, 8), dtype=np.float32))
    np.save(mask_path, np.zeros((8, 8, 8), dtype=np.float32))

    index_path = tmp_path / "index.csv"
    with index_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case_id", "split", "volume_path", "lesion_mask_path", "hemisphere"])
        writer.writeheader()
        writer.writerow(
            {
                "case_id": "case-001",
                "split": "train",
                "volume_path": str(volume_path),
                "lesion_mask_path": str(mask_path),
                "hemisphere": "left",
            }
        )
        writer.writerow(
            {
                "case_id": "case-002",
                "split": "test",
                "volume_path": str(volume_path),
                "lesion_mask_path": str(mask_path),
                "hemisphere": "right",
            }
        )

    manifest_path = build_index_manifest(
        index_path=index_path,
        output_path=tmp_path / "index_manifest.json",
        dataset_name="local-index",
        dataset_version="index-v1",
    )

    assert load_benchmark_manifest(manifest_path).dataset_version == "index-v1"

    exit_code = cli_main(["validate-manifest", "--manifest", str(manifest_path)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["valid"] is True
    assert payload["case_count"] == 2


def test_build_aisd_manifest_uses_public_fixed_test_ids(tmp_path: Path):
    image_root = tmp_path / "images"
    mask_root = tmp_path / "masks"
    image_root.mkdir()
    mask_root.mkdir()

    for case_id in ("case_025", "case_026"):
        np.save(image_root / f"{case_id}.npy", np.zeros((8, 8, 8), dtype=np.float32))
        np.save(mask_root / f"{case_id}.npy", np.zeros((8, 8, 8), dtype=np.float32))

    manifest_path = build_aisd_manifest(image_root, mask_root, tmp_path / "aisd_manifest.json")
    manifest = load_benchmark_manifest(manifest_path)
    split_by_case = {item.case_id: item.split for item in manifest.cases}

    assert split_by_case["case_025"] == "test"
    assert split_by_case["case_026"] == "train"
