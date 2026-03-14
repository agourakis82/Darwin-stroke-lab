from __future__ import annotations

import hashlib
import importlib.util
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter, sobel, zoom

from sounio_stroke_lab.atlas import build_aspects_atlas
from sounio_stroke_lab.config import DEFAULT_TARGET_SHAPE
from sounio_stroke_lab.hypercomplex import local_contrast
from sounio_stroke_lab.schemas import InputMode


@dataclass
class LoadedVolume:
    volume: np.ndarray
    input_mode: InputMode
    warnings: list[str]


@dataclass
class PreprocessedVolume:
    volume: np.ndarray
    asymmetry: np.ndarray
    gradient: np.ndarray
    contrast: np.ndarray
    hemisphere: str
    atlas: dict[str, np.ndarray]
    warnings: list[str]


def _hash_paths(paths: list[Path]) -> int:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return int.from_bytes(digest.digest()[:8], "big", signed=False)


def _supports_pydicom() -> bool:
    return importlib.util.find_spec("pydicom") is not None


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
        raise ValueError("Research volume must be 3D or 2D.")
    return array.astype(np.float32)


def _load_nifti(path: Path) -> np.ndarray:
    if not _supports_nibabel():
        raise RuntimeError("nibabel is required to load NIfTI files.")
    import nibabel as nib  # type: ignore

    array = np.asarray(nib.load(str(path)).get_fdata(dtype=np.float32), dtype=np.float32)
    if array.ndim != 3:
        raise ValueError("NIfTI volume must be 3D.")
    return array


def _load_dicom(paths: list[Path]) -> np.ndarray:
    import pydicom  # type: ignore

    slices: list[tuple[float, np.ndarray]] = []
    for path in paths:
        dataset = pydicom.dcmread(path)
        pixels = dataset.pixel_array.astype(np.float32)
        slope = float(getattr(dataset, "RescaleSlope", 1.0))
        intercept = float(getattr(dataset, "RescaleIntercept", 0.0))
        pixels = pixels * slope + intercept
        order = float(getattr(dataset, "InstanceNumber", len(slices)))
        slices.append((order, pixels))
    slices.sort(key=lambda item: item[0])
    volume = np.stack([pixels for _, pixels in slices], axis=0)
    if volume.max(initial=0.0) > 1.5:
        volume = (volume + 100.0) / 500.0
    return np.clip(volume, 0.0, 1.0)


def load_volume_from_paths(paths: list[Path]) -> LoadedVolume:
    suffixes = {path.suffix.lower() for path in paths}
    warnings: list[str] = []
    if ".npy" in suffixes:
        warnings.append("Research-only .npy input bypasses clinical image decoding.")
        return LoadedVolume(volume=_load_npy(paths[0]), input_mode=InputMode.research, warnings=warnings)
    if any(path.name.endswith(".nii") or path.name.endswith(".nii.gz") for path in paths):
        warnings.append("Research-only NIfTI input bypasses direct DICOM decoding.")
        return LoadedVolume(volume=_load_nifti(paths[0]), input_mode=InputMode.research, warnings=warnings)
    if suffixes.issubset({".dcm"}) and _supports_pydicom():
        return LoadedVolume(volume=_load_dicom(paths), input_mode=InputMode.dicom, warnings=warnings)
    if suffixes.issubset({".dcm"}) and not _supports_pydicom():
        raise RuntimeError("pydicom is required to decode DICOM series for scientific use.")
    else:
        warnings.append("Non-DICOM inputs run in demo mode and are excluded from scientific evidence.")
    seed = _hash_paths(paths)
    return LoadedVolume(volume=_demo_volume(seed), input_mode=InputMode.demo, warnings=warnings)


def _demo_volume(seed: int, shape: tuple[int, int, int] = DEFAULT_TARGET_SHAPE) -> np.ndarray:
    rng = np.random.default_rng(seed)
    zz, yy, xx = np.meshgrid(
        np.linspace(-1.0, 1.0, shape[0], dtype=np.float32),
        np.linspace(-1.0, 1.0, shape[1], dtype=np.float32),
        np.linspace(-1.0, 1.0, shape[2], dtype=np.float32),
        indexing="ij",
    )
    radial = np.sqrt(xx**2 + yy**2 + zz**2)
    base = 0.8 - 0.4 * radial + 0.05 * np.sin(yy * np.pi * 2.0)
    base += rng.normal(0.0, 0.03, size=shape).astype(np.float32)
    return np.clip(gaussian_filter(base, sigma=1.0), 0.0, 1.0).astype(np.float32)


def clip_and_normalize(volume: np.ndarray) -> np.ndarray:
    clipped = np.clip(volume, 0.0, 1.0)
    mean = float(clipped.mean())
    std = float(clipped.std()) or 1.0
    normalized = (clipped - mean) / std
    normalized = 0.5 + 0.18 * normalized
    return np.clip(normalized, 0.0, 1.0).astype(np.float32)


def resample_volume(volume: np.ndarray, shape: tuple[int, int, int] = DEFAULT_TARGET_SHAPE) -> np.ndarray:
    factors = tuple(target / current for target, current in zip(shape, volume.shape))
    return zoom(volume, factors, order=1).astype(np.float32)


def estimate_hemisphere(volume: np.ndarray) -> str:
    mid = volume.shape[2] // 2
    left = volume[:, :, :mid]
    right = volume[:, :, mid:]
    mirrored_right = np.flip(right, axis=2)
    mirrored_left = np.flip(left, axis=2)

    # Estimate which hemisphere is darker than its mirrored counterpart.
    left_deficit = float(np.clip(mirrored_right - left, 0.0, None).mean())
    right_deficit = float(np.clip(mirrored_left - right, 0.0, None).mean())
    if abs(left_deficit - right_deficit) > 1e-4:
        return "left" if left_deficit > right_deficit else "right"

    # Near-symmetric studies fall back to the darker mean hemisphere.
    return "left" if float(left.mean()) < float(right.mean()) else "right"


def prepare_volume(volume: np.ndarray, warnings: list[str] | None = None) -> PreprocessedVolume:
    warnings = list(warnings or [])
    resampled = resample_volume(clip_and_normalize(volume))
    mirrored = np.flip(resampled, axis=2)
    asymmetry = np.clip(mirrored - resampled, 0.0, None)
    gradient = np.sqrt(
        np.square(sobel(resampled, axis=0))
        + np.square(sobel(resampled, axis=1))
        + np.square(sobel(resampled, axis=2))
    )
    gradient = gradient / (float(gradient.max()) or 1.0)
    contrast = local_contrast(gaussian_filter(resampled, sigma=0.7))
    contrast = contrast / (float(contrast.max()) or 1.0)
    hemisphere = estimate_hemisphere(resampled)
    atlas = build_aspects_atlas(tuple(resampled.shape))[hemisphere]
    return PreprocessedVolume(
        volume=resampled,
        asymmetry=asymmetry.astype(np.float32),
        gradient=gradient.astype(np.float32),
        contrast=contrast.astype(np.float32),
        hemisphere=hemisphere,
        atlas=atlas,
        warnings=warnings,
    )
