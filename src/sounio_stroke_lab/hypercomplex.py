from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter


@dataclass(frozen=True)
class HypercomplexNumber:
    r: float
    i: float
    j: float
    k: float

    def conjugate(self) -> "HypercomplexNumber":
        return HypercomplexNumber(self.r, -self.i, -self.j, -self.k)

    def norm(self) -> float:
        return float(np.sqrt(self.r**2 + self.i**2 + self.j**2 + self.k**2))

    def __mul__(self, other: "HypercomplexNumber") -> "HypercomplexNumber":
        return HypercomplexNumber(
            self.r * other.r - self.i * other.i - self.j * other.j - self.k * other.k,
            self.r * other.i + self.i * other.r + self.j * other.k - self.k * other.j,
            self.r * other.j - self.i * other.k + self.j * other.r + self.k * other.i,
            self.r * other.k + self.i * other.j - self.j * other.i + self.k * other.r,
        )


def logistic(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -60.0, 60.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def local_contrast(volume: np.ndarray) -> np.ndarray:
    return np.abs(volume - gaussian_filter(volume, sigma=1.2))


def quaternion_channels(
    deficit: np.ndarray,
    asymmetry: np.ndarray,
    contrast: np.ndarray,
    gradient: np.ndarray,
) -> np.ndarray:
    return np.stack((deficit, asymmetry, contrast, gradient), axis=0)


def quaternion_energy(channels: np.ndarray) -> np.ndarray:
    return np.sqrt(np.clip(np.sum(np.square(channels), axis=0), 0.0, None))


def phase_coupling(channels: np.ndarray) -> np.ndarray:
    real, imag_i, imag_j, imag_k = channels
    return np.tanh(real * imag_i + imag_j * imag_k - imag_i * imag_k)
