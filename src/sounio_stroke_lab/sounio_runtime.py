from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

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
        env = os.environ.copy()
        env["SOUNIO_STDLIB_PATH"] = str(self.stdlib_path)
        with tempfile.TemporaryDirectory(prefix="sounio-runtime-") as tmp_dir:
            program_path = Path(tmp_dir) / "region_scoring.sio"
            program_path.write_text(source, encoding="utf-8")
            completed = subprocess.run(
                [str(self.souc_path), "run", str(program_path)],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
        scores: list[float] = []
        for line in completed.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            scores.append(float(line))
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

    def _run_program(self, source: str) -> list[float]:
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
        scores: list[float] = []
        for line in completed.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            scores.append(float(line))
        return scores

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
