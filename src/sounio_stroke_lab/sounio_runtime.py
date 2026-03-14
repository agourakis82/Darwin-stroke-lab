from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from sounio_stroke_lab.config import ATLAS_REGIONS


def _default_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.getenv("SOUNIO_ROOT")
    if env_root:
        roots.append(Path(env_root).expanduser())
    roots.extend(
        [
            Path.home() / "sounio",
            Path.home() / "sounio-lang-sounio",
        ]
    )
    return roots


def _candidate_binaries() -> list[Path]:
    candidates: list[Path] = []
    env_path = os.getenv("SOUNIO_SOUC_PATH")
    if env_path:
        candidates.append(Path(env_path).expanduser())
    which = shutil.which("souc")
    if which:
        candidates.append(Path(which))
    for root in _default_roots():
        candidates.append(root / "compiler" / "target" / "release" / "souc")
    return candidates


def _run_git(args: list[str], cwd: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.strip()


def _remote_is_official(remote_url: str) -> bool:
    normalized = remote_url.strip().lower()
    return normalized in {
        "git@github.com:sounio-lang/sounio.git",
        "https://github.com/sounio-lang/sounio.git",
        "https://github.com/sounio-lang/sounio",
    }


def _find_git_root(path: Path) -> Path | None:
    current = path.resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _format_number(value: float) -> str:
    return f"{value:.8f}"


def _array_literal(values: list[float]) -> str:
    return "[" + ", ".join(_format_number(value) for value in values) + "]"


def _int_array_literal(values: list[int]) -> str:
    return "[" + ", ".join(str(value) for value in values) + "]"


def _flatten_float_array(values: np.ndarray) -> list[float]:
    return np.asarray(values, dtype=np.float64).reshape(-1).tolist()


def _atlas_region_bits(atlas: dict[str, np.ndarray], shape: tuple[int, int, int]) -> list[int]:
    encoded = np.zeros(shape, dtype=np.int64)
    for region_index, region_name in enumerate(ATLAS_REGIONS):
        region_mask = np.asarray(atlas[region_name], dtype=bool)
        if region_mask.shape != shape:
            raise ValueError(f"Atlas region '{region_name}' does not match expected shape {shape}.")
        encoded |= region_mask.astype(np.int64) << region_index
    return encoded.reshape(-1).tolist()


@dataclass(frozen=True)
class SounioInferenceResult:
    region_scores: list[float]
    heatmap: np.ndarray


@dataclass(frozen=True)
class SounioRuntime:
    souc_path: Path
    stdlib_path: Path
    source: str

    @classmethod
    @lru_cache(maxsize=1)
    def auto(cls) -> "SounioRuntime | None":
        for candidate in _candidate_binaries():
            if not candidate.exists() or not os.access(candidate, os.X_OK):
                continue
            stdlib = cls._resolve_stdlib(candidate)
            if stdlib is not None:
                verified = cls._verify_candidate(candidate.resolve(), stdlib.resolve())
                if verified is not None:
                    return verified
        return None

    @classmethod
    def _verify_candidate(cls, binary: Path, stdlib: Path) -> "SounioRuntime | None":
        git_root = _find_git_root(binary)
        if git_root is None:
            return cls(souc_path=binary, stdlib_path=stdlib, source="explicit-or-path-binary")
        remote_url = _run_git(["remote", "get-url", "origin"], git_root)
        if not remote_url or not _remote_is_official(remote_url):
            return None
        status = _run_git(["status", "--porcelain"], git_root)
        if status:
            return None
        local_head = _run_git(["rev-parse", "HEAD"], git_root)
        remote_head = _run_git(["ls-remote", "https://github.com/sounio-lang/sounio.git", "HEAD"], git_root)
        if not local_head or not remote_head:
            return None
        github_head = remote_head.split()[0]
        if local_head != github_head:
            return None
        return cls(souc_path=binary, stdlib_path=stdlib, source=f"official-github-checkout:{github_head}")

    @staticmethod
    def _resolve_stdlib(binary: Path) -> Path | None:
        env_stdlib = os.getenv("SOUNIO_STDLIB_PATH")
        if env_stdlib:
            path = Path(env_stdlib).expanduser()
            if path.exists():
                return path
        for root in _default_roots():
            path = root / "stdlib"
            if path.exists():
                return path
        inferred = binary.parents[3] / "stdlib"
        if inferred.exists():
            return inferred
        return None

    def score_regions(
        self,
        deficit: list[float],
        asymmetry: list[float],
        smoothness: list[float],
        gradient_suppression: list[float],
    ) -> list[float]:
        if not all(len(values) == len(ATLAS_REGIONS) for values in (deficit, asymmetry, smoothness, gradient_suppression)):
            raise ValueError("Sounio runtime expects one feature vector per ASPECTS region.")
        source = self._build_program(deficit, asymmetry, smoothness, gradient_suppression)
        scores = self._run_program(source)
        if len(scores) != len(ATLAS_REGIONS):
            raise RuntimeError(f"Sounio runtime returned {len(scores)} values, expected {len(ATLAS_REGIONS)}.")
        return scores

    def score_linear_regions(
        self,
        feature_names: list[str],
        feature_rows: list[list[float]],
        weights: list[list[float]],
        bias: list[float],
    ) -> list[float]:
        if len(feature_rows) != len(ATLAS_REGIONS):
            raise ValueError("Sounio runtime expects one feature row per ASPECTS region.")
        if len(weights) != len(ATLAS_REGIONS) or len(bias) != len(ATLAS_REGIONS):
            raise ValueError("Sounio runtime expects one weight vector and bias per ASPECTS region.")
        feature_count = len(feature_names)
        if feature_count == 0:
            raise ValueError("Sounio runtime requires at least one feature.")
        for row in feature_rows:
            if len(row) != feature_count:
                raise ValueError("All feature rows must match feature_names length.")
        for row in weights:
            if len(row) != feature_count:
                raise ValueError("All weight rows must match feature_names length.")
        source = self._build_linear_program(feature_names, feature_rows, weights, bias)
        scores = self._run_program(source)
        if len(scores) != len(ATLAS_REGIONS):
            raise RuntimeError(f"Sounio runtime returned {len(scores)} values, expected {len(ATLAS_REGIONS)}.")
        return scores

    def infer_hypercomplex_heatmap(
        self,
        volume: np.ndarray,
        asymmetry: np.ndarray,
        gradient: np.ndarray,
        contrast: np.ndarray,
        atlas: dict[str, np.ndarray],
        removed_component: str | None = None,
    ) -> SounioInferenceResult:
        shape = self._validate_volumetric_inputs(volume, asymmetry, gradient, contrast)
        source = self._build_hypercomplex_inference_program(
            shape=shape,
            volume=_flatten_float_array(volume),
            asymmetry=_flatten_float_array(asymmetry),
            gradient=_flatten_float_array(gradient),
            contrast=_flatten_float_array(contrast),
            region_bits=_atlas_region_bits(atlas, shape),
            removed_component=removed_component,
        )
        return self._parse_inference_output(self._run_program_lines(source), shape)

    def infer_artifact_heatmap(
        self,
        feature_maps: dict[str, np.ndarray],
        atlas: dict[str, np.ndarray],
        feature_names: list[str],
        weights: list[list[float]],
        bias: list[float],
        feature_mean: list[float],
        feature_std: list[float],
    ) -> SounioInferenceResult:
        if not feature_names:
            raise ValueError("Sounio artifact inference requires at least one feature.")
        first_feature = feature_maps[feature_names[0]]
        shape = self._validate_feature_maps(feature_maps, feature_names)
        if len(weights) != len(ATLAS_REGIONS) or len(bias) != len(ATLAS_REGIONS):
            raise ValueError("Artifact weights and bias must contain one row per ASPECTS region.")
        if len(feature_mean) != len(feature_names) or len(feature_std) != len(feature_names):
            raise ValueError("Artifact feature normalization vectors must match feature_names length.")
        source = self._build_artifact_inference_program(
            shape=shape,
            feature_names=feature_names,
            feature_columns={name: _flatten_float_array(feature_maps[name]) for name in feature_names},
            region_bits=_atlas_region_bits(atlas, shape),
            weights=weights,
            bias=bias,
            feature_mean=feature_mean,
            feature_std=feature_std,
        )
        return self._parse_inference_output(self._run_program_lines(source), tuple(first_feature.shape))

    def _run_program(self, source: str) -> list[float]:
        scores: list[float] = []
        for line in self._run_program_lines(source):
            if line:
                scores.append(float(line))
        return scores

    def _run_program_lines(self, source: str) -> list[str]:
        env = os.environ.copy()
        env["SOUNIO_STDLIB_PATH"] = str(self.stdlib_path)
        with tempfile.TemporaryDirectory(prefix="sounio-runtime-") as tmp_dir:
            program_path = Path(tmp_dir) / "runtime_program.sio"
            program_path.write_text(source, encoding="utf-8")
            completed = subprocess.run(
                [str(self.souc_path), "run", str(program_path)],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
        return [line.strip() for line in completed.stdout.splitlines() if line.strip()]

    @staticmethod
    def _validate_volumetric_inputs(*arrays: np.ndarray) -> tuple[int, int, int]:
        shapes = {tuple(np.asarray(array).shape) for array in arrays}
        if len(shapes) != 1:
            raise ValueError("All volumetric Sounio inputs must share the same shape.")
        shape = next(iter(shapes))
        if len(shape) != 3:
            raise ValueError("Volumetric Sounio inference expects 3D arrays.")
        return shape

    @staticmethod
    def _validate_feature_maps(feature_maps: dict[str, np.ndarray], feature_names: list[str]) -> tuple[int, int, int]:
        missing = [name for name in feature_names if name not in feature_maps]
        if missing:
            raise ValueError(f"Artifact feature maps missing required keys: {', '.join(missing)}.")
        shape = tuple(np.asarray(feature_maps[feature_names[0]]).shape)
        if len(shape) != 3:
            raise ValueError("Artifact feature maps must be 3D.")
        for name in feature_names[1:]:
            if tuple(np.asarray(feature_maps[name]).shape) != shape:
                raise ValueError("All artifact feature maps must share the same shape.")
        return shape

    @staticmethod
    def _parse_inference_output(lines: list[str], shape: tuple[int, int, int]) -> SounioInferenceResult:
        section: str | None = None
        region_scores: list[float] = []
        heatmap_values: list[float] = []
        for line in lines:
            if line == "__REGIONS__":
                section = "regions"
                continue
            if line == "__HEATMAP__":
                section = "heatmap"
                continue
            if section == "regions":
                region_scores.append(float(line))
                continue
            if section == "heatmap":
                heatmap_values.append(float(line))
        expected_voxels = int(np.prod(shape))
        if len(region_scores) != len(ATLAS_REGIONS):
            raise RuntimeError(
                f"Sounio volumetric inference returned {len(region_scores)} region scores, expected {len(ATLAS_REGIONS)}."
            )
        if len(heatmap_values) != expected_voxels:
            raise RuntimeError(
                f"Sounio volumetric inference returned {len(heatmap_values)} voxels, expected {expected_voxels}."
            )
        heatmap = np.asarray(heatmap_values, dtype=np.float32).reshape(shape)
        return SounioInferenceResult(region_scores=region_scores, heatmap=heatmap)

    def _runtime_prelude(self) -> str:
        return """
fn clamp_unit(x: f64) -> f64 {
    if x < 0.0 {
        0.0
    } else {
        if x > 1.0 {
            1.0
        } else {
            x
        }
    }
}

fn clamp_nonnegative(x: f64) -> f64 {
    if x < 0.0 {
        0.0
    } else {
        x
    }
}

fn sigmoid(x: f64) -> f64 {
    var clipped = x
    if clipped < -60.0 {
        clipped = -60.0
    }
    if clipped > 60.0 {
        clipped = 60.0
    }
    1.0 / (1.0 + exp(-clipped))
}

fn idx(z: i64, y: i64, x: i64, height: i64, width: i64) -> i64 {
    z * height * width + y * width + x
}

fn region_member(bits: i64, region_index: i64) -> bool {
    (bits & (1 << region_index)) != 0
}

fn max_value(values: [f64]) -> f64 {
    var best: f64 = 0.0
    for value in values {
        if value > best {
            best = value
        }
    }
    if best > 0.0 {
        best
    } else {
        1.0
    }
}

fn neighborhood_mean(input: [f64], z: i64, y: i64, x: i64, depth: i64, height: i64, width: i64) -> f64 {
    var total: f64 = 0.0
    var count: f64 = 0.0
    for dz in -1..2 {
        for dy in -1..2 {
            for dx in -1..2 {
                let nz = z + dz
                let ny = y + dy
                let nx = x + dx
                if nz >= 0 && nz < depth && ny >= 0 && ny < height && nx >= 0 && nx < width {
                    total = total + input[idx(nz, ny, nx, height, width) as usize]
                    count = count + 1.0
                }
            }
        }
    }
    if count > 0.0 {
        total / count
    } else {
        0.0
    }
}

fn region_score(deficit: f64, asymmetry: f64, smoothness: f64, gradient_suppression: f64, use_energy: bool, use_phase: bool) -> f64 {
    var energy = sqrt(
        deficit * deficit +
        asymmetry * asymmetry +
        smoothness * smoothness +
        gradient_suppression * gradient_suppression
    )
    if !use_energy {
        energy = 0.0
    }
    var coupling = clamp_nonnegative(
        deficit * asymmetry +
        smoothness * gradient_suppression -
        asymmetry * gradient_suppression
    )
    if !use_phase {
        coupling = 0.0
    }
    let core =
        2.4 * deficit * (0.2 + asymmetry) +
        1.0 * energy * (0.15 + asymmetry) +
        0.4 * coupling +
        0.2 * smoothness * asymmetry
    clamp_unit(sigmoid(5.0 * (core - 0.85)))
}
"""

    def _build_program(
        self,
        deficit: list[float],
        asymmetry: list[float],
        smoothness: list[float],
        gradient_suppression: list[float],
    ) -> str:
        return f"""// Generated by sounio_stroke_lab for regional hypercomplex scoring.
struct HyperRegion {{
    r: f64,
    i: f64,
    j: f64,
    k: f64,
}}

fn clamp_unit(x: f64) -> f64 {{
    if x < 0.0 {{
        0.0
    }} else {{
        if x > 1.0 {{
            1.0
        }} else {{
            x
        }}
    }}
}}

fn sigmoid(x: f64) -> f64 {{
    1.0 / (1.0 + exp(-x))
}}

fn hyper_energy(h: HyperRegion) -> f64 {{
    sqrt(h.r * h.r + h.i * h.i + h.j * h.j + h.k * h.k)
}}

fn hyper_coupling(h: HyperRegion) -> f64 {{
    let raw = h.r * h.i + h.j * h.k - h.i * h.k
    if raw < 0.0 {{
        0.0
    }} else {{
        raw
    }}
}}

fn region_score(deficit: f64, asymmetry: f64, smoothness: f64, gradient_suppression: f64) -> f64 {{
    let h = HyperRegion {{
        r: deficit,
        i: asymmetry,
        j: smoothness,
        k: gradient_suppression,
    }}
    let energy = hyper_energy(h)
    let coupling = hyper_coupling(h)
    let core =
        2.4 * deficit * (0.2 + asymmetry) +
        1.0 * energy * (0.15 + asymmetry) +
        0.4 * coupling +
        0.2 * smoothness * asymmetry
    clamp_unit(sigmoid(5.0 * (core - 0.85)))
}}

fn main() with IO {{
    let deficit = {_array_literal(deficit)}
    let asymmetry = {_array_literal(asymmetry)}
    let smoothness = {_array_literal(smoothness)}
    let gradient_suppression = {_array_literal(gradient_suppression)}

    for i in 0..10 {{
        let score = region_score(deficit[i], asymmetry[i], smoothness[i], gradient_suppression[i])
        println(score)
    }}
}}
"""

    def _build_linear_program(
        self,
        feature_names: list[str],
        feature_rows: list[list[float]],
        weights: list[list[float]],
        bias: list[float],
    ) -> str:
        feature_columns = []
        for feature_index, feature_name in enumerate(feature_names):
            column = [row[feature_index] for row in feature_rows]
            feature_columns.append((feature_name, column))

        feature_defs = "\n    ".join(
            f"let feature_{feature_name} = {_array_literal(column)}"
            for feature_name, column in feature_columns
        )

        output_lines = []
        for region_index in range(len(ATLAS_REGIONS)):
            terms = [
                f"{_format_number(weights[region_index][feature_index])} * feature_{feature_name}[{region_index}]"
                for feature_index, feature_name in enumerate(feature_names)
            ]
            expression = " + ".join(terms + [_format_number(bias[region_index])])
            output_lines.append(f"    println(clamp_unit(sigmoid({expression})))")
        output_body = "\n".join(output_lines)

        return f"""// Generated by sounio_stroke_lab for trained regional linear scoring.
fn clamp_unit(x: f64) -> f64 {{
    if x < 0.0 {{
        0.0
    }} else {{
        if x > 1.0 {{
            1.0
        }} else {{
            x
        }}
    }}
}}

fn sigmoid(x: f64) -> f64 {{
    1.0 / (1.0 + exp(-x))
}}

fn main() with IO {{
    {feature_defs}
{output_body}
}}
"""

    def _build_hypercomplex_inference_program(
        self,
        shape: tuple[int, int, int],
        volume: list[float],
        asymmetry: list[float],
        gradient: list[float],
        contrast: list[float],
        region_bits: list[int],
        removed_component: str | None,
    ) -> str:
        depth, height, width = shape
        total_voxels = depth * height * width
        use_phase = "true" if removed_component != "hypercomplex_phase" else "false"
        use_energy = "true" if removed_component != "hypercomplex_energy" else "false"
        use_asymmetry = "true" if removed_component != "asymmetry_channel" else "false"
        return f"""// Generated by sounio_stroke_lab for volumetric hypercomplex inference.
{self._runtime_prelude()}

fn main() with IO {{
    let depth: i64 = {depth}
    let height: i64 = {height}
    let width: i64 = {width}
    let total_voxels: i64 = {total_voxels}
    let use_phase: bool = {use_phase}
    let use_energy: bool = {use_energy}
    let use_asymmetry: bool = {use_asymmetry}

    let volume = {_array_literal(volume)}
    let asymmetry_input = {_array_literal(asymmetry)}
    let gradient = {_array_literal(gradient)}
    let contrast = {_array_literal(contrast)}
    let region_bits = {_int_array_literal(region_bits)}

    var deficit: [f64; {total_voxels}] = [0.0; {total_voxels}]
    var asymmetry: [f64; {total_voxels}] = [0.0; {total_voxels}]
    var smoothness: [f64; {total_voxels}] = [0.0; {total_voxels}]
    var gradient_suppression: [f64; {total_voxels}] = [0.0; {total_voxels}]
    var energy: [f64; {total_voxels}] = [0.0; {total_voxels}]
    var coupling: [f64; {total_voxels}] = [0.0; {total_voxels}]
    var local_evidence_raw: [f64; {total_voxels}] = [0.0; {total_voxels}]
    var local_evidence: [f64; {total_voxels}] = [0.0; {total_voxels}]
    var region_scores: [f64; 10] = [0.0; 10]
    var signal: [f64; {total_voxels}] = [0.0; {total_voxels}]
    var heatmap: [f64; {total_voxels}] = [0.0; {total_voxels}]

    for voxel_index in 0..total_voxels {{
        let deficit_value = clamp_unit(clamp_nonnegative(0.54 - volume[voxel_index]))
        let asymmetry_raw = clamp_unit(asymmetry_input[voxel_index])
        let smoothness_value = clamp_unit(1.0 - contrast[voxel_index])
        let gradient_value = clamp_unit(1.0 - gradient[voxel_index])
        let energy_value = sqrt(
            deficit_value * deficit_value +
            asymmetry_raw * asymmetry_raw +
            smoothness_value * smoothness_value +
            gradient_value * gradient_value
        )
        let coupling_value = clamp_nonnegative(
            deficit_value * asymmetry_raw +
            smoothness_value * gradient_value -
            asymmetry_raw * gradient_value
        )
        deficit[voxel_index] = deficit_value
        asymmetry[voxel_index] = if use_asymmetry {{ asymmetry_raw }} else {{ 0.0 }}
        smoothness[voxel_index] = smoothness_value
        gradient_suppression[voxel_index] = gradient_value
        energy[voxel_index] = energy_value
        coupling[voxel_index] = coupling_value
    }}

    let energy_scale = max_value(energy)
    let coupling_scale = max_value(coupling)

    for voxel_index in 0..total_voxels {{
        var energy_value = clamp_unit(energy[voxel_index] / energy_scale)
        var coupling_value = clamp_unit(coupling[voxel_index] / coupling_scale)
        if !use_energy {{
            energy_value = 0.0
        }}
        if !use_phase {{
            coupling_value = 0.0
        }}
        energy[voxel_index] = energy_value
        coupling[voxel_index] = coupling_value
        local_evidence_raw[voxel_index] =
            1.8 * deficit[voxel_index] * (0.2 + asymmetry[voxel_index]) +
            0.9 * energy_value * (0.15 + asymmetry[voxel_index]) +
            0.35 * coupling_value +
            0.15 * smoothness[voxel_index] * asymmetry[voxel_index]
    }}

    let local_scale = max_value(local_evidence_raw)
    for voxel_index in 0..total_voxels {{
        local_evidence[voxel_index] = clamp_unit(local_evidence_raw[voxel_index] / local_scale)
    }}

    for region_index in 0..10 {{
        var deficit_sum: f64 = 0.0
        var asymmetry_sum: f64 = 0.0
        var smoothness_sum: f64 = 0.0
        var gradient_sum: f64 = 0.0
        var count: f64 = 0.0
        for voxel_index in 0..total_voxels {{
            if region_member(region_bits[voxel_index], region_index) {{
                deficit_sum = deficit_sum + deficit[voxel_index]
                asymmetry_sum = asymmetry_sum + asymmetry[voxel_index]
                smoothness_sum = smoothness_sum + smoothness[voxel_index]
                gradient_sum = gradient_sum + gradient_suppression[voxel_index]
                count = count + 1.0
            }}
        }}
        if count > 0.0 {{
            region_scores[region_index] = region_score(
                deficit_sum / count,
                asymmetry_sum / count,
                smoothness_sum / count,
                gradient_sum / count,
                use_energy,
                use_phase
            )
        }}
    }}

    for voxel_index in 0..total_voxels {{
        var region_map: f64 = 0.0
        for region_index in 0..10 {{
            if region_member(region_bits[voxel_index], region_index) {{
                region_map = region_map + region_scores[region_index]
            }}
        }}
        signal[voxel_index] =
            region_map * (0.25 + local_evidence[voxel_index]) +
            0.06 * gradient_suppression[voxel_index] * asymmetry[voxel_index]
    }}

    for z in 0..depth {{
        for y in 0..height {{
            for x in 0..width {{
                let voxel_index = idx(z, y, x, height, width)
                let smoothed = neighborhood_mean(signal, z, y, x, depth, height, width)
                heatmap[voxel_index as usize] = clamp_unit(sigmoid(5.0 * (smoothed - 0.42)))
            }}
        }}
    }}

    println("__REGIONS__")
    for region_index in 0..10 {{
        println(region_scores[region_index])
    }}
    println("__HEATMAP__")
    for voxel_index in 0..total_voxels {{
        println(heatmap[voxel_index])
    }}
}}
"""

    def _build_artifact_inference_program(
        self,
        shape: tuple[int, int, int],
        feature_names: list[str],
        feature_columns: dict[str, list[float]],
        region_bits: list[int],
        weights: list[list[float]],
        bias: list[float],
        feature_mean: list[float],
        feature_std: list[float],
    ) -> str:
        depth, height, width = shape
        total_voxels = depth * height * width
        feature_defs = []
        weight_defs = []
        sum_defs = []
        sum_updates = []
        standardized_defs = []
        logit_terms = []
        local_evidence_terms = []
        feature_scale = 1.0 / float(len(feature_names))
        for feature_index, feature_name in enumerate(feature_names):
            feature_defs.append(f"    let feature_{feature_name} = {_array_literal(feature_columns[feature_name])}")
            weight_column = [row[feature_index] for row in weights]
            weight_defs.append(f"    let weight_{feature_name} = {_array_literal(weight_column)}")
            sum_defs.append(f"        var sum_{feature_name}: f64 = 0.0")
            sum_updates.append(
                f"                sum_{feature_name} = sum_{feature_name} + feature_{feature_name}[voxel_index]"
            )
            standardized_defs.append(
                "            let standardized_{name} = ((sum_{name} / count) - {mean}) / {std}".format(
                    name=feature_name,
                    mean=_format_number(feature_mean[feature_index]),
                    std=_format_number(feature_std[feature_index]),
                )
            )
            logit_terms.append(f"weight_{feature_name}[region_index] * standardized_{feature_name}")
            local_evidence_terms.append(f"feature_{feature_name}[voxel_index]")

        signal_local_evidence = (
            f"({ ' + '.join(local_evidence_terms) }) * {_format_number(feature_scale)}"
            if local_evidence_terms
            else "0.5"
        )
        return f"""// Generated by sounio_stroke_lab for volumetric trained artifact inference.
{self._runtime_prelude()}

fn main() with IO {{
    let depth: i64 = {depth}
    let height: i64 = {height}
    let width: i64 = {width}
    let total_voxels: i64 = {total_voxels}
    let region_bits = {_int_array_literal(region_bits)}
    let bias = {_array_literal(bias)}
{chr(10).join(feature_defs)}
{chr(10).join(weight_defs)}

    var local_evidence: [f64; {total_voxels}] = [0.0; {total_voxels}]
    var region_scores: [f64; 10] = [0.0; 10]
    var signal: [f64; {total_voxels}] = [0.0; {total_voxels}]
    var heatmap: [f64; {total_voxels}] = [0.0; {total_voxels}]

    for voxel_index in 0..total_voxels {{
        local_evidence[voxel_index] = clamp_unit({signal_local_evidence})
    }}

    for region_index in 0..10 {{
{chr(10).join(sum_defs)}
        var count: f64 = 0.0
        for voxel_index in 0..total_voxels {{
            if region_member(region_bits[voxel_index], region_index) {{
{chr(10).join(sum_updates)}
                count = count + 1.0
            }}
        }}
        if count > 0.0 {{
{chr(10).join(standardized_defs)}
            let logit = {" + ".join(logit_terms)} + bias[region_index]
            region_scores[region_index] = clamp_unit(sigmoid(logit))
        }}
    }}

    for voxel_index in 0..total_voxels {{
        var region_map: f64 = 0.0
        for region_index in 0..10 {{
            if region_member(region_bits[voxel_index], region_index) {{
                region_map = region_map + region_scores[region_index]
            }}
        }}
        signal[voxel_index] = region_map * (0.8 + 0.4 * local_evidence[voxel_index])
    }}

    for z in 0..depth {{
        for y in 0..height {{
            for x in 0..width {{
                let voxel_index = idx(z, y, x, height, width)
                heatmap[voxel_index as usize] = clamp_unit(neighborhood_mean(signal, z, y, x, depth, height, width))
            }}
        }}
    }}

    println("__REGIONS__")
    for region_index in 0..10 {{
        println(region_scores[region_index])
    }}
    println("__HEATMAP__")
    for voxel_index in 0..total_voxels {{
        println(heatmap[voxel_index])
    }}
}}
"""
