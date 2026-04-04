from __future__ import annotations

import csv
import hashlib
import json
import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from sounio_stroke_lab.atlas import affected_regions_from_mask
from sounio_stroke_lab.config import ATLAS_REGIONS
from sounio_stroke_lab.preprocessing import load_volume_from_paths
from sounio_stroke_lab.schemas import BenchmarkCaseManifest, BenchmarkDatasetManifest


MASK_SUFFIXES = (".npy", ".npz", ".nii", ".nii.gz")
VOLUME_SUFFIXES = (".npy", ".npz", ".nii", ".nii.gz", ".dcm")
STACK_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")
DICOM_DIR_NAMES = ("dicom", "DICOM", "ncct", "NCCT", "series", "SERIES")
AISD_FIXED_TEST_IDS = {25, 29, 32, 35, 37, 43, 44, 63, 71, 83, 108, 134, 135, 159, 164, 201, 202, 211, 217, 221}


def _infer_reference_scope(metadata: dict[str, object]) -> str:
    explicit = metadata.get("reference_scope")
    if isinstance(explicit, str) and explicit:
        return explicit
    if metadata.get("volume_kind") == "png-stack" and metadata.get("source_layout") == "AISD image/mask export":
        return "segmentation_only"
    return "full_benchmark"


def _reference_flag(metadata: dict[str, object], key: str, default: bool) -> bool:
    explicit = metadata.get(key)
    if isinstance(explicit, bool):
        return explicit
    scope = _infer_reference_scope(metadata)
    if scope == "segmentation_only":
        if key == "segmentation_reference_available":
            return True
        return False
    return default


def _supports_nibabel() -> bool:
    return importlib.util.find_spec("nibabel") is not None


def _load_npy(path: Path) -> np.ndarray:
    array = np.load(path)
    if isinstance(array, np.lib.npyio.NpzFile):
        first_key = next(iter(array.files))
        array = array[first_key]
    if array.ndim == 2:
        array = np.repeat(array[np.newaxis, :, :], 8, axis=0)
    if array.ndim != 3:
        raise ValueError(f"Expected 3D array at {path}, got shape {array.shape}.")
    return array.astype(np.float32)


def _load_nifti(path: Path) -> tuple[np.ndarray, float]:
    if not _supports_nibabel():
        raise RuntimeError("nibabel is required to load NIfTI benchmark data.")
    import nibabel as nib  # type: ignore

    image = nib.load(str(path))
    array = image.get_fdata(dtype=np.float32)
    if array.ndim != 3:
        raise ValueError(f"Expected 3D NIfTI at {path}, got shape {array.shape}.")
    zooms = image.header.get_zooms()[:3]
    voxel_volume_ml = float(np.prod(zooms) / 1000.0) if zooms else 1.0
    return np.asarray(array, dtype=np.float32), voxel_volume_ml


def _dicom_voxel_volume_ml(dicom_paths: list[Path]) -> float:
    if not dicom_paths or importlib.util.find_spec("pydicom") is None:
        return 1.0
    import pydicom  # type: ignore

    try:
        dataset = pydicom.dcmread(dicom_paths[0], stop_before_pixels=True)
        pixel_spacing = [float(value) for value in getattr(dataset, "PixelSpacing", [1.0, 1.0])]
        slice_thickness = float(getattr(dataset, "SliceThickness", 1.0))
        return float((pixel_spacing[0] * pixel_spacing[1] * slice_thickness) / 1000.0)
    except Exception:
        return 1.0


def _extract_dicom_series_metadata(dicom_dir: Path) -> dict[str, object]:
    if importlib.util.find_spec("pydicom") is None:
        return {}
    import pydicom  # type: ignore

    dicom_paths = sorted(candidate for candidate in dicom_dir.iterdir() if candidate.is_file() and candidate.suffix.lower() == ".dcm")
    if not dicom_paths:
        return {}
    try:
        dataset = pydicom.dcmread(dicom_paths[0], stop_before_pixels=True)
    except Exception:
        return {}
    pixel_spacing = getattr(dataset, "PixelSpacing", None)
    metadata: dict[str, object] = {
        "manufacturer": getattr(dataset, "Manufacturer", None),
        "model_name": getattr(dataset, "ManufacturerModelName", None),
        "convolution_kernel": getattr(dataset, "ConvolutionKernel", None),
        "slice_thickness_mm": float(getattr(dataset, "SliceThickness", 0.0) or 0.0),
        "kvp": float(getattr(dataset, "KVP", 0.0) or 0.0),
        "pixel_spacing_mm": (
            [float(value) for value in pixel_spacing]
            if pixel_spacing is not None
            else None
        ),
        "rows": int(getattr(dataset, "Rows", 0) or 0),
        "columns": int(getattr(dataset, "Columns", 0) or 0),
        "dicom_file_count": len(dicom_paths),
    }
    return {key: value for key, value in metadata.items() if value not in {None, 0, 0.0, [], ""}}


def _load_volume(path: Path, *, treat_as_mask: bool = False) -> tuple[np.ndarray, float]:
    suffix = path.suffix.lower()
    if suffix == ".npy" or suffix == ".npz":
        array = _load_npy(path)
        if treat_as_mask:
            array = (array > 0.0).astype(np.float32)
        return array, 1.0
    if suffix == ".nii" or path.name.endswith(".nii.gz"):
        array, voxel_volume_ml = _load_nifti(path)
        if treat_as_mask:
            array = (array > 0.0).astype(np.float32)
        return array, voxel_volume_ml
    if path.is_dir():
        dicom_paths = sorted(candidate for candidate in path.iterdir() if candidate.is_file())
        return load_volume_from_paths(dicom_paths, treat_as_mask=treat_as_mask).volume, _dicom_voxel_volume_ml(dicom_paths)
    return load_volume_from_paths([path], treat_as_mask=treat_as_mask).volume, _dicom_voxel_volume_ml([path] if path.suffix.lower() == ".dcm" else [])


def _strip_all_suffixes(path: Path) -> str:
    name = path.name
    while True:
        stem = Path(name).stem
        if stem == name:
            return stem
        name = stem


def _resolve_path(manifest_path: Path, raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return (manifest_path.parent / path).resolve()


def _is_local_placeholder(path: Path) -> bool:
    try:
        stat_result = os.stat(path)
    except OSError:
        return False
    return stat_result.st_size > 0 and stat_result.st_blocks == 0


def _placeholder_issue(path: Path) -> str | None:
    if path.is_file() and _is_local_placeholder(path):
        return (
            f"Path is a cloud placeholder and not fully materialized locally: {path}. "
            "Move or download the dataset into a non-iCloud location before benchmarking."
        )
    if path.is_dir():
        try:
            files = sorted(candidate for candidate in path.iterdir() if candidate.is_file())
        except OSError:
            return None
        placeholder = next((candidate for candidate in files if _is_local_placeholder(candidate)), None)
        if placeholder is not None:
            return (
                f"Directory contains cloud placeholder files and is not fully materialized locally: {path} "
                f"(example: {placeholder.name}). Move or download the dataset into a non-iCloud location "
                "before benchmarking."
            )
    return None


def _infer_hemisphere(mask: np.ndarray) -> str:
    if float(mask.sum()) <= 0.0:
        return "right"
    mid = mask.shape[2] // 2
    left_mass = float(mask[:, :, :mid].sum())
    right_mass = float(mask[:, :, mid:].sum())
    return "left" if left_mass >= right_mass else "right"


def _stable_split(case_id: str) -> str:
    bucket = int(hashlib.sha256(case_id.encode("utf-8")).hexdigest(), 16) % 100
    return "train" if bucket < 70 else "val" if bucket < 85 else "test"


def _looks_like_dicom_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    dcm_files = [candidate for candidate in path.iterdir() if candidate.is_file() and candidate.suffix.lower() == ".dcm"]
    return len(dcm_files) >= 4


def _find_dicom_series_dir(case_dir: Path) -> Path | None:
    for name in DICOM_DIR_NAMES:
        candidate = case_dir / name
        if _looks_like_dicom_dir(candidate):
            return candidate
    if _looks_like_dicom_dir(case_dir):
        return case_dir
    nested = [candidate for candidate in case_dir.iterdir() if _looks_like_dicom_dir(candidate)]
    if not nested:
        return None
    return max(
        nested,
        key=lambda item: len([child for child in item.iterdir() if child.is_file() and child.suffix.lower() == ".dcm"]),
    )


def _find_mask_file(case_dir: Path) -> Path | None:
    exact_candidates = (
        "lesion_mask.nii.gz",
        "lesion_mask.nii",
        "lesion_mask.npy",
        "lesion_mask.npz",
        "mask.nii.gz",
        "mask.nii",
        "mask.npy",
        "mask.npz",
        "lesion-msk.nii.gz",
    )
    for name in exact_candidates:
        candidate = case_dir / name
        if candidate.exists():
            return candidate
    fuzzy = [
        candidate
        for candidate in case_dir.rglob("*")
        if candidate.is_file()
        and (
            candidate.name.endswith(MASK_SUFFIXES)
            and ("mask" in candidate.name.lower() or "lesion" in candidate.name.lower())
        )
    ]
    if not fuzzy:
        return None
    return sorted(fuzzy)[0]


@dataclass
class LoadedBenchmarkCase:
    case_id: str
    split: str
    volume: np.ndarray
    lesion_mask: np.ndarray
    hemisphere: str
    aspects_score: int
    voxel_volume_ml: float
    volume_path: Path
    lesion_mask_path: Path
    metadata: dict[str, object]
    reference_scope: str
    segmentation_reference_available: bool
    hemisphere_reference_available: bool
    region_reference_available: bool
    aspects_reference_available: bool

    @property
    def affected_regions(self) -> list[str]:
        return sorted(affected_regions_from_mask(self.lesion_mask, self.hemisphere))

    @property
    def reference_regions(self) -> list[str]:
        if not self.region_reference_available:
            return []
        return self.affected_regions

    @property
    def lesion_positive(self) -> bool:
        return bool(np.asarray(self.lesion_mask > 0.25, dtype=bool).any())


def load_benchmark_manifest(manifest_path: Path) -> BenchmarkDatasetManifest:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    return BenchmarkDatasetManifest.model_validate(payload)


def validate_benchmark_manifest(
    manifest_path: Path,
    required_splits: set[str] | None = None,
) -> tuple[BenchmarkDatasetManifest, list[str]]:
    manifest = load_benchmark_manifest(manifest_path)
    issues: list[str] = []
    required_splits = required_splits or {"train", "test"}
    if not manifest.cases:
        issues.append("Manifest contains no cases.")
        return manifest, issues

    seen_case_ids: set[str] = set()
    split_counts: dict[str, int] = {}
    for item in manifest.cases:
        if item.case_id in seen_case_ids:
            issues.append(f"Duplicate case_id detected: {item.case_id}")
        seen_case_ids.add(item.case_id)
        split_counts[item.split] = split_counts.get(item.split, 0) + 1

        volume_path = _resolve_path(manifest_path, item.volume_path)
        lesion_mask_path = _resolve_path(manifest_path, item.lesion_mask_path)
        if not volume_path.exists():
            issues.append(f"Missing volume path for {item.case_id}: {volume_path}")
        else:
            placeholder_issue = _placeholder_issue(volume_path)
            if placeholder_issue is not None:
                issues.append(f"{item.case_id}: {placeholder_issue}")
        if not lesion_mask_path.exists():
            issues.append(f"Missing lesion mask path for {item.case_id}: {lesion_mask_path}")
        else:
            placeholder_issue = _placeholder_issue(lesion_mask_path)
            if placeholder_issue is not None:
                issues.append(f"{item.case_id}: {placeholder_issue}")
        if volume_path == lesion_mask_path:
            issues.append(f"Volume and lesion mask resolve to the same path for {item.case_id}: {volume_path}")
        if not item.split:
            issues.append(f"Empty split for {item.case_id}")

    for split_name in sorted(required_splits):
        if split_name not in split_counts:
            issues.append(f"Manifest has no {split_name} split.")
    return manifest, issues


def load_manifest_cases(manifest_path: Path, split: str = "test") -> tuple[BenchmarkDatasetManifest, list[LoadedBenchmarkCase]]:
    manifest = load_benchmark_manifest(manifest_path)
    cases: list[LoadedBenchmarkCase] = []
    for item in manifest.cases:
        if item.split != split:
            continue
        volume_path = _resolve_path(manifest_path, item.volume_path)
        mask_path = _resolve_path(manifest_path, item.lesion_mask_path)
        volume_placeholder_issue = _placeholder_issue(volume_path)
        if volume_placeholder_issue is not None:
            raise ValueError(f"{item.case_id}: {volume_placeholder_issue}")
        mask_placeholder_issue = _placeholder_issue(mask_path)
        if mask_placeholder_issue is not None:
            raise ValueError(f"{item.case_id}: {mask_placeholder_issue}")
        volume, _ = _load_volume(volume_path)
        lesion_mask, voxel_volume_ml = _load_volume(mask_path, treat_as_mask=True)
        hemisphere = item.hemisphere or _infer_hemisphere(lesion_mask)
        derived_regions = affected_regions_from_mask(lesion_mask, hemisphere)
        aspects_score = item.aspects_score if item.aspects_score is not None else len(ATLAS_REGIONS) - len(derived_regions)
        reference_scope = _infer_reference_scope(item.metadata)
        segmentation_reference_available = _reference_flag(item.metadata, "segmentation_reference_available", True)
        hemisphere_reference_available = _reference_flag(
            item.metadata,
            "hemisphere_reference_available",
            item.hemisphere is not None,
        )
        region_reference_available = _reference_flag(
            item.metadata,
            "region_reference_available",
            item.hemisphere is not None and item.aspects_score is not None,
        )
        aspects_reference_available = _reference_flag(
            item.metadata,
            "aspects_reference_available",
            item.aspects_score is not None,
        )
        cases.append(
            LoadedBenchmarkCase(
                case_id=item.case_id,
                split=item.split,
                volume=volume,
                lesion_mask=lesion_mask,
                hemisphere=hemisphere,
                aspects_score=aspects_score,
                voxel_volume_ml=voxel_volume_ml,
                volume_path=volume_path,
                lesion_mask_path=mask_path,
                metadata=item.metadata,
                reference_scope=reference_scope,
                segmentation_reference_available=segmentation_reference_available,
                hemisphere_reference_available=hemisphere_reference_available,
                region_reference_available=region_reference_available,
                aspects_reference_available=aspects_reference_available,
            )
        )
    return manifest, cases


def build_isles24_manifest(dataset_root: Path, output_path: Path) -> Path:
    raw_cases = sorted(dataset_root.rglob("*_ncct.nii.gz"))
    entries: list[BenchmarkCaseManifest] = []
    for ncct_path in raw_cases:
        subject = next((part for part in ncct_path.parts if part.startswith("sub-")), ncct_path.stem)
        candidate_masks = list(ncct_path.parent.parent.rglob(f"{subject}*_lesion-msk.nii.gz"))
        if not candidate_masks:
            continue
        mask_path = candidate_masks[0]
        cta_path = next(iter(sorted(ncct_path.parent.parent.rglob(f"{subject}*_cta.nii.gz"))), None)
        ctp_path = next(iter(sorted(ncct_path.parent.parent.rglob(f"{subject}*_ctp.nii.gz"))), None)
        perfusion_maps = sorted(
            str(path.resolve())
            for path in ncct_path.parent.parent.rglob(f"{subject}*_space-ncct_ctp-*.nii.gz")
        )
        baseline_csv = next(iter(sorted(dataset_root.rglob(f"{subject}*_baseline-*.csv"))), None)
        outcome_csv = next(iter(sorted(dataset_root.rglob(f"{subject}*_outcome-*.csv"))), None)
        entries.append(
            BenchmarkCaseManifest(
                case_id=subject,
                split=_stable_split(subject),
                volume_path=str(ncct_path.resolve()),
                lesion_mask_path=str(mask_path.resolve()),
                metadata={
                    "dataset": "ISLES24",
                    "volume_kind": "nifti",
                    "cta_path": str(cta_path.resolve()) if cta_path else None,
                    "ctp_path": str(ctp_path.resolve()) if ctp_path else None,
                    "perfusion_maps": perfusion_maps,
                    "clinical_baseline_csv": str(baseline_csv.resolve()) if baseline_csv else None,
                    "outcome_csv": str(outcome_csv.resolve()) if outcome_csv else None,
                },
            )
        )
    manifest = BenchmarkDatasetManifest(
        dataset_name="ISLES24",
        dataset_version="isles24-manifest-v1",
        split_policy="stable subject hash 70/15/15",
        source="https://www.isles-challenge.org/",
        cases=entries,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return output_path


def build_dicom_cohort_manifest(
    dataset_root: Path,
    output_path: Path,
    dataset_name: str = "local-ncct-dicom",
    dataset_version: str = "local-dicom-manifest-v1",
    source: str = "local filesystem",
) -> Path:
    entries: list[BenchmarkCaseManifest] = []
    for case_dir in sorted(candidate for candidate in dataset_root.iterdir() if candidate.is_dir()):
        dicom_dir = _find_dicom_series_dir(case_dir)
        mask_path = _find_mask_file(case_dir)
        if dicom_dir is None or mask_path is None:
            continue
        case_id = case_dir.name
        entries.append(
            BenchmarkCaseManifest(
                case_id=case_id,
                split=_stable_split(case_id),
                volume_path=str(dicom_dir.resolve()),
                lesion_mask_path=str(mask_path.resolve()),
                metadata={
                    "dataset": dataset_name,
                    "volume_kind": "dicom-series",
                    "case_dir": str(case_dir.resolve()),
                    **_extract_dicom_series_metadata(dicom_dir),
                },
            )
        )
    if not entries:
        raise ValueError(f"No complete DICOM + lesion mask cases found under {dataset_root}.")
    manifest = BenchmarkDatasetManifest(
        dataset_name=dataset_name,
        dataset_version=dataset_version,
        split_policy="stable subject hash 70/15/15",
        source=source,
        cases=entries,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return output_path


def build_aisd_manifest(
    image_root: Path,
    mask_root: Path,
    output_path: Path,
    dataset_name: str = "AISD",
    dataset_version: str = "aisd-manifest-v1",
    source: str = "https://github.com/GriffinLiang/AISD",
) -> Path:
    split_policy = "AISD fixed public test ids, remaining cases marked as train"

    def finalize_manifest(entries: list[BenchmarkCaseManifest]) -> Path:
        nonlocal split_policy
        if entries and not any(entry.split == "test" for entry in entries):
            split_policy = "stable subject hash 70/15/15 fallback for AISD exports without public fixed test ids"
            entries = [
                entry.model_copy(update={"split": _stable_split(entry.case_id)})
                for entry in entries
            ]
        manifest = BenchmarkDatasetManifest(
            dataset_name=dataset_name,
            dataset_version=dataset_version,
            split_policy=split_policy,
            source=source,
            cases=entries,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        return output_path

    def has_stack_images(case_dir: Path) -> bool:
        return case_dir.is_dir() and any(
            child.is_file() and child.suffix.lower() in STACK_IMAGE_SUFFIXES
            for child in case_dir.iterdir()
        )

    entries: list[BenchmarkCaseManifest] = []
    image_dirs = {path.name: path for path in sorted(image_root.iterdir()) if has_stack_images(path)}
    mask_dirs = {path.name: path for path in sorted(mask_root.iterdir()) if has_stack_images(path)}
    shared_case_ids = sorted(set(image_dirs) & set(mask_dirs))
    for case_id in shared_case_ids:
        numeric_case_id = "".join(character for character in case_id if character.isdigit())
        split = "test" if numeric_case_id and int(numeric_case_id) in AISD_FIXED_TEST_IDS else "train"
        entries.append(
            BenchmarkCaseManifest(
                case_id=case_id,
                split=split,
                volume_path=str(image_dirs[case_id].resolve()),
                lesion_mask_path=str(mask_dirs[case_id].resolve()),
                metadata={
                    "dataset": dataset_name,
                    "volume_kind": "png-stack",
                    "source_layout": "AISD image/mask export",
                    "reference_scope": "segmentation_only",
                    "segmentation_reference_available": True,
                    "hemisphere_reference_available": False,
                    "region_reference_available": False,
                    "aspects_reference_available": False,
                },
            )
        )
    if entries:
        return finalize_manifest(entries)

    mask_by_id: dict[str, Path] = {}
    for path in sorted(mask_root.rglob("*")):
        if path.is_file() and path.name.endswith(MASK_SUFFIXES):
            mask_by_id[_strip_all_suffixes(path)] = path

    entries = []
    for volume_path in sorted(image_root.rglob("*")):
        if not volume_path.is_file() or not volume_path.name.endswith(MASK_SUFFIXES):
            continue
        case_id = _strip_all_suffixes(volume_path)
        mask_path = mask_by_id.get(case_id)
        if mask_path is None:
            continue
        numeric_case_id = "".join(character for character in case_id if character.isdigit())
        split = "test" if numeric_case_id and int(numeric_case_id) in AISD_FIXED_TEST_IDS else "train"
        entries.append(
            BenchmarkCaseManifest(
                case_id=case_id,
                split=split,
                volume_path=str(volume_path.resolve()),
                lesion_mask_path=str(mask_path.resolve()),
                metadata={
                    "dataset": dataset_name,
                    "volume_kind": "nifti-or-array",
                    "source_layout": "AISD image/mask export",
                },
            )
        )
    if not entries:
        raise ValueError(f"No paired AISD image/mask cases found under {image_root} and {mask_root}.")
    return finalize_manifest(entries)


def build_index_manifest(
    index_path: Path,
    output_path: Path,
    dataset_name: str,
    dataset_version: str,
    source: str = "local index",
) -> Path:
    suffix = index_path.suffix.lower()
    if suffix == ".csv":
        with index_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    elif suffix in {".jsonl", ".ndjson"}:
        rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        raise ValueError(f"Unsupported index file type: {index_path}")

    entries: list[BenchmarkCaseManifest] = []
    for row in rows:
        case_id = str(row["case_id"])
        split = str(row.get("split") or _stable_split(case_id))
        aspects_score = row.get("aspects_score")
        hemisphere = row.get("hemisphere")
        metadata = dict(row)
        entries.append(
            BenchmarkCaseManifest(
                case_id=case_id,
                split=split,
                volume_path=str(row["volume_path"]),
                lesion_mask_path=str(row["lesion_mask_path"]),
                hemisphere=str(hemisphere) if hemisphere else None,
                aspects_score=int(aspects_score) if aspects_score not in {None, ""} else None,
                metadata=metadata,
            )
        )
    if not entries:
        raise ValueError(f"No cases found in {index_path}.")
    manifest = BenchmarkDatasetManifest(
        dataset_name=dataset_name,
        dataset_version=dataset_version,
        split_policy="from index file or stable subject hash fallback",
        source=source,
        cases=entries,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return output_path
